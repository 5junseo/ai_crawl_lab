"""gold 대비 채점 (README §4-1, §8).

태스크가 둘이라 채점도 둘이다.

  detail  상세 1건 -> 필드별 정오
  list    목록 N건 -> gold 행과 도구 행을 제목으로 짝지은 뒤, 짝지어진 행에서만 필드 정오.
          짝을 못 찾은 gold 행 = 누락(recall), gold 에 없는 도구 행 = 환각(precision).

필드별 규칙(스키마의 x_score):
  str_exact     정규화(NFKC, 공백/괄호류 제거) 후 완전일치
  str_partial   정규화 후 한쪽이 다른 쪽을 포함하면 정답 (작성자·부서)
  number        숫자 파싱 후 일치 (조회수)
  datetime      파싱 후 일치. 예측이 더 거친 단위면 그 단위까지만 비교
  bool          Y/N, 있음/없음, O/X 를 접어서 비교
  list_set      원소 정규화 후 집합 일치. 순서는 보지 않는다 (첨부파일명)
  text_similar  토큰 자카드 유사도가 임계값 이상이면 정답 (본문)
  enum          x_enum_map 으로 원문 -> 코드 변환 후 비교

판정값:
  CORRECT / WRONG / MISSING(gold 있는데 못 채움) / HALLUCINATION(gold 없는데 채움) / SKIP(둘 다 없음)
  NOT_SCORED(gold 가 아직 __TODO__ — 분모에서 통째로 뺀다)

환각을 WRONG 과 섞지 않는 이유: 운영 파이프라인에 들어가면 조용히 오염시키는 쪽은 환각이다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import schema_utils as su

CORRECT = "CORRECT"
WRONG = "WRONG"
MISSING = "MISSING"
HALLUCINATION = "HALLUCINATION"
SKIP = "SKIP"
NOT_SCORED = "NOT_SCORED"   # gold 미작성(__TODO__) — 분모에서 제외

_PUNCT = re.compile("[\\s　()\\[\\]{}<>「」『』\"'`·,.\\-_/\\\\|:;~!@#$%^&*+=]")
# 인코딩 깨짐의 흔적. 두 가지만 본다.
#   U+FFFD  디코딩 실패로 치환된 문자. 가장 확실한 신호다.
#   한글을 다른 코드로 읽었을 때 나오는 라틴 확장 문자·기호가 3자 이상 연속
#           ('수요기관' -> 'ìˆ˜ìš”ê¸°ê´€', '조달청' -> 'Á¶´ÞÃ»'). 라틴 글자만이 아니라
#           같이 튀어나오는 기호(ˆ ˜ ´ ¶ » 등)까지 한 묶음으로 봐야 걸린다.
#
# 안 쓰는 것 — 자모 연속([ㄱ-ㆎ]{3,})과 U+FEFF(BOM). 둘 다 멀쩡한 원문에서 나온다.
# 업체명을 'ㅇㅇㅇ' 로 가린 본문(G2B ntc-648 '토탈ㅇㅇㅇ가')과 제목 맨 앞에 BOM 이 박힌
# 공지(G2B '﻿2027년도 조달청...')가 실제로 있다. 이 칸은 도구를 탓하는 자리라
# 오탐이 나면 리포트가 거짓말을 한다.
_MJ_CHARS = ("¡-ɏƒˆ˜–—‘-”"
             "†-•…‰‹›€™")
_MOJIBAKE = re.compile("�|[" + _MJ_CHARS + "]{3,}")
_ZW = re.compile("[﻿​-‍⁠]")     # 폭 없는 문자. 비교 전에 지운다.
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


# ------------------------------------------------------------------ 정규화
def norm_str(v: Any) -> str:
    if v is None:
        return ""
    s = unicodedata.normalize("NFKC", _ZW.sub("", str(v))).strip().lower()
    return _PUNCT.sub("", s)


def looks_mojibake(v: Any) -> bool:
    """디코딩 실패 흔적. ENCODING 실패모드 판정용."""
    if isinstance(v, (list, tuple)):
        return any(looks_mojibake(x) for x in v)
    return bool(v) and bool(_MOJIBAKE.search(str(v)))


_KO_UNITS = (("조", 10 ** 12), ("억", 10 ** 8), ("만", 10 ** 4))


def parse_number(v: Any) -> Optional[int]:
    """'1,234' -> 1234. '1.2만' 같은 한글 단위도 받는다."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = unicodedata.normalize("NFKC", str(v)).replace(",", "").replace(" ", "")
    s = s.replace("원", "").replace("₩", "").replace("회", "")
    if not s:
        return None
    total, rest, used = 0, s, False
    for token, mult in _KO_UNITS:
        if token in rest:
            head, rest = rest.split(token, 1)
            m = re.search(r"(\d+(?:\.\d+)?)$", head)
            if m:
                total += int(float(m.group(1)) * mult)
                used = True
    if used:
        m = re.search(r"(\d+)", rest)
        if m:
            total += int(m.group(1))
        return total
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


