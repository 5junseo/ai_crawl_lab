"""Crawl4AI — 사람이 쓴 CSS 스키마로 추출 (README §7 Phase 1).

LLM 을 쓰지 않는 모드다. 셀렉터는 사람이 준다(=H3). 그래서 대조군(`scrapy_baseline`)과
**주는 정보량이 같다.** 비교의 초점이 정확도가 아니라 여기에 있다.

  - 같은 지식을 주면 손으로 짠 코드만큼 뽑아내는가
  - 브라우저를 띄우는 값(콜드 스타트·건당 시간)이 얼마인가
  - **CSS 만으로 표현되지 않는 화면이 있는가**

마지막 항목이 이 도구의 진짜 시험이다. D2B 는 표라 쉽고, G2B 는 값이 id 붙은 input 에
들어 있어 지목이 되지만, 한전은 ExtJS 가 id 를 세션마다 새로 발급해서 '제목 라벨 옆 칸'을
CSS 로 가리킬 수가 없다. 남은 수단은 클래스와 순서뿐이다
(`input.x-form-required-field` 의 n번째). 그게 얼마나 버티는지가 결과로 남는다.

스키마는 이 파일에 박아 둔다. 손으로 쓴 셀렉터가 곧 이 도구의 입력이기 때문이다.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig,
                      JsonCssExtractionStrategy)

from .base import Adapter, RawPage, RunResult
from harness.meter import Stopwatch

DETAIL_FIELDS = ("title", "author", "dept", "category", "posted_at",
                 "period_start", "period_end", "views", "attachments", "body")


def _txt(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()
    return s or None


def _int(v: Any) -> Optional[Any]:
    s = _txt(v)
    if not s:
        return None
    d = re.sub(r"[^\d-]", "", s)
    return int(d) if d else s


def _dt(v: Any) -> Optional[str]:
    s = _txt(v)
    if not s:
        return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
                  r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None
    out = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m.group(4):
        out += f" {int(m.group(4)):02d}:{m.group(5)}"
        if m.group(6):
            out += f":{m.group(6)}"
    return out


def _lines(v: Any) -> Optional[str]:
    """본문 html -> 줄 구조를 살린 텍스트."""
    if not v:
        return None
    s = re.sub(r"</?(?:br|p|div|tr|li)\b[^>]*>", "\n", str(v), flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    import html as H
    s = H.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if x) or None


def _F(name, sel, typ="text", **kw):
    """셀렉터가 빈 문자열이면 키를 아예 뺀다.
    crawl4ai 는 'selector' 키가 있으면 element.select(...) 를 돌리고 결과가 없으면 포기한다.
    키가 없을 때만 '그 요소 자신'을 쓴다. list 안쪽 필드가 여기 걸린다."""
    d = {"name": name, "type": typ}
    if sel:
        d["selector"] = sel
    d.update(kw)
    return d


# --------------------------------------------------------------- D2B (정적 표)
# 게시판마다 컬럼 수가 다르다. NEWS04 는 헤더에 작성자가 있는데 행에는 없다(td 5칸).
D2B_LIST_COLS = {
    "NEWS01": {"seq": 1, "title": 2, "attach": 3, "author": 4, "posted_at": 5, "views": 6},
    "NEWS02": {"seq": 1, "title": 2, "attach": 3, "author": 5, "posted_at": 6, "views": 7},
    "NEWS03": {"seq": 1, "title": 2, "attach": 3, "author": 5, "posted_at": 6, "views": 7},
    "NEWS04": {"seq": 1, "title": 2, "attach": 3, "posted_at": 4, "views": 5},
}


def d2b_list_schema(board: str) -> dict:
    c = D2B_LIST_COLS[board]
    fields = [_F("seq", f"td:nth-child({c['seq']})"),
              _F("title", f"td:nth-child({c['title']})"),
              _F("attach", f"td:nth-child({c['attach']})", "html"),
              _F("posted_at", f"td:nth-child({c['posted_at']})"),
              _F("views", f"td:nth-child({c['views']})")]
    if "author" in c:
        fields.append(_F("author", f"td:nth-child({c['author']})"))
    return {"name": "d2b_list", "baseSelector": "div.tbl_list table tbody tr",
            "fields": fields}     # 목록은 tbl_list, 상세는 tbl_data 다


D2B_DETAIL_SCHEMA = {
    "name": "d2b_detail",
    "baseSelector": "div.tbl_data",
    "fields": [
        _F("title", "tr:nth-child(1) th"),
        _F("author", "tr:nth-child(2) td:nth-child(2)"),
        _F("dept", "tr:nth-child(2) td:nth-child(4)"),
        _F("category", "tr:nth-child(3) td:nth-child(2)"),
        _F("period", "tr:nth-child(3) td:nth-child(4)"),
        _F("body_ta", "textarea", "text"),
        _F("body_td", "td.td_noti", "html"),
        {"name": "files", "selector": "td a.tover", "type": "list",
         "fields": [_F("name", "", "text")]},
    ],
}

# --------------------------------------------------------------- G2B (WebSquare)
G2B_LIST_SCHEMA = {
    "name": "g2b_list",
    "baseSelector": "#mf_wfm_container_grdPst_body_table tbody tr",
    "fields": [_F("seq", "td:nth-child(1)"), _F("title", "td:nth-child(4)"),
               _F("author", "td:nth-child(10)"), _F("posted_at", "td:nth-child(11)"),
               _F("views", "td:nth-child(13)")],
}

G2B_DETAIL_SCHEMA = {
    "name": "g2b_detail",
    "baseSelector": "body",
    "fields": [
        _F("title", "#mf_wfm_container_ibxPstNm", "attribute", attribute="value"),
        _F("category", "#mf_wfm_container_ibxBbsClsfPritm", "attribute", attribute="value"),
        _F("views", "#mf_wfm_container_ibxPstInqCnt", "attribute", attribute="value"),
        _F("dept", "#mf_wfm_container_ibxOdn1Col", "attribute", attribute="value"),
        _F("posted_at", "#mf_wfm_container_ibxInptDt", "attribute", attribute="value"),
        _F("period", "#mf_wfm_container_ibxNtcPrd", "attribute", attribute="value"),
        _F("body", "#mf_wfm_container_tbxPstCn", "html"),
        {"name": "files", "selector": 'table[id$="_grdFile_body_table"] tbody tr',
         "type": "list", "fields": [_F("cell", "td:nth-child(5)")]},
    ],
}

# --------------------------------------------------------------- 한전 (ExtJS)
# id 가 세션마다 바뀐다. 클래스 + 순서 말고는 지목할 방법이 없다.
KEPCO_LIST_SCHEMA = {
    "name": "kepco_list",
    "baseSelector": "table.x-grid-item",
    "fields": [_F("c1", "td:nth-child(1) div"), _F("c2", "td:nth-child(2) div"),
               _F("c3", "td:nth-child(3) div"), _F("c9", "td:nth-child(9) div"),
               _F("c10", "td:nth-child(10) div")],
}

KEPCO_DETAIL_SCHEMA = {
    "name": "kepco_detail",
    "baseSelector": "body",
    "fields": [
        {"name": "req", "selector": "input.x-form-required-field", "type": "list",
         "fields": [_F("v", "", "attribute", attribute="value")]},
        {"name": "txt", "selector": "input.x-form-text-default", "type": "list",
         "fields": [_F("v", "", "attribute", attribute="value")]},
        _F("body", "textarea.x-form-required-field", "text"),
        {"name": "files", "selector": "div[class*=uploader-file]", "type": "list",
         "fields": [_F("name", "", "text")]},
    ],
}


class Crawl4AICssAdapter(Adapter):
    name = "crawl4ai_css"
    supports_fetch = True
    needs_llm = False

    def setup(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
        self._loop.run_until_complete(self._crawler.start())

    def teardown(self) -> None:
        try:
            self._loop.run_until_complete(self._crawler.close())
            self._loop.close()
        except Exception:
            pass

    # ---- 스키마 고르기 -------------------------------------------------
    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    def _schema_for(self, target: str, case_id: str, task: str) -> dict:
        if target == "t1_d2b":
            if task == "list":
                return d2b_list_schema(case_id.split("-")[0])
            return D2B_DETAIL_SCHEMA
        if target == "t2_g2b":
            return G2B_LIST_SCHEMA if task == "list" else G2B_DETAIL_SCHEMA
        if target == "t3_kepco_srm":
            return KEPCO_LIST_SCHEMA if task == "list" else KEPCO_DETAIL_SCHEMA
        raise KeyError(f"CSS 스키마를 짜 둔 적 없는 대상: {target}")

    # ---- 결과 정리 -----------------------------------------------------
    def _shape(self, target: str, task: str, recs: list) -> dict:
        if task == "list":
            return {"items": self._shape_list(target, recs)}
        return self._shape_detail(target, recs[0] if recs else {})

    @staticmethod
    def _shape_list(target: str, recs: list) -> list:
        out = []
        for r in recs:
            if target == "t3_kepco_srm":
                seq = _txt(r.get("c1"))
                if not (seq or "").isdigit():
                    continue          # 좌측 메뉴 트리도 x-grid-item 이다. 숫자 번호만 행이다
                out.append({"seq": seq, "title": _txt(r.get("c2")),
                            "author": _txt(r.get("c9")), "posted_at": _dt(r.get("c10")),
                            "views": None,
                            "has_attachment": (_int(r.get("c3")) or 0) > 0})
                continue
            seq = _txt(r.get("seq"))
            if not (seq or "").isdigit():
                continue              # 헤더행·안내행 제거
            rec = {"seq": seq, "title": _txt(r.get("title")),
                   "author": _txt(r.get("author")), "posted_at": _dt(r.get("posted_at")),
                   "views": _int(r.get("views")), "has_attachment": None}
            if target == "t1_d2b":
                rec["has_attachment"] = "ico_file" in (r.get("attach") or "")
            out.append(rec)
        return out

    @staticmethod
    def _shape_detail(target: str, r: dict) -> dict:
        d = {f: None for f in DETAIL_FIELDS}
        d["attachments"] = []
        if target == "t1_d2b":
            d["title"] = _txt(r.get("title"))
            d["author"] = _txt(r.get("author"))
            d["dept"] = _txt(r.get("dept"))
            d["category"] = _txt(r.get("category"))
            p = (_txt(r.get("period")) or "").split("~")
            d["period_start"] = _dt(p[0] if p else None)
            d["period_end"] = _dt(p[1] if len(p) > 1 else None)
            d["body"] = _txt(r.get("body_ta")) or _lines(r.get("body_td"))
            d["attachments"] = [re.sub(r"\s*\([^)]*\)\s*$", "", x).strip()
                                for x in (_txt(f.get("name")) for f in (r.get("files") or []))
                                if x]
        elif target == "t2_g2b":
            d["title"] = _txt(r.get("title"))
            d["category"] = _txt(r.get("category"))
            d["views"] = _int(r.get("views"))
            d["dept"] = _txt(r.get("dept"))
            d["posted_at"] = _dt(r.get("posted_at"))
            prd = _txt(r.get("period")) or ""
            if "~" in prd:                  # '사용안함' 이면 공지기간이 없는 것이다
                a, b = prd.split("~", 1)
                d["period_start"], d["period_end"] = _dt(a), _dt(b)
            body = _lines(r.get("body"))
            d["body"] = body if body and len(re.sub(r"\W", "", body)) >= 5 else None
            d["attachments"] = [x for x in (_txt(f.get("cell")) for f in (r.get("files") or []))
                                if x and re.search(r"\.[A-Za-z0-9]{2,5}$", x)]
        else:                                # 한전 — 클래스와 순서로만 지목한다
            req = [_txt(x.get("v")) for x in (r.get("req") or [])]
            txt = [_txt(x.get("v")) for x in (r.get("txt") or [])]
            d["category"] = req[0] if len(req) > 0 else None
            d["period_start"] = _dt(req[1]) if len(req) > 1 else None
            d["period_end"] = _dt(req[2]) if len(req) > 2 else None
            d["title"] = req[3] if len(req) > 3 else None
            d["author"] = txt[-1] if txt else None
            d["body"] = _txt(r.get("body"))
            d["attachments"] = [x for x in (_txt(f.get("name")) for f in (r.get("files") or []))
                                if x]
        return d

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)

        with Stopwatch() as sw:
            try:
                css = self._schema_for(target, case_id, task)
                cfg = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(css),
                                       cache_mode=CacheMode.BYPASS, verbose=False)
                with sw.stage("fetch"):
                    res = self._loop.run_until_complete(self._crawler.arun(url=url, config=cfg))
                if not res.success:
                    raise RuntimeError(res.error_message or "crawl 실패")
                with sw.stage("parse"):
                    recs = json.loads(res.extracted_content or "[]")
                    data = self._shape(target, task, recs)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = len((res.html or "").encode("utf-8"))
        return RunResult(case_id=case_id, tool=self.name, ok=True, data=data, metrics=m,
                         meta={"task": task, "target_id": target,
                               "css_schema": css.get("name"), "ts": time.time()})
