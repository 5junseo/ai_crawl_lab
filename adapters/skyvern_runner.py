"""Skyvern — 브라우저 에이전트 (README §7 Phase 3).

`browseruse` 와 같은 계열이다(스텝마다 화면을 보고 다음 행동을 고른다). 다른 것은 **어디서
도느냐**다. browser-use 는 우리 파이썬 프로세스 안에서 돌지만, Skyvern 은 **서버**다 —
컨테이너 안에서 자기 브라우저를 띄우고, 우리는 HTTP 로 과제를 던지고 결과를 기다린다.

    POST /v1/run/tasks    {prompt, url, data_extraction_schema, max_steps, proxy_location}
    GET  /v1/runs/{id}    -> {status, output}

## 클라우드는 이 벤치마크에 못 올린다 (Firecrawl 과 같은 이유)

Skyvern 클라우드는 URL 을 **자기 서버가 가져간다.** 우리는 동결 스냅샷을 사설 주소로
먹이므로 클라우드는 거기 원리상 못 닿는다. `SKYVERN_API_KEY` 도 없다.
**셀프호스팅으로만 잰다.** 그러면 크레딧(step 과금)은 0 이고, 지출은 전부 우리 프록시를
지나는 토큰이다 — 이미 걸린 상한 안에 있다.

## 엔진 — v1 은 이 벤치마크에 못 쓴다 (도구가 못해서가 아니라 API 가 그렇다)

`/v1/run/tasks` 의 기본 엔진은 `skyvern-1.0` 인데, 그 분기는 이렇게 되어 있다
(`routes/agent_protocol.py`).

    url = run_request.url
    data_extraction_goal = None          # <- 여기
    navigation_goal = run_request.prompt
    if not url:                          # URL 이 **없을 때만** LLM 으로 목표를 쪼갠다
        task_generation = await task_v1_service.generate_task(...)
        data_extraction_goal = task_generation.data_extraction_goal

즉 **URL 을 주면 `data_extraction_goal` 이 영원히 `None`** 이다. 우리는 어느 페이지를 볼지
지정해야 하므로 URL 을 반드시 준다 -> v1 은 탐색만 하고 **추출을 아예 시도하지 않는다.**
실측하면 `status="completed"` 인데 `output` 도 `extracted_information` 도 `None` 이다
(2026-09-10). 조용히 '정확도 0'이 되는 모양이라 특히 위험하다.

그래서 **`engine="skyvern-2.0"`** 을 쓴다. v2 는 `extracted_information_schema` 를 그대로
받고 URL 도 받는다. 이건 우회가 아니라 **이 과제에 맞는 유일한 엔진**이다.

## 컨테이너 안에서 `127.0.0.1` 은 자기 자신이다

리플레이 서버는 **호스트**에서 돈다. 컨테이너에 `http://127.0.0.1:8899/...` 를 주면
자기 자신을 찌르고 빈 페이지를 받는다 — 그러면 조용히 '정확도 0'이 된다.
`host.docker.internal` 로 바꿔서 준다(compose 가 `host-gateway` 로 매핑해 둔다).
러너의 `--url-host` 가 그 일을 하고, 이 어댑터는 혹시 그냥 들어와도 여기서 한 번 더 고친다.

## 기본값에서 바꾼 것

**1. `proxy_location="NONE"` (기본 `RESIDENTIAL`).**
   기본값이면 트래픽을 주거용 프록시 업체로 돌린다. 우리는 그런 계약이 없고, 무엇보다
   **고정 스냅샷을 외부로 내보내는 셈**이다. 이 저장소는 외부망을 `capture.py` 에서만 친다.

**2. `max_steps=12` (기본 `MAX_STEPS_PER_RUN=50`).**
   `browseruse` 와 **같은 값**이다. 두 에이전트를 비교하려면 스텝 예산이 같아야 한다.
   상한에 닿았는지는 결과에 적는다 — 그 케이스는 '도구가 못 했다'가 아니라 '내가 끊었다'다.

**3. `OPENAI_COMPATIBLE_SUPPORTS_VISION=true`** (컨테이너 `.env`, 기본 `false`).
   이 도구의 정식 provider 설정(OPENAI_*/ANTHROPIC_*/GEMINI_*)은 전부 vision 이 켜져 있고
   우리 모델도 vision 을 지원한다. `false` 로 두면 도구가 아니라 **눈을 가린 도구**를 재게
   되고, vision 을 켜고 잰 `browseruse` 와도 조건이 어긋난다.

**4. `SKYVERN_TELEMETRY=false`** (기본 `true`). PostHog 로 나간다.

## 비용 집계에 태그를 못 쓴다

LLM 을 부르는 것은 **컨테이너**다. 우리가 그 요청 본문이나 헤더를 못 건드리므로
(`browseruse` 는 `default_headers` 로 실을 수 있었지만 여기는 그 자리가 없다)
`llm_tagged = False` 다 — Firecrawl 과 같다. 러너가 시간창만으로 집계하고 **동시성 1 을
강제한다.** 여러 도구가 동시에 프록시를 치면 시간창으로는 누구 비용인지 못 가른다.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

import requests

from .base import Adapter, RawPage, RunResult, is_budget_stop
from harness import normalize as nz, schema_utils as su
from harness.dotenv import load_env
from harness.meter import Stopwatch

SELFHOST_URL = "http://127.0.0.1:8000"

# 셀프호스팅 클론 위치. 여기 `.skyvern/credentials.toml` 에 조직 토큰이 들어 있다.
SKYVERN_HOME_DEFAULT = os.path.expanduser(r"~\skyvern-selfhost")

# 컨테이너가 우리 프록시/리플레이 서버에 닿는 이름. compose 의 extra_hosts 가 매핑한다.
HOST_FROM_CONTAINER = "host.docker.internal"

# 엔진. **v1(`skyvern-1.0`)은 이 벤치마크에 못 쓴다** — 모듈 도크의 '엔진' 절 참고.
ENGINE = os.environ.get("AICRAWL_SKYVERN_ENGINE", "skyvern-2.0")

# `browseruse` 의 MAX_STEPS 와 **같은 값**이어야 한다. 모듈 도크 참고.
MAX_STEPS = int(os.environ.get("AICRAWL_SKYVERN_MAX_STEPS", "12"))

# 한 케이스를 기다려 줄 시간.
#
# **이 값이 짧으면 도구가 아니라 내 인내심을 재게 된다.** 600초로 뒀더니 22건 중 2건이
# 아직 `running` 인 상태에서 잘렸고, 그게 `TOOL_ERROR` 로 기록됐다(2026-09-10).
# 스텝 상한(12)이 이미 실행 길이를 묶고 있으므로 이 값은 그 상한이 실제로 걸릴 만큼
# 넉넉해야 한다 — 여기서 끊기면 스텝 상한이 무슨 값이든 의미가 없어진다.
POLL_TIMEOUT_S = float(os.environ.get("AICRAWL_SKYVERN_TIMEOUT", "1800"))
POLL_EVERY_S = 3.0

_DONE = {"completed", "failed", "terminated", "canceled", "timed_out"}


class SkyvernAdapter(Adapter):
    name = "skyvern"
    supports_fetch = False        # 에이전트가 알아서 연다. fetch/extract 를 못 가른다
    needs_llm = True
    # 컨테이너가 LLM 을 부르므로 요청에 태그를 못 싣는다 -> 시간창 집계 + 동시성 1.
    llm_tagged = False

    # ---- 생명주기 -----------------------------------------------------
    def setup(self) -> None:
        load_env()
        self.model = self.opts.get("model") or os.environ.get("MODEL_NAME") or ""
        self.run_id = self.opts.get("run_id") or ""
        self.api_url = (os.environ.get("SKYVERN_API_URL") or SELFHOST_URL).rstrip("/")
        self.max_steps = int(self.opts.get("max_steps") or MAX_STEPS)
        self.cloud = "api.skyvern.com" in self.api_url

        key = os.environ.get("SKYVERN_API_KEY") or ""
        if not key:
            # 셀프호스팅은 첫 기동 때 키를 파일로 떨군다(compose 가 ./.skyvern 을 마운트).
            key = self._key_from_disk()
        if not key:
            raise RuntimeError(
                "Skyvern API 키가 없다. 셀프호스팅이면 컨테이너가 뜬 뒤 "
                "skyvern-selfhost/.skyvern/ 에 생기고, 아니면 .env 의 SKYVERN_API_KEY 를 "
                "채워라")
        self._s = requests.Session()
        self._s.headers.update({"x-api-key": key, "Content-Type": "application/json"})

    @staticmethod
    def _key_from_disk() -> str:
        """셀프호스팅이 첫 기동 때 만들어 둔 **조직 토큰**을 읽는다.

        `entrypoint-skyvern.sh` 가 조직을 만들고 그 토큰을 `.skyvern/credentials.toml` 에
        `orgs = [{name="Skyvern", cred="<토큰>"}]` 로 적는다. compose 가 그 디렉터리를
        호스트로 마운트하므로 밖에서 읽을 수 있다. **값은 로그에 찍지 않는다.**
        """
        base = os.environ.get("SKYVERN_HOME") or SKYVERN_HOME_DEFAULT
        p = os.path.join(base, ".skyvern", "credentials.toml")
        if not os.path.exists(p):
            return ""
        txt = open(p, "r", encoding="utf-8", errors="replace").read()
        m = re.search(r'cred\s*=\s*"([^"]{20,})"', txt)
        return m.group(1) if m else ""

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    def _reachable(self, url: str) -> str:
        """컨테이너에서 닿는 주소로 바꾼다. 모듈 도크 참고."""
        if self.cloud:
            return url
        return re.sub(r"//(127\.0\.0\.1|localhost)([:/])", f"//{HOST_FROM_CONTAINER}\\2", url)

    # ---- 결과 정리 ----------------------------------------------------
    def _shape(self, task: str, answer: Any, schema: dict) -> dict:
        """다른 LLM 도구와 **같은** `normalize.coerce`. 사이트별 분기 없음."""
        rules = su.score_rules(schema)
        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except Exception:
                answer = {}
        if isinstance(answer, list):
            answer = {"items": answer}
        if not isinstance(answer, dict):
            answer = {}
        if task != "list":
            # 상세인데 한 겹 싸여 오는 경우가 있다 ({"extracted_information": {...}}).
            if not (set(answer) & set(rules)) and len(answer) == 1:
                inner = next(iter(answer.values()))
                if isinstance(inner, dict):
                    answer = inner
            return nz.coerce(answer, rules)
        rows = answer.get("items")
        if not isinstance(rows, list):
            rows = next((v for v in answer.values()
                         if isinstance(v, list) and v and isinstance(v[0], dict)), [])
        return {"items": [nz.coerce(r, rules) for r in rows if isinstance(r, dict)]}

    # ---- 서버 대화 ----------------------------------------------------
    def _start(self, url: str, schema: dict) -> dict:
        body = {
            "prompt": su.task_prompt(schema),
            "url": self._reachable(url),
            "data_extraction_schema": su.prompt_schema(schema),   # x_ 확장 키를 뺀 순수 스키마
            "engine": ENGINE,              # v1 은 URL 을 주면 추출을 안 한다 - 모듈 도크 참고
            "max_steps": self.max_steps,
            "proxy_location": "NONE",      # 기본 RESIDENTIAL - 모듈 도크 참고
        }
        r = self._s.post(f"{self.api_url}/v1/run/tasks", json=body, timeout=60)
        self._raise_with_body(r)
        return r.json()

    @staticmethod
    def _raise_with_body(r: "requests.Response") -> None:
        """실패하면 **서버가 뭐라고 했는지**까지 예외에 담는다.

        `raise_for_status()` 만 쓰면 `400 Bad Request for url: ...` 만 남아서 결과 파일만
        보고는 원인을 알 수 없다. 실제로 첫 실행이 그래서 한 번 헛돌았다 — 진짜 이유는
        `{"detail":"The host in your url is blocked: host.docker.internal"}` 였다.
        """
        if r.ok:
            return
        detail = (r.text or "")[:600]
        raise RuntimeError(f"Skyvern {r.status_code} {r.request.method} {r.url}: {detail}")

    def _wait(self, run_id: str) -> dict:
        t0 = time.time()
        last = {}
        while time.time() - t0 < POLL_TIMEOUT_S:
            r = self._s.get(f"{self.api_url}/v1/runs/{run_id}", timeout=30)
            self._raise_with_body(r)
            last = r.json()
            if str(last.get("status") or "").lower() in _DONE:
                return last
            time.sleep(POLL_EVERY_S)
        # **기다리기를 포기하면 반드시 서버 쪽도 멈춰야 한다.**
        # 안 그러면 실행이 계속 돌면서 (1) 돈을 더 쓰고 (2) 그 LLM 호출이 **다음 케이스의
        # 시간창에 섞여** 남의 비용으로 집계된다. 이 도구는 태그를 못 실어 시간창으로만
        # 가르므로(llm_tagged=False) 오염을 막을 다른 방법이 없다.
        # 2026-09-10 실측: 이걸 안 해서 한전 목록 한 건이 97호출/1,248원으로 찍혔다.
        self._cancel(run_id)
        raise TimeoutError(
            f"{POLL_TIMEOUT_S:.0f}초 안에 끝나지 않았다 (마지막 상태 {last.get('status')!r}). "
            f"실행은 취소했다. AICRAWL_SKYVERN_TIMEOUT 으로 늘릴 수 있다")

    def _cancel(self, run_id: str) -> None:
        """실행을 서버에서 멈춘다. 실패해도 측정을 죽이지 않는다."""
        for path in (f"/v1/runs/{run_id}/cancel", f"/v1/runs/{run_id}/cancel/"):
            try:
                if self._s.post(f"{self.api_url}{path}", timeout=30).ok:
                    return
            except Exception:
                pass

    @staticmethod
    def _steps(final: dict) -> Optional[int]:
        """스텝 수는 실행 응답의 `step_count` 를 그대로 쓴다.

        태스크별 엔드포인트를 따로 두드리지 않는 이유는 v2 가 **워크플로 실행**(`wr_...`)으로
        돌아서 `/api/v1/tasks/{id}/steps` 에 안 잡히기 때문이다. 못 읽어도 측정을 죽이지
        않는다 — 비용의 진짜 출처는 프록시 로그다.
        """
        v = final.get("step_count")
        return int(v) if isinstance(v, (int, float)) else None

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                with sw.stage("llm"):     # 에이전트는 fetch 와 추론을 못 가른다
                    started = self._start(url, schema)
                    rid = started.get("run_id") or started.get("task_id")
                    final = self._wait(rid)
                with sw.stage("parse"):
                    status = str(final.get("status") or "").lower()
                    steps = self._steps(final)
                    fail = final.get("failure_reason") or final.get("error") or ""
                    if fail and is_budget_stop(RuntimeError(str(fail))):
                        # 상한에 걸린 걸 서버가 '실패한 태스크'로 삼켰으면 여기서 되살린다.
                        raise RuntimeError(str(fail))
                    answer = final.get("output")
                    if status != "completed" or answer is None:
                        return self._unfinished(case_id, task, target, final, steps, sw)
                    data = self._shape(task, answer, schema)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.steps = steps
        if self.cloud and steps:
            m.units = {"step": float(steps)}
        return RunResult(
            case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "deployment": "cloud" if self.cloud else "selfhost",
                  "api_url": self.api_url, "run_id_tool": rid,
                  "steps": steps, "max_steps": self.max_steps,
                  "hit_max_steps": bool(steps and steps >= self.max_steps),
                  "engine": final.get("run_type"),
                  "proxy_location": "NONE",
                  # 도구 원본 응답. 내 정리가 뭘 바꿨는지 여기서 확인한다.
                  "tool_answer": json.loads(json.dumps(answer, ensure_ascii=False,
                                                       default=str))})

    def _unfinished(self, case_id: str, task: str, target: Optional[str],
                    final: dict, steps: Optional[int], sw: Stopwatch) -> RunResult:
        """끝났는데 답이 없다.

        스텝 상한에 닿았으면 `TIMEOUT` 이다 — 도구가 **내가 준 예산 안에** 못 끝냈다는
        뜻이고, 그 예산이 얼마였는지도 같이 적어야 읽을 수 있다.
        """
        status = str(final.get("status") or "").lower()
        hit = bool(steps and steps >= self.max_steps)
        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.steps = steps
        return RunResult(
            case_id=case_id, tool=self.name, ok=False, target_id=target,
            error_class="TIMEOUT" if (hit or status == "timed_out") else "TOOL_ERROR",
            error_msg=(f"상태 {status!r} 로 끝났고 output 이 없다"
                       + (f" (스텝 {steps}, 상한 {self.max_steps} 에 닿았다)" if hit else "")
                       + (f" - {final.get('failure_reason')}" if final.get("failure_reason") else "")),
            metrics=m,
            meta={"task": task, "target_id": target, "ts": time.time(),
                  "steps": steps, "max_steps": self.max_steps, "hit_max_steps": hit,
                  "status": status, "failure_reason": final.get("failure_reason"),
                  "run_id_tool": final.get("run_id")})
