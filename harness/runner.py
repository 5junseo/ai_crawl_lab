"""매트릭스 실행 (도구 x 케이스 x 반복).

  python -m harness.runner --tools echo --repeat 1
  python -m harness.runner --phase 1 --targets t1_d2b --repeat 1
  python -m harness.runner --tools autoscraper --task list
  python -m harness.runner --tools crawl4ai_llm --repeat 3 --concurrency 8

결과는 results/<run_id>/<tool>/<case_id>__rN.json 에 '원본 그대로' 남긴다.
채점 로직이 바뀌어도 재실행 없이 재채점할 수 있어야 하기 때문이다(README §5).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import registry                      # noqa: E402
from adapters.base import BUDGET_STOP, Metrics, RunResult   # noqa: E402
from harness import meter, schema_utils as su       # noqa: E402
from harness.dotenv import load_env                 # noqa: E402
from harness.replay_server import ReplayServer, iter_cases  # noqa: E402

load_env()   # .env 없으면 조용히 넘어간다 (비LLM 도구는 키가 필요 없다)

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIXTURES = ROOT / "fixtures"


def new_run_id(tag: str = "") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{tag}" if tag else stamp


def select_cases(targets: Optional[list] = None, limit: Optional[int] = None,
                 task: Optional[str] = None) -> list:
    cases = iter_cases(FIXTURES)
    if targets:
        cases = [c for c in cases if c["target_id"] in targets]
    if task:
        cases = [c for c in cases if c.get("task", su.DEFAULT_TASK) == task]
    if limit:
        by_target: dict[str, int] = {}
        picked = []
        for c in cases:
            n = by_target.get(c["target_id"], 0)
            if n < limit:
                picked.append(c)
                by_target[c["target_id"]] = n + 1
        cases = picked
    return cases


def _run_one(adapter: Any, case: dict, url: str, run_id: str,
             repeat_i: int, model: Optional[str], litellm_log: Optional[str]) -> RunResult:
    # 목록 페이지와 상세 페이지는 스키마가 다르다. fixture 의 meta.task 가 그걸 고른다.
    task = case.get("task", su.DEFAULT_TASK)
    schema = su.load_schema(task)
    t0 = time.time()
    adapter.opts["case_id"] = case["case_id"]
    adapter.opts["task"] = task
    try:
        res = adapter.extract(url, schema)
    except Exception as e:                      # 어댑터가 예외를 흘려도 매트릭스는 계속 돈다
        res = adapter._fail(case["case_id"], e)
    t1 = time.time()

    res.target_id = case["target_id"]
    res.meta.update({"run_id": run_id, "repeat": repeat_i, "url": url, "task": task,
                     "origin_url": case.get("origin_url"), "model": model})

    if getattr(adapter, "needs_llm", False):
        # 태그를 못 실는 도구는 시간창만으로 가른다(동시성 1 강제 — run_tool 참고).
        tag = f"{run_id}:{adapter.name}" if getattr(adapter, "llm_tagged", True) else None
        usage = meter.read_litellm_window(t0, t1, tag=tag, log_path=litellm_log)
        if usage.calls:
            meter.attach_cost(res.metrics, model=model, usage=usage)
        res.meta["cost_attribution"] = "tag" if tag else "window_only"
    return res


def _budget_skip(case: dict, url: str, run_id: str, repeat_i: int,
                 model: Optional[str], tool: str) -> RunResult:
    """앞 케이스에서 지갑이 멈춘 뒤의 케이스. **도구를 부르지 않고** 그대로 적는다.

    한 번 상한에 걸리면 남은 케이스도 전부 같은 곳에서 걸린다. 그래도 계속 부르면 같은
    예외를 케이스 수만큼 다시 받으면서 시간만 쓴다. 그렇다고 행을 아예 안 남기면 나중에
    '왜 n 이 작지?' 가 된다. **안 쟀다는 사실을 행으로 남긴다.**
    """
    task = case.get("task", su.DEFAULT_TASK)
    return RunResult(
        case_id=case["case_id"], tool=tool, ok=False, target_id=case["target_id"],
        error_class=BUDGET_STOP,
        error_msg="앞선 케이스에서 예산 상한에 걸렸다. 이 케이스는 도구를 부르지 않았다 "
                  "- 도구 실패가 아니라 미측정이다",
        metrics=Metrics(),
        meta={"run_id": run_id, "repeat": repeat_i, "url": url, "task": task,
              "origin_url": case.get("origin_url"), "model": model,
              "budget_stop": "cascade"})


def save(res: RunResult, run_id: str) -> Path:
    out = RESULTS / run_id / res.tool
    out.mkdir(parents=True, exist_ok=True)
    # case_id 는 대상 안에서만 유일하다. G2B 와 한전에 똑같이 'ntc-p1' 이 있어서
    # 대상을 안 붙이면 한쪽이 다른 쪽을 조용히 덮는다.
    p = out / f"{res.target_id or 'unknown'}__{res.case_id}__r{res.meta.get('repeat', 0)}.json"
    p.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p


def run_tool(tool: str, cases: list, srv: ReplayServer, *, run_id: str, repeat: int,
             concurrency: int, model: Optional[str], litellm_log: Optional[str],
             live: bool) -> dict:
    try:
        cls = registry.load(tool)
    except registry.AdapterUnavailable as e:
        print(f"  [{tool}] SKIP - {e}")
        return {"tool": tool, "skipped": str(e), "runs": 0}

    stats = {"tool": tool, "runs": 0, "ok": 0, "fail": 0, "budget_stop": 0}
    # 어댑터에 model/run_id 를 넘긴다. LLM 도구는 어떤 모델을 부를지 알아야 하고,
    # 프록시 로그를 run_id:tool 태그로 걸러 내려면 그 태그를 요청에 실어야 한다.
    # 기존 어댑터는 opts 를 무시하므로 영향이 없다.
    if concurrency > 1 and getattr(cls, "needs_llm", False) and not getattr(cls, "llm_tagged", True):
        # 요청에 태그를 못 실는 도구는 프록시 로그를 시간창으로만 가른다. 동시에 돌리면
        # 남의 호출이 섞여 들어와도 **아무 경고 없이** 비용이 그럴듯하게 나온다.
        print(f"  [{tool}] SKIP - 태그를 못 싣는 도구라 --concurrency 1 에서만 비용이 정확하다")
        return {"tool": tool, "skipped": "llm_tagged=False 인데 concurrency>1", "runs": 0}

    with cls(model=model, run_id=run_id) as adapter:   # __enter__ 가 콜드 스타트를 잰다
        print(f"  [{tool}] setup {adapter.setup_ms:.0f}ms, cases={len(cases)} x{repeat}")
        jobs = []
        for r in range(repeat):
            for c in cases:
                url = c.get("origin_url") if live else srv.url_for(c["target_id"], c["case_id"])
                jobs.append((c, url, r))

        # 지갑이 한 번 멈추면 남은 케이스는 부르지 않는다. concurrency>1 이면 이미 날아간
        # 요청 몇 개는 그대로 끝나는데, 그것들도 상한에 걸려 돈은 안 나간다.
        stopped = threading.Event()

        def work(job):
            c, url, r = job
            if stopped.is_set():
                res = _budget_skip(c, url, run_id, r, model, tool)
            else:
                res = _run_one(adapter, c, url, run_id, r, model, litellm_log)
                if res.error_class == BUDGET_STOP:
                    stopped.set()
            save(res, run_id)
            return res

        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                results = list(ex.map(work, jobs))
        else:
            results = [work(j) for j in jobs]

        for res in results:
            stats["runs"] += 1
            if res.error_class == BUDGET_STOP:
                # 실패로 세지 않는다. 도구가 아니라 지갑이 멈춘 것이고, 이 케이스는
                # 실패한 게 아니라 안 쟀다.
                stats["budget_stop"] += 1
                flag = ".. 예산정지(미측정)"
            else:
                stats["ok" if res.ok else "fail"] += 1
                flag = "ok " if res.ok else f"!! {res.error_class}"
            wall = res.metrics.wall_ms or 0
            print(f"    {res.case_id:<22s} [{res.meta.get('task','?'):<6s}] "
                  f"r{res.meta.get('repeat')} {flag} {wall:8.0f}ms")
        if stats["budget_stop"]:
            print(f"  [{tool}] ** 예산 상한에 걸려 {stats['budget_stop']}건을 재지 못했다. "
                  f"도구 실패가 아니다 — 채점에서 빠진다. "
                  f"`python -m harness.ledger` 로 확인하고 AICRAWL_MAX_USD 를 올려라")
    return stats


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="도구 x 케이스 매트릭스 실행")
    ap.add_argument("--tools", nargs="*", help="도구 이름들 (기본: --phase 로 선택)")
    ap.add_argument("--phase", type=int, help="README §7 단계별 도구 묶음")
    ap.add_argument("--targets", nargs="*", help="대상 필터 (예: t1_d2b t2_g2b)")
    ap.add_argument("--limit", type=int, help="대상별 케이스 수 상한 (Phase 3 표본 최소화용)")
    ap.add_argument("--task", choices=["list", "detail"], help="태스크 한정 (기본: 둘 다)")
    ap.add_argument("--repeat", type=int, default=1, help="반복 횟수 (변동성 측정은 3)")
    ap.add_argument("--concurrency", type=int, default=1, choices=[1, 8],
                    help="README §8 속도 조건")
    ap.add_argument("--model", help="LLM 도구가 쓸 모델명 (pricing.yaml 키와 같아야 한다)")
    ap.add_argument("--litellm-log", help="LiteLLM JSONL 경로")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--replay-host", default="127.0.0.1",
                    help="리플레이 서버 바인드 주소. 컨테이너 안 도구를 재려면 0.0.0.0")
    ap.add_argument("--url-host",
                    help="도구에 알려 줄 호스트명(기본: 바인드 주소). "
                         "셀프호스팅 Firecrawl 처럼 컨테이너에서 도는 도구는 "
                         "host.docker.internal 이어야 호스트의 리플레이 서버에 닿는다")
    ap.add_argument("--live", action="store_true",
                    help="fixtures 대신 실사이트를 친다. 기본은 리플레이(사이트 무부하).")
    ap.add_argument("--tag", default="", help="run_id 접미사")
    args = ap.parse_args(argv)

    tools = args.tools or registry.by_phase(args.phase)
    if not tools:
        print("실행할 도구가 없다. --tools 또는 --phase 를 지정하라.")
        return 2

    cases = select_cases(args.targets, args.limit, args.task)
    if not cases:
        print("fixtures 가 비었다. python -m harness.capture 로 먼저 동결하라.")
        return 2

    run_id = new_run_id(args.tag)
    if args.live:
        print("[runner] 경고: --live. 실사이트를 직접 친다. 비교 공정성이 깨질 수 있다.")

    print(f"[runner] run_id={run_id} tools={tools} cases={len(cases)} "
          f"repeat={args.repeat} concurrency={args.concurrency}")

    with ReplayServer(host=args.replay_host, port=args.port, url_host=args.url_host) as srv:
        print(f"[runner] replay {srv.base_url}")
        stats = [run_tool(t, cases, srv, run_id=run_id, repeat=args.repeat,
                          concurrency=args.concurrency, model=args.model,
                          litellm_log=args.litellm_log, live=args.live)
                 for t in tools]

    manifest = {
        "run_id": run_id,
        "started": datetime.now().isoformat(timespec="seconds"),
        "tools": tools, "targets": args.targets, "repeat": args.repeat,
        "concurrency": args.concurrency, "model": args.model, "live": args.live,
        "replay_base_url": None if args.live else f"http://{args.url_host or args.replay_host}:{args.port}",
        "cases": [f"{c['target_id']}/{c['case_id']}[{c.get('task')}]" for c in cases],
        "stats": stats,
        "task": args.task,
        "schemas": {t: su.load_schema(t).get("$id") for t in su.TASKS},
    }
    mpath = RESULTS / run_id / "manifest.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[runner] done -> {mpath.parent}")
    print(f"[runner] 채점: python -m harness.report --run {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
