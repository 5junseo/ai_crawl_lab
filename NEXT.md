# 이어서 진행하기

갱신 2026-09-10 (9차: **도구 9/9 완료**). 세션이 끊겨도 이 파일부터 읽으면 이어진다.
설계 **근거**는 `README.md`, 도구 비교 **결과**는 `RESULTS.md`,
이 파일은 **현재 상태 · 다음 할 일 · 지켜야 할 것**만 담는다.

> ## Phase 1 완료 (2026-09-09)
>
> 비LLM 4종 끝. LLM 파이프라인이 **실제로** 한 바퀴 돌았다 —
> `.env` → LiteLLM 프록시 → Gemini → JSONL → meter → 채점.
> `crawl4ai_genschema` run `20260909-154455-gs`, 모델 `gemini-2.5-flash`, 22/22 성공, **510원**.
>
> ```
> conda activate aicrawl
> set PYTHONUTF8=1 && set PYTHONPATH=. && set AICRAWL_MAX_USD=5
> litellm --config harness/litellm_config.yaml --port 4000
> conda activate crawl4ai
> python -m harness.runner --tools <도구> --model gemini-2.5-flash
> ```
>
> `PYTHONUTF8` 을 빼면 프록시가 한글 주석에서 죽고, `AICRAWL_MAX_USD` 를 빼면 코드 기본값 $5 가
> 걸린다. 상한은 `litellm_settings.max_budget` 이 아니라 `harness/litellm_logger.py` 가 건다
> (프록시 것은 DB 없이는 무시된다 — 실측 확인).
>
> **Phase 2 완료.** `crawl4ai_llm` 343원 / `scrapegraphai` 323원 / `firecrawl` 228원.
> **Phase 3 완료.** `browseruse` 889원 / `skyvern` 약 4,508원.
> 누적 지출 **7,334원** / 상한 11,040원 · 남은 돈 **3,706원**.
>
> ```
> conda activate aicrawl
> python tools/serve_proxy.py --port 4000      # .env 로드 + UTF8 + 원장 상한을 같이 챙긴다
> python -m harness.ledger                      # 지금까지 얼마 썼나
> ```
>
> **상한이 이제 전부 걸려 있다**(2026-09-09). `harness/ledger.py` 가 토큰(프록시 로그)과
> 비토큰(크레딧·스텝)을 **합쳐서** 세고, 프록시를 재시작해도 이어진다. 도구별 단위 상한은
> `pricing.yaml` 의 `services.<도구>.max_units` — Firecrawl 900크레딧 / Skyvern 4,500크레딧
> (각 무료 티어의 90%). 단가도 단위 상한도 없는 서비스는 **호출 자체가 거부된다.**
>
> ## Phase 1~3 전부 완료 — 도구 9/9 (2026-09-10)
>
> 결과는 `RESULTS.md`. 누적 지출 **7,334원 / 상한 11,040원**, 남은 돈 3,706원.
>
> | 도구 | 22건 총액 | run |
> |---|---|---|
> | `scrapy_baseline` `autoscraper` `crawl4ai_css` | 0원 | Phase 1 |
> | `crawl4ai_genschema` | 510원 | `20260909-154455-gs` |
> | `crawl4ai_llm` / `scrapegraphai` / `firecrawl` | 343 / 323 / **228원** | Phase 2 |
> | `browseruse` | 889원 | `20260910-093715-bu` |
> | `skyvern` | **약 4,508원** | `20260910-110903-sk` |
>
> **`skyvern` 요약** — 22건 중 17건 성공. 가장 비싸고 가장 느리다(같은 D2B 상세 한 건을
> `firecrawl` 6.08원/6초, `browseruse` 34.14원/42초, `skyvern` 136.87원/225초 — **다섯 도구
> 전부 정확도 1.0**). 실패 5건은 **전부 도구 쪽 사유**다: 목록 2건은 *1페이지를 뽑아 놓고
> 2페이지로 넘어가려다* 12스텝 소진(과제는 1페이지만 요구한다), 2건은 도구 자신의
> `LLM_REASONING_ERROR`, 1건은 계획 반복 50회 초과.
>
> **에이전트 2종이 출력 상한 4096 을 똑같이 기본으로 들고 있고** 52행 목록에서 둘 다
> 무너진다(`browseruse` 는 그 값만 올리면 0.95/1.0/1.0). 변환 기반 도구는 상한이 없어 1.0.
>
> ### 남은 것 (전부 `RESULTS.md` '아직 안 잰 것' 에도 있다)
>
> - ~~**fetch 벤치**~~ — **재지 않기로 결정** (2026-09-10). 실사이트를 에이전트에게 주면
>   외부망 규칙(3초 간격)을 못 지키고 재현도 안 된다. 표의 어떤 칸도 '실사이트 도달'을 뜻하지 않는다.
> - **변동성**(`repeat>1`) — 특히 에이전트. 같은 페이지에서 스텝 수가 갈리면 비용이 갈린다.
> - **모델 교체 민감도** — 전부 `gemini-2.5-flash` 한 모델의 값이다.
> - **`skyvern` SPA 비용 재측정** — 실패한 실행이 뒤 케이스 시간창을 오염시켰다.
>   어댑터는 고쳤지만(`_cancel`) 다시 재지는 않았다. 믿을 값은 D2B 건당 94원.
>
> ### 정리 (다음에 켤 때)
>
> ```
> cd ~/skyvern-selfhost && docker compose down     # skyvern + postgres
> ```
> Docker 를 꺼도 결과·원장은 파일에 남는다. 프록시만 있으면 재채점은 언제든 된다:
> `python -m harness.report --run <run_id>`

