"""Crawl4AI — LLM 으로 셀렉터를 **한 번** 만들고 그 뒤로는 공짜 (README §7 Phase 1).

`crawl4ai_css` 와 같은 추출 엔진을 쓰되, CSS 스키마를 사람이 아니라 LLM 이 쓴다.
그래서 이 도구의 시험은 정확도 하나가 아니라 세 가지다.

  1. **사람이 쓴 셀렉터를 대신할 수 있는가** — `crawl4ai_css` 와 같은 페이지, 같은 엔진,
     같은 채점. 차이는 셀렉터를 누가 썼는가뿐이다. 두 줄을 나란히 놓으면 그게 그대로 답이다.
  2. **한 번 만든 스키마가 다른 페이지에서 버티는가** — 대상·태스크마다 스키마를 **하나만**
     만들어 캐시하고 나머지 케이스에 재사용한다. D2B 는 게시판 4종의 컬럼 구성이 서로 달라서
     NEWS01 로 만든 스키마가 NEWS02~04 에서 어떻게 되는지가 바로 드러난다.
     `autoscraper` 와 정확히 같은 조건(예시 1건 -> 나머지 일반화)이라 둘을 직접 비교할 수 있다.
  3. **얼마나 드는가** — LLM 호출이 케이스당이 아니라 (대상 x 태스크)당 1회다. 22건에 6회.
     건당 비용은 그 6회를 나눈 값이고, 케이스를 늘릴수록 0 에 수렴한다. Phase 2 의
     '매 페이지마다 LLM' 도구들과 비용 축에서 갈리는 지점이 정확히 여기다.

## 힌트 등급

셀렉터를 **주지 않는다**. 주는 것은 전 도구 공통인 `schema/*.json` 과 거기서 뽑은 자연어
설명(`schema_utils.task_prompt`)뿐이다. `crawl4ai_css`(H3, 셀렉터 제공)보다 한 등급 아래고,
LLM 도구 3종과는 같은 문구를 쓴다.

**gold 는 어디에도 넣지 않는다.** `autoscraper` 는 구조상 정답 값을 예시로 줘야 해서 그 케이스를
채점에서 뺐지만, 여기는 LLM 이 정답을 본 적이 없다. 스키마 생성에 쓴 페이지도 그냥 채점한다
(사람이 그 페이지를 보고 셀렉터를 쓰는 `crawl4ai_css` 와 같은 조건이다).

## 결과 정리

돌아온 레코드를 스키마 필드에 맞추는 일은 **사이트를 모르는 채로** 한다(`_coerce`).
'번호 칸이 숫자인 행만 진짜 행이다' 같은 규칙을 여기 넣으면 `crawl4ai_css` 에 손으로 넣었던
사이트 지식을 뒷문으로 다시 넣는 셈이고, 그러면 두 도구를 비교하는 의미가 없어진다.
LLM 이 baseSelector 를 잘못 골라 메뉴 항목까지 긁어 오면 그건 이 도구의 결과다.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig,
                      JsonCssExtractionStrategy, LLMConfig)

from .base import Adapter, RawPage, RunResult
from harness import normalize as nz, schema_utils as su
from harness.dotenv import proxy
from harness.meter import Stopwatch

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "genschema"

DEFAULT_MODEL = "claude-haiku-4-5"

# 스키마를 만들 때 보여줄 페이지. 대상·태스크마다 하나뿐이다.
# D2B 는 NEWS01 을 쓴다 — `autoscraper` 와 같은 페이지라야 두 도구의 일반화 능력을
# 같은 조건에서 비교할 수 있다.
GEN_CASE = {
    ("t1_d2b", "list"): "NEWS01-p1",
    ("t1_d2b", "detail"): "NEWS01-6071",
    ("t2_g2b", "list"): "ntc-p1",
    ("t2_g2b", "detail"): "ntc-777",
    ("t3_kepco_srm", "list"): "ntc-p1",
    ("t3_kepco_srm", "detail"): "ntc-4267",
}


# 값 정규화는 `harness/normalize.py` 로 옮겼다(2026-09-09). 다른 env 에서 도는
# `scrapegraphai` 어댑터도 **같은 후처리**를 써야 하는데, 이 모듈은 top-level 에서
# crawl4ai 를 import 하므로 여기 두면 가져다 쓸 수 없다. 동작은 그대로다.


class Crawl4AIGenSchemaAdapter(Adapter):
    name = "crawl4ai_genschema"
    supports_fetch = True
    needs_llm = True

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        self.model = self.opts.get("model") or os.environ.get("GENSCHEMA_MODEL") or DEFAULT_MODEL
        self.run_id = self.opts.get("run_id") or ""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
        self._loop.run_until_complete(self._crawler.start())
        self._schemas: dict = {}
        CACHE.mkdir(parents=True, exist_ok=True)

    def teardown(self) -> None:
        try:
            self._loop.run_until_complete(self._crawler.close())
            self._loop.close()
        except Exception:
            pass

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    # ---- 스키마 생성 (LLM 1회) ------------------------------------------
    def _cache_path(self, target: str, task: str) -> Path:
        return CACHE / f"{target}__{task}__{self.model.replace('/', '_')}.json"

    def _html_of(self, url: str) -> str:
        """추출 때와 **같은 렌더 결과**를 스키마 생성에 쓴다.
        동결본 원문을 그대로 넣으면 브라우저가 다시 돌린 스크립트의 결과와 어긋나서,
        LLM 이 화면에 없는 DOM 을 보고 셀렉터를 쓰게 된다."""
        cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, verbose=False)
        res = self._loop.run_until_complete(self._crawler.arun(url=url, config=cfg))
        if not res.success:
            raise RuntimeError(res.error_message or "생성용 페이지 로드 실패")
        return res.html or ""

    def _generate(self, url: str, target: str, task: str, schema: dict) -> dict:
        gen_case = GEN_CASE.get((target, task))
        if not gen_case:
            raise KeyError(f"스키마 생성 케이스를 정해두지 않은 대상: {target}/{task}")
        gen_url = re.sub(r"(/f/[^/]+/)[^/?]+", rf"\g<1>{gen_case}", url)
        html = self._html_of(gen_url)

        base_url, api_key = proxy()
        llm = LLMConfig(provider=f"openai/{self.model}", api_token=api_key, base_url=base_url)

        from crawl4ai.models import TokenUsage
        usage = TokenUsage()
        t0 = time.time()
        css = JsonCssExtractionStrategy.generate_schema(
            html=html,
            schema_type="CSS",
            query=su.task_prompt(schema),
            target_json_example=su.json_example(schema),   # object 여야 한다(모듈 도크 참고)
            llm_config=llm,
            usage=usage,
            # 동시성 8 에서 시간창만으로는 도구를 못 가른다. 프록시 로그에 태그를 실어 둔다.
            # **`metadata=` 가 아니라 `extra_body` 여야 한다.** litellm 의 `metadata` 인자는
            # 클라이언트 쪽 로깅용이라 프록시 요청 본문에 실리지 않는다. 그걸로 보내면
            # 프록시 로그의 tags 가 None 이 되고, meter 의 tag 필터가 전부 걸러 내
            # **비용이 0 으로 집계된다.** (2026-09-09 mock 프록시로 세 방식 실측 확인)
            # `extra_args=` 로 한 겹 더 싸면 안 된다. generate_schema 의 **kwargs 가 그대로
            # aperform_completion_with_backoff 의 extra_args 가 되고, 거기서 다시 풀려
            # acompletion 인자가 된다. 한 겹 더 싸면 litellm 이 모르는 키가 되어 조용히 버려진다.
            extra_body={"metadata": {"tags": [f"{self.run_id}:{self.name}", target, task]}},
        )
        rec = {
            "css_schema": css,
            "meta": {
                "model": self.model, "target_id": target, "task": task,
                "gen_case": gen_case, "gen_html_bytes": len(html.encode("utf-8")),
                "gen_wall_ms": round((time.time() - t0) * 1000, 1),
                # 도구가 보고한 토큰. **비용 계산에는 쓰지 않는다**(README §4-3) —
                # 프록시 로그가 유일한 소스다. 둘이 어긋나면 그 자체가 결과다.
                "tool_reported_usage": {"prompt": usage.prompt_tokens,
                                        "completion": usage.completion_tokens,
                                        "total": usage.total_tokens},
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        self._cache_path(target, task).write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec

    def _schema_for(self, url: str, target: str, task: str, schema: dict) -> tuple:
        key = (target, task)
        if key in self._schemas:
            return self._schemas[key], "memory"
        p = self._cache_path(target, task)
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            self._schemas[key] = rec
            return rec, "disk"
        rec = self._generate(url, target, task, schema)
        self._schemas[key] = rec
        return rec, "generated"

    # ---- 결과 정리 (사이트를 모르는 채로) ---------------------------------
    @staticmethod
    def _coerce(rec: dict, rules: dict) -> dict:
        """LLM 이 돌려준 레코드를 스키마 타입에 맞춘다. 사이트별 분기는 넣지 않는다."""
        return nz.coerce(rec, rules)

    def _shape(self, task: str, recs: list, schema: dict) -> dict:
        rules = su.score_rules(schema)
        if task == "list":
            return {"items": [self._coerce(r, rules) for r in recs if isinstance(r, dict)]}
        return self._coerce(recs[0] if recs and isinstance(recs[0], dict) else {}, rules)

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                with sw.stage("llm"):          # 캐시 히트면 0 에 가깝다. 그게 이 도구의 요점이다
                    rec, source = self._schema_for(url, target, task, schema)
                css = rec["css_schema"]
                cfg = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(css),
                                       cache_mode=CacheMode.BYPASS, verbose=False)
                with sw.stage("fetch"):
                    res = self._loop.run_until_complete(self._crawler.arun(url=url, config=cfg))
                if not res.success:
                    raise RuntimeError(res.error_message or "crawl 실패")
                with sw.stage("parse"):
                    out = json.loads(res.extracted_content or "[]")
                    data = self._shape(task, out if isinstance(out, list) else [out], schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = len((res.html or "").encode("utf-8"))
        gm = rec.get("meta", {})
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  # 이 케이스에서 LLM 을 실제로 불렀는지. 비용을 읽을 때 반드시 같이 본다 —
                  # 22건 중 6건에만 비용이 붙고 나머지는 0 이다.
                  "schema_source": source,
                  # 이 케이스에서 만든 스키마는 나머지 케이스가 공짜로 쓴다. 리포트가
                  # 이 비용을 케이스 수만큼 곱하지 않도록 '1회성'으로 표시한다.
                  "cost_oneoff": source == "generated",
                  "gen_case": gm.get("gen_case"), "gen_model": gm.get("model"),
                  "base_selector": (rec["css_schema"] or {}).get("baseSelector"),
                  "gen_fields": [f.get("name") for f in (rec["css_schema"] or {}).get("fields", [])],
                  "tool_reported_usage": gm.get("tool_reported_usage")})
