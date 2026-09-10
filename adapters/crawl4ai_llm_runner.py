"""Crawl4AI — **페이지마다** LLM 에게 직접 추출시킨다 (README §7 Phase 2).

`crawl4ai_genschema` 와 짝을 이룬다. 엔진도 프롬프트도 같고, 다른 것은 하나다.

    genschema  LLM 이 **셀렉터를 쓴다**. (대상 x 태스크)당 1회. 그 뒤 22건은 공짜.
    llm        LLM 이 **값을 읽는다**. 케이스마다 1회. 케이스가 늘면 비용도 늘어난다.

비용 축이 갈리는 지점이 정확히 여기다. 정확도가 비슷하다면 이 도구를 고를 이유가 없고,
정확도가 확실히 높다면 그 차이가 곧 '건당 과금의 값'이다. 그 둘을 나란히 놓는 게 목적이다.

## input_format — 이 도구에서 제일 중요한 결정

기본값 `markdown` 을 쓴다. crawl4ai 의 전제 자체가 'LLM 이 읽기 좋은 마크다운'이라
이걸 바꾸면 다른 도구를 재는 셈이 된다. 다만 **대가가 있고, 미리 실측해 뒀다** (2026-09-09):

    D2B    html  58,849 -> markdown  20,253 (34.4%)   gold 값 7/7 살아남음
    G2B    html 1,247,429 -> markdown 18,288 ( 1.5%)  gold 값 3/6  (dept·posted_at·views 소실)
    한전    html  331,711 -> markdown  14,834 ( 4.5%)  gold 값 4/6  (period_start·end 소실)

사라지는 값은 전부 **SPA 가 `<input value="...">` 에 넣어 둔 것**이다. 마크다운 변환은 폼
요소를 글로 옮기지 않는다. 즉 **G2B·한전 상세에는 LLM 이 아무리 잘해도 못 넘는 천장이 있다.**
점수가 낮게 나오면 '모델이 못 읽었다'가 아니라 '모델에게 보여주지도 않았다'로 읽어야 한다.

`input_format="html"` 로 바꾸면 값은 살아나지만 G2B 상세가 한 건에 **35만 토큰**이다.
2048 토큰 청크로 쪼개면 한 페이지에 170회 호출이다 — 실행하지 않아도 성립하지 않는 걸 알 수 있다.
이 트레이드오프 자체가 이 도구의 성격이므로 리포트에 남긴다.

## chunk_token_threshold — 기본값을 안 쓴 유일한 항목

기본 2048 은 컨텍스트 4K 시절 값이다. 지금 페이지는 마크다운으로 5~7K 토큰이라
기본값이면 **공지 한 건이 3~4조각으로 잘려** 서로 다른 호출에 흩어진다. 한 레코드를
쪼개 놓고 합치는 건 도구 성능이 아니라 내 병합 코드를 재는 것이고, 호출 수가 4배로 늘어
비용까지 왜곡된다. 1M 컨텍스트 모델을 쓰는 사람이라면 당연히 올릴 값이라 올렸다.
그래도 넘치는 페이지가 있을 수 있으므로 조각별 결과를 합치는 코드는 남겨 둔다.

## 결과 정리

`crawl4ai_genschema` 와 **같은 `_coerce`** 를 쓴다(사이트별 분기 없음). 두 도구의 차이가
후처리 차이로 오염되면 안 되기 때문이다.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig,
                      LLMConfig, LLMExtractionStrategy)

from .base import Adapter, RawPage, RunResult
from .crawl4ai_genschema_runner import Crawl4AIGenSchemaAdapter
from harness import schema_utils as su
from harness.dotenv import proxy
from harness.meter import Stopwatch

# 마크다운 5~7K 토큰짜리 페이지가 한 조각에 들어가는 값. 위 도크 참고.
CHUNK_TOKENS = 16384


class Crawl4AILlmAdapter(Crawl4AIGenSchemaAdapter):
    """genschema 와 브라우저 기동·정규화를 공유하고, 추출 전략만 갈아 끼운다."""

    name = "crawl4ai_llm"
    supports_fetch = True
    needs_llm = True

    # ---- 조각 합치기 ---------------------------------------------------
    @staticmethod
    def _merge(blocks: list) -> dict:
        """청크가 여럿이면 조각마다 레코드가 하나씩 온다. 먼저 채워진 값을 살린다.

        crawl4ai 는 조각마다 `index`/`error` 같은 자체 키를 붙여 보낸다. 스키마에 없는
        키는 `_coerce` 가 어차피 버리지만, 여기서 굳이 걸러내지 않는다 — 도구가 뭘 줬는지는
        원본(results/*.json)에 그대로 남아야 한다.
        """
        out: dict = {}
        for b in blocks:
            if not isinstance(b, dict):
                continue
            for k, v in b.items():
                if v in (None, "", [], {}):
                    continue
                if k not in out:
                    out[k] = v
        return out

    def _shape_llm(self, task: str, blocks: list, schema: dict) -> dict:
        rules = su.score_rules(schema)
        if task != "list":
            return self._coerce(self._merge(blocks), rules)

        # 목록: 도구가 {"items": [...]} 로 줄 수도, 레코드를 그냥 나열할 수도 있다. 둘 다 받는다.
        items: list = []
        for b in blocks:
            if isinstance(b, dict) and isinstance(b.get("items"), list):
                items += [x for x in b["items"] if isinstance(x, dict)]
            elif isinstance(b, dict):
                items.append(b)
        return {"items": [self._coerce(r, rules) for r in items]}

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                base_url, api_key = proxy()
                strat = LLMExtractionStrategy(
                    llm_config=LLMConfig(provider=f"openai/{self.model}",
                                         api_token=api_key, base_url=base_url),
                    instruction=su.task_prompt(schema),
                    schema=su.prompt_schema(schema),      # x_ 확장 키를 뺀 순수 JSON Schema
                    extraction_type="schema",
                    input_format="markdown",
                    apply_chunking=True,
                    chunk_token_threshold=CHUNK_TOKENS,
                    verbose=False,
                    # genschema 와 같은 이유로 extra_body 여야 한다. 여기서는 한 겹만 싼다
                    # (LLMExtractionStrategy 는 extra_args 를 그대로 completion 에 넘긴다).
                    extra_args={"extra_body": {
                        "metadata": {"tags": [f"{self.run_id}:{self.name}", target, task]}}},
                )
                cfg = CrawlerRunConfig(extraction_strategy=strat,
                                       cache_mode=CacheMode.BYPASS, verbose=False)
                with sw.stage("llm"):     # fetch 와 추출이 한 호출에 묶여 있다
                    res = self._loop.run_until_complete(self._crawler.arun(url=url, config=cfg))
                if not res.success:
                    raise RuntimeError(res.error_message or "crawl 실패")
                with sw.stage("parse"):
                    blocks = json.loads(res.extracted_content or "[]")
                    if isinstance(blocks, dict):
                        blocks = [blocks]
                    data = self._shape_llm(task, blocks, schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = len((res.html or "").encode("utf-8"))
        md = res.markdown.raw_markdown if hasattr(res.markdown, "raw_markdown") else str(res.markdown)
        u = getattr(strat, "total_usage", None)
        errs = [b.get("error") for b in blocks if isinstance(b, dict) and b.get("error")]
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "input_format": "markdown", "chunk_token_threshold": CHUNK_TOKENS,
                  "markdown_chars": len(md), "blocks": len(blocks),
                  "block_errors": errs[:3],
                  # 도구가 보고한 토큰. 비용 계산에는 쓰지 않는다 — 프록시 로그가 소스다.
                  "tool_reported_usage": (
                      {"prompt": u.prompt_tokens, "completion": u.completion_tokens}
                      if u else None)})