_DT_PATTERNS = (
    ("%Y-%m-%d %H:%M:%S", 6),
    ("%Y-%m-%d %H:%M", 5),
    ("%Y-%m-%d", 3),
    ("%Y-%m-%dT%H:%M:%S", 6),
    ("%Y%m%d%H%M%S", 6),
    ("%Y%m%d", 3),
)
_WEEKDAY = re.compile(r"\([월화수목금토일]\)")


def parse_dt(v: Any) -> Optional[tuple]:
    """(datetime, 유효 정밀도). 정밀도 3=날짜, 5=분, 6=초."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v, 6
    s = unicodedata.normalize("NFKC", str(v)).strip()
    s = re.sub(r"[./]", "-", s)
    s = _WEEKDAY.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    for fmt, prec in _DT_PATTERNS:
        try:
            return datetime.strptime(s, fmt), prec
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4) is None:
        return datetime(y, mo, d), 3
    prec = 6 if m.group(6) is not None else 5
    return datetime(y, mo, d, int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)), prec


_TRUE = {"y", "yes", "true", "o", "1", "있음", "있다", "첨부", "포함"}
_FALSE = {"n", "no", "false", "x", "0", "없음", "없다", "미첨부"}


def parse_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = norm_str(v)
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def norm_list(v: Any) -> Optional[list]:
    """첨부파일명 목록 정규화. 문자열 하나만 오면 1건짜리 목록으로 본다."""
    if v is None:
        return None
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n,;|]", v) if x.strip()]
    if not isinstance(v, (list, tuple)):
        return None
    return sorted(filter(None, (norm_str(x) for x in v)))


def tokens(v: Any) -> set:
    return set(_TOKEN.findall(unicodedata.normalize("NFKC", str(v or "")).lower()))


def similarity(a: Any, b: Any) -> float:
    """토큰 자카드. 본문처럼 긴 텍스트를 완전일치로 재면 전원 오답이 된다."""
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def to_enum(v: Any, mapping: dict, allowed: list) -> Optional[str]:
    if v is None or v == "":
        return None
    s = str(v).strip()
    if s in allowed:
        return s
    n = norm_str(s)
    for a in allowed:
        if a and norm_str(a) == n:
            return a
    # 긴 키 우선: '정정공고'가 '공고'보다 먼저 걸려야 한다
    for kor, code in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        k = norm_str(kor)
        if k and k in n:
            return code
    return s  # 매핑 불가 -> WRONG 으로 떨어진다


# ------------------------------------------------------------------ 필드 채점
def _is_empty(v: Any, rule: str) -> bool:
    if v is None:
        return True
    if rule == "list_set":
        return False          # [] 는 '첨부 없음'이라는 값이다
    if isinstance(v, bool):
        return False          # False 도 값이다
    if isinstance(v, str) and not v.strip():
        return True
    return False


def compare_field(rule: str, pred: Any, gold: Any, enum_map: Optional[dict] = None,
                  allowed: Optional[list] = None, threshold: float = 0.9) -> str:
    enum_map = enum_map or {}
    allowed = allowed or []
    p_empty, g_empty = _is_empty(pred, rule), _is_empty(gold, rule)
    if p_empty and g_empty:
        return SKIP
    if p_empty:
        return MISSING
    if g_empty:
        return HALLUCINATION

    if rule in ("number", "money"):
        a, b = parse_number(pred), parse_number(gold)
        return CORRECT if (a is not None and a == b) else WRONG
    if rule == "datetime":
        a, b = parse_dt(pred), parse_dt(gold)
        if not a or not b:
            return WRONG
        prec = min(a[1], b[1])
        fmt = "%Y-%m-%d" if prec <= 3 else ("%Y-%m-%d %H:%M" if prec == 5 else "%Y-%m-%d %H:%M:%S")
        return CORRECT if a[0].strftime(fmt) == b[0].strftime(fmt) else WRONG
    if rule == "bool":
        a, b = parse_bool(pred), parse_bool(gold)
        return CORRECT if (a is not None and a == b) else WRONG
    if rule == "list_set":
        a, b = norm_list(pred), norm_list(gold)
        return CORRECT if (a is not None and a == b) else WRONG
    if rule == "text_similar":
        return CORRECT if similarity(pred, gold) >= threshold else WRONG
    if rule == "enum":
        return CORRECT if to_enum(pred, enum_map, allowed) == to_enum(gold, enum_map, allowed) else WRONG
    if rule == "str_partial":
        a, b = norm_str(pred), norm_str(gold)
        if not a or not b:
            return WRONG
        return CORRECT if (a in b or b in a) else WRONG
    return CORRECT if norm_str(pred) == norm_str(gold) else WRONG


# ------------------------------------------------------------------ 결과 구조
@dataclass
class CaseScore:
    case_id: str
    tool: str
    target_id: Optional[str] = None
    task: str = "detail"
    verdicts: dict = field(default_factory=dict)          # detail 전용: 필드 -> 판정
    field_counts: dict = field(default_factory=dict)      # 필드 -> [correct, scored]
    correct: int = 0
    scored: int = 0
    hallucinations: int = 0
    missing: int = 0
    not_scored: int = 0
    # list 태스크 전용
    records_gold: int = 0
    records_pred: int = 0
    records_matched: int = 0
    schema_problems: list = field(default_factory=list)
    encoding_suspect: list = field(default_factory=list)
    ok: bool = True
    error_class: Optional[str] = None

    @property
    def accuracy(self) -> Optional[float]:
        return None if self.scored == 0 else self.correct / self.scored

    @property
    def precision(self) -> Optional[float]:
        return None if not self.records_pred else self.records_matched / self.records_pred

    @property
    def recall(self) -> Optional[float]:
        return None if not self.records_gold else self.records_matched / self.records_gold

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["accuracy"] = self.accuracy
        d["precision"] = self.precision
        d["recall"] = self.recall
        return d

    def _bump(self, fld: str, verdict: str) -> None:
        cell = self.field_counts.setdefault(fld, [0, 0])
        if verdict in (SKIP, HALLUCINATION, NOT_SCORED):
            if verdict == HALLUCINATION:
                self.hallucinations += 1
            elif verdict == NOT_SCORED:
                self.not_scored += 1
            return
        cell[1] += 1
        self.scored += 1
        if verdict == CORRECT:
            cell[0] += 1
            self.correct += 1
        elif verdict == MISSING:
            self.missing += 1


def _finalize(cs: CaseScore) -> CaseScore:
    if not cs.error_class:
        # 환각이 스키마 위반보다 위험하다(조용히 오염시킨다) -> 우선 노출
        if cs.encoding_suspect:
            cs.error_class = "ENCODING"
        elif cs.hallucinations:
            cs.error_class = "HALLUCINATION"
        elif cs.schema_problems:
            cs.error_class = "SCHEMA_VIOLATION"
    return cs


# ------------------------------------------------------------------ detail 채점
def score_detail(pred: dict, gold: dict, cs: CaseScore, schema: dict) -> CaseScore:
    rules = su.score_rules(schema)
    maps = su.enum_maps(schema)
    props = schema["properties"]
    for fld, rule in rules.items():
        g = gold.get(fld)
        if g == su.GOLD_TODO:
            cs.verdicts[fld] = NOT_SCORED
            cs._bump(fld, NOT_SCORED)
            continue
        v = compare_field(rule, pred.get(fld), g, maps.get(fld), props[fld].get("enum"),
                          su.similarity_threshold(schema, fld))
        cs.verdicts[fld] = v
        cs._bump(fld, v)
        if looks_mojibake(pred.get(fld)):
            cs.encoding_suspect.append(fld)
    return cs


# ------------------------------------------------------------------ list 채점
def _record_key(item: dict, schema: dict) -> str:
    k = norm_str(item.get(su.match_key(schema)))
    if k:
        return k
    fb = su.fallback_key(schema)
    return norm_str(item.get(fb)) if fb else ""


def align(pred_items: list, gold_items: list, schema: dict) -> tuple:
    """(짝지어진 [(pred, gold)], 짝 없는 pred, 짝 없는 gold).

    제목으로 짝짓는다. 같은 제목이 여러 건이면 목록 순서대로 먼저 온 것과 짝짓는다.
    """
    buckets: dict = {}
    for g in gold_items:
        buckets.setdefault(_record_key(g, schema), []).append(g)
    matched, extra = [], []
    for p in pred_items:
        k = _record_key(p, schema)
        pool = buckets.get(k)
        if pool:
            matched.append((p, pool.pop(0)))
        else:
            extra.append(p)
    leftover = [g for pool in buckets.values() for g in pool]
    return matched, extra, leftover


def score_list(pred: dict, gold: dict, cs: CaseScore, schema: dict) -> CaseScore:
    rules = su.score_rules(schema)
    maps = su.enum_maps(schema)
    props = su._props(schema)

    pred_items = pred.get("items") or []
    gold_items = gold.get("items") or []
    pred_items = [x for x in pred_items if isinstance(x, dict)]
    matched, extra, leftover = align(pred_items, gold_items, schema)

    cs.records_pred = len(pred_items)
    cs.records_gold = len(gold_items)
    cs.records_matched = len(matched)

    # 짝 없는 도구 행 = 환각(없는 글을 만들어냄) / 짝 없는 gold 행 = 누락
    cs.hallucinations += len(extra)
    cs.missing += len(leftover)

    for p, g in matched:
        for fld, rule in rules.items():
            gv = g.get(fld)
            if gv == su.GOLD_TODO:
                cs._bump(fld, NOT_SCORED)
                continue
            v = compare_field(rule, p.get(fld), gv, maps.get(fld), props[fld].get("enum"))
            cs._bump(fld, v)
            if looks_mojibake(p.get(fld)):
                cs.encoding_suspect.append(f"{fld}@{_record_key(p, schema)[:20]}")
    return cs


# ------------------------------------------------------------------ 진입점
def score_case(pred: dict, gold: Any, *, case_id: str, tool: str,
               target_id: Optional[str] = None, ok: bool = True,
               error_class: Optional[str] = None, schema: Optional[dict] = None,
               task: Optional[str] = None) -> CaseScore:
    sch = schema if schema is not None else su.load_schema(task or su.DEFAULT_TASK)
    t = su.task_of(sch)
    cs = CaseScore(case_id=case_id, tool=tool, target_id=target_id, task=t,
                   ok=ok, error_class=error_class)

    if gold == su.GOLD_TODO or gold is None:
        cs.not_scored = len(su.fields(sch))
        return cs

    pred = pred if isinstance(pred, dict) else {}
    cs.schema_problems = su.validate(pred, sch)
    if t == "list":
        score_list(pred, gold, cs, sch)
    else:
        score_detail(pred, gold, cs, sch)
    return _finalize(cs)


# ------------------------------------------------------------------ 변동성
def _norm_value(v: Any, rule: str) -> Any:
    if rule in ("number", "money"):
        return parse_number(v)
    if rule == "datetime":
        d = parse_dt(v)
        return d[0].isoformat() if d else None
    if rule == "bool":
        return parse_bool(v)
    if rule == "list_set":
        return tuple(norm_list(v) or ())
    if rule == "text_similar":
        return None      # 본문은 아래에서 유사도로 따로 본다
    return norm_str(v) or None


def variance(preds: list, schema: Optional[dict] = None) -> dict:
    """동일 (tool, case) N회 반복 결과의 값 흔들림. README §8 'variance'.

    detail: 값이 갈린 필드 수. list: 값이 갈린 필드 + 행 집합이 갈렸으면 'items' 1점.
    """
    sch = schema if schema is not None else su.load_schema()
    rules = su.score_rules(sch)
    unstable: list = []

    if su.is_list_schema(sch):
        keysets = [tuple(sorted(_record_key(i, sch) for i in (p.get("items") or [])
                                if isinstance(i, dict))) for p in preds]
        if len(set(keysets)) > 1:
            unstable.append("items")
        common = set(keysets[0]).intersection(*[set(k) for k in keysets[1:]]) if keysets else set()
        by_run = [{_record_key(i, sch): i for i in (p.get("items") or []) if isinstance(i, dict)}
                  for p in preds]
        for fld, rule in rules.items():
            if any(len({_norm_value(run.get(k, {}).get(fld), rule) for run in by_run}) > 1
                   for k in common):
                unstable.append(fld)
        return {"runs": len(preds), "unstable_fields": unstable, "variance": len(unstable)}

    for fld, rule in rules.items():
        if rule == "text_similar":
            vals = [p.get(fld) for p in preds]
            base = vals[0]
            thr = su.similarity_threshold(sch, fld)
            if any(similarity(base, v) < thr for v in vals[1:]):
                unstable.append(fld)
            continue
        if len({_norm_value(p.get(fld), rule) for p in preds}) > 1:
            unstable.append(fld)
    return {"runs": len(preds), "unstable_fields": unstable, "variance": len(unstable)}
