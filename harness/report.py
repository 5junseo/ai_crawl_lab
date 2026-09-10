"""results/<run_id> 재채점 -> CSV + Markdown (README §8 최종 리포트 컬럼).

results 는 원본 응답을 그대로 보관하므로, 채점 규칙을 고쳐도 재실행 없이 여기만 다시 돌리면 된다.

목록(list)과 상세(detail)는 성격이 달라 한 줄로 합치지 않는다. 비LLM 도구는 목록에서 강하고
LLM·에이전트는 상세에서 강한데, 합치면 그 차이가 지워지기 때문이다.

  python -m harness.report --run 20260903-153000
  python -m harness.report --run 20260903-153000 --gold-dir gold
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import BUDGET_STOP    # noqa: E402
from harness import schema_utils as su   # noqa: E402
from harness import score as S           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
GOLD = ROOT / "gold"

# `건당스텝` 은 에이전트류(browseruse/skyvern)에만 값이 있다. 이 도구들은 비용이 페이지가
# 아니라 **스텝**에 붙으므로 '건당 얼마'만 적으면 왜 그 값이 나왔는지 읽을 수 없다.
COLUMNS = ["tool", "tier", "task", "성공률", "필드정확도", "정밀도", "재현율",
           "환각", "gold미작성", "변동성", "콜드ms", "웜ms",
           "건당스텝", "건당토큰", "건당원", "1만건월원", "주요실패모드"]


def load_gold(gold_dir: Path) -> dict:
    """{(target_id, task, case_id): row}. gold/<target_id>/<task>.json 을 읽는다."""
    out: dict = {}
    if not gold_dir.exists():
        return out
    for tdir in sorted(p for p in gold_dir.iterdir() if p.is_dir()):
        for task in su.TASKS:
            p = tdir / f"{task}.json"
            if not p.exists():
                continue
            schema = su.load_schema(task)
            rows = json.loads(p.read_text(encoding="utf-8"))
            for case_id, row in rows.items():
                out[(tdir.name, task, str(case_id))] = (
                    su.GOLD_TODO if row == su.GOLD_TODO
                    else su.normalize_gold_row(row, schema))
    return out


def load_results(run_dir: Path) -> list:
    out = []
    for p in sorted(run_dir.glob("*/*.json")):
        if p.name == "manifest.json":
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"  ! 깨진 결과 파일: {p}")
    return out


def _tier_of(target_id: Optional[str]) -> str:
    return (target_id or "?").split("_", 1)[0].upper()


def _task_of(result: dict) -> str:
    return (result.get("meta") or {}).get("task") or su.DEFAULT_TASK


def _median(xs: list) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _mean(xs: list) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def build(run_id: str, gold_dir: Path = GOLD) -> dict:
    run_dir = RESULTS / run_id
    if not run_dir.exists():
        raise SystemExit(f"results/{run_id} 없음")
    gold = load_gold(gold_dir)
    results = load_results(run_dir)
    if not results:
        raise SystemExit(f"results/{run_id} 에 결과 파일이 없다")

    per_case = []
    preds_by_key = defaultdict(list)     # (tool, target, task, case) -> [data,...]
    missing_gold = set()

    excluded = []
    budget_stopped = []
    for r in results:
        task = _task_of(r)
        # 도구에 예시로 건네준 케이스는 채점하지 않는다. 정답을 알려주고 그 답을
        # 맞혔는지 세는 셈이라 점수가 아니다 (AutoScraper 는 구조상 예시가 필수다).
        why = (r.get("meta") or {}).get("excluded")
        if why:
            excluded.append(f"{r['tool']}/{r.get('target_id')}/{r['case_id']}[{task}] - {why}")
            continue
        # 예산 상한에 걸린 케이스는 **도구를 잰 값이 아니다.** 여기 넣으면 성공률이 떨어지고
        # 필드정확도에 0 이 섞이고 '주요실패모드' 가 그걸로 찍힌다 — 전부 내 지갑을 잰
        # 숫자다. 통째로 빼고 아래에 따로 센다.
        if r.get("error_class") == BUDGET_STOP:
            budget_stopped.append(f"{r['tool']}/{r.get('target_id')}/{r['case_id']}[{task}]")
            continue
        key = (r.get("target_id"), task, r["case_id"])
        preds_by_key[(r["tool"],) + key].append(r.get("data") or {})
        g = gold.get(key)
        if g is None:
            missing_gold.add(f"{key[0]}/{key[2]}[{task}]")
            continue
        cs = S.score_case(r.get("data") or {}, g, case_id=r["case_id"], tool=r["tool"],
                          target_id=r.get("target_id"), ok=r.get("ok", False),
                          error_class=r.get("error_class"), schema=su.load_schema(task))
        per_case.append({"score": cs, "result": r, "task": task})

    var_by_bucket = defaultdict(list)
    for (tool, target, task, _case), preds in preds_by_key.items():
        if len(preds) > 1:
            var_by_bucket[(tool, _tier_of(target), task)].append(
                S.variance(preds, su.load_schema(task))["variance"])

    # ------------------------------------------------------------ 집계
    rows = []
    buckets = defaultdict(list)
    for item in per_case:
        buckets[(item["score"].tool, _tier_of(item["score"].target_id), item["task"])].append(item)

    for (tool, tier, task), items in sorted(buckets.items()):
        n = len(items)
        scores = [i["score"] for i in items]
        metrics = [i["result"].get("metrics") or {} for i in items]
        oks = [i for i in items if i["result"].get("ok")]

        krw = [m.get("cost_krw") for m in metrics if m.get("cost_krw") is not None]
        krw_mean = round(sum(krw) / len(krw), 2) if krw else None
        toks = [(m.get("in_tokens") or 0) + (m.get("out_tokens") or 0) for m in metrics]

        # 1만건 환산: **한 번 쓰고 재사용하는 비용을 케이스 수만큼 곱하면 안 된다.**
        # crawl4ai_genschema 는 (대상 x 태스크)당 LLM 1회로 셀렉터를 만들고 그 뒤로는 공짜다.
        # 그걸 평균 내서 1만배 하면 실제의 100배가 넘는 숫자가 헤드라인에 박힌다
        # (실측: 한전 목록 건당 126.54원 x 1만 = 126만원. 진짜 값은 그 1회 비용 379원이다).
        # 어댑터가 meta.cost_oneoff 로 '이건 1회성'이라고 표시한 케이스를 따로 뗀다.
        oneoff_krw = sum(m.get("cost_krw") or 0
                         for i, m in zip(items, metrics)
                         if (i["result"].get("meta") or {}).get("cost_oneoff"))
        marg = [m.get("cost_krw") or 0
                for i, m in zip(items, metrics)
                if not (i["result"].get("meta") or {}).get("cost_oneoff")]
        marg_mean = (sum(marg) / len(marg)) if marg else 0.0
        if krw_mean is None:
            per_10k = None
        elif oneoff_krw:
            per_10k = round(oneoff_krw + marg_mean * 10000)
        else:
            per_10k = round(krw_mean * 10000)

        modes = defaultdict(int)
        for i in items:
            ec = i["result"].get("error_class") or i["score"].error_class
            if ec:
                modes[ec] += 1
        vlist = var_by_bucket.get((tool, tier, task), [])

        rows.append({
            "tool": tool,
            "tier": tier,
            "task": task,
            "성공률": round(len(oks) / n, 3) if n else None,
            "필드정확도": _mean([s.accuracy for s in scores]),
            "정밀도": _mean([s.precision for s in scores]) if task == "list" else None,
            "재현율": _mean([s.recall for s in scores]) if task == "list" else None,
            "환각": sum(s.hallucinations for s in scores),
            "gold미작성": sum(s.not_scored for s in scores),
            "변동성": round(sum(vlist) / len(vlist), 2) if vlist else None,
            "콜드ms": _median([m.get("setup_ms") for m in metrics]),
            "웜ms": _median([m.get("wall_ms") for m in metrics]),
            "건당스텝": _mean([m.get("steps") for m in metrics]),
            "건당토큰": round(sum(toks) / n) if n and any(toks) else None,
            "건당원": krw_mean,
            "1만건월원": per_10k,
            "주요실패모드": max(modes.items(), key=lambda kv: kv[1])[0] if modes else "-",
            "_n": n,
        })

    # 필드별 정확도 — 어느 필드가 어려운가. 태스크마다 필드가 다르므로 따로 낸다.
    field_acc = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for item in per_case:
        for fld, (c, t) in item["score"].field_counts.items():
            cell = field_acc[(item["score"].tool, item["task"])][fld]
            cell[0] += c
            cell[1] += t

    return {
        "run_id": run_id,
        "rows": rows,
        "per_case": per_case,
        "field_acc": {f"{tool} / {task}": {f: (c[0] / c[1] if c[1] else None)
                                           for f, c in d.items()}
                      for (tool, task), d in field_acc.items()},
        "missing_gold": sorted(missing_gold),
        "excluded": sorted(excluded),
        "budget_stopped": sorted(budget_stopped),
        "cases_scored": len(per_case),
    }


# ------------------------------------------------------------------ 출력
def write_csv(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in report["rows"]:
            w.writerow(r)


def _md_table(headers: list, rows: list) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("-" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def write_md(report: dict, path: Path) -> None:
    lines = [f"# 공지사항 수집 벤치마크 — run `{report['run_id']}`", "",
             f"채점 케이스 {report['cases_scored']}건.", ""]

    for task, label in (("list", "목록 (한 페이지에서 N건 추출)"),
                        ("detail", "상세 (1건 필드 추출)")):
        rows = [r for r in report["rows"] if r["task"] == task]
        if not rows:
            continue
        # 값이 하나도 없는 칸은 표에서 뺀다 — 에이전트가 없는 run 에 빈 '건당스텝' 열이
        # 서 있으면 '0스텝'으로 읽힌다.
        cols = [c for c in COLUMNS
                if (task == "list" or c not in ("정밀도", "재현율"))
                and (c != "건당스텝" or any(r.get("건당스텝") is not None for r in rows))]
        lines += [f"## {label}", "", _md_table(cols, [[r[c] for c in cols] for r in rows]), ""]

    if report["field_acc"]:
        lines += ["## 필드별 정확도", ""]
        for key, d in sorted(report["field_acc"].items()):
            task = key.split("/")[-1].strip()
            flds = su.fields(su.load_schema(task))
            lines += [f"**{key}**", "",
                      _md_table(flds, [[None if d.get(f) is None else round(d[f], 2)
                                        for f in flds]]), ""]

    if report.get("budget_stopped"):
        n = len(report["budget_stopped"])
        lines += ["", "## 예산 정지로 **재지 못한** 케이스 (도구 실패가 아니다)", "",
                  f"{n}건. 지출 상한에 걸려 요청이 나가지 않았다. **도구가 실패한 게 아니라 "
                  "내 지갑이 멈춘 것**이므로 성공률·정확도·실패모드 어디에도 넣지 않았다. "
                  "이 도구의 그 칸들은 남은 케이스만으로 낸 값이고, 그만큼 n 이 작다.", ""]
        lines += [f"- {c}" for c in report["budget_stopped"][:50]]
        if n > 50:
            lines.append(f"- ... 외 {n - 50}건")
        lines.append("")
    if report.get("excluded"):
        lines += ["", "## 채점 제외 (도구에 예시로 준 케이스)", "",
                  "예시를 준 페이지로 채점하면 정답을 알려주고 맞혔는지 세는 셈이다. "
                  "그래서 그 도구만 n 이 작다.", ""]
        lines += [f"- {c}" for c in report["excluded"][:50]]
    if report["missing_gold"]:
        lines += ["## gold 없는 케이스 (채점 제외)", ""]
        lines += [f"- {c}" for c in report["missing_gold"][:50]]
        if len(report["missing_gold"]) > 50:
            lines.append(f"- ... 외 {len(report['missing_gold']) - 50}건")
        lines.append("")

    lines += ["## 읽는 법", "",
              "- **목록/상세를 합치지 않는다.** 비LLM 도구는 목록에서, LLM·에이전트는 상세에서 강하다.",
              "- **정밀도/재현율**(목록만): gold 에 없는 행을 만들어내면 정밀도가, 있는 행을 놓치면",
              "  재현율이 떨어진다. **필드정확도**는 짝지어진 행에서만 계산한 값이다.",
              "- **환각**은 gold 가 비어 있는데 값을 채운 횟수(목록에서는 없는 게시글을 만든 횟수)다.",
              "  정확도와 별도로 본다 — 파이프라인에 그대로 들어가면 조용히 오염되는 쪽이 이것이다.",
              "- **gold미작성**이 0이 아니면 정답셋이 덜 채워진 것이고 그 필드는 분모에서 빠졌다.",
              "  `python -m harness.gold_init --target <id> --check` 로 남은 칸을 확인하라.",
              "- **변동성**은 동일 페이지 반복 시 값이 갈린 필드 수의 평균이다(repeat>1 일 때만).",
              "- **건당원**은 LiteLLM 프록시 로그 기준이며 캐시 히트 토큰을 분리 계산한 값이다.",
              "  도구가 자체 보고한 토큰 수는 쓰지 않는다.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="results 재채점 -> CSV + Markdown")
    ap.add_argument("--run", required=True)
    ap.add_argument("--gold-dir", default=str(GOLD))
    ap.add_argument("--out", help="출력 디렉터리 (기본: results/<run_id>)")
    args = ap.parse_args(argv)

    rep = build(args.run, Path(args.gold_dir))
    out = Path(args.out or (RESULTS / args.run))
    write_csv(rep, out / "report.csv")
    write_md(rep, out / "report.md")
    (out / "scores.json").write_text(
        json.dumps([i["score"].to_dict() for i in rep["per_case"]],
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"[report] {rep['cases_scored']}건 채점")
    if rep.get("budget_stopped"):
        print(f"[report] ** 예산 정지로 미측정 {len(rep['budget_stopped'])}건 "
              f"(도구 실패 아님 — 채점에서 제외)")
    if rep.get("excluded"):
        print(f"[report] 학습 예시로 채점 제외 {len(rep['excluded'])}건")
    if rep["missing_gold"]:
        print(f"[report] gold 없음 {len(rep['missing_gold'])}건 (채점 제외)")
    for r in rep["rows"]:
        extra = (f" 정밀도={r['정밀도']} 재현율={r['재현율']}" if r["task"] == "list" else "")
        print(f"  {r['tool']:<18s} {r['tier']:<4s} {r['task']:<7s} n={r['_n']:<3d} "
              f"성공률={r['성공률']} 정확도={r['필드정확도']}{extra} "
              f"환각={r['환각']} 모드={r['주요실패모드']}")
    print(f"[report] -> {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
