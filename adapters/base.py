"""모든 도구 어댑터가 지키는 공통 계약 (README §6).

harness 는 이 인터페이스만 알고, 도구별 라이브러리는 어댑터 안에만 갇힌다.
어댑터는 채점하지 않는다 — 추출 결과와 계측치만 돌려준다.
"""
from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# 실패 모드 (README §8). score/report 가 이 문자열을 그대로 집계한다.
ERROR_CLASSES = (
    "RENDER_FAIL",       # 페이지가 뜨지 않음 / 빈 DOM
    "BLOCKED",           # 차단·캡차·403
    "SCHEMA_VIOLATION",  # 스키마 밖 키, 타입 불일치
    "HALLUCINATION",     # 채점 단계에서만 판정 (어댑터가 쓰지 않음)
    "TIMEOUT",
    "ENCODING",          # 한글 깨짐
    "TOOL_ERROR",        # 도구 내부 예외
)

# **실패 모드가 아니다.** 그래서 위 튜플에 일부러 넣지 않았다.
#
# 지출 상한에 걸리면 도구는 예외를 받고, 그 케이스는 지금까지 `TOOL_ERROR` 로 남았다.
# 그러면 리포트의 '주요실패모드' 칸에 그게 찍히고 **'에이전트가 실패했다'** 로 읽힌다.
# 실제로는 도구가 아니라 **내 지갑이 멈춘 것**이고, 그 케이스는 실패한 게 아니라
# **아예 재지 않은 것**이다. 도구를 잰 숫자와 지갑을 잰 숫자를 같은 칸에 적지 않는다.
# `harness/report.py` 가 이 값을 보고 채점에서 통째로 뺀 뒤 따로 세어 보여 준다.
BUDGET_STOP = "BUDGET_STOP"

# `harness.ledger.BUDGET_MARK` 와 **같은 문자열**이다. import 하지 않고 베껴 둔 이유는
# 어댑터가 도구별 env 에서 도는데 `harness.ledger` 는 pricing 을 읽느라 pyyaml 을 물고,
# 그게 없는 env 에서는 import 자체가 실패하기 때문이다. 상한을 알아보는 코드가
# 의존성 때문에 조용히 안 도는 쪽이 훨씬 나쁘다.
BUDGET_MARK = "[AICRAWL_BUDGET_STOP]"


def is_budget_stop(exc: BaseException) -> bool:
    """이 예외가 예산 정지인가.

    경로가 둘이라 판정도 둘이다.

      1. `harness.ledger.guard` (크레딧·스텝) — 어댑터와 **같은 프로세스**라 예외 클래스가
         그대로 온다. isinstance 로 잡는다.
      2. 프록시의 토큰 상한 — **다른 프로세스**다. 도구는 이걸 HTTP 오류로 받으므로
         클래스가 남지 않고, 라이브러리마다 예외 타입도 제각각이다(openai.APIError,
         litellm 계열, 그냥 RuntimeError...). 남는 건 메시지뿐이라 표식으로 잡는다.
    """
    if BUDGET_MARK in f"{exc}":
        return True
    try:
        from harness.ledger import BudgetExceeded
    except Exception:
        return False
    return isinstance(exc, BudgetExceeded)


@dataclass
class RawPage:
    """fetch 단계 산출물. fetch 를 지원하지 않는 도구는 None 을 반환한다."""
    url: str
    body: bytes = b""
    encoding: Optional[str] = None
    content_type: Optional[str] = None
    markdown: Optional[str] = None
    screenshot_path: Optional[str] = None

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")

    @property
    def page_bytes(self) -> int:
        return len(self.body)


@dataclass
class Metrics:
    wall_ms: Optional[float] = None
    setup_ms: Optional[float] = None
    fetch_ms: Optional[float] = None   # fetch/extract 통합 도구는 None
    llm_ms: Optional[float] = None

    llm_calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    cached_in_tokens: int = 0          # 반드시 분리 — 합치면 비용이 3~4배 틀어진다

    cost_usd: Optional[float] = None
    cost_krw: Optional[float] = None

    page_bytes: Optional[int] = None
    steps: Optional[int] = None        # 에이전트류 액션 수

    # 비토큰 과금 도구용 (firecrawl credit, skyvern step)
    units: dict[str, float] = field(default_factory=dict)