---

## 1. 과제

조달 사이트 **3곳의 공지사항**을 수집하는 하나의 과제로 도구 7종을 비교한다.
태스크는 둘 — `list`(목록 1페이지에서 N건) / `detail`(상세 1건에서 필드 10개). 둘을 한 점수로 합치지 않는다.

| 대상 | targets | 상태 | 성격 |
|---|---|---|---|
| **D2B** 국방전자조달 | `t1_d2b` | fixtures 14 / **gold 14 확정** | 정적 GET. 게시판 4종 (일반화 축) |
| **G2B** 나라장터 | `t2_g2b` | fixtures 4 / **gold 4 확정** | WebSquare SPA. 값이 `w2textbox` div. 상세 URL 없음 |
| **한전 SRM** | `t3_kepco_srm` | fixtures 4 / **gold 4 확정** | ExtJS SPA. iframe 진입, 그리드 id 매번 바뀜 |

대상은 이 3곳뿐이다. nego.g2b(EUC-KR 보조 표본)는 **2026-09-08 제외**.
→ 대상이 전부 UTF-8 이므로 `ENCODING` 실패 모드는 **실측되지 않는다.** 리포트에서 그 칸이 0인 것은
"도구가 잘했다"가 아니라 **"안 쟀다"**로 읽어야 한다.

### D2B 케이스 14건

```
list   4건 : NEWS01-p1  NEWS02-p1  NEWS03-p1  NEWS04-p1     (게시판마다 1페이지)
detail 10건: NEWS01-6071/6050/6040   NEWS02-6260/6255/6246
             NEWS03-6259/6258        NEWS04-6231/6230
```

게시판 4종을 다 넣은 이유는 **컬럼 구성이 서로 다르기 때문**이다(NEWS02 부대명 / NEWS03 부서명).
한 게시판에 맞춘 셀렉터가 다른 게시판에서 무너지는지를 여기서 본다.
**NEWS04 함정**: 헤더에 `작성자` 칸이 있는데 본문 행에는 없다(th 6 / td 5) — 위치로만 맞추는 도구는
작성일자를 작성자로 읽는다. 의도적으로 넣었다.

---

## 2. 다음 순서

```
1. D2B 나머지 12건 캡처          [완료 2026-09-08]
2. G2B·한전 셀렉터 확인 + 캡처   [완료 2026-09-08]
3. gold 22건 작성·확정           [완료 2026-09-09]
4. Phase 1 어댑터 4종            [완료 2026-09-09]
5. Phase 2 LLM 3종                [완료 2026-09-09]
6. Phase 3 에이전트 2종            ← 지금 여기 (browseruse 다음)
6. Phase 3 에이전트 2종
```

fixtures 22건이 전부 동결됐다(D2B 14 / G2B 4 / 한전 4). gold 22건도 확정됐다.

### 2번에서 실제로 확인한 것 (전부 실측, 추측 없음)

