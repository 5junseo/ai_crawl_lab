"""대조군 — 사람이 직접 짠 셀렉터 (README §7 Phase 1).

이건 '도구'가 아니라 **기준선**이다. 개발자가 사이트를 직접 보고 셀렉터를 박아 넣었을 때
무엇을 얻고 무엇을 치르는지를 재기 위해 있다. 그래서 규칙이 다르다.

  - 힌트 등급을 지키지 않는다. 사이트 구조를 전부 알고 쓴다(H3 상당).
  - 정확도가 높은 게 당연하다. **여기서 볼 것은 정확도가 아니라 나머지 셋이다** —
    비용 0, 속도 최상, 그리고 '사이트가 바뀌면 사람이 다시 짜야 한다'는 취약성.
    이 취약성은 숫자로 안 나오므로 리포트에서 각주로 읽어야 한다.

  - **gold 를 만든 harness/gold_ref.py 를 import 하지 않는다.** 같은 코드로 정답을 만들고
    같은 코드로 답안을 내면 100점은 당연하고 아무 정보도 없다. 여기서는 실제 스파이더가
    쓰는 방식(parsel CSS/XPath 셀렉터)으로 따로 짠다. 두 구현이 어긋나면 둘 중 하나가
    틀린 것이므로, 이 어댑터가 gold 와 불일치하는 지점은 그 자체로 점검 신호다.

Scrapy 엔진(스케줄러·미들웨어)은 돌리지 않는다. 케이스마다 URL 하나를 받는 구조라
엔진을 띄우면 측정값이 크롤 오케스트레이션 비용에 묻힌다. 스파이더의 parse 부분,
즉 parsel 셀렉터만 그대로 쓴다.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import requests
from parsel import Selector

from .base import Adapter, RawPage, RunResult
from harness.meter import Stopwatch

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DETAIL_FIELDS = ("title", "author", "dept", "category", "posted_at",
                 "period_start", "period_end", "views", "attachments", "body")
LIST_FIELDS = ("seq", "title", "author", "posted_at", "views", "has_attachment")


# --------------------------------------------------------------------- 값 정리
def one(sel, css: str) -> Optional[str]:
    v = sel.css(css).get()
    return clean(v) if v else None


def clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    return s or None


def text_of(sel) -> Optional[str]:
    return clean(" ".join(sel.css("::text").getall()))


def to_int(s) -> Optional[int]:
    if s is None:
        return None
    d = re.sub(r"[^\d-]", "", str(s))
    return int(d) if d else None


def to_dt(s) -> Optional[str]:
    """화면 표기를 'YYYY-MM-DD[ HH:MM[:SS]]' 로 맞춘다."""
    if not s:
        return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
                  r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", str(s))
    if not m:
        return None
    out = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m.group(4):
        out += f" {int(m.group(4)):02d}:{m.group(5)}"
        if m.group(6):
            out += f":{m.group(6)}"
    return out


def blocks(sel) -> Optional[str]:
    """본문용. 줄 구조를 살려서 텍스트로 만든다."""
    html = sel.get() or ""
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", html)
    import html as H
    txt = H.unescape(txt).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in txt.split("\n")]
    return "\n".join(l for l in lines if l) or None


# --------------------------------------------------------------------- D2B
class D2BSpider:
    """정적 HTML. 게시판 4종이 컬럼 구성만 다르다."""

    LIST_MAP = {"번호": "seq", "제목": "title", "첨부": "has_attachment",
                "작성자": "author", "작성일자": "posted_at", "등록일자": "posted_at",
                "조회": "views", "조회수": "views"}

    def list(self, sel: Selector) -> dict:
        heads = [h for h in (clean(x) for x in sel.css("th::text").getall()) if h]
        rows = [r for r in sel.css("tbody tr") if "fn_detail" in (r.get() or "")]
        items = []
        for r in rows:
            tds = r.css("td")
            # NEWS04 는 헤더에 '작성자'가 있는데 행에는 없다(th 6 / td 5).
            # 위치로 맞추면 작성일자가 작성자로 들어간다. 양끝을 고정하고 가운데만 맞춘다.
            names = heads[:3] + heads[3:-2][-(len(tds) - 5):] + heads[-2:] \
                if len(tds) > 5 else heads[:3] + heads[-2:]
            rec = {f: None for f in LIST_FIELDS}
            for name, td in zip(names, tds):
                key = self.LIST_MAP.get(name)
                if key == "has_attachment":
                    rec[key] = bool(td.css("span.ico_file"))
                elif key == "views":
                    rec[key] = to_int(text_of(td))
                elif key == "posted_at":
                    rec[key] = to_dt(text_of(td))
                elif key:
                    rec[key] = text_of(td)
            if rec["title"]:
                items.append(rec)
        return {"items": items}

    def detail(self, sel: Selector) -> dict:
        tbl = None
        for t in sel.css("table"):
            if t.xpath('.//th[@scope="row"][normalize-space()="작성자"]'):
                tbl = t
                break
        d = {f: None for f in DETAIL_FIELDS}
        if tbl is None:
            return d
        d["title"] = text_of(tbl.css('th[colspan="4"]'))
        for th in tbl.css('th[scope="row"]'):
            lab = text_of(th)
            td = th.xpath("following-sibling::td[1]")
            if not td:
                continue
            if lab == "작성자":
                d["author"] = text_of(td)
            elif lab in ("부서명", "담당부서", "부대명"):
                d["dept"] = text_of(td)
            elif lab == "구분":
                d["category"] = text_of(td)
            elif lab == "게시기간":
                parts = (text_of(td) or "").split("~")
                d["period_start"] = to_dt(parts[0] if parts else None)
                d["period_end"] = to_dt(parts[1] if len(parts) > 1 else None)
            elif lab == "첨부파일":
                d["attachments"] = [re.sub(r"\s*\([^)]*\)\s*$", "", a).strip()
                                    for a in (clean(x) for x in td.css("a::text").getall())
                                    if a]
        # 본문은 textarea(오래된 글) 또는 td.td_noti(최근 글) 둘 중 하나다
        body = tbl.css("textarea")
        d["body"] = blocks(body) if body else blocks(tbl.css("td.td_noti"))
        if d["attachments"] is None:
            d["attachments"] = []
        return d                      # posted_at / views 는 상세 화면에 없다


# --------------------------------------------------------------------- G2B
class G2BSpider:
    """WebSquare. 값이 숨은 input 과 w2textbox div 에 나뉘어 있다."""

    IBX = {"ibxPstNm": "title", "ibxBbsClsfPritm": "category",
           "ibxPstInqCnt": "views", "ibxOdn1Col": "dept", "ibxInptDt": "posted_at"}

    def list(self, sel: Selector) -> dict:
        # 헤더 글자가 th 바로 밑이 아니라 안쪽 span 에 있다. ::text 로는 하나도 안 잡힌다.
        heads = [text_of(th) or "" for th in
                 sel.css("table.gridHeaderTableDefault th")]
        items = []
        for r in sel.css("#mf_wfm_container_grdPst_body_table tbody tr"):
            tds = r.css("td")
            if len(tds) != len(heads):
                continue
            g = {h: td for h, td in zip(heads, tds)}
            seq = text_of(g.get("게시물번호")) if "게시물번호" in g else None
            if not seq:
                continue
            items.append({
                "seq": seq,
                "title": text_of(g.get("제목")),
                "author": text_of(g.get("작성자")),
                "posted_at": to_dt(text_of(g.get("작성일시"))),
                "views": to_int(text_of(g.get("조회수"))),
                "has_attachment": None,       # 목록에 첨부 컬럼이 없다
            })
        return {"items": items}

    def detail(self, sel: Selector) -> dict:
        d = {f: None for f in DETAIL_FIELDS}
        for key, field in self.IBX.items():
            v = sel.css(f"#mf_wfm_container_{key}::attr(value)").get()
            v = clean(v)
            if not v:
                continue
            d[field] = to_int(v) if field == "views" else (
                to_dt(v) if field == "posted_at" else v)
        prd = clean(sel.css("#mf_wfm_container_ibxNtcPrd::attr(value)").get())
        if prd and "~" in prd:                # '사용안함' 이면 공지기간이 없는 것이다
            a, b = prd.split("~", 1)
            d["period_start"], d["period_end"] = to_dt(a), to_dt(b)
        body = sel.css("#mf_wfm_container_tbxPstCn")
        if body:
            t = blocks(body)
            # 본문이 이미지 한 장뿐인 글이 있다. 그때는 텍스트 값이 없는 것이다.
            d["body"] = t if t and len(re.sub(r"\W", "", t)) >= 5 else None
        files = []
        for r in sel.css('table[id$="_grdFile_body_table"] tbody tr'):
            for td in r.css("td"):
                v = text_of(td)
                if v and re.search(r"\.[A-Za-z0-9]{2,5}$", v):
                    files.append(v)
                    break
        d["attachments"] = files
        return d                      # 작성자 항목은 상세 화면에 없다 -> null


# --------------------------------------------------------------------- 한전 SRM
class KepcoSpider:
    """ExtJS. 그리드 id 가 세션마다 바뀌므로 헤더 이름으로 찾아 들어간다."""

    COLS = ["공지번호", "제목", "첨부", "업무구분", "공고시작일", "공고종료일",
            "공동이용사", "품목구분", "등록자", "등록일자"]

    def list(self, sel: Selector) -> dict:
        best = []
        # locked/normal 두 패널로 쪼개져 한 행이 DOM 에 두 번 나온다.
        # 10칸을 다 가진 쪽만 쓴다.
        for row in sel.css("table.x-grid-item"):
            cells = [text_of(c) for c in row.css("td.x-grid-cell div.x-grid-cell-inner")]
            if len(cells) == len(self.COLS):
                best.append(dict(zip(self.COLS, cells)))
        return {"items": [{
            "seq": d["공지번호"],
            "title": d["제목"],
            "author": d["등록자"],
            "posted_at": to_dt(d["등록일자"]),
            "views": None,                    # 목록에 조회수 컬럼이 없다
            "has_attachment": (to_int(d["첨부"]) or 0) > 0,
        } for d in best]}

    # 라벨의 조상 중 클래스가 'x-form-item' 인 것이 그 필드 한 칸이다.
    # contains(@class,'x-form-item') 로 쓰면 라벨 자신(x-form-item-label)이 먼저 걸린다.
    FIELD = ("ancestor::*[contains(concat(' ',normalize-space(@class),' '),"
             "' x-form-item ')][1]")
    PANEL = ("ancestor::*[contains(concat(' ',normalize-space(@class),' '),"
             "' x-panel ')][1]")

    def detail(self, sel: Selector) -> dict:
        d = {f: None for f in DETAIL_FIELDS}
        # 화면 위쪽 검색 폼에도 '업무구분' '품목구분' 라벨이 있다. 그대로 훑으면
        # 검색창의 빈 값이 상세 값을 덮는다. '내용' 라벨이 있는 패널만 상세 폼이다.
        scope = sel
        for lab in sel.css("label span.x-form-item-label-inner"):
            if text_of(lab) == "내용":
                p = lab.xpath(self.PANEL)
                if p:
                    scope = p[0]
                break
        for lab in scope.css("label span.x-form-item-label-inner"):
            name = text_of(lab)
            fields = lab.xpath(self.FIELD)
            if not fields or not name:
                continue
            box = fields[0]
            vals = [v for v in (clean(x) for x in box.css("input::attr(value)").getall()) if v]
            if name == "제목":
                d["title"] = vals[0] if vals else None
            elif name == "업무구분":
                d["category"] = vals[0] if vals else None
            elif name == "등록자":
                d["author"] = vals[0] if vals else None
            elif name == "공지기간":
                d["period_start"] = to_dt(vals[0]) if vals else None
                d["period_end"] = to_dt(vals[1]) if len(vals) > 1 else None
            elif name == "내용":
                ta = box.css("textarea::text").get()
                d["body"] = blocks(box.css("textarea")) if ta else None
            elif name == "첨부파일":
                d["attachments"] = [v for v in (
                    text_of(x) for x in box.css("div[class*=uploader-file]")) if v]
        if d["attachments"] is None:
            d["attachments"] = []
        return d                      # dept / posted_at / views 는 화면에 없다 -> null


SPIDERS = {"t1_d2b": D2BSpider(), "t2_g2b": G2BSpider(), "t3_kepco_srm": KepcoSpider()}


class ScrapyBaselineAdapter(Adapter):
    name = "scrapy_baseline"
    supports_fetch = True
    needs_llm = False

    def setup(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def teardown(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def fetch(self, url: str) -> Optional[RawPage]:
        r = self.session.get(url, timeout=30)
        ctype = r.headers.get("Content-Type") or ""
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype, re.I)
        return RawPage(url=url, body=r.content,
                       encoding=(m.group(1).lower() if m else "utf-8"), content_type=ctype)

    @staticmethod
    def target_of(url: str) -> Optional[str]:
        """리플레이 URL(/f/<target_id>/<case_id>)에서 대상을 읽는다.
        실제 스파이더도 어느 사이트를 긁는지는 알고 시작한다."""
        m = re.search(r"/f/([^/]+)/", url)
        return m.group(1) if m else None

    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target = self.opts.get("target_id") or self.target_of(url)
        spider = SPIDERS.get(target)
        with Stopwatch() as sw:
            try:
                with sw.stage("fetch"):
                    page = raw or self.fetch(url)
                if spider is None:
                    raise KeyError(f"셀렉터를 짜 둔 적 없는 대상: {target}")
                with sw.stage("parse"):
                    sel = Selector(text=page.text)
                    data = spider.list(sel) if task == "list" else spider.detail(sel)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = page.page_bytes
        return RunResult(case_id=case_id, tool=self.name, ok=True, data=data,
                         metrics=m,
                         meta={"encoding": page.encoding, "task": task,
                               "target_id": target, "ts": time.time()})
