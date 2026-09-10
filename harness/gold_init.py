"""정답셋(gold) 작성 도구.

정답을 다른 데서 가져오지 않는다. 동결된 fixtures 를 사람이 읽고 확정한 값이 gold 다.
그래서 두 가지가 중요해진다.

  1. **null 은 단언이다.** '페이지에 그 값이 없다'는 뜻이며, 그 자리를 채운 도구는 환각으로 집계된다.
     아직 안 본 필드를 null 로 두면 멀쩡한 도구가 환각 판정을 받는다 -> 반드시 __TODO__ 로 두어라.
  2. gold 를 만든 사람이 도구 출력을 먼저 보면 그 도구 쪽으로 정답이 끌려간다.
     **fixtures 원문만 보고 작성하고, 작성이 끝난 뒤에 도구를 돌려라.**

  python -m harness.gold_init --target t1_d2b                  # 빈 템플릿 생성/보강
  python -m harness.gold_init --target t1_d2b --open NEWS01-6071   # 원문을 텍스트로 덤프
  python -m harness.gold_init --target t1_d2b --check           # 미작성/위반 점검

저장 위치: gold/<target_id>/detail.json, gold/<target_id>/list.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema_utils as su   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
GOLD = ROOT / "gold"

TODO = su.GOLD_TODO


def fixture_meta(target_id: str) -> dict:
    """{case_id: meta}"""
    d = FIXTURES / target_id
    if not d.exists():
        raise SystemExit(f"fixtures/{target_id} 없음 - harness.capture 를 먼저 돌려라")
    out = {}
    for page in sorted(d.glob("*/page.html")):
        cd = page.parent
        mp = cd / "meta.json"
        meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        meta.setdefault("task", "detail")
        out[cd.name] = meta
    return out


def gold_path(target_id: str, task: str) -> Path:
    return GOLD / target_id / f"{task}.json"


def load_gold_file(target_id: str, task: str) -> dict:
    p = gold_path(target_id, task)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_gold_file(target_id: str, task: str, data: dict) -> Path:
    p = gold_path(target_id, task)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def _todo_detail(schema: dict) -> dict:
    return {f: TODO for f in su.fields(schema)}


def scaffold(target_id: str) -> dict:
    """fixtures 에 있는데 gold 에 없는 케이스를 __TODO__ 로 채운다. 기존 값은 건드리지 않는다."""
    metas = fixture_meta(target_id)
    added = {}
    for task in su.TASKS:
        schema = su.load_schema(task)
        gold = load_gold_file(target_id, task)
        n = 0
        for case_id, meta in metas.items():
            if meta.get("task") != task:
                continue
            if case_id not in gold:
                # list 는 행 수를 사람이 정해야 하므로 통째로 __TODO__ 로 둔다
                gold[case_id] = TODO if task == "list" else _todo_detail(schema)
                n += 1
            elif task == "detail" and isinstance(gold[case_id], dict):
                for f in su.fields(schema):
                    gold[case_id].setdefault(f, TODO)
        if gold:
            save_gold_file(target_id, task, gold)
        added[task] = n
    return added


def _provenance(target_id: str) -> dict:
    """{task/case: 'ref_draft'|'human'}. 파일이 없으면 전부 사람이 쓴 것으로 본다."""
    p = GOLD / target_id / "_provenance.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def check(target_id: str) -> dict:
    metas = fixture_meta(target_id)
    rep = {"target": target_id, "tasks": {}}
    for task in su.TASKS:
        schema = su.load_schema(task)
        gold = load_gold_file(target_id, task)
        cases = [c for c, m in metas.items() if m.get("task") == task]
        info = {"fixtures": len(cases), "gold": len(gold),
                "missing_cases": [c for c in cases if c not in gold],
                "todo": {}, "problems": {}, "orphan": [c for c in gold if c not in cases]}
        for case_id, row in gold.items():
            if row == TODO:
                info["todo"][case_id] = ["(전체)"]
                continue
            if task == "list":
                items = (row or {}).get("items")
                if items is None:
                    info["problems"][case_id] = ["items 없음"]
                    continue
                bad = [f"items[{i}].{f}" for i, it in enumerate(items)
                       for f in su.fields(schema) if it.get(f, TODO) == TODO]
                if bad:
                    info["todo"][case_id] = bad[:6]
                concrete = {"items": [{k: v for k, v in it.items() if v != TODO} for it in items]}
            else:
                todo = [f for f in su.fields(schema) if row.get(f, TODO) == TODO]
                if todo:
                    info["todo"][case_id] = todo
                concrete = {k: v for k, v in row.items() if v != TODO}
            problems = su.validate(concrete, schema)
            if problems:
                info["problems"][case_id] = problems[:6]
        # 참조 추출기(harness.gold_ref)가 채운 초안은 아직 정답이 아니다.
        # 사람이 --confirm 으로 human 표시를 해야 완료로 센다.
        prov = _provenance(target_id)
        info["draft"] = sorted(c for c in gold
                               if prov.get(f"{task}/{c}", "human") == "ref_draft")
        info["done"] = bool(info["gold"]) and not info["todo"] and not info["problems"] \
            and not info["missing_cases"] and not info["draft"]
        rep["tasks"][task] = info
    return rep


def dump_text(target_id: str, case_id: str, limit: int = 8000) -> str:
    """정답을 눈으로 채우기 위한 원문 덤프. 태그를 걷어내고 공백만 정리한다."""
    d = FIXTURES / target_id / case_id
    if not d.exists():
        raise SystemExit(f"fixtures/{target_id}/{case_id} 없음")
    mp = d / "meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    body = (d / "page.html").read_bytes()
    text = body.decode(meta.get("encoding") or "utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    task = meta.get("task", "detail")
    head = (f"# {target_id}/{case_id}  [task={task}]\n"
            f"# url: {meta.get('url')}\n"
            f"# encoding: {meta.get('encoding')}  bytes: {meta.get('bytes')}\n"
            f"# 채점 필드: {', '.join(su.fields(su.load_schema(task)))}\n\n")
    return head + text[:limit]


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="gold 정답셋 작성 보조")
    ap.add_argument("--target", required=True)
    ap.add_argument("--check", action="store_true", help="미작성/위반 항목만 점검")
    ap.add_argument("--open", dest="open_case", help="케이스 원문을 텍스트로 덤프")
    ap.add_argument("--limit", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.open_case:
        print(dump_text(args.target, args.open_case, args.limit))
        return 0

    if args.check:
        rep = check(args.target)
        all_done = True
        for task, info in rep["tasks"].items():
            if not info["fixtures"] and not info["gold"]:
                continue
            print(f"[gold] {args.target}/{task}: fixtures {info['fixtures']}건 / "
                  f"gold {info['gold']}건")
            if info["missing_cases"]:
                print(f"   gold 없음 {len(info['missing_cases'])}건: {info['missing_cases'][:8]}")
            if info["orphan"]:
                print(f"   fixture 없는 gold {len(info['orphan'])}건: {info['orphan'][:8]}")
            if info["todo"]:
                print(f"   미작성(__TODO__) {len(info['todo'])}건:")
                for c, flds in list(info["todo"].items())[:8]:
                    print(f"     {c}: {', '.join(flds)}")
            if info["problems"]:
                print("   스키마 위반:")
                for c, ps in list(info["problems"].items())[:8]:
                    print(f"     {c}: {ps}")
            if info.get("draft"):
                print(f"   사람 미확인(참조 추출기 초안) {len(info['draft'])}건: "
                      f"{info['draft'][:8]}")
            print("   -> 완료" if info["done"] else "   -> 미완성")
            all_done = all_done and info["done"]
        return 0 if all_done else 1

    added = scaffold(args.target)
    for task, n in added.items():
        p = gold_path(args.target, task)
        if p.exists():
            print(f"[gold] {p} 갱신. 새 케이스 {n}건.")
    print(f"[gold] 원문 보기: python -m harness.gold_init --target {args.target} --open <case_id>")
    print(f"[gold] 점검:     python -m harness.gold_init --target {args.target} --check")
    print(f"[gold] 주의: 페이지에 값이 없으면 null(첨부는 [])로 두어라. "
          f"{TODO} 를 남기면 채점에서 빠진다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