`capture_nav()` 는 처음 설계대로는 두 사이트 모두 실패한다. 다섯 가지가 달랐다.

| 문제 | 실제 | 대응 |
|---|---|---|
| 레이어 팝업 | G2B 는 진입마다 공지 레이어 4겹이 클릭을 가로챈다 | `{close_popups: true}` step |
| 별도 창 | 한전은 담합신고·피싱주의 등 창 3~4개가 같이 열린다 | 같은 step 이 대상 창 빼고 닫는다 |
| iframe | 한전 '더보기'는 `main/splogin.jsp` iframe 안에 있다 | step 의 `frame:` 힌트 |
| 동적 id | 한전 그리드 id(`gridview-1214`)는 세션마다 바뀐다 | `row_selector_js` 로 런타임 계산 |
| 케이스별 행 | 상세 3건이 서로 다른 행이어야 한다 | case 의 `row:` (1부터) |

그래서 `nav` 를 **절차(steps) DSL** 로 바꿨다. `{wait}` / `{close_popups}` / `{click, frame, expect_popup}`.

**G2B**
- 목록 `https://www.g2b.go.kr/ehelpdesk/R23AB00000134L_01/` 은 **세션 부트스트랩 없이 직접 열린다.**
  메인에서 `a[id$=btnBbsAdd]`(더보기)를 눌러 새 창으로도 갈 수 있지만 URL 직행이 더 안정적이다.
- **상세는 URL 이 없다.** `R23AB00000134D_01` 화면 id 는 있으나 직접 열면 타임아웃. 행을 눌러야 한다.
- 값은 숨긴 `<input>` 옆의 `<div class="w2textbox">` 에 들어 있다. **텍스트로 추출 가능** (조회수 4,215 확인).
- 목록 컬럼 11개에 **첨부가 없다** → `has_attachment` 는 gold `null`.
- 함정: 목록의 게시판분류 셀에 필터 드롭다운 **전체 옵션이 통째로** 들어 있다.
  그대로 긁으면 `전체사용자등록/보안/인증전자조달(입찰...` 이 값으로 잡힌다. 정답은 `기타`.

**한전 SRM**
- **비로그인으로 100/3919건 조회된다.**
- 메인 위젯(`#noticeDisplayView`)은 제목이 `...` 로 잘린 티저다. 목록 태스크에 못 쓴다.
- `KepcoListPopup.jsp` 는 이름과 달리 목록이 아니라 팝업공지 창이다(직접 열면 빈 페이지).
- 목록 컬럼 10개에 **조회수가 없다** → `views` 는 gold `null`. 첨부는 숫자(0/1/12)다.
- 그리드가 locked/normal 두 패널로 쪼개져 **한 행이 DOM 에 두 번** 나온다(`data-recordindex` 중복).
  버퍼 렌더링이라 화면은 100건인데 DOM 에는 **52건**만 있다.
- 상세 화면에도 **목록 DOM 이 그대로 남는다.** 도구가 상세 대신 목록을 읽는지 여기서 갈린다.
- 상세에 `dept` / `posted_at` / `views` 가 없다 → gold `null` 3개.

> 검증: 한전 상세 3건은 `meta.api_capture` 의 `boardNo` 가 case_id(4267/4265/4232)와 일치.
> G2B 상세 3건은 제목이 서로 다름을 확인. 22건 sha256 전부 다르다.

### 3. gold — **확정 완료 (2026-09-09)**

22건 전부 `_provenance.json` 에 `human`. `gold_init --check` 6개 태스크 모두 '완료'.
근거: 동결본과 화면 캡처로 값 대조(불일치 0), `null` 14칸은 화면에 항목 자체가 없음을 확인.

**필드 매핑 결정 4건**은 `schema/notice_detail.json` 의 `[결정 2026-09-09]` 주석에 박아뒀다.
요지는 하나다 — **상세 태스크는 상세 화면에 있는 값만 적는다.** 목록에만 있는 작성자·작성일자를
상세로 끌어오지 않는다(G2B author, D2B·한전 posted_at 이 `null` 인 이유). 상세 페이지 하나만
주고 목록에만 있는 값을 요구하면 추출이 아니라 추측을 채점하게 된다.

### 4. 어댑터 — Phase 1 네 종 완료

