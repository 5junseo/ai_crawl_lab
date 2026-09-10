# ai_crawl_lab

AI 기반 크롤링 도구를 **비용 / 속도 / 정확도 / 안정성** 기준으로 실측 비교하는 벤치마크 프로젝트.

**공통 과제는 "조달 사이트 3곳의 공지사항을 수집한다"** 하나로 고정한다. 과제가 같아야 도구가 비교된다.

**측정 대상은 도구 6종을 8가지 구성으로 편 것 + 대조군 1 = 9회다.**
Crawl4AI 는 성격이 완전히 다른 3가지 모드(`css` / `genschema` / `llm`)로 나눠 쟀다 —
같은 라이브러리지만 비용이 0원과 63만원으로 갈리므로 한 줄에 적을 수 없다.

> **측정 완료 (2026-09-10).** 9/9 실행, 22건 × 9회, 누적 지출 7,334원.
> **결과와 도구별 평가는 [RESULTS.md](RESULTS.md)** 에 있다.
> 현재 상태와 남은 일은 **[NEXT.md](NEXT.md)**, 이 문서는 **설계 근거**를 담는다.

**어떤 기존 코드베이스와도 분리된 독립 프로젝트**다.
외부 데이터베이스에 접속하지 않고, 기존 수집 코드도 가져오지 않는다.
정답셋도 **이 프로젝트에서 직접 만든다**(§4-1). 출력은 로컬 JSON 파일뿐.

---

## 1. 왜 분리했나

출발점이던 환경은 **Python 3.5.6** 이라 대상 도구를 하나도 못 돌린다.

| 도구 | 최소 Python | 비고 |
|---|---|---|
| AutoScraper | 3.6+ | 유일하게 가벼움 |
| Crawl4AI | 3.10+ | playwright 동반 |
| ScrapeGraphAI | 3.10+ | pydantic v2 |
| Firecrawl (py client) | 3.8+ | 서버는 Node/Docker |
| browser-use | **3.11+** | playwright + LLM |
| Skyvern | **3.11+** | Docker 권장 |

도구끼리도 playwright / litellm / pydantic 버전이 충돌한다.
→ **1 도구 = 1 env** 원칙. Skyvern은 Docker로 격리.

---

## 2. 무엇을 판단하려는가

도구를 하나의 점수로 줄 세우지 않는다. 성격이 다르므로 목적을 3개로 쪼갠다.

| 시나리오 | 질문 | 주 지표 | 해당 도구군 |
|---|---|---|---|
| **A. 신규 사이트 착수** | 게시판 1개 새로 붙이는 사람시간이 줄어드는가 | 사람 개입 시간, 첫 성공까지 시도 횟수 | browser-use, Skyvern, ScrapeGraphAI |
| **B. 변경 내성** | 사이트 개편 시 무수정으로 복구되는가 | 셀렉터 파손 후 성공률 | Skyvern, Crawl4AI+LLM, Firecrawl |
| **C. 상시 대량 수집 단가** | 건당 원가/속도가 견딜 만한가 | 건당 원, 건/분 | AutoScraper, Crawl4AI(비LLM), Scrapy |

> **착수 전 전망:** C 는 기존 Scrapy 가 이미 최적이고, 실익은 A·B 에 있으며
> 그중 **A(신규 게시판 셀렉터 1회 생성)** 가 가성비가 가장 높을 것이다.
>
> **실측 결과 — 대체로 맞았다.** C 는 대조군이 전 칸 1.0 / 4~46ms / 0원이라 어떤 도구도 이기지 못했다.
> A 는 `crawl4ai_genschema` 가 1만 건 12~127원으로 유일하게 규모에서 버텼다(다른 LLM 도구는 60만원 대).
>
> **다만 예상 못 한 축이 하나 더 나왔다** — 값이 **입력 필드 안에 있는 폐쇄형 SPA**.
> 여기서는 HTML→텍스트 변환이 값을 통째로 버려서 변환 기반 도구 전부가 0.286~0.397 에 묶였고,
> 살아 있는 DOM 을 읽는 **에이전트만 0.619~0.905** 를 냈다. A·B·C 어디에도 안 들어가는,
> **'그 도구가 아니면 아예 안 되는' 자리**다.
>
> **B(변경 내성)는 이번에 재지 않았다.** 사이트 개편을 모사하지 않았기 때문이다.

### 태스크는 둘이다

| task | 입력 | 출력 | 어느 도구가 강한가 |
|---|---|---|---|
| `list` | 공지 목록 1페이지 | 게시글 N건 배열 | 반복 구조를 뽑는 **비LLM** 도구 |
| `detail` | 공지 상세 1건 | 필드 10개 | 라벨-값을 이해하는 **LLM·에이전트** |

한 점수로 합치지 않는다. 합치면 그 차이가 지워진다.

---

## 3. 대상 사이트

대상 3곳. 실제로 찔러 확인한 결과가 티어를 갈랐다(2026-09-03 / G2B 재확인 2026-09-08).

| 티어 | 사이트 | targets | 렌더 | 인코딩 | 확인된 성격 |
|---|---|---|---|---|---|
| T1 | **D2B** 국방전자조달 | `t1_d2b` | 서버 렌더 | UTF-8 | 평문 GET 으로 목록·상세 모두 취득. 게시판 4종 |
| T2 | **G2B** 나라장터 | `t2_g2b` | WebSquare SPA | UTF-8 | 목록은 팝업 + JSON API, 세션 없이 403 |
| T3 | **SRM** 한국전력 | `t3_kepco_srm` | ExtJS SPA | UTF-8 | `POST /router` Ext.Direct RPC + CSRF + 세션 |

