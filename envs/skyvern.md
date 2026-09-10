# Skyvern (Docker)

**conda env 를 만들지 않는다.** 이 도구의 '환경'은 컨테이너이고, 우리 어댑터는 HTTP 로만
말하므로 `requests` 만 있으면 된다 — 그래서 harness 와 같은 `aicrawl` 에서 돈다.

**클라우드는 이 벤치마크에 못 올린다.** Firecrawl 과 같은 구조다 — SaaS 서버가 URL 을
직접 가져가므로 우리 리플레이 서버(사설 주소)에 원리상 못 닿는다. 셀프호스팅으로만 잰다.

## 띄우기 (실측 2026-09-10, skyvern `fa66df6`)

```bash
git clone --depth 1 https://github.com/Skyvern-AI/skyvern.git ~/skyvern-selfhost
cd ~/skyvern-selfhost
# .env 는 아래 표대로 직접 쓴다 (env.litellm.example 이 출발점)
docker compose up -d postgres skyvern        # UI(skyvern-ui)는 벤치마크에 필요 없다
```

첫 기동 때 조직과 API 토큰을 만들어 `.skyvern/credentials.toml` 의
`orgs = [{name="Skyvern", cred="<토큰>"}]` 에 적는다. compose 가 그 디렉터리를 호스트로
마운트하므로 어댑터가 밖에서 읽는다(`SkyvernAdapter._key_from_disk`).
`.env` 의 `SKYVERN_API_KEY` 로 덮어쓸 수도 있다.

## `.env` — 기본값에서 바꾼 것과 그 이유

| 키 | 값 | 왜 |
|---|---|---|
| `ENABLE_OPENAI_COMPATIBLE` / `LLM_KEY` | `true` / `OPENAI_COMPATIBLE` | LLM 을 우리 LiteLLM 프록시로 돌린다. 벤더를 직접 치게 두면 **비용이 리포트에서 사라지고 지출 상한도 안 걸린다** |
| `OPENAI_COMPATIBLE_API_BASE` | `http://host.docker.internal:4000/v1` | 컨테이너 안에서 `127.0.0.1` 은 자기 자신이다 |
| `OPENAI_COMPATIBLE_SUPPORTS_VISION` | `true` (기본 `false`) | 이 도구의 정식 provider 설정은 전부 vision 이 켜져 있고 우리 모델도 지원한다. `false` 로 두면 도구가 아니라 **눈을 가린 도구**를 재게 되고, vision 을 켜고 잰 `browseruse` 와 조건이 어긋난다 |
| `MAX_STEPS_PER_RUN` | `12` (기본 `50`) | `browseruse` 와 **같은 스텝 예산**. 기본 50 은 지출이 열려 있다는 뜻이다 |
| `SKYVERN_TELEMETRY` | `false` (기본 `true`) | PostHog 로 나간다. 이 저장소는 외부망을 `capture.py` 에서만 친다 |
| `ALLOWED_HOSTS` | `["host.docker.internal"]` | **이게 없으면 한 건도 못 돈다** — 아래 |

키는 `.env` 에만 둔다. 벤더 키를 어댑터에 직접 주지 않는다.

## 함정: `.internal` 로 끝나는 호스트를 SSRF 로 막는다

기본값이면 태스크 생성이 400 으로 거절된다.

```
{"detail":"The host in your url is blocked: host.docker.internal"}
```

`skyvern/utils/url_validators.py` 가 `.internal` 접미사를 내부 호스트로 보고 끊는다
(Firecrawl 의 `safeFetch.ts` 와 **같은 종류의 벽**이다). 푸는 것은 공식 설정
`ALLOWED_HOSTS` 이고, **이건 모드 스위치가 아니라 정확히 일치하는 호스트만 여는 목록**이다
(`_is_allowed_host` 가 exact match). 그래서 저 한 줄이 여는 것은 그 호스트 하나뿐이다.

## 돌리기

```bash
conda activate aicrawl
python tools/serve_proxy.py --port 4000        # 프록시부터
python -m harness.runner --tools skyvern --model gemini-2.5-flash \
       --replay-host 0.0.0.0 --url-host host.docker.internal
```

`--replay-host 0.0.0.0` 이 있어야 컨테이너가 리플레이 서버에 붙고,
`--url-host host.docker.internal` 이 있어야 컨테이너가 **자기 자신이 아닌** 호스트를 본다.
둘 중 하나만 빠져도 도구는 빈 페이지를 받고 **조용히 '정확도 0'** 이 된다.

## 비용

셀프호스팅에는 step 크레딧이 없다. 지출은 전부 프록시를 지나는 토큰이고 이미 걸린 상한
안에 있다. 클라우드로 돌릴 때만 `harness.ledger` 가 `pricing.yaml:services.skyvern_cloud`
로 step 을 **요청 전에** 막는다.

컨테이너가 LLM 을 부르므로 요청에 태그를 못 싣는다 → `llm_tagged = False` →
러너가 시간창만으로 집계하고 **동시성 1 을 강제한다**.
