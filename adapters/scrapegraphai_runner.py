"""ScrapeGraphAI — 그래프 파이프라인으로 페이지마다 LLM 추출 (README §7 Phase 2).

`crawl4ai_llm` 과 목적이 같다(페이지마다 LLM 이 값을 읽는다). 다른 것은 **구조**다.
crawl4ai 는 '마크다운 -> LLM 1회'인 단일 단계지만, 이 도구는 노드 그래프다.

    SmartScraperGraph = Fetch -> Parse -> GenerateAnswer
                        (playwright)  (html2text + 청킹)  (청크당 1회 + 병합 1회)

**청크가 N개면 LLM 호출이 N+1회다**(청크마다 병렬 호출 + 마지막에 병합 호출).
청크가 1개면 병합 없이 1회. 그래서 이 도구의 비용은 페이지 길이에 **선형이 아니라 계단식**으로
붙는다. 그 계단이 어디서 밟히는지가 이 도구를 재는 이유다.

## 이 도구에 준 것 / 안 준 것

셀렉터를 주지 않는다. 다른 LLM 도구와 **똑같은** `schema_utils.task_prompt` 문구와
`schema/*.json` 을 준다. 단 이 도구는 스키마를 pydantic 클래스로 받으므로 JSON Schema 를
그대로 옮긴 모델을 만들어 넘긴다(`_pydantic_of`) — 담는 그릇만 다르고 내용은 같다.

## 내가 기본값을 바꾼 것 — 두 개뿐이고 둘 다 이유가 있다

**1. `model_tokens=16384`.** 이 값이 곧 청크 크기다(`ParseNode.chunk_size = model_token`).
지정하지 않으면 도구가 `models_tokens` 표에서 모델을 못 찾아 경고를 찍고 **8192** 로 떨어진다.
우리 페이지는 마크다운으로 5~7K 토큰이라 그 값이면 페이지마다 청크 수가 갈리고, 그러면
호출 수·비용이 페이지 운에 좌우된다. `crawl4ai_llm` 의 `CHUNK_TOKENS` 와 **같은 16384** 를
줘서 두 도구에 같은 청크 예산을 맞췄다. 이래야 호출 수 차이가 내 설정이 아니라 그래프 구조의
차이가 된다.

**2. `model_instance` 로 LLM 을 직접 만들어 넘긴다.** 문자열 모델명(`openai/gemini-2.5-flash`)
으로도 되지만 그 경로에는 `extra_body` 를 실을 자리가 없다. 태그가 없으면 프록시 로그에서
이 도구의 호출을 골라낼 수 없고, meter 가 전부 걸러 내 **비용이 0 으로 집계된다**
(`crawl4ai_genschema` 에서 실측으로 확인한 함정). 그래프·프롬프트·청킹·병합은 전부 도구 것이고
내가 지정하는 것은 '어느 클라이언트로 부를까'뿐이다.

## "NA" — 이 도구의 없음 표식을 우리 스키마의 null 로 옮긴다

ScrapeGraphAI 의 프롬프트에는 `If you don't find the answer put as value "NA".` 가 박혀 있다
(`prompts/generate_answer_node_prompts.py`). 도구가 정한 '값 없음' 표식이다.
우리 gold 는 같은 뜻을 `null` 로 쓴다. 그대로 두면 **값이 없다고 정확히 말한 필드가 전부
오답이 되고, gold 가 null 인 자리에는 환각 판정까지 붙는다.** 도구의 추출력이 아니라
표기 관습을 재게 되므로 여기서 `null` 로 옮긴다.

옮기는 대상은 **정확히 `NA` / `N/A` 문자열뿐**이다(대소문자 무시, 앞뒤 공백 제외).
'NA 로 시작하는 값'이나 'NA 가 포함된 값'까지 건드리면 진짜 본문을 지우게 된다.
몇 개를 옮겼는지는 `meta.na_fields` 에 남긴다 — 이 판단이 점수를 얼마나 움직였는지
나중에 되짚을 수 있어야 한다. 도구 원본 응답도 `meta.tool_answer` 에 그대로 보관한다.

## 텔레메트리

이 도구는 기본값이 **켜짐**이라 그래프 이름·소스 URL·모델명·스키마를 자기 서버로 보낸다
(`telemetry/telemetry.py`, `g_telemetry_enabled = ... default True`). 이 저장소는 외부망을
`harness/capture.py` 에서만 친다. import 전에 끈다.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

# import 시점에 읽히므로 **반드시 import 보다 먼저** 꺼야 한다.
os.environ.setdefault("SCRAPEGRAPHAI_TELEMETRY_ENABLED", "False")

from pydantic import BaseModel                       # noqa: E402
from scrapegraphai.graphs import SmartScraperGraph   # noqa: E402
from scrapegraphai.telemetry import disable_telemetry  # noqa: E402

from .base import Adapter, RawPage, RunResult        # noqa: E402
from harness import normalize as nz, schema_utils as su  # noqa: E402
from harness.dotenv import proxy                     # noqa: E402
from harness.meter import Stopwatch                  # noqa: E402

disable_telemetry()

DEFAULT_MODEL = "gemini-2.5-flash"

# `crawl4ai_llm.CHUNK_TOKENS` 와 같은 값이어야 한다. 위 도크 참고.
MODEL_TOKENS = 16384

# 이 도구가 '못 찾았다'를 적는 방식. 우리 스키마의 null 과 같은 뜻이다.
_NA = {"na", "n/a"}

def _pydantic_of(schema: dict, name: str = "Notice") -> type[BaseModel]:
    """JSON Schema -> pydantic 모델.

    이 도구는 스키마를 pydantic 으로만 받는다(`utils/output_parser.py` 가 BaseModel 이
    아니면 예외를 던진다). 변환기는 `harness/schema_utils.py` 에 하나만 둔다 — browser-use
    도 같은 변환이 필요한데, 도구마다 따로 들고 있으면 같은 `schema/*.json` 에서 도구마다
    다른 모델이 나오고 그러면 도구가 아니라 내 변환기 둘을 비교하게 된다.
    """
    return su.pydantic_model(schema, name)


def _strip_na(v: Any, hits: list, path: str) -> Any:
    """도구의 'NA' 를 null 로. 정확히 그 문자열일 때만 건드린다."""
    if isinstance(v, str) and v.strip().lower() in _NA:
        hits.append(path)
        return None
    if isinstance(v, list):
        return [_strip_na(x, hits, f"{path}[]") for x in v]
    if isinstance(v, dict):
        return {k: _strip_na(x, hits, f"{path}.{k}" if path else k) for k, x in v.items()}
    return v


class ScrapeGraphAIAdapter(Adapter):
    name = "scrapegraphai"
    supports_fetch = True
    needs_llm = True

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        self.model = self.opts.get("model") or DEFAULT_MODEL
        self.run_id = self.opts.get("run_id") or ""
        self._base_url, self._api_key = proxy()
        # 브라우저는 그래프가 케이스마다 자기 것을 띄운다(FetchNode -> ChromiumLoader).
        # 여기서 미리 띄워 둘 수 있는 상태가 없으므로 setup 은 사실상 비어 있고,
        # 그래서 이 도구의 콜드 스타트는 첫 케이스의 wall 안에 들어간다.

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    def _llm(self, target: str, task: str):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=self.model, api_key=self._api_key, base_url=self._base_url,
            temperature=0, streaming=False,
            # 프록시 로그에서 이 도구의 호출만 골라내는 태그. 없으면 비용이 0 으로 집계된다.
            extra_body={"metadata": {"tags": [f"{self.run_id}:{self.name}", target, task]}},
        )

    # ---- 결과 정리 ----------------------------------------------------
    def _shape(self, task: str, answer: Any, schema: dict) -> tuple:
        """도구 응답 -> 스키마 모양. 후처리는 다른 LLM 도구와 **같은** `normalize.coerce`."""
        hits: list = []
        answer = _strip_na(answer, hits, "")
        rules = su.score_rules(schema)
        if not isinstance(answer, dict):
            answer = {}
        if task != "list":
            return nz.coerce(answer, rules), hits
        rows = answer.get("items")
        if not isinstance(rows, list):
            # 스키마를 줬는데도 다른 키로 싸서 올 때가 있다. **레코드(dict)가 든 리스트**만
            # 받는다 - 아무 리스트나 받으면 attachments 같은 문자열 배열을 목록으로 착각한다.
            rows = next((v for v in answer.values()
                         if isinstance(v, list) and v and isinstance(v[0], dict)), [])
        return {"items": [nz.coerce(r, rules) for r in rows if isinstance(r, dict)]}, hits

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                cfg = {
                    "llm": {"model_instance": self._llm(target, task),
                            "model_tokens": MODEL_TOKENS},
                    "verbose": False,
                    "headless": True,
                }
                graph = SmartScraperGraph(prompt=su.task_prompt(schema), source=url,
                                          config=cfg, schema=_pydantic_of(schema))
                with sw.stage("llm"):     # fetch·parse·LLM 이 한 run() 안에 묶여 있다
                    answer = graph.run()
                with sw.stage("parse"):
                    data, na = self._shape(task, answer, schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        state = graph.final_state or {}
        chunks = state.get("parsed_doc") or state.get("doc") or []
        doc = state.get("doc") or []
        page = getattr(doc[0], "page_content", "") if doc else ""
        m.page_bytes = len(page.encode("utf-8"))
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "graph": "SmartScraperGraph", "model_tokens": MODEL_TOKENS,
                  # 청크 수 = LLM 호출 수의 근거. N>1 이면 병합 호출이 하나 더 붙는다.
                  "chunks": len(chunks) if isinstance(chunks, list) else 1,
                  "fetched_chars": len(page),
                  # 'NA' 를 null 로 옮긴 자리. 이 판단이 점수를 얼마나 움직였는지 되짚는 근거.
                  "na_fields": na[:40], "na_count": len(na),
                  # 도구 원본 응답('NA' 포함). 내 정리가 뭘 바꿨는지 여기서 확인한다.
                  "tool_answer": json.loads(json.dumps(answer, ensure_ascii=False,
                                                       default=str)),
                  # 도구가 보고한 토큰. 비용 계산에는 쓰지 않는다 — 프록시 로그가 소스다.
                  "tool_reported_usage": graph.execution_info})