- **G2B** 공지 목록의 실제 엔드포인트는 `POST /fm/fma/fmaa/Pst/selectNtcPstList.do` 이고
  **HTML 이 아니라 JSON API** 다. 화면상으로는 메인에서 버튼을 누르면 **새 창(팝업)** 으로 열린다.
  메인에서 세션을 부트스트랩(쿠키 XTVID/WHATAP)한 뒤 Referer 를 붙여 호출해도
  `403 {"ErrorCode":-801,"접근 권한이 존재하지 않습니다."}` 로 막힌다 —
  쿠키만으로는 부족하고 WebSquare 가 붙이는 인증 요소가 필요하다. **브라우저 없이는 목록에 도달 못 한다.**
  진입 URL(`?bodyDataKey=<uuid>&key=<blob>`)도 세션마다 새로 발급되므로 case_id 로 쓸 수 없다 →
  렌더 후 DOM 을 동결하고 case_id 는 공지 번호로 우리가 부여한다.
- **한전 SRM** 은 `api.js` 에 Ext.Direct 메서드 3,463개(`findAnnounceList` 등)가 선언돼 있다.
  공지사항은 **비로그인으로 열린다**(2026-09-08 확인) — 계정·인증서 없이 진행한다.
  RPC 를 직접 쏘는 무단 탐침은 하지 않고, 브라우저로 정상 화면을 띄우며 오가는 `/router` 응답만
  참조용으로 `meta.json` 에 보관한다.
- **인코딩 표본은 없다.** 대상 3곳이 전부 UTF-8 이라 `ENCODING` 실패 모드는 실측되지 않는다.
  실패 모드 분류에는 남겨두되(다른 사이트를 추가하면 즉시 동작한다), 리포트에서 이 칸이 0인 것은
  "도구가 인코딩을 잘 처리했다"가 아니라 **"시험하지 않았다"**로 읽어야 한다.

---

## 4. 핵심 설계 결정

### 4-1. 정답셋(gold set) = 직접 작성한 정답

**동결된 fixtures 를 사람이 읽고 확정한 값이 정답**이다. 느리지만 이 방식만의 이점이 있다 —
정답이 어떤 도구의 출력에도 오염되지 않는다.

```
detail (10): title, author, dept, category, posted_at,
             period_start, period_end, views, attachments, body
list   (6) : seq, title, author, posted_at, views, has_attachment
```

`schema/notice_detail.json` / `schema/notice_list.json` 이 유일한 소스다.
필드 정의와 채점 규칙(`x_score`)이 전부 거기 있고 코드는 그걸 읽는다. `gold/` 도 같은 필드명을 쓴다.

채점 규칙 — 문자열은 정규화 후 완전일치 / 작성자·부서는 부분일치 / 숫자는 파싱 후 일치 /
날짜는 파싱 후 일치(예측이 더 거친 단위면 그 단위까지만) / 첨부는 이름 집합 일치 /
**본문은 토큰 자카드 유사도 0.9 이상이면 정답**.

> 본문을 완전일치로 재면 줄바꿈·구분자 처리 차이로 전원 오답이 된다. 유사도로 재면 정상적인
> 공백 차이는 통과하고 **LLM 이 본문을 요약해 버리는 것은 잡힌다** — 그게 여기서 보고 싶은 것이다.

**작성 규칙 두 가지가 결과를 좌우한다.**

- `null` 은 "페이지에 그 값이 없다"는 **단언**이다. 그 자리를 채운 도구는 환각으로 집계된다.
  (첨부가 없으면 `null` 이 아니라 `[]`)
- 아직 안 본 칸은 `null` 이 아니라 `__TODO__` 로 둔다. 채점에서 통째로 빠지고(`NOT_SCORED`)
  리포트의 `gold미작성` 열에 남는다. 이 구분이 없으면 멀쩡한 도구가 환각 판정을 받는다.

```bash
python -m harness.gold_init --target t1_d2b                      # 빈 칸을 __TODO__ 로 생성
python -m harness.gold_init --target t1_d2b --open NEWS01-6071   # 원문을 텍스트로 덤프해 보며 채움
python -m harness.gold_init --target t1_d2b --check              # 미작성/스키마 위반 점검
```

> **작성 순서**: gold 를 먼저 확정하고 그 다음에 도구를 돌린다. 도구 출력을 보고 정답을 적으면
> 그 도구 쪽으로 정답이 끌려가 벤치마크가 무의미해진다.

### 4-2. 오프라인 리플레이 (fetch와 extract 분리)

실사이트를 도구마다 반복 호출하면 (a) 사이트 부하 (b) 매번 페이지가 달라 비교 불공정
(c) 재채점마다 재요청 문제가 생긴다.

```
[fetch 벤치]  실사이트 1회 → fixtures/<target>/<case_id>/{page.html, page.png, meta.json}
                              ↑ 네트워크·렌더링 속도는 이때만 측정
                              ↑ meta.json 의 task(list|detail) 가 채점 스키마를 고른다

[extract 벤치] fixtures를 로컬 HTTP 서버(127.0.0.1:8899)로 서빙
               → 전 도구가 동일 스냅샷을 봄. 무한 재실행, 사이트 무부하
```

