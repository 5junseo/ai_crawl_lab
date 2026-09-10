# targets/

대상 1곳 = 파일 1개. 여기에 적는 건 **URL 패턴이 아니라 "실제로 찔러서 확인한 사실"** 이다.
추측을 적으면 나중에 캡처가 조용히 어긋난다.

## 파일

| 파일 | 대상 | 렌더 | 힌트 등급 |
|---|---|---|---|
| `t1_d2b.yaml` | 국방전자조달 공지사항 (게시판 4종) | static | H1 |
| `t2_g2b.yaml` | 나라장터 운영자 공지사항 | js (WebSquare) | H2 |
| `t3_kepco_srm.yaml` | 한국전력 SRM 공지사항 | post_session (ExtJS) | H2 |

## 필드

- `cases.list` / `cases.detail` — `{id, url, row}` 목록.
  **`url` 이 있으면** 그 주소를 직접 친다(D2B).
  **`url` 이 없고 `nav.entry` 가 있으면** `capture_nav()` 가 절차를 밟는다(G2B·한전).
  `row` 는 상세 케이스가 목록의 **몇 번째 행인가**(1부터). 상세마다 다른 행을 열기 위한 것.
- `nav.steps` — 목록까지 도달하는 절차. 순서대로 실행한다.
  ```yaml
  - {wait: 5000}                                  # 대기(ms)
  - {close_popups: true}                          # 대상 창 외 창 + 레이어 팝업 닫기
  - {click: "a#noticeListMore", frame: "splogin"}  # frame 은 URL 부분 문자열
  - {click: "...", expect_popup: true}            # 새 창이 열리면 그 창을 대상으로 삼는다
  ```
  `close_popups` 는 장식이 아니다. G2B 는 진입마다 공지 레이어가 4겹 뜨고 한전은 별도 창이
  3~4개 열린다. 안 닫으면 **다음 클릭이 팝업에 먹힌다.**
- `nav.row_selector` / `nav.row_selector_js` — 상세 진입용 행 셀렉터.
  한전은 그리드 id 가 세션마다 바뀌므로 `_js`(셀렉터 문자열을 반환하는 JS)로 런타임에 만든다.
  `row_cell` 은 행 안에서 누를 셀 번호, `cell_selector` 는 셀을 고르는 셀렉터.
- `nav.list_api` — 이 주소의 XHR 응답을 `meta.api_capture` 에 보관한다.
  **참조용이며 채점 입력이 아니다.** '도구가 무엇을 놓쳤나' 를 볼 때만 쓴다.
- `hint_level` — 그 대상에서 도구에 허용하는 정보량(README §11). 어댑터는 이 이상을 쓰면 안 된다.
- `fetch.needs_browser` — true 면 playwright 로 렌더 후 DOM 을 동결한다.
  이 경우 원본 바이트가 아니므로 `ENCODING` 실패 모드 판정에서 제외된다.

## 케이스 채우는 순서

```bash
python -m harness.capture --target <id> --dry-run   # 라우팅만 확인 (네트워크 안 침)
python -m harness.capture --target <id>             # 실제 동결. 기본 3초 간격
python -m harness.gold_init --target <id>           # 정답 템플릿 생성
```

SPA 대상은 캡처를 해봐야 case_id 를 알 수 있다. 화면의 공지번호를 읽어 우리가 붙인다
(G2B `ntc-<게시물번호>`, 한전 `ntc-<공지번호>`). 그래서 `nav` 만 있는 상태로 시작해서
목록을 한 번 동결한 뒤 상세 케이스를 적는다.

셀렉터는 **추측하지 말고 렌더된 화면을 보고 정한다.** 세 사이트 다 처음 짠 셀렉터가 틀렸다.

## 뺀 것

- **nego.g2b**(조달청 평가위원시스템, EUC-KR) — 2026-09-08 제외.
  대상 3곳이 전부 UTF-8 이 되어 `ENCODING` 실패 모드는 실측되지 않는다.
  EUC-KR 사이트를 다시 붙이면 그 항목이 바로 되살아난다.