@dataclass
class RunResult:
    case_id: str
    tool: str
    ok: bool
    target_id: Optional[str] = None
    error_class: Optional[str] = None
    error_msg: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    raw: Optional[str] = None          # 중간 산출물(markdown/html) 보존
    metrics: Metrics = field(default_factory=Metrics)
    meta: dict[str, Any] = field(default_factory=dict)   # run_id, repeat, model 등

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Adapter(ABC):
    """도구 1개 = 어댑터 1개. env 가 달라도 인터페이스는 같다."""

    name: str = "base"
    supports_fetch: bool = True
    needs_llm: bool = False

    # 이 도구의 LLM 요청에 `metadata.tags` 를 실을 수 있는가.
    # 자기가 알아서 프록시를 부르는 도구(셀프호스팅 Firecrawl 등)는 우리가 요청 본문을
    # 못 건드리므로 False 다. 그런 도구는 프록시 로그를 **시간창만으로** 갈라야 하고,
    # 따라서 동시성 1 에서만 비용이 정확하다. 러너가 이 값을 보고 막는다.
    llm_tagged: bool = True

    def __init__(self, **opts: Any) -> None:
        self.opts = opts
        self.setup_ms: Optional[float] = None

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        """브라우저/모델 기동. 콜드 스타트를 여기서만 태운다."""

    def teardown(self) -> None:
        pass

    def __enter__(self) -> "Adapter":
        t0 = time.perf_counter()
        self.setup()
        self.setup_ms = (time.perf_counter() - t0) * 1000
        return self

    def __exit__(self, *exc: Any) -> None:
        self.teardown()

    # ---- 본체 ---------------------------------------------------------
    def fetch(self, url: str) -> Optional[RawPage]:
        """원문/마크다운 취득. 미지원 도구는 None."""
        return None

    @abstractmethod
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        """schema 에 맞는 dict 를 채운 RunResult 반환."""

    # ---- 공통 헬퍼 ----------------------------------------------------
    def _fail(self, case_id: str, exc: BaseException,
              error_class: Optional[str] = None) -> RunResult:
        """실패 1건. **예산 정지는 실패 모드로 적지 않는다**(BUDGET_STOP 주석 참고).

        `error_class` 를 명시하면 그대로 쓴다 — 도구가 스스로 RENDER_FAIL 등을 판정한 경우다.
        """
        if error_class is None:
            error_class = BUDGET_STOP if is_budget_stop(exc) else "TOOL_ERROR"
        return RunResult(
            case_id=case_id,
            tool=self.name,
            ok=False,
            error_class=error_class,
            error_msg=redact(f"{type(exc).__name__}: {exc}"),
            meta={"traceback": redact(traceback.format_exc(limit=5))},
        )


def redact(text: str) -> str:
    """실패 기록에서 키를 지운다.

    `results/` 는 실패한 케이스의 예외 메시지와 트레이스백을 그대로 저장한다. 도구가
    `api_key=...` 를 담은 요청 객체를 repr 로 찍어 예외에 실으면 **키가 결과 파일에 박힌다.**
    `results/` 는 git 제외지만 리포트로 복사되고 눈으로도 본다. 나가는 길목에서 지운다.

    지우는 대상은 os.environ 에 **실제로 들어 있는 값**이다. 정규식으로 키 모양을 추측하면
    형식이 바뀔 때마다 새므로, 값 자체를 찾아 이름으로 바꾼다.
    """
    import os
    import re as _re

    if not text:
        return text
    hits = [(v, k) for k, v in os.environ.items()
            if len(v) >= 12 and _re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, _re.I)]
    for value, name in sorted(hits, key=lambda x: -len(x[0])):
        text = text.replace(value, f"<redacted:{name}>")
    return text