로컬 서버로 서빙하는 이유: 대부분 도구가 URL 입력 인터페이스이기 때문.
**`Content-Type: text/html; charset=euc-kr` 헤더까지 원본 그대로 재현**해야 인코딩 비교가 유효하다.
`page.html` 은 응답 **바이트 그대로** 저장한다 — 디코딩해서 저장하면 인코딩 비교가 무의미해진다.

> G2B·한전은 렌더 후 DOM 을 동결하므로(`mode: rendered_dom`) 원본 바이트가 아니다.
> 그 케이스는 `ENCODING` 실패 모드 판정에서 제외한다.

### 4-3. 비용은 LiteLLM proxy로 중앙 계측

도구별 토큰 회계 방식이 제각각(청킹 중복 카운트, 캐시 토큰 미집계)이라 자체 리포트를 믿으면 안 된다.
모든 도구의 `base_url`을 로컬 프록시로 돌려 한 곳에서 로깅한다.

```
browser-use  ─┐
Crawl4AI      ├→ LiteLLM proxy (127.0.0.1:4000) → Anthropic / Ollama
ScrapeGraphAI ┘        └ 요청별 로그: 모델, in/out/cache 토큰, latency
```

- Firecrawl 클라우드는 credit 과금 → `pricing.yaml` 에 환산 테이블 별도
- Skyvern은 step 과금 → step 수 × 단가
- **캐시 히트 토큰을 분리 집계하지 않으면 비용이 3~4배 틀어진다**

최종 산출: **건당 원**, **월 1만건 환산 비용**

---

## 5. 폴더 구조

```
ai_crawl_lab/
  README.md                 # 설계 근거 (이 문서)
  RESULTS.md                # 도구 비교 결과 + 도구별 평가 + 함정 기록
  NEXT.md                   # 현재 상태 · 다음 할 일
  pricing.yaml              # 모델별 in/out/cache 단가 + 원화 환산 + services(크레딧·스텝)
  .env.example              # 키는 .env 에만. 커밋 금지
  tools/serve_proxy.py      # LiteLLM 프록시 기동 (.env 로드 + UTF8 + 상한을 같이 챙긴다)
  targets/                  # 대상 정의 (케이스 URL, 렌더 방식, 확인된 사실)
    t1_d2b.yaml  t2_g2b.yaml  t3_kepco_srm.yaml
  schema/
    notice_list.json        # 목록 N건 스키마 + x_score 규칙 — 단일 소스
    notice_detail.json      # 상세 1건 스키마 + x_score 규칙
  fixtures/                 # 동결된 페이지 스냅샷            [git 제외]
  gold/<target>/{list,detail}.json    # 정답 JSON             [git 제외]
  adapters/                 # 도구별 러너 — 동일 인터페이스
    base.py                       # Adapter / RawPage / RunResult / Metrics
    registry.py                   # 도구 -> 모듈 lazy 매핑 (env 없는 도구는 SKIP)
    echo_runner.py                # 배선 점검용. 도구 아님
    scrapy_baseline_runner.py     # 사람이 짠 셀렉터 = 대조군
    autoscraper_runner.py
    crawl4ai_css_runner.py        # 비LLM
    crawl4ai_llm_runner.py
    crawl4ai_genschema_runner.py  # LLM 1회 → 셀렉터 캐시 재사용
    scrapegraphai_runner.py
    firecrawl_runner.py
    browseruse_runner.py
    skyvern_runner.py
  harness/
    schema_utils.py         # 스키마 로드 / x_ 확장 제거 / gold 행 정규화 / pydantic 변환
    normalize.py            # LLM 출력 타입 보정 — **전 도구가 같은 것을 쓴다**
    gold_init.py            # 정답셋 생성·점검 (__TODO__ 관리)
    gold_ref.py             # 참고 구현. **채점에 쓰지 않는다** (아래 경고)
    dotenv.py               # .env 로드 + 프록시 주소/키
    litellm_config.yaml     # 프록시 모델 라우팅 (클라우드 + Ollama)
    litellm_logger.py       # 요청 1건 = logs/litellm.jsonl 1줄 + 요청 전 상한 검사
    ledger.py               # 프로젝트 누적 지출 원장 (토큰 + 크레딧·스텝) + 상한
    capture.py              # 실사이트 1회 호출 -> fixtures 동결 (외부망은 여기서만)
    replay_server.py        # fixtures 서빙 (charset 원본 재현)
    runner.py               # 매트릭스 실행
    meter.py                # 시간/토큰/비용 계측 (LiteLLM 로그 집계)
    score.py                # gold 대비 채점 + 환각/변동성 판정
    report.py               # CSV + Markdown 리포트
  envs/                     # 도구별 requirements + README.md + skyvern.md(컨테이너)
  logs/
    litellm.jsonl           # 토큰 지출 단일 소스           [git 제외]
    spend.jsonl             # 비토큰 지출(크레딧·스텝)      [git 제외]
  results/
    <run_id>/<tool>/<case_id>__rN.json   # 원본 응답 보존 → 채점 로직 변경 시 재채점
```

`.gitignore` 필수: `fixtures/ gold/ results/ logs/ .env` — 수집한 페이지와 API 키다.