```
scrapy_baseline   [완료]  22건 전부 정확도 1.0
autoscraper       [완료]  19건 채점 (3건은 예시로 소진)
crawl4ai_css      [완료]  22건 전부 정확도 1.0. 대조군의 20~140배 시간
crawl4ai_genschema[완료]  22건. 목록 SPA 2곳 1.0 / 한전 상세 0.238. 510원
crawl4ai_llm      [완료]  22건. 목록 3곳 1.0 / D2B 상세 1.0 / SPA 상세 0.3. 343원
scrapegraphai     [완료]  22건. 목록·D2B 상세는 crawl4ai_llm 과 동일. **환각 0**. 323원
firecrawl         [완료]  22건. 점수는 scrapegraphai 와 여섯 줄 전부 동일. 228원 (셀프호스팅)
```

#### `firecrawl` 를 다시 돌리려면

도커 스택은 **저장소 밖**에 있다(`~/firecrawl-selfhost`, v2.11.162).
`.env` 와 `docker-compose.override.yaml` 두 개만 우리가 넣었고 벤더 파일은 안 건드렸다.

```bash
cd ~/firecrawl-selfhost && docker compose up -d                 # Docker Desktop 먼저 켤 것
cd <repo> && python tools/serve_proxy.py --port 4000              # 다른 창
python -m harness.runner --tools firecrawl --model gemini-2.5-flash \
       --replay-host 0.0.0.0 --url-host host.docker.internal
```

`--replay-host 0.0.0.0 --url-host host.docker.internal` 이 **둘 다** 있어야 한다.
컨테이너 안의 Firecrawl 이 호스트의 고정본을 가져가야 하기 때문이다.
그리고 `ALLOW_LOCAL_WEBHOOKS=true` 가 **api 컨테이너에** 걸려 있어야 한다(override 파일).

`scrapegraphai` 는 **최신판으로는 import 조차 안 된다**(의존성 선언이 모순).
`envs/scrapegraphai.txt` 에 1.64.0 + langchain 0.3 계열로 전부 못 박아 뒀다.
env 이름은 `registry.py` 의 `ToolSpec.env` 와 같아야 한다 — `envs/README.md` 를 고쳤다.

점수·소견·함정은 전부 `RESULTS.md` 에 있다. 여기서는 실행 방법과 결정만 적는다.

#### `crawl4ai_genschema` 에서 미리 정한 것

| 항목 | 결정 | 왜 |
|---|---|---|
| 힌트 | 셀렉터 **안 준다**. `schema/*.json` + `schema_utils.task_prompt` 만 | `crawl4ai_css`(H3)보다 한 등급 아래. LLM 도구 3종과 같은 문구 |
| 스키마 생성 단위 | (대상 × 태스크)당 1개. D2B 는 `NEWS01` 로 만들어 `NEWS02~04` 에 재사용 | `autoscraper` 와 같은 조건이라 '예시 1건 일반화'를 직접 비교할 수 있다 |
| 채점 제외 | **없다.** 22건 전부 채점 | gold 를 준 적이 없다. 사람이 페이지 보고 셀렉터 쓰는 `crawl4ai_css` 와 같은 조건 |
| 결과 정리 | 사이트별 분기 금지 (`_coerce` 는 스키마 타입만 본다) | `crawl4ai_css` 에 손으로 넣은 사이트 지식을 뒷문으로 넣으면 비교가 무의미 |
| 캐시 | `cache/genschema/<대상>__<태스크>__<모델>.json` | 지우면 다시 과금된다. 모델을 바꾸면 파일이 따로 생긴다 |

`adapters/scrapy_baseline_runner.py` — parsel(Scrapy 셀렉터)로 사이트 3곳 스파이더를 짰다.
env `scrapy_baseline` (python 3.11 / scrapy 2.18 / parsel 1.11).

```
python -m harness.runner --tool scrapy_baseline      # 22건, 총 5초
python -m harness.report --run <run_id>
```

**이건 도구가 아니라 기준선이다.** 정확도 1.0 은 당연하다(구조를 다 알고 짰다).
여기서 볼 것은 나머지 셋 — 비용 0, 상세 1건당 4~46ms, 그리고 **사이트가 바뀌면 사람이
다시 짜야 한다**는 취약성. 마지막 항목은 숫자로 안 나오니 리포트에서 각주로 읽어야 한다.

