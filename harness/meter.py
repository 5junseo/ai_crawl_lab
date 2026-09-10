"""시간/토큰/비용 계측 (README §4-3, §6).

원칙 두 개:
 1. 도구가 스스로 보고하는 토큰 수는 믿지 않는다. 청킹 중복 카운트와 캐시 토큰 누락 때문.
    진짜 소스는 LiteLLM proxy 로그 한 곳뿐이다.
 2. 캐시 히트 토큰은 반드시 분리 집계한다. 입력가로 뭉치면 비용이 3~4배 틀어진다.

사용:
    with Stopwatch() as sw:
        with sw.stage("fetch"):
            page = adapter.fetch(url)
        with sw.stage("llm"):
            data = adapter.extract(...)
    m = sw.to_metrics()
    m = attach_cost(m, model="claude-opus-5")
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = ROOT / "pricing.yaml"
DEFAULT_LITELLM_LOG = ROOT / "logs" / "litellm.jsonl"


# ------------------------------------------------------------------ 단가표
@lru_cache(maxsize=1)
def load_pricing(path: Optional[str] = None) -> dict:
    import yaml  # env 에 pyyaml 필요

    p = Path(path) if path else PRICING_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def usd_krw(pricing: Optional[dict] = None) -> float:
    return float((pricing or load_pricing())["meta"]["usd_krw"])


class PricingError(RuntimeError):
    pass


def cost_of(model: str, in_tokens: int = 0, out_tokens: int = 0,
            cached_in_tokens: int = 0, cache_write_tokens: int = 0,
            pricing: Optional[dict] = None) -> float:
    """USD. cached_in_tokens 는 in_tokens 에 포함되지 않은 별도 값으로 취급한다."""
    pr = pricing or load_pricing()
    row = pr["llm"].get(model)
    if row is None:
        raise PricingError(f"pricing.yaml 에 모델 없음: {model}")
    missing = [k for k in ("input", "output") if row.get(k) is None]
    if missing:
        raise PricingError(f"{model}: 단가 미기입 {missing} - pricing.yaml 을 채워라")
    per_m = 1_000_000.0
    usd = in_tokens * row["input"] / per_m + out_tokens * row["output"] / per_m
    usd += cached_in_tokens * (row.get("cache_read") or 0.0) / per_m
    usd += cache_write_tokens * (row.get("cache_write") or row["input"]) / per_m
    return usd


def service_cost(service: str, units: float, kind: Optional[str] = None,
                 pricing: Optional[dict] = None) -> Optional[float]:
    """firecrawl credit / skyvern step 처럼 토큰이 아닌 과금."""
    pr = pricing or load_pricing()
    row = (pr.get("services") or {}).get(service)
    if not row:
        raise PricingError(f"pricing.yaml services 에 없음: {service}")
    unit_price = row.get("usd_per_unit")
    if unit_price is None:
        return None  # 플랜 미확정 — 비용 칸을 비워두고 리포트에 '미확정'으로 표시
    mult = 1.0
    if kind and isinstance(row.get("credits"), dict):
        mult = float(row["credits"].get(kind, 1))
    return units * mult * float(unit_price)


# ------------------------------------------------------------------ 시간 계측
@dataclass
class Stopwatch:
    t0: float = 0.0
    stages: dict = field(default_factory=dict)
    setup_ms: Optional[float] = None

    def __enter__(self) -> "Stopwatch":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stages["_wall"] = (time.perf_counter() - self.t0) * 1000

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        s = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - s) * 1000

    @property
    def wall_ms(self) -> float:
        return self.stages.get("_wall") or (time.perf_counter() - self.t0) * 1000

    def to_metrics(self, metrics: Any = None) -> Any:
        from adapters.base import Metrics

        m = metrics or Metrics()
        m.wall_ms = round(self.wall_ms, 2)
        m.setup_ms = None if self.setup_ms is None else round(self.setup_ms, 2)
        if "fetch" in self.stages:
            m.fetch_ms = round(self.stages["fetch"], 2)
        if "llm" in self.stages:
            m.llm_ms = round(self.stages["llm"], 2)
        return m


# ------------------------------------------------------------------ LiteLLM 로그
@dataclass
class LLMUsage:
    model: str = ""
    calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    cached_in_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0

    def add(self, other: "LLMUsage") -> "LLMUsage":
        self.model = self.model or other.model
        self.calls += other.calls
        self.in_tokens += other.in_tokens
        self.out_tokens += other.out_tokens
        self.cached_in_tokens += other.cached_in_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.latency_ms += other.latency_ms
        return self


def _get(d: dict, *keys: str, default: Any = 0) -> Any:
    for k in keys:
        cur: Any = d
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return default


def parse_litellm_line(entry: dict) -> LLMUsage:
    """LiteLLM 로깅 콜백이 남기는 JSON 한 줄 -> 사용량.

    키 이름은 LiteLLM 버전마다 흔들리므로 후보를 여러 개 본다.
    Anthropic 계열은 cache_creation_input_tokens / cache_read_input_tokens 를 따로 준다.
    """
    u = LLMUsage(model=str(_get(entry, "model", "response.model", "kwargs.model", default="")))
    u.calls = 1
    u.in_tokens = int(_get(entry, "usage.prompt_tokens", "response.usage.prompt_tokens",
                           "usage.input_tokens", "response.usage.input_tokens"))
    u.out_tokens = int(_get(entry, "usage.completion_tokens", "response.usage.completion_tokens",
                            "usage.output_tokens", "response.usage.output_tokens"))
    u.cached_in_tokens = int(_get(
        entry,
        "usage.cache_read_input_tokens", "response.usage.cache_read_input_tokens",
        "usage.prompt_tokens_details.cached_tokens",
        "response.usage.prompt_tokens_details.cached_tokens"))
    u.cache_write_tokens = int(_get(
        entry, "usage.cache_creation_input_tokens",
        "response.usage.cache_creation_input_tokens"))
    # 일부 구현은 prompt_tokens 에 캐시 토큰을 포함시킨다 -> 이중계산 방지
    if u.cached_in_tokens and u.in_tokens >= u.cached_in_tokens:
        overlap = _get(entry, "usage.prompt_tokens_details.cached_tokens", default=None)
        if overlap is not None:
            u.in_tokens -= u.cached_in_tokens
    u.latency_ms = float(_get(entry, "latency_ms", "response_ms", default=0.0))
    return u


def read_litellm_window(start_ts: float, end_ts: float, *, tag: Optional[str] = None,
                        log_path: Optional[str] = None) -> LLMUsage:
    """시간창(+선택적 tag)으로 프록시 로그를 집계.

    주의: 동시성 8 조건에서는 시간창만으로 도구를 분리할 수 없다. 그 경우 어댑터가
    metadata.tags 또는 user 필드에 run_id/tool/case_id 를 실어야 하고, 여기서 tag 로 거른다.
    """
    path = Path(log_path or os.environ.get("LITELLM_JSONL", DEFAULT_LITELLM_LOG))
    total = LLMUsage()
    if not path.exists():
        return total
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = float(_get(entry, "startTime", "start_time", "timestamp", default=0.0) or 0.0)
            if ts and not (start_ts <= ts <= end_ts):
                continue
            if tag and tag not in json.dumps(entry.get("metadata", {}), ensure_ascii=False):
                continue
            total.add(parse_litellm_line(entry))
    return total


def attach_cost(metrics: Any, model: Optional[str] = None, usage: Optional[LLMUsage] = None,
                pricing: Optional[dict] = None) -> Any:
    """Metrics 에 토큰/비용을 채워 넣는다. 단가가 없으면 cost 는 None 으로 남긴다."""
    pr = pricing or load_pricing()
    if usage is not None:
        metrics.llm_calls = usage.calls
        metrics.in_tokens = usage.in_tokens
        metrics.out_tokens = usage.out_tokens
        metrics.cached_in_tokens = usage.cached_in_tokens
        model = model or usage.model
    if not model or (metrics.in_tokens == 0 and metrics.out_tokens == 0):
        return metrics
    try:
        usd = cost_of(model, metrics.in_tokens, metrics.out_tokens,
                      metrics.cached_in_tokens,
                      usage.cache_write_tokens if usage else 0, pricing=pr)
    except PricingError:
        return metrics
    metrics.cost_usd = round(usd, 6)
    metrics.cost_krw = round(usd * usd_krw(pr), 2)
    return metrics


if __name__ == "__main__":
    pr = load_pricing()
    print("usd_krw:", usd_krw(pr))
    for m in pr["llm"]:
        try:
            usd = cost_of(m, 30000, 1500, 20000, pricing=pr)
            print(f"  {m:32s} 건당 ${usd:.5f}  = {usd * usd_krw(pr):.2f}원"
                  f"  / 월 1만건 {usd * usd_krw(pr) * pr['report']['monthly_volume']:,.0f}원")
        except PricingError as e:
            print(f"  {m:32s} (skip) {e}")