> **`harness/gold_ref.py` 는 비교 대상 도구들과 무관하다.**
> 여기서 나온 값을 도구 점수에 쓰지 않고, **도구 출력을 보고 `gold_ref.py` 를 고치지도 않는다.**
> 그렇게 하면 벤치마크가 무의미해진다.

---

## 6. 공통 인터페이스

어댑터가 이 계약만 지키면 harness는 도구를 몰라도 된다.

```
Adapter.setup()                      # 브라우저/모델 기동 (콜드 스타트 별도 계측)
Adapter.fetch(url) -> RawPage        # 원문/마크다운. 미지원 도구는 None
Adapter.extract(url, schema) -> RunResult
Adapter.teardown()

# 태스크는 runner 가 self.opts["task"] 로 알려주고, schema 인자도 그에 맞춰 들어온다.
# list  -> {"items": [ {...}, ... ]}
# detail-> {필드: 값, ...}

RunResult:
  case_id, tool, ok, error_class
  data   : dict     # schema 준수 추출 결과
  raw    : str      # 중간 산출물(markdown/html) 보존
  metrics:
    wall_ms, setup_ms, fetch_ms, llm_ms
    llm_calls, in_tokens, out_tokens, cached_in_tokens
    cost_usd, cost_krw
    page_bytes, steps          # steps: 에이전트류 액션 수
```

fetch/extract가 합쳐진 도구(Firecrawl extract, Skyvern)는 `fetch_ms=None` 으로 두고 end-to-end만 비교한다.

---

## 7. 진행 단계 — **전부 완료 (2026-09-10)**

각 단계가 무엇을 확인하려던 것이고 실제로 무엇이 나왔는지만 적는다. 숫자는 `RESULTS.md`.

### Phase 0 — 기반 구축 (코드는 적고, 공수의 절반)

동결 스냅샷 22건 + 정답셋 22건 확정. **도구를 하나도 돌리기 전에 끝냈다.**
정답셋을 나중에 만들면 도구 출력을 보고 정답을 고치게 되고, 그러면 벤치마크가 무의미해진다.

- `schema/notice_{list,detail}.json` — list 6필드 / detail 10필드 + 채점 규칙(`x_score`)을 스키마 안에만 둔다
- `harness/*` 전량 — capture / replay_server / runner / score / report / meter / ledger
- fixtures 22건 (D2B 14 · G2B 4 · 한전 4), sha256 전부 다름을 확인
- gold 22건 전부 `_provenance: human`. `null` 14칸은 **화면에 항목 자체가 없음**을 확인한 값이다

### Phase 1 — 비LLM 베이스라인

`scrapy_baseline`(대조군) · `autoscraper` · `crawl4ai_css` · `crawl4ai_genschema`.
**정확도 상한선(1.0)과 속도 상한선(4~46ms)을 여기서 확보**했다. 이후 모든 도구는 이 선과의 거리로 읽는다.

### Phase 2 — LLM 추출 3종

`crawl4ai_llm` · `scrapegraphai` · `firecrawl`. 전 도구에 **같은 모델·같은 문구·같은 스키마**를 줬다.
그런데도 점수가 갈렸고, **갈린 자리는 전부 HTML→텍스트 변환기였다**(§9 참고).

### Phase 3 — 에이전트 2종

`browseruse` · `skyvern`. **표본을 줄이지 않고 22건 전부** 돌렸다 —
프로브로 건당 비용을 먼저 재고 예산을 확인한 뒤 진행했다.

> 원래 계획은 "사이트당 2케이스 × 1회"였다. 프로브 결과 `browseruse` 22건이 889원으로
> 예산 안에 들어와 전량으로 바꿨다. `skyvern` 은 22건 4,508원이라 **상한을 $5 → $8 로 올려**
> 진행했다(사용자 승인, 2026-09-10).

### Phase 4 — 채점 및 리포트

`results/` 는 원본 응답을 그대로 보관하므로 채점 규칙을 고쳐도 재실행 없이 다시 채점된다.
실제로 채점 코드를 여러 번 고치는 동안 **LLM 을 한 번도 다시 부르지 않았다.**

### 실행 기록

| 도구 | run_id | 결과 |
|---|---|---|
| `scrapy_baseline` | `20260909-113311` | 전 칸 1.0 / 0원 |
| `autoscraper` | `20260909-115058` | 목록 재현율 0.10~0.25 |
| `crawl4ai_css` | `20260909-120533` | 전 칸 1.0 / 0원 |
| `crawl4ai_genschema` | `20260909-154455-gs` | 510원 / 환각 17 |
| `crawl4ai_llm` | `20260909-160710-llm` | 343원 / 환각 4 |
| `scrapegraphai` | `20260909-163521-sg` | 323원 / **환각 0** |
| `firecrawl` | `20260909-171937-fc` | **228원** / 환각 0 |
| `browseruse` | `20260910-093715-bu` | 889원 / 한전 상세 **0.905** |
| `skyvern` | `20260910-110903-sk` | 4,508원 / 17건 성공 |

---

## 8. 측정 지표 정의

### 속도
- 동시성 **1** / **8** 두 조건 (실 스루풋은 LLM rate limit에 먼저 걸림)
- **콜드 / 웜 분리** — browser-use·Skyvern은 브라우저 기동만 수 초~수십 초
- fetch 단계(네트워크·렌더링)와 extract 단계(LLM)를 **반드시 따로** 기록

