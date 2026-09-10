"""schema/*.json 단일 소스 관리.

태스크가 둘이다 — `list`(목록 N건)와 `detail`(상세 1건). 각각 스키마 파일이 하나씩이고,
채점 규칙(x_score)은 그 안에만 적는다. 코드가 규칙을 따로 들고 있지 않다.

- 도구/LLM 에 넘길 때는 x_ 확장 키를 제거한 순수 JSON Schema 를 준다(prompt_schema).
- gold/*.json 은 스키마와 같은 필드명을 쓴다(별도 컬럼 매핑 없음).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"

# 태스크 이름 -> 스키마 파일
TASKS = {"list": "notice_list.json", "detail": "notice_detail.json"}
DEFAULT_TASK = "detail"

# gold 에서 '아직 안 봤다'를 뜻하는 표식. null(= 값 없음 단언)과 구분해야 한다.
GOLD_TODO = "__TODO__"


@lru_cache(maxsize=8)
def load_schema(task: str = DEFAULT_TASK) -> dict[str, Any]:
    """task('list'|'detail') 또는 스키마 파일 경로를 받는다."""
    name = TASKS.get(task)
    p = SCHEMA_DIR / name if name else Path(task)
    if not p.exists():
        raise SystemExit(f"스키마 없음: {p} (task 는 {list(TASKS)} 중 하나)")
    return json.loads(p.read_text(encoding="utf-8"))


def is_list_schema(schema: dict[str, Any]) -> bool:
    return "x_list" in schema or "items" in schema.get("properties", {})


def task_of(schema: dict[str, Any]) -> str:
    return "list" if is_list_schema(schema) else "detail"


def _props(schema: dict[str, Any]) -> dict[str, Any]:
    """채점 대상 속성. list 스키마면 레코드 1건의 속성을 돌려준다."""
    if is_list_schema(schema):
        return schema["properties"]["items"]["items"]["properties"]
    return schema["properties"]


def match_key(schema: dict[str, Any]) -> str:
    return (schema.get("x_list") or {}).get("match_key", "title")


def fallback_key(schema: dict[str, Any]) -> str | None:
    return (schema.get("x_list") or {}).get("fallback_key")


def _strip_x(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_x(v) for k, v in node.items() if not k.startswith("x_")}
    if isinstance(node, list):
        return [_strip_x(v) for v in node]
    return node


def prompt_schema(schema: dict | None = None) -> dict[str, Any]:
    """도구/LLM 에 그대로 넘길 수 있는 순수 JSON Schema."""
    return _strip_x(schema if schema is not None else load_schema())


# ------------------------------------------------------------------ LLM 입력
# LLM 을 쓰는 도구들(genschema / crawl4ai_llm / scrapegraphai / firecrawl / 에이전트 2종)이
# **똑같은 문구**를 받도록 여기서 한 번만 만든다. 도구마다 다르게 쓰면 "누가 더 좋은가"가
# 아니라 "누구에게 더 친절한 프롬프트를 썼는가"를 재게 된다 (README §11 힌트 등급).
#
# 셀렉터·XPath·태그 이름은 여기에 절대 넣지 않는다. 그건 H3 이고, 이 도구들은 H1~H2 다.
_HEAD = {
    "list": ("Extract every notice row shown on this Korean public-procurement notice "
             "LIST page. One record per visible row, in screen order, including pinned "
             "rows at the top. Do not invent rows that are not on the page."),
    "detail": ("Extract the single notice shown on this Korean public-procurement notice "
               "DETAIL page. If the page does not show a value, leave it null - do not "
               "guess it and do not copy it from anywhere else on the page."),
}


def task_prompt(schema: dict | None = None) -> str:
    """도구에 주는 자연어 과제 설명. 필드 설명은 스키마(단일 소스)에서 그대로 가져온다."""
    sch = schema if schema is not None else load_schema()
    props = _props(sch)
    lines = [f"- {k} ({_typestr(v)}): {v.get('description', '').strip()}"
             for k, v in props.items()]
    head = _HEAD[task_of(sch)]
    return head + "\n\nFields to fill (descriptions are Korean and quote the " \
                  "on-screen labels):\n" + "\n".join(lines)


def _typestr(spec: dict) -> str:
    t = spec.get("type", "string")
    t = [t] if isinstance(t, str) else list(t)
    return "|".join(t)


_EXAMPLE = {"string": "문자열", "integer": 0, "boolean": True, "array": ["문자열"]}


def json_example(schema: dict | None = None) -> str:
    """target_json_example. **레코드 1건**의 모양만 보여준다.

    list 태스크라도 배열이 아니라 object 를 준다 - crawl4ai 의
    `_extract_expected_fields` 가 `.keys()` 를 부르기 때문에 배열을 주면 거기서 죽는다.
    """
    props = _props(schema if schema is not None else load_schema())
    out = {}
    for k, v in props.items():
        t = v.get("type", "string")
        t = [t] if isinstance(t, str) else list(t)
        prim = next((x for x in t if x != "null"), "string")
        out[k] = _EXAMPLE.get(prim, "문자열")
    return json.dumps(out, ensure_ascii=False, indent=2)


def fields(schema: dict | None = None) -> list[str]:
    return list(_props(schema if schema is not None else load_schema()).keys())


def score_rules(schema: dict | None = None) -> dict[str, str]:
    props = _props(schema if schema is not None else load_schema())
    return {k: v.get("x_score", "str_exact") for k, v in props.items()}


def enum_maps(schema: dict | None = None) -> dict[str, dict[str, str]]:
    props = _props(schema if schema is not None else load_schema())
    return {k: v["x_enum_map"] for k, v in props.items() if "x_enum_map" in v}


def similarity_threshold(schema: dict, field: str, default: float = 0.9) -> float:
    return float(_props(schema).get(field, {}).get("x_similarity_threshold", default))


def empty_record(schema: dict | None = None) -> dict[str, Any]:
    sch = schema if schema is not None else load_schema()
    if is_list_schema(sch):
        return {"items": []}
    return {f: None for f in fields(sch)}


def normalize_gold_row(row: dict[str, Any], schema: dict | None = None) -> dict[str, Any]:
    """gold 한 건을 스키마 형태로 정렬한다. 모르는 키는 버린다.

    null 은 '페이지에 그 값이 없다'는 단언이고, GOLD_TODO 는 '아직 안 봤다'다.
    둘을 섞으면 덜 채운 gold 때문에 멀쩡한 도구가 환각 판정을 받는다.
    """
    sch = schema if schema is not None else load_schema()
    if is_list_schema(sch):
        flds = fields(sch)
        items = row.get("items") or []
        return {"items": [{f: it.get(f) for f in flds} for it in items if isinstance(it, dict)]}
    return {f: row.get(f) for f in fields(sch)}


# ------------------------------------------------------------------ 검증
def _type_ok(v: Any, types: list[str]) -> bool:
    if isinstance(v, bool):
        return "boolean" in types
    if isinstance(v, int):
        return "integer" in types or "number" in types
    if isinstance(v, float):
        return "number" in types
    if isinstance(v, str):
        return "string" in types
    if isinstance(v, list):
        return "array" in types
    if isinstance(v, dict):
        return "object" in types
    return False


def _check_props(data: dict[str, Any], props: dict[str, Any], where: str = "") -> list[str]:
    problems: list[str] = []
    for k in data:
        if k not in props:
            problems.append(f"{where}unknown field: {k}")
    for k, spec in props.items():
        if k not in data or data[k] is None:
            continue
        v = data[k]
        types = spec.get("type", [])
        types = [types] if isinstance(types, str) else list(types)
        if not _type_ok(v, types):
            problems.append(f"{where}{k}: type {type(v).__name__} not in {types}")
            continue
        if isinstance(v, list):
            it = (spec.get("items") or {}).get("type")
            if it and any(not _type_ok(x, [it]) for x in v):
                problems.append(f"{where}{k}: array 원소 타입이 {it} 가 아니다")
        if "enum" in spec and v not in spec["enum"] and not _enum_mappable(v, spec):
            problems.append(f"{where}{k}: {v!r} not in enum {spec['enum']}")
    return problems


def validate(data: dict[str, Any], schema: dict | None = None) -> list[str]:
    """가벼운 검증. jsonschema 의존 없이 스키마 위반 사유만 문자열로 돌려준다."""
    sch = schema if schema is not None else load_schema()
    if not isinstance(data, dict):
        return [f"최상위가 object 가 아님: {type(data).__name__}"]
    if is_list_schema(sch):
        items = data.get("items")
        if items is None:
            return ["items 없음"]
        if not isinstance(items, list):
            return [f"items 가 array 가 아님: {type(items).__name__}"]
        problems = [f"unknown field: {k}" for k in data if k != "items"]
        props = _props(sch)
        for i, it in enumerate(items[:200]):
            if not isinstance(it, dict):
                problems.append(f"items[{i}]: object 가 아님")
                continue
            problems += _check_props(it, props, where=f"items[{i}].")
        return problems
    return _check_props(data, sch["properties"])


def _enum_mappable(v: Any, spec: dict[str, Any]) -> bool:
    """도구가 코드 대신 한글 라벨을 준 건 스키마 위반으로 치지 않는다.
    x_enum_map 으로 접히는 값이면 통과시키고, 실제 정오 판정은 score.compare_field 가 한다."""
    mapping = spec.get("x_enum_map")
    if not mapping or not isinstance(v, str):
        return False
    n = "".join(v.split()).lower()
    return any("".join(k.split()).lower() in n for k in mapping)


if __name__ == "__main__":
    import sys

    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK
    print(json.dumps(prompt_schema(load_schema(task)), ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ pydantic
# LLM 도구 중에는 스키마를 JSON Schema 가 아니라 **pydantic 모델로만** 받는 것들이 있다
# (ScrapeGraphAI 의 output parser, browser-use 의 output_model_schema). 그 도구들이 각자
# 변환기를 들고 있으면 같은 `schema/*.json` 에서 도구마다 다른 모델이 나올 수 있고, 그러면
# 도구를 비교하는 게 아니라 내 변환기 두 개를 비교하게 된다. 여기 하나만 둔다.
#
# pydantic 은 **함수 안에서** import 한다. 이 모듈은 비LLM env(autoscraper, scrapy_baseline)
# 에서도 import 되는데 거기엔 pydantic 이 없다.

_PY_TYPE = {"string": str, "integer": int, "number": float, "boolean": bool}


def _field_type(spec: dict[str, Any]) -> Any:
    from typing import Optional as _Opt
    t = spec.get("type", "string")
    t = [t] if isinstance(t, str) else list(t)
    prim = next((x for x in t if x != "null"), "string")
    if prim == "array":
        item = (spec.get("items") or {}).get("type", "string")
        return _Opt[list[_PY_TYPE.get(item, str)]]
    return _Opt[_PY_TYPE.get(prim, str)]


def pydantic_model(schema: dict[str, Any], name: str = "Notice") -> Any:
    """JSON Schema -> pydantic 모델. 필드명·타입·설명을 그대로 옮긴다.

    설명 문구는 `schema/*.json` 에서 오므로 프롬프트로 주는 것과 **같은 말**이다.
    목록 스키마면 `items: list[Row]` 를 가진 바깥 모델을 한 겹 더 씌운다.
    """
    from pydantic import Field, create_model

    props = _props(schema)
    row = create_model(name, **{                      # type: ignore[call-overload]
        k: (_field_type(v), Field(default=None, description=(v.get("description") or "").strip()))
        for k, v in props.items()})
    if not is_list_schema(schema):
        return row
    return create_model(f"{name}List", items=(list[row], Field(  # type: ignore[valid-type]
        default_factory=list,
        description=(schema["properties"]["items"].get("description") or "").strip())))