> **교차검증 성과**: 이 어댑터는 gold 를 만든 `harness/gold_ref.py` 를 import 하지 않고
> 완전히 다른 방식(정규식 vs CSS/XPath 셀렉터)으로 따로 짰다. 두 구현이 **1,010칸 전부에서
> 일치**했다. 한쪽만으로는 알 수 없던 gold 신뢰도가 이걸로 올라갔다.

#### 어댑터 붙이며 고친 것

- `runner.save()` 가 결과를 `<case_id>__rN.json` 으로 써서 **G2B 와 한전의 `ntc-p1` 이
  서로를 덮었다**(22건 실행 → 파일 21개). `<target_id>__<case_id>__rN.json` 으로 바꿨다.
- `score.looks_mojibake` 오탐 2종. `[ㄱ-ㆎ]{3,}` 이 업체명을 가린 `토탈ㅇㅇㅇ가`(G2B ntc-648)를,
  `U+FEFF` 가 제목에 BOM 이 박힌 공지를 인코딩 깨짐으로 봤다. 둘 다 멀쩡한 원문이다.
  대신 `U+FFFD` 와 '라틴 확장 문자·기호 3연속'(`ìˆ˜ìš”` / `Á¶´ÞÃ»`)만 본다.
  `norm_str` 은 폭 없는 문자를 지운 뒤 비교한다.

#### AutoScraper 결과 (run 20260909-115058)

```
              태스크    n   정확도  정밀도  재현율  실패모드
T1 D2B        detail    9   0.153    -      -      -
T1 D2B        list      4   0.833   1.0    0.25   SCHEMA_VIOLATION
T2 G2B        detail    2   0.786    -      -      SCHEMA_VIOLATION
T2 G2B        list      1   1.0     1.0    0.10   SCHEMA_VIOLATION
T3 한전        detail    2   0.714    -      -      -
T3 한전        list      1   0.8     1.0    1.0    -
```

**정밀도는 다 1.0 인데 재현율이 무너진다.** 뽑은 건 맞지만 못 뽑는 게 많다는 뜻이고,
이 도구의 성격을 그대로 보여준다. 환각 0.

- **T1 list 재현율 0.25 = 게시판 4종을 넣은 이유의 답.** NEWS01 로 배운 규칙이 그 게시판에서는
  다 맞지만 NEWS02~04 에서는 한 행도 못 뽑는다. detail 도 같다(0.153 = 학습 게시판만 성공).
- **T3 한전 list 재현율 1.0 (52행 전부).** ExtJS 그리드는 행 구조가 완전히 균일해서
  한 줄만 보여줘도 나머지를 다 따라온다. 사이트 성격이 점수를 가른다.
- **예시로 못 주는 값이 있다.** 게시기간은 '2025-12-10 ~ 2026-12-31' 이 한 텍스트 노드라
  시작일만 떼어 가리킬 수 없고, 첨부는 '파일명.jpg (125.8K)' 처럼 크기가 붙어 있다.
  본문은 너무 길다. 이 셋은 규칙 자체가 안 만들어진다 -> 항상 null.
- `SCHEMA_VIOLATION` 은 조회수를 `'7,750'` 문자열로 돌려주기 때문이다. 텍스트만 뽑는 도구라
  타입을 못 맞춘다. 채점은 숫자로 파싱해서 비교하므로 정확도에는 영향이 없다.

#### 이 도구에만 적용한 규칙 (다른 도구와 나란히 볼 때 반드시 같이 읽을 것)

AutoScraper 는 구조상 예시 없이 아무것도 못 한다. 그래서 **항상 H3** 이고 다음을 준다.

  - detail: 대상마다 케이스 하나를 통째로 예시로 준다. **그 케이스는 채점에서 뺀다**
    (`meta.excluded` -> 리포트의 '채점 제외' 절). 그래서 이 도구만 n 이 작다.
  - list: 첫 케이스의 **1행만** 준다. 나머지 행·나머지 게시판은 채점한다.

`_pick_rules` — 별칭 하나에 규칙이 여러 개 생긴다('관리자'가 페이지 곳곳에 있으면 7개).
전부 합치면 열 길이가 어긋나 행이 밀리므로, **학습 페이지에서 예시를 그대로 재현하는 규칙만**
남긴다. 채점 대상 페이지는 보지 않는다.