### 안정성 (정확도보다 중요)
- `variance` : 동일 페이지 3회 반복 시 값이 갈린 필드 수 (목록은 행 집합이 갈리면 `items` 1점)
- 실패 모드 분류 : `RENDER_FAIL / BLOCKED / SCHEMA_VIOLATION / HALLUCINATION / TIMEOUT / ENCODING / TOOL_ERROR`
  (동시 해당 시 노출 우선순위 `ENCODING > HALLUCINATION > SCHEMA_VIOLATION`)
- **환각 판정** : gold에 없는 값을 그럴듯하게 채우는 경우. 목록에서는 **없는 게시글을 만들어내는 것**.
  파이프라인에 그대로 들어가면 조용히 오염되므로 별도 카운트

### 최종 리포트 컬럼
```
tool | tier | task | 성공률 | 필드정확도 | 정밀도 | 재현율 | 환각 | gold미작성 | 변동성
     | 콜드ms | 웜ms | 건당스텝 | 건당토큰 | 건당원 | 1만건/월 원 | 주요실패모드
```
- 정밀도/재현율은 `list` 태스크에만 나온다. 필드정확도는 **짝지어진 행에서만** 계산한 값이다.
- `gold미작성` 이 0이 아니면 정답셋이 덜 채워진 것이고, 그 필드는 분모에서 빠져 있다.
- **`건당스텝`** 은 에이전트류에만 값이 있다. 이 도구들은 비용이 페이지가 아니라 **스텝**에 붙으므로
  '건당 얼마'만 적으면 왜 그 값이 나왔는지 읽을 수 없다(실측: 같은 사이트 안에서 3~14스텝, 12배 차이).
  값이 하나도 없는 run 에서는 이 열을 아예 뺀다 — 빈 칸이 '0스텝'으로 읽히기 때문이다.

### 실패 모드가 아닌 것 — `BUDGET_STOP`

지출 상한에 걸린 케이스는 **실패 모드 집계에 넣지 않는다.** 도구가 실패한 게 아니라
**내 지갑이 멈춘 것**이고, 그 케이스는 실패한 게 아니라 **아예 재지 않은 것**이다.
채점에서 통째로 빼고 리포트에 따로 세어 보여 준다.

상한이 걸리는 경로가 둘이라 판정도 둘이다 — 원장(`ledger.guard`)은 어댑터와 같은 프로세스라
예외 클래스가 그대로 오지만, **토큰 상한은 프록시가 막으므로 도구에는 HTTP 오류로 와서
클래스가 남지 않는다.** 그래서 양쪽 메시지에 `[AICRAWL_BUDGET_STOP]` 표식을 박고 문자열로 잡는다.
한 번 걸리면 남은 케이스는 **도구를 부르지도 않고** 그 사실만 행으로 남긴다.

---

## 9. 사전 예측 vs 실측 — 무엇이 맞았고 무엇이 틀렸나

아래는 **도구를 돌리기 전에 적어 둔 예측**과 실제 결과다. 예측을 지우지 않고 남긴다 —
어디서 틀렸는지가 다음 벤치마크의 설계 근거가 되기 때문이다.

| 예측 | 결과 | |
|---|---|---|
| **Crawl4AI 의 진짜 가치는 LLM 이 아니라 셀렉터 1회 생성**(`genschema`)이고, 이 저장소에 가장 잘 맞는 후보다 | **맞았다.** 1만 건 환산 12~127원으로 LLM 계열 중 유일하게 규모에서 버틴다. `crawl4ai_llm` 은 같은 조건에서 63만원 | ✅ |
| `LLMExtractionStrategy` 는 긴 페이지를 청킹해 토큰이 급증한다 | **맞았다.** 기본 `chunk_token_threshold=2048` 이면 공지 한 건이 3~4조각으로 잘려 호출이 4배가 된다. 16384 로 올려 22건=22회로 맞췄다 | ✅ |
| browser-use / Skyvern 은 **대량 수집용이 아니다** | **맞았다.** 1만 건 16.8만~94만원. 건당 시간도 17~588초 | ✅ |
| 한전 SRM 처럼 RPC 뒤에 숨은 사이트가 **에이전트의 진짜 시험대다** | **맞았다. 그리고 에이전트가 이겼다.** 변환 기반 도구가 전부 0.238~0.333 에 묶인 자리에서 `browseruse` 가 **0.905** 를 냈다 — 스스로 JS 를 써서 폼 `.value` 를 읽었다 | ✅ |
| Firecrawl 클라우드는 **국내 IP 차단 가능성**이 있다 | **틀렸다. 더 근본적인 문제였다.** 차단이 아니라 **구조상 불가**다 — SaaS 서버가 URL 을 직접 가져가므로 사설 주소의 동결본에 원리상 못 닿는다. Skyvern 클라우드도 같다 | ❌ |
| AutoScraper 는 D2B 에서 **압도적일 것** | **틀렸다.** 정답 예시를 주고 시작(H3)했는데도 D2B 상세 **0.153**, 목록 재현율 0.25. 예시를 준 페이지 밖으로 못 나간다 | ❌ |
| ScrapeGraphAI + Ollama 는 비용 0이지만 **한국어 장문에서 품질 저하** | **안 쟀다.** GPU 8GB 로는 `litellm_config.yaml` 의 Ollama placeholder 를 못 채운다. 클라우드 모델로만 측정했다 | ⏸ |
| 인코딩 위험은 대상 3곳이 전부 UTF-8 이라 **드러나지 않는다** | **맞았다.** `ENCODING` 실패 모드는 실측되지 않았다. 리포트에서 그 칸이 0인 것은 '잘했다'가 아니라 **'안 쟀다'** 로 읽어야 한다 | ✅ |
| **D2B NEWS04 의 함정** — 헤더에 `작성자` 가 있는데 본문 행에는 없다(th 6 / td 5) | **설계대로 작동했다.** 컬럼을 위치로 맞추는 도구가 여기서 걸렸다. `crawl4ai_genschema` D2B 목록 0.588 / 환각 10 | ✅ |
| 첨부파일(HWP/PDF) **내용**은 전 도구 범위 밖 | 그대로. 파일 '이름'만 채점했다 | — |

