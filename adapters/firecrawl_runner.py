"""Firecrawl — 스크랩 + JSON 추출 (README §7 Phase 2).

## 클라우드는 이 벤치마크에 **올릴 수 없다**

먼저 이 도구만의 제약을 적는다. Firecrawl 클라우드는 URL 을 **자기 서버가 직접 가져간다.**
우리 extract 벤치는 동결 스냅샷을 `http://127.0.0.1:8899/f/...` 로 먹이는 구조라, 그 주소는
Firecrawl 서버 입장에서 자기 자신의 로컬호스트다 — 절대 못 닿는다.

우회로는 셋뿐이고 둘은 못 쓴다.

    (1) 스냅샷을 공개 URL 로 노출  -> 고정본을 인터넷에 여는 것이다. 안 한다.
    (2) --live 로 실사이트를 친다  -> 다른 도구는 스냅샷을 보는데 이 도구만 실물을 본다.
                                     같은 표에 못 올린다(README §5).
    (3) 셀프호스팅                 -> 우리 호스트 안에서 도니 리플레이 서버에 닿는다.

그래서 **기본은 셀프호스팅**이다. 이건 우회가 아니라 결과다 — 'SaaS 크롤러는 사설망 안의
페이지를 못 본다'는 것 자체가 도구 선택에 필요한 정보다.

클라우드를 굳이 재려면 `--live` 와 `FIRECRAWL_API_KEY` 가 둘 다 있어야 하고, 그 숫자는
**다른 도구와 같은 표에 놓지 않는다.**

## 셀프호스팅이면 크레딧이 0 이다

Firecrawl 의 과금은 크레딧인데(스크랩 1 + JSON 포맷 4 = 페이지당 5), 셀프호스팅에는
크레딧이 없다. 대신 JSON 추출이 부르는 LLM 비용이 그대로 나온다.
그 호출을 **우리 LiteLLM 프록시로 돌려놨다**(`OPENAI_BASE_URL`) — 벤더를 직접 치게 두면
그 비용이 리포트에서 사라지고 지출 상한도 안 걸린다.

클라우드로 돌릴 때만 `harness.ledger` 로 크레딧을 **요청 전에** 막는다. 토큰 상한은
프록시를 안 지나는 이 지출을 하나도 못 막기 때문이다.

## 비용 집계에 태그를 못 쓴다

다른 LLM 도구는 요청에 `extra_body.metadata.tags` 를 실어 프록시 로그에서 자기 호출만
골라냈다. Firecrawl 은 **자기가 프록시를 부르므로** 우리가 그 요청에 태그를 못 넣는다.
그래서 이 어댑터는 `llm_tagged = False` 를 선언하고, 러너는 시간창만으로 집계한다.
**그 대신 동시성 1 이 강제된다** — 여러 도구가 동시에 프록시를 치면 시간창만으로는
누구 비용인지 못 가른다. 러너가 이 조건을 검사한다.

## 기본값을 그대로 둔 것 — `only_main_content`

Firecrawl 의 기본값은 `True`(본문만 남기고 네비게이션·푸터를 버린다)이고 **그게 이 도구의
정체성**이라 건드리지 않는다. 공지 상세가 폼이라 값이 같이 날아갈 수 있는데, 그러면 그건
내 설정 실수가 아니라 이 도구의 성격이다. 결과에 `only_main_content` 를 같이 적어 둔다.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

from firecrawl import Firecrawl

from .base import Adapter, RawPage, RunResult
from harness import normalize as nz, schema_utils as su
from harness.dotenv import load_env
from harness.meter import Stopwatch

SELFHOST_URL = "http://127.0.0.1:3002"

# 페이지당 크레딧: scrape 1 + JSON(advanced format) 4. pricing.yaml 과 같은 값이어야 한다.
CREDIT_KIND = "scrape_json"


class FirecrawlAdapter(Adapter):
    name = "firecrawl"
    supports_fetch = True
    needs_llm = True
    # 요청에 태그를 못 실는다(모듈 도크 참고). 러너가 시간창 집계로 넘어가고 동시성 1 을 요구한다.
    llm_tagged = False

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        load_env()
        self.model = self.opts.get("model") or os.environ.get("MODEL_NAME") or ""
        self.run_id = self.opts.get("run_id") or ""
        self.api_url = os.environ.get("FIRECRAWL_API_URL") or SELFHOST_URL
        key = os.environ.get("FIRECRAWL_API_KEY") or ""
        self.cloud = bool(key) and "api.firecrawl.dev" in self.api_url
        # 셀프호스팅은 `USE_DB_AUTHENTICATION=false` 라 키를 안 본다. SDK 가 빈 키를 거부하므로
        # 자리만 채운다 — 진짜 키가 아니고, 나가는 곳도 우리 호스트뿐이다.
        self._fc = Firecrawl(api_key=key or "self-hosted", api_url=self.api_url)

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    # ---- 결과 정리 ----------------------------------------------------
    def _shape(self, task: str, answer: Any, schema: dict) -> dict:
        """다른 LLM 도구와 **같은** `normalize.coerce`. 사이트별 분기 없음."""
        rules = su.score_rules(schema)
        if not isinstance(answer, dict):
            answer = {}
        if task != "list":
            return nz.coerce(answer, rules)
        rows = answer.get("items")
        if not isinstance(rows, list):
            rows = next((v for v in answer.values()
                         if isinstance(v, list) and v and isinstance(v[0], dict)), [])
        return {"items": [nz.coerce(r, rules) for r in rows if isinstance(r, dict)]}

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                if self.cloud:
                    if re.search(r"//(127\.0\.0\.1|localhost|host\.docker\.internal)[:/]", url):
                        raise RuntimeError(
                            "Firecrawl 클라우드에 사설 주소를 줬다. 클라우드는 자기 서버가 "
                            "URL 을 가져가므로 리플레이 서버에 닿지 못한다 - 조용히 빈 "
                            "페이지를 받는 대신 여기서 멈춘다(모듈 도크 참고).")
                    # 크레딧은 프록시를 안 지난다. **요청 전에** 원장으로 막는다.
                    from harness import ledger
                    self._credit_usd = ledger.guard(
                        "firecrawl_cloud", CREDIT_KIND, 1,
                        run_id=self.run_id, tool=self.name, case_id=case_id, target=target)
                else:
                    self._credit_usd = 0.0     # 셀프호스팅에는 크레딧이 없다

                # **`JsonFormat` 객체가 아니라 dict 로 준다.** SDK 4.42 는 JsonFormat 을
                # 직렬화할 때 값이 None 인 `checkPromptInjection` 까지 실어 보내는데,
                # 셀프호스팅 서버(v2.11.162)는 모르는 키라며 400 을 낸다. dict 로 주면
                # 그 키가 아예 안 생긴다(2026-09-09 실측).
                fmt = {"type": "json", "prompt": su.task_prompt(schema),
                       "schema": su.prompt_schema(schema)}
                # `markdown` 을 같이 받는다. 기본 포맷이라 **크레딧이 안 붙고**(추가 4크레딧은
                # JSON 같은 advanced format 에만 붙는다) 추출에도 영향이 없다.
                # 이걸 안 받으면 이 도구만 페이지 크기가 0 으로 남아 다른 도구와 비교가 안 된다.
                with sw.stage("llm"):          # fetch 와 추출이 한 요청에 묶여 있다
                    doc = self._fc.scrape(url, formats=[fmt, "markdown"])
                with sw.stage("parse"):
                    answer = getattr(doc, "json", None)
                    data = self._shape(task, answer, schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        md = getattr(doc, "markdown", None) or ""
        html = getattr(doc, "html", None) or ""
        m.page_bytes = len((html or md).encode("utf-8"))
        if self.cloud:
            m.units = {"credit": 5.0}          # scrape 1 + json 4
        meta_obj = getattr(doc, "metadata", None)
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "deployment": "cloud" if self.cloud else "selfhost",
                  "api_url": self.api_url,
                  # 이 도구의 정체성인 기본값. 껐다 켰다 하면 다른 도구를 재는 셈이다.
                  "only_main_content": True,
                  "markdown_chars": len(md),
                  "status_code": getattr(meta_obj, "status_code", None),
                  "credit_usd": self._credit_usd,
                  # 도구 원본 응답. 내 정리가 뭘 바꿨는지 여기서 확인한다.
                  "tool_answer": json.loads(json.dumps(answer, ensure_ascii=False,
                                                       default=str))})
