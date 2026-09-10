"""프로젝트 전체 지출 원장과 상한.

## 왜 따로 필요한가

`harness/litellm_logger.py` 의 상한은 **LiteLLM 프록시를 지나는 지출만** 막는다.
그런데 Firecrawl 은 크레딧, Skyvern 은 스텝으로 과금하고 **그 요청은 프록시를 안 지난다.**
토큰 상한이 $5 로 걸려 있어도 크레딧 지출은 한 푼도 못 막는다. 여기가 그 구멍이다.

게다가 프록시 쪽 상한은 **프로세스 안에서만** 누적된다 — 프록시를 재시작하면 0 부터 다시
센다(2026-09-09 실측). 상한이라기보다 '이번에 띄운 프록시가 쓴 돈'이다.

그래서 원장을 파일로 둔다. 두 축을 한 곳에서 더한다.

    LLM 토큰   logs/litellm.jsonl 의 cost_usd 합   (프록시가 쓴다. 여기서는 읽기만)
    비토큰     logs/spend.jsonl                    (어댑터가 요청 **전에** 쓴다)

**LLM 지출을 여기 다시 적지 않는다.** 한 지출을 두 파일에 적으면 언젠가 반드시 두 번
세게 된다. 프록시 로그가 그쪽의 단일 소스이고, 이 모듈은 그걸 읽어서 더할 뿐이다.

## 상한 두 겹

    AICRAWL_MAX_USD    프로젝트 전체 USD (기본 5). 토큰 + 크레딧 + 스텝을 다 더한 값.
    services.<도구>.max_units   그 도구에 허용할 총 단위 수 (pricing.yaml)

USD 상한만 두면 단가를 잘못 적었을 때 그대로 새 나간다. 반대로 단위 상한만 두면 도구가
늘 때마다 빠뜨린다. **둘 다 걸고, 요청을 보내기 전에 본다.** 쓰고 나서 세는 건 상한이
아니라 사후 보고다.

## 쓰는 법

    from harness import ledger
    ledger.guard("firecrawl_cloud", "scrape_json", 1,        # 요청 1건분
                 run_id=self.run_id, tool=self.name, case_id=case_id)
    ...실제 요청...

`guard` 는 (1) 상한을 넘는지 보고 넘으면 `BudgetExceeded` 를 던지고, (2) 안 넘으면
원장에 먼저 적고 USD 를 돌려준다. **먼저 적는다** — 요청이 실패해도 크레딧은 이미
빠져나간 경우가 있어서, 덜 적는 쪽보다 더 적는 쪽이 안전하다.

현황 보기:  python -m harness.ledger
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from harness.meter import (PricingError, load_pricing, service_cost, usd_krw,
                           DEFAULT_LITELLM_LOG)

ROOT = Path(__file__).resolve().parent.parent
SPEND_LOG = ROOT / "logs" / "spend.jsonl"


# 예산 정지 표식. **메시지 안에 박아 둔다.**
#
# 상한이 걸리는 경로가 둘이고, 둘의 모양이 다르다.
#   1. 이 모듈의 `guard` -> 어댑터와 같은 프로세스라 `BudgetExceeded` 가 그대로 간다.
#   2. 프록시(`harness/litellm_logger.py`) -> **다른 프로세스**다. 도구는 HTTP 오류로
#      받으므로 예외 클래스가 남지 않는다. 남는 것은 메시지 문자열뿐이다.
# 그래서 양쪽 메시지에 같은 표식을 넣고, 받는 쪽은 그 문자열로 알아본다.
# 한글 문구로 맞추면 문구를 다듬을 때마다 조용히 새므로 ASCII 표식을 쓴다.
BUDGET_MARK = "[AICRAWL_BUDGET_STOP]"


class BudgetExceeded(RuntimeError):
    """상한 초과. 요청을 보내기 **전에** 던진다."""


def max_usd() -> float:
    """프로젝트 **누적** 지출 상한(USD). 추가분이 아니라 처음부터의 총액과 비교한다.

    `.env` 를 여기서 한 번 읽는다. 안 읽으면 CLI(`python -m harness.ledger`)가
    코드 기본값 $5 를 보고하는데 **실제로 강제되는 값은 `.env` 의 값**이라, 상한을
    확인하려고 부르는 바로 그 도구가 틀린 숫자를 말하게 된다(2026-09-10 실측).
    """
    try:
        from harness.dotenv import load_env
        load_env()
    except Exception:
        pass                      # .env 가 없어도 상한은 걸려야 한다
    return float(os.environ.get("AICRAWL_MAX_USD", "5") or 0)


# ------------------------------------------------------------------ 합계
def _sum_jsonl(path: Path, field: str) -> float:
    if not path.exists():
        return 0.0
    total = 0.0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line).get(field)
            except json.JSONDecodeError:
                continue
            if isinstance(v, (int, float)):
                total += float(v)
    return total


def service_units(service: Optional[str] = None) -> dict:
    """도구별 누적 단위 수(크레딧/스텝)."""
    out: dict = {}
    if not SPEND_LOG.exists():
        return out
    with SPEND_LOG.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s = r.get("service")
            if not s or (service and s != service):
                continue
            out[s] = out.get(s, 0.0) + float(r.get("units_billed") or 0)
    return out


def totals(litellm_log: Optional[str] = None) -> dict:
    """{'llm_usd', 'service_usd', 'usd', 'units': {...}} — 프로젝트 누적."""
    llm = _sum_jsonl(Path(litellm_log) if litellm_log else DEFAULT_LITELLM_LOG, "cost_usd")
    svc = _sum_jsonl(SPEND_LOG, "usd")
    return {"llm_usd": llm, "service_usd": svc, "usd": llm + svc,
            "units": service_units()}


# ------------------------------------------------------------------ 상한
def check(add_usd: float = 0.0, service: Optional[str] = None,
          add_units: float = 0.0, pricing: Optional[dict] = None) -> dict:
    """이 지출을 **더해도** 되는지 본다. 안 되면 BudgetExceeded."""
    t = totals()
    cap = max_usd()
    if cap > 0 and t["usd"] + add_usd > cap:
        raise BudgetExceeded(
            f"{BUDGET_MARK} 지출 상한 초과: 이미 ${t['usd']:.6g} "
            f"(토큰 ${t['llm_usd']:.6g} + 서비스 ${t['service_usd']:.6g}) 를 썼고 "
            f"이번 요청 ${add_usd:.6g} 를 더하면 상한 ${cap:.6g} 를 넘는다. "
            f"AICRAWL_MAX_USD 를 올려라")
    if service:
        row = (( pricing or load_pricing()).get("services") or {}).get(service) or {}
        lim = row.get("max_units")
        used = t["units"].get(service, 0.0)
        if lim is not None and used + add_units > float(lim):
            raise BudgetExceeded(
                f"{BUDGET_MARK} {service} 단위 상한 초과: 이미 {used:g}{row.get('unit','')} 를 썼고 "
                f"이번 {add_units:g} 를 더하면 상한 {lim} 를 넘는다. "
                f"pricing.yaml 의 services.{service}.max_units 를 올려라")
    return t


def charge(service: str, kind: str, units: float, usd: Optional[float],
           units_billed: Optional[float] = None, **meta: Any) -> dict:
    """원장에 한 줄 적는다. `usd` 가 None(단가 미확정)이어도 단위 수는 남긴다."""
    row = {"ts": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "service": service, "kind": kind, "units": units,
           "units_billed": units_billed if units_billed is not None else units,
           "usd": usd, "priced": usd is not None, **meta}
    SPEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SPEND_LOG.open("a", encoding="utf-8") as fh:      # append 는 원자적으로 취급
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def guard(service: str, kind: str, units: float = 1.0, *,
          pricing: Optional[dict] = None, **meta: Any) -> Optional[float]:
    """상한 확인 + 원장 기록을 한 번에. 요청을 보내기 **전에** 부른다.

    돌려주는 값은 이번 요청의 USD(단가 미확정이면 None)다.
    """
    pr = pricing or load_pricing()
    row = (pr.get("services") or {}).get(service)
    if not row:
        raise PricingError(f"pricing.yaml services 에 없음: {service}")
    mult = float((row.get("credits") or {}).get(kind, 1))
    billed = units * mult
    usd = service_cost(service, units, kind, pricing=pr)
    if usd is None:
        # 단가를 모르면 USD 상한으로는 못 막는다. **단위 상한만이라도 반드시 건다.**
        if row.get("max_units") is None:
            raise BudgetExceeded(
                f"{BUDGET_MARK} {service}: usd_per_unit 도 max_units 도 없다. 상한 없이 유료 API 를 "
                f"부를 수 없다. pricing.yaml 을 채워라")
    check(add_usd=usd or 0.0, service=service, add_units=billed, pricing=pr)
    charge(service, kind, units, usd, units_billed=billed, **meta)
    return usd


# ------------------------------------------------------------------ CLI
def main() -> int:
    t = totals()
    rate = usd_krw()
    cap = max_usd()
    print(f"토큰   ${t['llm_usd']:.6f}  ({t['llm_usd'] * rate:,.0f}원)   <- logs/litellm.jsonl")
    print(f"서비스 ${t['service_usd']:.6f}  ({t['service_usd'] * rate:,.0f}원)   <- logs/spend.jsonl")
    print(f"합계   ${t['usd']:.6f}  ({t['usd'] * rate:,.0f}원)"
          + (f"  / 상한 ${cap:.6g} ({cap * rate:,.0f}원)  남은 돈 "
             f"{(cap - t['usd']) * rate:,.0f}원" if cap > 0 else "  / 상한 없음"))
    svcs = (load_pricing().get("services") or {})
    for name, row in svcs.items():
        used = t["units"].get(name, 0.0)
        lim = row.get("max_units")
        print(f"  {name:<16} {used:g}{row.get('unit','')} "
              + (f"/ {lim} 상한" if lim is not None else "/ 단위 상한 없음"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
