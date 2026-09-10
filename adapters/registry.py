"""도구 이름 -> 어댑터 모듈 레지스트리.

어댑터는 각자 다른 env 에서 돈다(1 도구 = 1 env). 그래서 여기서는 절대 top-level import
하지 않고, 실제로 그 도구를 돌릴 때만 lazy import 한다. env 가 없는 도구가 하나 있다고
harness 전체가 죽으면 안 된다.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolSpec:
    name: str
    module: str            # adapters.<module>
    cls: str
    env: str               # envs/<file>.txt — 이 도구를 돌릴 conda env
    phase: int             # README §7 진행 단계
    needs_llm: bool = False
    note: str = ""


REGISTRY: dict[str, ToolSpec] = {
    # --- Phase 1: 비LLM 베이스라인 (비용 0) ---
    "scrapy_baseline": ToolSpec(
        "scrapy_baseline", "scrapy_baseline_runner", "ScrapyBaselineAdapter",
        "scrapy_baseline", 1, note="대조군. 사람이 직접 짠 셀렉터."),
    "autoscraper": ToolSpec(
        "autoscraper", "autoscraper_runner", "AutoScraperAdapter",
        "autoscraper", 1, note="JS 렌더 없음. D2B 에서만 유효."),
    "crawl4ai_css": ToolSpec(
        "crawl4ai_css", "crawl4ai_css_runner", "Crawl4AICssAdapter",
        "crawl4ai", 1),
    "crawl4ai_genschema": ToolSpec(
        "crawl4ai_genschema", "crawl4ai_genschema_runner", "Crawl4AIGenSchemaAdapter",
        "crawl4ai", 1, needs_llm=True,
        note="LLM 1회로 셀렉터 생성 후 캐시 재사용. 이 저장소에 가장 잘 맞는 후보(README §9)."),

    # --- Phase 2: LLM 추출 3종 ---
    "crawl4ai_llm": ToolSpec(
        "crawl4ai_llm", "crawl4ai_llm_runner", "Crawl4AILlmAdapter",
        "crawl4ai", 2, needs_llm=True, note="긴 페이지 청킹으로 토큰 급증 주의."),
    "scrapegraphai": ToolSpec(
        "scrapegraphai", "scrapegraphai_runner", "ScrapeGraphAIAdapter",
        "scrapegraphai", 2, needs_llm=True),
    "firecrawl": ToolSpec(
        "firecrawl", "firecrawl_runner", "FirecrawlAdapter",
        "firecrawl", 2, needs_llm=True,
        note="**셀프호스팅으로만 잰다.** 클라우드는 자기 서버가 URL 을 가져가므로 "
             "리플레이 서버(사설 주소)에 원리상 못 닿는다. 요청에 태그를 못 실어 "
             "llm_tagged=False -> 동시성 1 강제."),

    # --- Phase 3: 에이전트 2종 (티어당 2케이스 x 1회) ---
    "browseruse": ToolSpec(
        "browseruse", "browseruse_runner", "BrowserUseAdapter",
        "browseruse", 3, needs_llm=True,
        note="에이전트. 비용이 페이지가 아니라 **스텝**에 붙는다 - 스텝 수를 먼저 프로브로 "
             "재고 돌린다. use_judge 는 끄고(자기 채점은 추출이 아니다) use_vision 은 "
             "기본값 유지. 태그는 default_headers 로 실어 llm_tagged=True."),
    "skyvern": ToolSpec(
        "skyvern", "skyvern_runner", "SkyvernAdapter",
        # 이 도구의 '환경'은 conda env 가 아니라 **컨테이너**다. 어댑터는 HTTP 로만 말하므로
        # requests 만 있으면 되고, 그래서 harness 와 같은 aicrawl 에서 돈다.
        "aicrawl", 3, needs_llm=True,
        note="에이전트. **셀프호스팅으로만 잰다** - 클라우드는 Firecrawl 과 같은 구조라 "
             "고정본에 원리상 못 닿는다. 컨테이너가 LLM 을 부르므로 태그를 못 실어 "
             "llm_tagged=False -> 동시성 1 강제. 리플레이 URL 은 host.docker.internal "
             "이어야 한다(--url-host)."),

    # --- harness 자체 검증용 (도구 아님) ---
    "echo": ToolSpec(
        "echo", "echo_runner", "EchoAdapter", "aicrawl", 0,
        note="fixtures/replay/score/report 배선 점검용. 벤치마크 결과에 넣지 않는다."),
}


class AdapterUnavailable(RuntimeError):
    """env 미구성 또는 어댑터 미구현. 매트릭스에서 그 도구만 건너뛴다."""


def load(tool: str):
    spec = REGISTRY.get(tool)
    if spec is None:
        raise KeyError(f"등록되지 않은 도구: {tool} (가능: {', '.join(REGISTRY)})")
    try:
        mod = importlib.import_module(f"adapters.{spec.module}")
    except ImportError as e:
        raise AdapterUnavailable(
            f"{tool}: adapters/{spec.module}.py 를 불러올 수 없다 "
            f"(env: envs/{spec.env}.txt) - {e}") from e
    try:
        return getattr(mod, spec.cls)
    except AttributeError as e:
        raise AdapterUnavailable(f"{tool}: {spec.module}.{spec.cls} 없음") from e


def by_phase(phase: Optional[int] = None) -> list:
    return [s.name for s in REGISTRY.values() if phase is None or s.phase == phase]
