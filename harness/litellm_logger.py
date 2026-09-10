"""LiteLLM proxy 커스텀 콜백 — 요청 1건 = JSONL 1줄, 그리고 **지출 상한**.

도구가 스스로 보고하는 토큰은 믿지 않는다(청킹 중복 카운트, 캐시 토큰 누락). 여기가 유일한 소스다.
meter.read_litellm_window() 가 이 파일을 읽는다.

프록시 기동:
    set PYTHONUTF8=1 && set PYTHONPATH=.
    litellm --config harness/litellm_config.yaml --port 4000

## 지출 상한을 왜 여기서 거나

`litellm_settings.max_budget` 은 **DB 가 붙어 있어야만 작동한다.** 없으면 프록시가
경고 한 줄만 남기고 요청을 전부 통과시킨다(2026-09-09 실측 — 상한 $0.0000001 로 5회 전부 통과).
Postgres 를 세우는 건 이 저장소 범위 밖이라, 상한을 프록시 안쪽 훅에서 직접 건다.

에이전트 도구는 스텝마다 대화가 누적돼 케이스 하나가 폭주할 수 있다. 그때 사람이 보고
끄기를 기다리면 늦는다. `async_pre_call_hook` 은 **호출 전에** 돌므로 초과분은 아예 안 나간다.

  AICRAWL_MAX_USD=5      상한(USD). 0 이하면 끈다. 기본 5.

누적은 **프로젝트 전체 기준**이다(`harness/ledger.py`). 프록시를 재시작해도 이어서 센다 —
프로세스 안에서만 세면 재시작이 곧 상한 해제가 되기 때문이다. 그리고 그 합계에는
Firecrawl 크레딧·Skyvern 스텝처럼 **이 프록시를 안 지나는 지출**도 들어 있다.
즉 여기서 막히는 이유가 토큰이 아닐 수도 있다.

단가는 `pricing.yaml` 을 쓴다(리포트와 같은 소스). 거기 없는 모델은 litellm 이 계산한
`response_cost` 로 대신 센다. 둘 다 없으면 그 호출은 0 으로 세고 JSONL 에 `priced: false` 를
남긴다 — 값을 지어내는 것보다 못 셌다고 적는 게 낫다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

LOG_PATH = Path(os.environ.get("LITELLM_JSONL",
                               Path(__file__).resolve().parent.parent / "logs" / "litellm.jsonl"))
_LOCK = threading.Lock()

MAX_USD = float(os.environ.get("AICRAWL_MAX_USD", "5") or 0)


try:
    from harness.ledger import BUDGET_MARK
except Exception:        # 원장을 못 불러와도 표식은 같아야 한다 (여기가 상한의 마지막 문지기다)
    BUDGET_MARK = "[AICRAWL_BUDGET_STOP]"


class BudgetExceeded(Exception):
    """지출 상한 초과.

    이 예외는 **프록시 프로세스 안에서** 죽는다. 도구가 받는 것은 HTTP 오류이고 거기 남는
    것은 메시지뿐이라, 아래 문구에 `BUDGET_MARK` 를 박아 둔다. 도구 쪽
    (`adapters/base.py`)이 그 표식을 보고 `TOOL_ERROR` 가 아니라 `BUDGET_STOP` 으로
    기록한다 — 실패한 것은 도구가 아니라 내 지갑이다.
    """


def _price(model: str, usage: dict) -> tuple:
    """(USD, 값을 실제로 매겼는가). pricing.yaml 이 우선."""
    from harness.meter import cost_of, PricingError

    name = (model or "").split("/")[-1]
    inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cached = int(usage.get("cache_read_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    try:
        return cost_of(name, inp - cached, out, cached, written), True
    except (PricingError, KeyError, TypeError):
        return 0.0, False


def _usage_of(response_obj: Any) -> dict:
    u = getattr(response_obj, "usage", None) or {}
    if hasattr(u, "model_dump"):
        u = u.model_dump()
    elif hasattr(u, "dict"):
        u = u.dict()
    elif not isinstance(u, dict):
        u = dict(getattr(u, "__dict__", {}) or {})
    return u


def _write(row: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str)
    with _LOCK, LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _row(kwargs: dict, response_obj: Any, start_time: Any, end_time: Any, ok: bool) -> dict:
    def _ts(v: Any) -> float:
        return v.timestamp() if hasattr(v, "timestamp") else float(v or 0.0)

    st, et = _ts(start_time), _ts(end_time)
    meta = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    return {
        "ok": ok,
        "model": kwargs.get("model") or getattr(response_obj, "model", None),
        "startTime": st,
        "endTime": et,
        "latency_ms": round((et - st) * 1000, 2) if st and et else None,
        "usage": _usage_of(response_obj),
        # runner 가 붙이는 태그(run_id:tool). 동시성 8 조건에서 도구를 가르는 유일한 수단이다.
        "metadata": {k: v for k, v in meta.items()
                     if k in ("tags", "user", "headers", "requester_metadata")},
        "logged_at": time.time(),
    }


try:  # 프록시 안에서만 litellm 이 import 된다
    from litellm.integrations.custom_logger import CustomLogger

    class JsonlLogger(CustomLogger):
        def __init__(self) -> None:
            super().__init__()
            self.spend_usd = 0.0
            self.unpriced_calls = 0

        # ---- 회계 -----------------------------------------------------
        def _account(self, kwargs: dict, row: dict) -> dict:
            usd, priced = _price(row.get("model") or "", row.get("usage") or {})
            if not priced:
                fallback = kwargs.get("response_cost")
                if isinstance(fallback, (int, float)):
                    usd, priced = float(fallback), True
            with _LOCK:
                if not priced:
                    self.unpriced_calls += 1
                self.spend_usd += usd
                total = self.spend_usd
            row["cost_usd"] = round(usd, 8)
            row["priced"] = priced
            row["spend_usd_running"] = round(total, 8)
            row["max_usd"] = MAX_USD
            return row

        def _log(self, kwargs, response_obj, start_time, end_time, ok: bool) -> None:
            row = _row(kwargs, response_obj, start_time, end_time, ok)
            _write(self._account(kwargs, row) if ok else row)

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._log(kwargs, response_obj, start_time, end_time, True)

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._log(kwargs, response_obj, start_time, end_time, False)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._log(kwargs, response_obj, start_time, end_time, True)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            self._log(kwargs, response_obj, start_time, end_time, False)

        # ---- 상한 -----------------------------------------------------
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
            """호출 **전에** 막는다. 넘긴 뒤에 세는 건 상한이 아니라 사후 보고다.

            합계는 원장(`harness.ledger`)에서 가져온다 — 프록시 재시작으로 초기화되지 않고,
            프록시를 안 지나는 크레딧/스텝 지출까지 같이 센다. 원장을 못 읽으면
            프로세스 안 합계로 물러선다(상한을 아예 안 거는 것보다 낫다).
            """
            if MAX_USD <= 0:
                return None
            with _LOCK:
                local, unpriced = self.spend_usd, self.unpriced_calls
            try:
                from harness import ledger
                t = ledger.totals(litellm_log=str(LOG_PATH))
                spent, detail = t["usd"], (f"토큰 ${t['llm_usd']:.6g} + "
                                           f"서비스 ${t['service_usd']:.6g}")
            except Exception as e:                      # 원장이 깨져도 요청은 막아야 한다
                spent, detail = local, f"원장을 못 읽어 프로세스 합계로 판단({type(e).__name__})"
            if spent < MAX_USD:
                return None
            raise BudgetExceeded(
                f"{BUDGET_MARK} 지출 상한 초과: ${spent:.6g} / ${MAX_USD:.6g} 를 이미 썼다 ({detail}). "
                f"AICRAWL_MAX_USD 를 올려라"
                + (f" (단가를 못 매긴 호출 {unpriced}건은 이 합계에 안 들어 있다)"
                   if unpriced else ""))

    handler = JsonlLogger()

except ImportError:      # harness 쪽 env 에서 import 해도 죽지 않게
    handler = None