행 수는 **키 열(제목)이 뽑힌 만큼**으로 센다. 가장 긴 열에 맞추면 G2B 에서 빈 행 8개가 생겨
환각으로 집계되는데, 그건 도구 출력이 아니라 내가 지어낸 행이다.

#### 붙이며 고친 것

- **`autoscraper` 가 `beautifulsoup4` 4.13+ 에서 아예 동작하지 않는다.** `_get_valid_attrs` 가
  style 없는 요소에 `{'style': ''}` 를 붙이는데 새 bs4 는 그걸 매칭하지 않아, 조상 체인이
  안 쌓이고(규칙 길이 1) `get_result_similar` 가 늘 빈 목록을 준다. 3줄짜리 목록에서도 실패.
  `envs/autoscraper.txt` 에 `beautifulsoup4==4.12.3` 으로 못 박고 사유를 적었다.
  **이걸 못 잡았으면 도구가 아니라 내 의존성 해석을 재는 벤치마크가 됐다.**
- AutoScraper 는 결과 문자열을 **NFKD 로 분해**해서 돌려준다('사기' -> 'ᄉ...').
  채점은 NFKC 로 정규화하므로 점수에는 영향이 없지만, 어댑터가 규칙을 고를 때는 되돌려야 한다.
- `report.py` 에 `meta.excluded` 처리를 넣었다. 예시로 준 케이스를 채점하면 정답을 알려주고
  맞혔는지 세는 셈이다. 리포트에 제외 사유가 같이 남는다.

### 5. 그 다음 (아직 멀다)

도구별 서브 env 구축 → Phase 1 비LLM 4종(`scrapy_baseline` → `autoscraper` → `crawl4ai_css` →
`crawl4ai_genschema`) → Phase 2 LLM 3종 → Phase 3 에이전트 2종.
**어댑터 4종 구현 / 5종 미구현**. 미구현 도구는 registry 등록만 되어 있고
`AdapterUnavailable` 로 SKIP 된다. 계약은 `adapters/base.py`, 참고 구현은 `adapters/echo_runner.py`.
LLM 도구는 `crawl4ai_genschema_runner.py` 를 본뜨면 된다 — 프록시 접속(`harness/dotenv.proxy`),
태그 실기(`extra_body`), 공통 프롬프트(`schema_utils.task_prompt` / `json_example`)가 다 거기 있다.

---

## 3. 환경 (2026-09-08 구축 완료)

```
conda env: aicrawl   python 3.11.16
  playwright 1.62.0 (+ chromium headless shell 151.0.7922.34)
  litellm 1.100.0 / requests 2.34.2 / pyyaml 6.0.3

실행: <anaconda>\envs\aicrawl\python.exe -m harness.<모듈>
      (conda activate aicrawl 후에는 그냥  python -m harness.<모듈>)
```

도구별 서브 env 는 아직 없다(`envs/*.txt` 에 requirements 만 준비).

---

## 4. 검증 상태

### 실제로 돌려본 것

- 전체 파이프라인이 **실제 D2B 페이지**에서 한 바퀴 돈다: `capture → replay → extract → score → report`
  ```
  echo  T1  list    정확도=1.0  정밀도=1.0  재현율=1.0   (7행 전부 매칭)
  echo  T1  detail  정확도=0.375                        (title/dept/category만)
  ```
  `echo` 는 배선 점검용 어댑터이며 도구가 아니다(registry phase=0, 리포트 대상 아님).
- 리플레이 서버가 `Content-Type: text/html; charset=euc-kr` 를 원본 그대로 재현 (EUC-KR 왕복 확인)
- 채점 규칙 단위 확인: 본문 유사도, 첨부 집합, 날짜 정밀도, 환각/누락, `__TODO__` 제외
- playwright 브라우저 실제 기동 (렌더 + 스크린샷)

### 코드만 있고 실행 못 해본 것

- ~~`capture_nav()`~~ → 2026-09-08 실행 완료. G2B 4건 + 한전 4건 동결.
- ~~`harness/litellm_logger.py` + `litellm_config.yaml`~~ → 2026-09-09 확인.
  mock 모델만 둔 임시 config 로 프록시를 띄우고 **어댑터 → 프록시 → JSONL → meter** 를
  한 바퀴 돌렸다. 4회 호출(1회 + 검증 재시도 3회)이 그대로 로그에 남고,
  `read_litellm_window` 의 시간창·태그 두 경로가 같은 값을 냈다.
  여기서 잡은 것 둘: **config 를 cp949 로 읽어 한글 주석에서 죽는 것**(→ `PYTHONUTF8=1`),
  **태그를 `metadata=` 로 보내면 프록시에 안 닿는 것**(→ `extra_body`).
