"""LLM 이 돌려준 값을 스키마 타입에 맞추는 **공용** 후처리.

여기에 **사이트별 분기를 넣지 않는다.** '번호 칸이 숫자인 행만 진짜 행이다' 같은 규칙을
넣는 순간 `crawl4ai_css` 에 사람이 손으로 넣었던 사이트 지식이 뒷문으로 다시 들어오고,
그러면 "사람 셀렉터 대비 얼마나 하는가"를 재는 의미가 사라진다.

**LLM 도구 전부(`crawl4ai_genschema` / `crawl4ai_llm` / `scrapegraphai` / ...)가 이 한 벌을
공유한다.** 도구마다 후처리가 다르면 도구 차이가 아니라 내 코드 차이를 재게 된다.
원래 `adapters/crawl4ai_genschema_runner.py` 안에 있었으나, 그 모듈은 top-level 에서
`crawl4ai` 를 import 하므로 다른 env 의 어댑터가 가져다 쓸 수 없어 여기로 옮겼다
(2026-09-09, 동작 변경 없음).
"""
from __future__ import annotations

import re
from typing import Any, Optional


def txt(v: Any) -> Optional[str]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    s = re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()
    return s or None


def multiline(v: Any) -> Optional[str]:
    """본문용. 줄 구조는 살리고 좌우 공백만 턴다."""
    if v is None:
        return None
    s = str(v).replace("\xa0", " ").replace("\r\n", "\n")
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if x) or None


def num(v: Any) -> Optional[Any]:
    """숫자로 못 바꾸면 **원문을 그대로 남긴다.**

    여기서 예외를 던지면 케이스 전체가 `TOOL_ERROR` 가 되어, '조회수 칸에 날짜를 넣었다'는
    한 필드짜리 실수가 '도구가 페이지를 못 읽었다'로 둔갑한다. 실패 모드를 잘못 적으면
    벤치마크가 거짓말을 한다. 원문을 남기면 채점이 `SCHEMA_VIOLATION` 으로 정확히 잡는다.
    (실측: LLM 이 D2B NEWS02 의 조회수 칸에 '2026-08-28' 을 넣었을 때 여기서 죽었다)"""
    if isinstance(v, int):
        return v
    s = txt(v)
    if s is None:
        return None
    c = re.sub(r"[,\s]", "", s)
    # 숫자 + 짧은 단위꼬리('7,750회')까지만 숫자로 본다. '2026-08-28' 처럼 숫자가 섞였을 뿐인
    # 값에서 앞자리만 떼어 오면 도구가 넣지도 않은 숫자를 내가 지어내는 꼴이 된다.
    m = re.fullmatch(r"(-?\d+)\D{0,3}", c)
    return int(m.group(1)) if m else s


def dt(v: Any) -> Optional[str]:
    s = txt(v)
    if s is None:
        return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
                  r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return s                       # 날짜로 안 읽히면 그대로 둔다. 채점이 오답으로 센다
    out = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m.group(4):
        out += f" {int(m.group(4)):02d}:{m.group(5)}"
        if m.group(6):
            out += f":{m.group(6)}"
    return out


_TRUE = {"y", "yes", "true", "1", "o", "있음", "첨부", "有"}
_FALSE = {"n", "no", "false", "0", "x", "없음", "無", "-"}


def boolean(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = txt(v)
    if s is None:
        return None                    # 빈 칸은 '모름'이다. False 로 접으면 없는 단언을 만든다
    low = s.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return True                        # 아이콘 alt·파일명 등 무언가 들어 있으면 첨부가 있는 것


def strlist(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    for x in v:
        if isinstance(x, dict):        # LLM 이 {name: ...} 로 감싸 오는 경우
            x = next((y for y in x.values() if isinstance(y, str)), None)
        s = txt(x)
        if s:
            out.append(s)
    return out


def coerce(rec: dict, rules: dict) -> dict:
    """레코드 한 건을 스키마 타입(x_score)에 맞춘다. 사이트별 분기는 넣지 않는다."""
    out: dict = {}
    for f, rule in rules.items():
        v = rec.get(f)
        if rule == "number":
            out[f] = num(v)
        elif rule == "datetime":
            out[f] = dt(v)
        elif rule == "bool":
            out[f] = boolean(v)
        elif rule == "list_set":
            out[f] = strlist(v)
        elif rule == "text_similar":
            out[f] = multiline(v)
        else:
            out[f] = txt(v)
    return out