### 예측하지 못했던 것 — 실측으로만 나온 것

- **HTML→텍스트 변환기가 모델·프롬프트보다 먼저 결과를 가른다.** 같은 페이지·같은 모델·같은 문구인데
  crawl4ai 의 마크다운은 달력 위젯을 `2050/12/31` 날짜 하나로 보여줬고 html2text 는 1950~2050 연도
  목록으로 펼쳐 보여줬다. 환각이 **4건과 0건**으로 갈렸다.
- **에이전트 2종이 출력 토큰 상한 4096 을 똑같이 기본값으로 들고 있다.** 52행짜리 목록 JSON 이
  거기 안 들어가 둘 다 무너진다. 변환 기반 도구는 상한이 없어(실측 최대 17,200토큰) 같은 페이지에서 1.0.
- **에이전트는 시키지 않은 일을 한다.** `skyvern` 목록 실패 2건이 *"1페이지는 뽑았는데 2페이지로
  넘어가려다"* 스텝을 소진한 것이다. 과제는 1페이지만 요구했고 다른 도구는 전부 한 페이지만 읽었다.
- **`browseruse` 는 본문을 복사하지 않고 다시 타이핑한다.** 15건 중 5건이 잘렸고(최악 876자→103자),
  **앞부분은 원문과 글자 단위로 같아** 검수로 걸러내기가 가장 어렵다.

## 10. 빠른 시작

env 는 **1 도구 = 1 env** 다. 만드는 법은 `envs/README.md`, Skyvern 은 컨테이너라 `envs/skyvern.md`.

```bash
conda create -n aicrawl python=3.11 -y && conda activate aicrawl
pip install -r envs/aicrawl.txt
cp .env.example .env          # 키를 채운다. 키는 .env 에만 둔다
```

### 1) 페이지 동결 → `fixtures/`

**외부망은 여기서만 친다.** 기본 3초 간격이고 줄이지 않는다.

```bash
python -m harness.capture --target t1_d2b              # 정적
python -m harness.capture --target t2_g2b --browser    # SPA 는 렌더 후 DOM 동결
```

### 2) 정답셋 작성 → `gold/`

**도구를 돌리기 전에 끝낸다.** 나중에 만들면 도구 출력을 보고 정답을 고치게 된다.

```bash
python -m harness.gold_init --target t1_d2b
python -m harness.gold_init --target t1_d2b --check    # __TODO__ 남은 칸 확인
```

### 3) LLM 프록시 기동 — LLM 도구를 돌리기 전에

**비용은 여기서만 잡힌다.** 도구가 벤더를 직접 치면 그 지출이 리포트에서 사라지고 상한도 안 걸린다.

```bash
python tools/serve_proxy.py --port 4000    # .env 로드 + PYTHONUTF8 + 원장 상한을 같이 챙긴다
python -m harness.ledger                   # 지금까지 얼마 썼나 / 상한이 얼마인가
```

> 맨손으로 `litellm --config ...` 를 띄우면 세 가지를 빠뜨린다 — `PYTHONUTF8=1`(없으면 한글 주석에서
> 죽는다), `.env` 로딩(`/v1/responses` 경로가 `os.environ` 에서 키를 직접 찾는다), 상한값.

### 4) 매트릭스 실행 → `results/<run_id>/`

```bash
python -m harness.runner --tools crawl4ai_llm --model gemini-2.5-flash --tag llm

# 컨테이너 안에서 도는 도구(Firecrawl 셀프호스팅 · Skyvern)는 두 옵션이 **둘 다** 필요하다
python -m harness.runner --tools skyvern --model gemini-2.5-flash        --replay-host 0.0.0.0 --url-host host.docker.internal --tag sk
```

`--replay-host 0.0.0.0` 이 있어야 컨테이너가 리플레이 서버에 붙고,
`--url-host host.docker.internal` 이 있어야 컨테이너가 **자기 자신이 아닌** 호스트를 본다.
하나만 빠져도 도구는 빈 페이지를 받고 **오류 없이 '정확도 0'** 이 된다.

### 5) 재채점 → `report.csv` / `report.md`

```bash
python -m harness.report --run <run_id>
```

`results/` 에 원본 응답이 그대로 남으므로 **채점 규칙을 고쳐도 LLM 을 다시 부르지 않는다.**

### 지출 상한

`AICRAWL_MAX_USD` 는 **프로젝트 누적 총액**이다(추가분이 아니다). `harness/ledger.py` 가
토큰(프록시 로그)과 비토큰(크레딧·스텝)을 **합쳐서** 세고, 프록시를 재시작해도 이어진다.
도구별 단위 상한은 `pricing.yaml` 의 `services.<도구>.max_units`.
**단가도 단위 상한도 없는 서비스는 호출 자체가 거부된다.**