- ~~`harness/runner.py` 를 22건 전체로~~ → 어댑터 3종으로 각각 22건 완주.
- ~~어댑터 6종 미구현~~ → 남은 것은 `firecrawl` / `browseruse` / `skyvern` 3종.
- ~~실제 벤더 API 호출 0회~~ → Gemini 로 3개 런(66건) 완주. 누적 1,195원.
- 지출 상한을 `harness/ledger.py` 로 옮겼다 — 토큰+크레딧+스텝 합산, 프로세스 밖 누적,
  요청 **전에** 차단. 네 경로(단위 상한 / USD 상한 / 단가 없음 / 상한 없음) 전부 실측 확인.
- 리플레이 서버에 `--replay-host` / `--url-host` 를 추가했다. 컨테이너 안에서 도는
  도구(셀프호스팅 Firecrawl)가 호스트의 고정본에 닿으려면 둘을 갈라야 한다.
- 값 정규화를 `harness/normalize.py` 로 뽑았다(2026-09-09, 동작 변경 없음).
  LLM 도구가 env 를 넘나들며 **같은 후처리**를 쓰게 하려는 것 — 도구마다 다르면
  도구 차이가 아니라 내 코드 차이를 재게 된다.

---

## 5. 열린 결정

| # | 항목 | 상태 |
|---|---|---|
| 1 | **Ollama 모델** | GPU 사양 미확인. `pricing.yaml` / `litellm_config.yaml` placeholder 교체 필요 |
| 2 | **Firecrawl / Skyvern 단가** | 플랜 미확정. `pricing.yaml:services` 비어 있음 |
| 3 | ~~gold 22건 사람 확인~~ | 확정 (2026-09-09). `_provenance.json` 전부 `human` |
| 6 | **`ANTHROPIC_API_KEY`** | **없음. LLM 도구 4종이 여기서 막혀 있다.** `.env.example` → `.env` |
| 7 | **genschema 모델** | 기본 `claude-haiku-4-5`. 견적은 `RESULTS.md`. `--model` 로 바꾼다 |
| 4 | ~~G2B·한전 case_id~~ | 확정: G2B `ntc-<게시물번호>`, 한전 `ntc-<공지번호>` |
| 5 | ~~한전 목록 행 수~~ | 확정: **52행 전부 유지** (2026-09-09 사용자 결정) |

---

## 6. 반드시 지킬 것

- **gold 를 먼저 확정하고 도구를 돌린다.** 도구 출력을 보고 정답을 적으면 그 도구 쪽으로 정답이 끌려간다.
- **`null` 은 "페이지에 값이 없다"는 단언**, `__TODO__` 는 "아직 안 봤다". 섞으면 멀쩡한 도구가
  환각 판정을 받는다. (첨부 없음은 `null` 이 아니라 `[]`)
- **힌트는 등급(H0~H3)으로 고정하고 전 도구에 동일하게 준다** (README §11). 즉흥적으로 주면
  "누가 더 좋은가" 대신 "누구에게 더 친절했는가"를 재게 된다.
  현재: D2B `H1` / G2B·한전 `H2`. AutoScraper 는 구조상 항상 `H3`(예시 1건 필수).
- **fetch 와 extract 를 합치지 않는다.** 비LLM 도구가 G2B 에서 "도달 불가"인 것과 "스냅샷 주면 90점"인
  것은 완전히 다른 정보다. 같은 칸에 적으면 안 된다.
- **외부망은 `harness/capture.py` 에서만 친다.** 기본 3초 간격. 줄이지 말 것.
- **한전 `/router` 에 RPC 를 직접 쏘지 않는다.** 브라우저로 정상 화면을 띄우고 오가는 응답만 보관한다.
- `page.html` 은 **응답 바이트 그대로** 저장한다. 디코딩해서 저장하면 인코딩 비교가 무의미해진다.
- `fixtures/ gold/ results/ .env` 는 git 제외.
