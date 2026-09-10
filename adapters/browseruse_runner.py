"""browser-use — 브라우저를 직접 모는 LLM 에이전트 (README §7 Phase 3).

## 앞의 6종과 근본적으로 다른 것: 비용이 페이지가 아니라 **스텝**에 붙는다

`crawl4ai_llm` / `scrapegraphai` / `firecrawl` 은 전부 '페이지 1개 -> 텍스트 -> LLM 1회'다.
이 도구는 아니다. 매 스텝마다 **그때까지의 대화 전체 + 현재 화면(스크린샷 + DOM 요약)** 을
다시 입력에 싣고 다음 행동을 고른다. 그래서

    비용 ~ (스텝 수) x (스텝당 화면 크기 + 그때까지 쌓인 대화)

즉 스텝 수에 **선형이 아니라 제곱에 가깝게** 붙는다. 스텝이 3이면 싸고 12면 네 배가 아니라
훨씬 더 나온다. 그래서 이 도구를 재기 전에 **스텝 수부터 프로브로 확인한다.** 그 값을
모르는 채로 22건을 돌리면 견적이 두 배씩 빗나간다(전례: genschema).

`meta.steps` 와 `metrics.steps` 에 스텝 수를 남긴다(= `history.number_of_steps()`,
히스토리 항목 수다. 계획 갱신 같은 항목이 섞여 **`max_steps` 보다 큰 값이 나올 수 있다** —
상한이 안 먹은 게 아니다. 그래서 상한 도달 판정은 `>=` 로 한다). 이 도구의 비용표는 '건당 얼마'보다
'스텝당 얼마 x 스텝 몇 개'로 읽어야 한다.

**실측(2026-09-10, D2B 상세 1건):** 6스텝 / LLM 5회 / 입력 50,287토큰(캐시 9,591) /
출력 2,101토큰 / **28.46원** / 31.4초. 같은 케이스를 `firecrawl` 은 3.6원에 냈다.
6스텝 중 **4개가 `scroll`** 이었다 — 페이지를 '읽는' 데가 아니라 '찾는' 데 쓴 스텝이다.

## 이 도구에 준 것 / 안 준 것

셀렉터를 주지 않는다. 다른 LLM 도구와 **똑같은** `schema_utils.task_prompt` 문구와
`schema/*.json` 을 준다. 스키마는 pydantic 으로 받으므로 `schema_utils.pydantic_model`
(ScrapeGraphAI 와 **같은 변환기**)을 쓴다.

과제 문장에 URL 을 넣는다. 그건 힌트가 아니라 **어느 페이지를 볼지 지정하는 것**이고,
다른 도구도 전부 같은 URL 을 받는다.

## 기본값에서 바꾼 것 — 셋, 전부 이유가 있다

**1. `use_judge=False` (기본 True).**
   기본값이면 답을 다 낸 뒤에 **스크린샷을 최대 10장 붙여 자기 트레이스를 채점하는 LLM
   호출이 하나 더** 나간다(`Agent._judge_trace`). 이건 추출이 아니라 **자기 채점**이다.
   켜 두면 다른 6종에 없는 기능의 값이 이 도구의 '건당 추출 비용'에 섞여 들어가고,
   스크린샷 10장이라 그 한 번이 스텝 몇 개보다 비쌀 수 있다. 껐다. 판단은 우리 gold 가 한다.

**2. `max_steps` 를 건다 (기본 500).**
   에이전트가 헤매면 500스텝까지 간다 = 지출이 열려 있다는 뜻이다. 상한(`harness.ledger`)이
   결국 막지만 그건 프로젝트 예산을 태운 뒤다. 케이스 하나가 몇 스텝을 먹는지가 이 도구의
   측정 대상이므로 **상한에 닿았는지를 결과에 적는다**(`meta.hit_max_steps`). 그 케이스는
   '도구가 못 했다'가 아니라 '내가 끊었다'로 읽어야 한다.

**3. `frequency_penalty=None` (기본 0.3).**
   이건 취향이 아니라 **안 그러면 한 요청도 못 보낸다.** 이 도구의 OpenAI 래퍼는
   `frequency_penalty=0.3` 을 기본으로 실어 보내는데(주석: "4.1-mini 류가 \t 를 무한
   생성하는 걸 막는다"), Gemini 는 그 파라미터를 거부한다 —
   `400 Penalty is not enabled for models/gemini-2.5-flash` (2026-09-10 실측).
   도구가 OpenAI 모델을 전제로 만들어졌다는 뜻이고, 그 자체가 결과다. 리포트에 적는다.

`use_vision` 은 **기본 True 그대로** 뒀다. 화면을 보고 판단하는 게 이 도구의 정체성이라
끄면 다른 도구를 재는 셈이다. 대신 이미지 토큰이 비용의 큰 몫이라는 것을 결과에 적는다.
`enable_planning` 도 기본값이다 — 계획 세우기는 과제를 **수행하는** 일부라 남긴다.

## 비용 집계 — 태그를 헤더로 싣는다

`browser_use.ChatOpenAI` 에는 `extra_body` 가 없어서 다른 도구처럼
`metadata.tags` 를 못 싣는다. 대신 `default_headers` 가 있고, LiteLLM 프록시는 요청 헤더를
로그의 `metadata.headers` 에 그대로 남긴다(2026-09-10 실측). meter 의 태그 필터는 그
metadata 를 통째로 훑으므로 **헤더에 실은 태그로도 걸린다.**
그래서 이 도구는 `llm_tagged = True` 다 — Firecrawl 과 달리 동시성 제한이 필요 없다.

## 예산 정지를 삼키지 않게

이 도구는 LLM 이 실패하면 스스로 재시도한다(`max_failures=5`). 지출 상한에 걸린 것도
그냥 '실패한 스텝'으로 삼켜서, 밖에서 보면 **'에이전트가 못 했다'** 로 보인다. 실제로는
지갑이 멈춘 것이다. 그래서 히스토리의 오류 문자열에서 예산 표식을 찾아 밖으로 다시 던진다
(`adapters/base.py` 의 `BUDGET_STOP` 참고).

## 로그의 `I/O operation on closed pipe` 는 오류가 아니다

케이스마다 `asyncio.run()` 을 새로 열고 닫으므로 브라우저 서브프로세스의 파이프 transport 가
GC 시점에 정리되면서 윈도우에서 `Exception ignored in _ProactorBasePipeTransport.__del__`
트레이스백을 찍는다. **`Exception ignored` 라 실행은 그대로 이어지고** 결과에도 영향이 없다.
로그에서 트레이스백을 보고 실패로 세지 말 것.

## 외부망 — 텔레메트리와 확장 자동 다운로드를 둘 다 끈다

기본값이 **켜짐**인 것이 셋이다.

  - `ANONYMIZED_TELEMETRY`   PostHog 익명 통계
  - `BROWSER_USE_CLOUD_SYNC` 클라우드 동기화
  - `enable_default_extensions`  uBlock Origin / cookie / ClearURLs 확장을
                                 **실행 때 인터넷에서 내려받아** 브라우저에 심는다
                                 (2026-09-10 실측: "Downloading uBlock Origin Lite extension").

이 저장소는 외부망을 `harness/capture.py` 에서만 친다. 셋 다 끈다. 확장은 성능 문제이기도
하다 — 광고 차단기가 붙은 브라우저와 안 붙은 브라우저는 같은 조건이 아니고, 우리가 먹이는
것은 광고가 없는 **고정 스냅샷**이라 켜 봐야 얻을 것도 없다.
환경변수는 import 보다 먼저 세팅해야 한다(import 시점에 읽힌다).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

# import 시점에 읽히므로 **반드시 browser_use import 보다 먼저** 꺼야 한다.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_DISABLE_EXTENSIONS", "1")

from browser_use import Agent, BrowserProfile, ChatOpenAI   # noqa: E402

from .base import Adapter, RawPage, RunResult, is_budget_stop   # noqa: E402
from harness import normalize as nz, schema_utils as su         # noqa: E402
from harness.dotenv import load_env, proxy                      # noqa: E402
from harness.meter import Stopwatch                             # noqa: E402

DEFAULT_MODEL = "gemini-2.5-flash"

# 케이스 하나에 허용할 스텝 상한. 기본값 500 은 지출이 열려 있다는 뜻이라 쓰지 않는다.
# 공지 한 페이지를 읽는 데 이보다 많이 쓰면 그건 '더 주면 됐다'가 아니라 못 하고 있는 것이다.
MAX_STEPS = int(os.environ.get("AICRAWL_BROWSERUSE_MAX_STEPS", "12"))

# 출력 토큰 상한. **기본은 건드리지 않는다**(도구 기본값 4096, `llm/openai/chat.py`).
# 이 값을 세팅하면 그 값으로 덮는다 — 진단용이다. 왜 필요한지는 아래.
#
#   한전 목록(100행 x 6필드)의 JSON 은 4096토큰에 안 들어간다. 모델이 잘린 출력을 내고
#   ("Model output was truncated at max_completion_tokens=4096"), 에이전트가 그걸 재시도하다
#   스텝을 다 쓴다 -> `TIMEOUT`, 재현율 0 (2026-09-10 실측).
#   그런데 `crawl4ai_llm` / `scrapegraphai` / `firecrawl` 은 출력 상한을 아예 안 걸어
#   모델 기본값을 썼고 셋 다 한전 목록 1.0 이다. 즉 그 실패는 **'에이전트가 그리드를 못
#   읽는다'가 아니라 '이 도구의 기본 출력 상한이 100행 JSON 을 못 담는다'** 이다.
#   둘은 다른 정보라 표에 한 칸으로 적으면 안 된다. 표는 기본값으로 두고, 이 값을 올린
#   재실행을 **따로** 붙여 원인을 가른다.
MAX_OUT_TOKENS = os.environ.get("AICRAWL_BROWSERUSE_MAX_OUT")


class BrowserUseAdapter(Adapter):
    name = "browseruse"
    supports_fetch = False        # 에이전트가 알아서 연다. fetch/extract 를 못 가른다
    needs_llm = True
    llm_tagged = True             # 헤더로 태그를 싣는다 (모듈 도크 참고)

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        load_env()
        self.model = self.opts.get("model") or DEFAULT_MODEL
        self.run_id = self.opts.get("run_id") or ""
        self._base_url, self._api_key = proxy()
        self.max_steps = int(self.opts.get("max_steps") or MAX_STEPS)
        self.max_out = self.opts.get("max_out") or MAX_OUT_TOKENS
        # 헤드리스. 다른 도구(crawl4ai/scrapegraphai)도 헤드리스라 같은 조건이다.
        # 확장 자동 다운로드도 여기서 한 번 더 끈다. 환경변수만 믿지 않는 이유는
        # 그 기본값이 `default_factory` 로 읽히는 자리라 import 순서에 걸리기 때문이다.
        self._profile = BrowserProfile(headless=True, enable_default_extensions=False)

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    def _llm(self, target: Optional[str], task: str) -> ChatOpenAI:
        """프록시를 보게 묶고, 태그를 **헤더로** 싣는다."""
        return ChatOpenAI(
            model=self.model, api_key=self._api_key, base_url=self._base_url,
            temperature=0,
            # 기본 0.3 이면 Gemini 가 400 을 낸다 (모듈 도크 3번). 끄는 게 아니라 못 쓴다.
            frequency_penalty=None,
            **({"max_completion_tokens": int(self.max_out)} if self.max_out else {}),
            default_headers={"x-aicrawl-tag":
                             f"{self.run_id}:{self.name}|{target or '-'}|{task}"})

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

    @staticmethod
    def _budget_in(history: Any) -> Optional[str]:
        """히스토리에 삼켜진 예산 정지를 찾아낸다. 없으면 None."""
        for e in (history.errors() or []):
            if e and is_budget_stop(RuntimeError(str(e))):
                return str(e)
        return None

    def _no_answer(self, case_id: str, task: str, target: Optional[str],
                   history: Any, sw: Stopwatch) -> RunResult:
        """에이전트가 끝내 답을 안 냈다.

        스텝 상한에 닿았으면 `TIMEOUT` 이다 — 도구가 **내가 준 예산 안에** 못 끝냈다는
        뜻이고, 그 예산이 얼마였는지도 같이 적어야 읽을 수 있다. 상한에 닿지도 않았는데
        답이 없으면 도구 쪽 문제라 `TOOL_ERROR` 다.
        """
        steps = history.number_of_steps()
        hit = steps >= self.max_steps
        errs = [str(e) for e in (history.errors() or []) if e]
        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.steps = steps
        return RunResult(
            case_id=case_id, tool=self.name, ok=False, target_id=target,
            error_class="TIMEOUT" if hit else "TOOL_ERROR",
            error_msg=(f"{steps}스텝을 쓰고도 답을 내지 않았다"
                       + (f" (스텝 상한 {self.max_steps} 에 닿았다)" if hit else "")
                       + (f" - 마지막 오류: {errs[-1][:200]}" if errs else "")),
            metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(), "steps": steps,
                  "hit_max_steps": hit, "max_steps": self.max_steps,
                  "is_done": history.is_done(), "errors": errs,
                  "final_result": history.final_result(),
                  "max_completion_tokens": int(self.max_out) if self.max_out else 4096,
                  "actions": history.action_names()})

    # ---- 본체 ---------------------------------------------------------
    async def _run(self, url: str, schema: dict, task: str, target: Optional[str]) -> tuple:
        prompt = (f"{su.task_prompt(schema)}\n\n"
                  f"Read this page and return the result: {url}")
        agent = Agent(
            task=prompt,
            llm=self._llm(target, task),
            output_model_schema=su.pydantic_model(schema),
            browser_profile=self._profile,
            use_judge=False,          # 자기 채점은 추출이 아니다 (모듈 도크 참고)
        )
        history = await agent.run(max_steps=self.max_steps)
        try:
            await agent.close()
        except Exception:
            pass                      # 브라우저 정리 실패가 측정값을 덮지 않게
        return history

    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                with sw.stage("llm"):     # 에이전트는 fetch 와 추론을 못 가른다
                    history = asyncio.run(self._run(url, schema, task, target))
                with sw.stage("parse"):
                    # 상한에 걸린 걸 이 도구가 '실패한 스텝'으로 삼켰으면 여기서 되살린다.
                    hit = self._budget_in(history)
                    if hit:
                        raise RuntimeError(hit)
                    answer = history.structured_output
                    if answer is None:
                        # 답을 못 냈다. 여기서 빈 dict 로 넘기면 **필드가 전부 null 인
                        # '성공'** 이 되어 정확도 0 과 구분이 안 된다. 갈라서 적는다.
                        return self._no_answer(case_id, task, target, history, sw)
                    if hasattr(answer, "model_dump"):
                        answer = answer.model_dump()
                    elif isinstance(answer, str):
                        answer = json.loads(answer)
                    data = self._shape(task, answer, schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        steps = history.number_of_steps()
        m.steps = steps
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "steps": steps,
                  # 상한에 닿았으면 '도구가 못 했다'가 아니라 '내가 끊었다'로 읽어야 한다.
                  "hit_max_steps": steps >= self.max_steps,
                  "max_steps": self.max_steps,
                  "is_done": history.is_done(),
                  "urls": [u for u in (history.urls() or []) if u][:20],
                  "actions": history.action_names(),
                  "errors": [str(e) for e in (history.errors() or []) if e],
                  # 이 도구의 정체성이라 기본값을 그대로 뒀다. 이미지 토큰이 비용의 큰 몫이다.
                  "use_vision": True,
                  "use_judge": False,
                  # None 이면 도구 기본값 4096 을 그대로 썼다는 뜻이다.
                  "max_completion_tokens": int(self.max_out) if self.max_out else 4096,
                  # 도구 원본 응답. 내 정리가 뭘 바꿨는지 여기서 확인한다.
                  "tool_answer": json.loads(json.dumps(answer, ensure_ascii=False,
                                                       default=str))})