상한에 걸린 케이스는 `TOOL_ERROR` 가 아니라 **`BUDGET_STOP`** 으로 남고 채점에서 통째로 빠진다 —
도구가 실패한 게 아니라 **지갑이 멈춘 것**이고, 그 케이스는 실패가 아니라 **안 잰 것**이다.

배선 점검용으로 `echo` 어댑터가 있다(registry phase=0). 도구가 아니며 리포트 대상이 아니다.

---

## 11. 힌트 등급 — "어디까지 알려주고 시작하나"

도구마다 필요한 사전 정보가 다르다. AutoScraper 는 구조상 정답 예시 1건이 없으면 아예 못 돌고,
browser-use 는 URL 하나만 줘도 스스로 찾아 들어간다. 이걸 그냥 두면 "누가 더 좋은가"가 아니라
"누구에게 더 친절했는가"를 재게 된다.

그래서 힌트를 **등급으로 고정하고 전 도구에 동일하게 준다.** 등급은 `targets/*.yaml` 의
`hint_level` 에 박아두고, 어댑터는 그 이상을 절대 쓰지 않는다.

| 등급 | 주는 것 | 재는 것 |
|---|---|---|
| **H0** | 사이트 루트 URL + 자연어 목표 | 진입 경로를 스스로 찾는가 (시나리오 A의 상한) |
| **H1** | + 목표 페이지의 정확한 URL | 렌더링·인코딩 처리 능력 |
| **H2** | + 진입 절차 (새 창으로 열림, 렌더 대기, 세션 필요) | 절차를 알려줘도 수행할 수 있는가 |
| **H3** | + 셀렉터 또는 정답 예시 1건 | 순수 추출 정확도만 |

**핵심 결과는 점수가 아니라 "그 도구가 성공한 최소 등급"이다.**
H0에서 되는 도구와 H3을 줘야 되는 도구는 실무 가치가 완전히 다르다.

### 실제로 준 등급과 그 결과

**실측에서 전 도구에 동일하게 준 등급은 `targets/*.yaml` 에 박혀 있다** —
D2B `H1`, G2B·한전 `H2`. 예외는 `autoscraper` 하나로, 예시 없이 구조상 아무것도 못 하므로
**항상 H3** 이다. 그래서 이 도구의 점수는 **다른 도구보다 유리한 조건에서 나온 값**이다.

| 도구 | 준 등급 | 그 등급에서 나온 결과 (상세 D2B/G2B/한전) |
|---|---|---|
| `scrapy_baseline` (대조군) | H3 | 1.0 / 1.0 / 1.0 — 사람이 짠 셀렉터라 당연하다 |
| `autoscraper` | **H3** (예외) | **0.153** / 0.786 / 0.714 — 정답을 주고 시작했는데도 이 점수다 |
| `crawl4ai_css` | H3 | 1.0 / 1.0 / 1.0 |
| `crawl4ai_genschema` | H1 / H2 | 0.812 / 0.603 / 0.238 — 셀렉터를 LLM 이 만들었다 |
| `crawl4ai_llm` | H1 / H2 | **1.0** / 0.302 / 0.333 |
| `scrapegraphai` | H1 / H2 | **1.0** / 0.397 / 0.286 |
| `firecrawl` | H1 / H2 | **1.0** / 0.397 / 0.286 |
| `browseruse` | H1 / H2 | 0.925 / **0.738** / **0.905** |
| `skyvern` | H1 / H2 | 0.938 / 0.667 / 0.619 |

**H0(사이트 루트만 주고 스스로 찾게 하기)은 측정하지 않았다.** 그건 fetch 벤치의 영역이고,
이번 측정은 전부 동결 스냅샷 위의 extract 다(§11-1). 에이전트의 진짜 강점인
'진입 경로를 스스로 찾는가'는 **아직 안 잰 항목**이다.

### 11-1. 그래서 3사이트 × 7도구가 전부 가능한가

**extract 벤치(리플레이)는 전부 가능하다.** fixtures 를 로컬 서버가 정적 HTML 로 서빙하므로
G2B 렌더 DOM 위에서 AutoScraper 도 돈다. 전 도구가 동일 스냅샷을 보므로 이게 공정한 비교다.

**fetch 벤치(실사이트)는 전부 불가능하고, 그게 결과다.** 비LLM 도구는 G2B·한전에 도달조차 못 한다.
0점이 아니라 **"도달 불가"로 기록**한다 — 이 둘을 같은 칸에 적으면 안 된다.

```
fetch  : 스스로 그 페이지에 도달했는가        -> 도구가 갈린다. 시나리오 A/B
extract: 같은 스냅샷에서 값을 뽑았는가        -> 전 도구 동일 조건. 시나리오 C
```

README §4-2 에서 fetch 와 extract 를 나눈 진짜 이유가 이것이다.

---

## 12. 결정 기록

| # | 항목 | 결정 | 영향 |
|---|---|---|---|
| 1 | 공통 과제 | **조달 3사이트 공지사항 수집** (G2B / D2B / 한전 SRM) | 입찰공고 스키마 폐기, 공지 게시판 스키마로 교체 |
| 2 | 태스크 단위 | **목록 + 상세 둘 다** | 스키마 2개, 채점 2종(행 정렬 + 필드). 리포트도 태스크별로 분리 |
| 3 | 본문 채점 | **토큰 자카드 유사도 ≥ 0.9** | 공백 차이는 통과, 요약·누락은 잡힌다 |
| 4 | LLM 제공자 | 계획은 Claude 주력 + Ollama 보조 → **실측은 `gemini-2.5-flash` 단일** | 도구 간 비교는 같은 모델이라 공정하다. 대신 **'이 도구의 한계'가 아니라 '이 도구 + 이 모델의 한계'** 다. Ollama 는 GPU 8GB 로 못 돌려 미측정 |
| 5 | 정답셋 경로 | **기존 수집 코드·외부 DB 미사용. fixtures 를 보고 직접 작성** | `gold_init.py`, `__TODO__`/`NOT_SCORED` 도입 |
| 6 | env 구성 | **1 도구 = 1 env, 전부 생성 완료** | 9개 어댑터가 6개 conda env + 컨테이너 2종에서 돈다. Skyvern 은 conda env 없이 `aicrawl` 에서 HTTP 로만 말한다 |
| 7 | 힌트 제공 방식 | **등급(H0~H3)으로 고정, 전 도구 동일** | 즉흥적 힌트는 "누가 더 좋은가" 대신 "누구에게 더 친절했는가"를 재게 된다 (§11) |
| 8 | 한전 SRM 접근 | **비로그인 공개 공지로 진행** | 계정·인증서 불필요. T3 유지 |
| 9 | Firecrawl·Skyvern 배포 형태 | **셀프호스팅으로만 측정** | 클라우드는 SaaS 서버가 URL 을 직접 가져가 사설 주소의 동결본에 **원리상 못 닿는다.** 우회가 아니라 결과다 — 'SaaS 크롤러는 사설망을 못 본다' |
| 10 | 에이전트 표본 | 계획 '사이트당 2케이스' → **22건 전량** | 프로브로 건당 비용을 먼저 재고 예산을 확인한 뒤 바꿨다. 표본이 다르면 다른 7종과 같은 표에 못 올린다 |
| 11 | 지출 상한 | **프로젝트 누적 총액**(`AICRAWL_MAX_USD`), 파일 원장으로 토큰+비토큰 합산 | 프록시 안에서만 세면 **재시작이 곧 상한 해제**이고, 크레딧·스텝 과금은 프록시를 안 지나 아예 안 세진다. 요청 **전에** 막는다 |
| 12 | 상한 정지의 기록 | **`BUDGET_STOP`** 으로 분리, 실패 모드 집계에서 제외 | 도구를 잰 숫자와 지갑을 잰 숫자를 한 칸에 적지 않는다(§8) |
| 13 | 상한값 | $5 → **$8** (2026-09-10, 사용자 승인) | Skyvern 22건 견적이 남은 예산을 넘겨서. 누적 총액 기준이다 |
| 14 | fetch 벤치 | **재지 않고 종료** (2026-09-10, 사용자 판단) | 에이전트에게 실사이트를 주면 스스로 수십 번 요청해 **'외부망은 capture.py 에서만, 3초 간격'** 규칙을 강제할 수 없다. 게다가 페이지가 매일 달라 **재현이 안 되므로** 동결본 위의 22건과 같은 표에 못 올린다. 돈(1,000원 안쪽)이 아니라 이 둘이 이유다 |

### 남은 것

측정은 끝났고, 아래는 **이번에 재지 않은 것**이다. `RESULTS.md` 의 '아직 안 잰 것' 과 같다.

- **fetch 벤치 — 재지 않기로 결정했다**(결정 기록 #14). 이번 표는 전부 동결 스냅샷 위의 extract 다.
  그래서 **이 표의 어떤 칸도 '실사이트에 도달할 수 있다'를 주장하지 않는다.**
  특히 `autoscraper` 의 G2B 목록 1.0 은 **스냅샷을 줬을 때** 값이다 — 실사이트에서는 JS 렌더가 안 돼
  그 페이지를 볼 수조차 없다. 비LLM 도구가 '도달 불가'인 것과 '스냅샷 주면 90점'인 것은
  완전히 다른 정보이며 **같은 칸에 적으면 안 된다.**
- **변동성** — 전부 `repeat=1` 이다. 특히 에이전트는 같은 페이지에서 스텝 수가 갈리면 비용이 그대로
  갈리는데, `browseruse` D2B 상세만 봐도 3~9스텝(16~52원)으로 벌어졌다. 그게 페이지 차이인지
  실행마다 달라지는 것인지는 `repeat>1` 을 돌려야 안다.
- **모델 교체 민감도** — 전부 `gemini-2.5-flash` 한 모델의 값이다. 한전 상세 0.238 이 더 센 모델로
  올라가는지 모른다.
- **`ENCODING` 실패 모드** — 대상 3곳이 전부 UTF-8 이라 실측되지 않았다. 리포트에서 이 칸이 0인 것은
  '도구가 잘했다'가 아니라 **'안 쟀다'** 다.
- **Ollama(로컬 모델)** — GPU 8GB 로는 `litellm_config.yaml` 의 placeholder 를 못 채운다.
- **`skyvern` SPA 비용 재측정** — 실패한 실행이 다음 케이스의 측정창에 비용을 흘렸다.
  어댑터는 고쳤지만(`_cancel`) 다시 재지 않았다. 믿을 수 있는 값은 D2B 건당 94원과 총액 4,508원이다.
