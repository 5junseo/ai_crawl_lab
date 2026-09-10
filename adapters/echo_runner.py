"""harness 배선 점검용 어댑터. 도구가 아니다.

replay_server -> adapter -> results -> score -> report 경로가 살아 있는지만 확인한다.
표준 라이브러리만 쓰는 아주 단순한 HTML 표 파서이며, 벤치마크 리포트 대상이 아니다
(registry phase=0). 도구 비교 수치에 절대 섞지 말 것.
"""
from __future__ import annotations

import html
import re
import time
import urllib.request
from typing import Optional

from .base import Adapter, RawPage, RunResult
from harness.meter import Stopwatch

_TAG = re.compile(r"<[^>]+>")
_TR = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh]\b.*?</t[dh]>", re.S | re.I)
_TH = re.compile(r"<th\b.*?</th>", re.S | re.I)

# 상세 페이지의 라벨 -> 스키마 필드
_LABELS = {
    "작성자": "author",
    "등록자": "author",
    "부서명": "dept",
    "담당부서": "dept",
    "부대명": "dept",
    "구분": "category",
    "작성일자": "posted_at",
    "등록일자": "posted_at",
    "조회": "views",
    "조회수": "views",
}


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


class EchoAdapter(Adapter):
    name = "echo"
    supports_fetch = True
    needs_llm = False

    # ---- fetch --------------------------------------------------------
    def fetch(self, url: str) -> Optional[RawPage]:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type") or ""
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype, re.I)
        return RawPage(url=url, body=body, encoding=(m.group(1).lower() if m else "utf-8"),
                       content_type=ctype)

    # ---- extract ------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        with Stopwatch() as sw:
            try:
                with sw.stage("fetch"):
                    page = raw or self.fetch(url)
                text = page.text
                data = self._list(text) if task == "list" else self._detail(text)
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = page.page_bytes
        return RunResult(case_id=case_id, tool=self.name, ok=True, data=data,
                         raw=text[:20000], metrics=m,
                         meta={"encoding": page.encoding, "task": task, "ts": time.time()})

    # ---- 표 파싱 -------------------------------------------------------
    def _list(self, text: str) -> dict:
        """tbody 의 행을 헤더 컬럼에 맞춰 레코드로 만든다."""
        head = [_text(x) for x in _TH.findall(text)]
        cols = [_LABELS.get(h, {"번호": "seq", "제목": "title", "첨부": "_attach"}.get(h))
                for h in head]
        body = re.search(r"<tbody\b.*?</tbody>", text, re.S | re.I)
        items = []
        for tr in _TR.findall(body.group(0) if body else ""):
            cells = [_text(c) for c in _TD.findall(tr)]
            raw_cells = _TD.findall(tr)
            if not cells or len(cells) < 2:
                continue
            rec: dict = {}
            for i, cell in enumerate(cells):
                key = cols[i] if i < len(cols) else None
                if key == "_attach":
                    rec["has_attachment"] = "ico_file" in raw_cells[i]
                elif key == "views":
                    rec["views"] = int(re.sub(r"[^\d]", "", cell) or 0) if cell else None
                elif key:
                    rec[key] = cell or None
            if rec.get("title"):
                items.append(rec)
        return {"items": items}

    def _detail(self, text: str) -> dict:
        data = {f: None for f in schema_fields()}
        # 제목: 상세 표의 첫 th
        ths = [_text(x) for x in _TH.findall(text)]
        if ths:
            data["title"] = ths[0]
        # 라벨 th 다음의 td
        for m in re.finditer(r"<th\b.*?</th>\s*<td\b.*?</td>", text, re.S | re.I):
            frag = m.group(0)
            label = _text(_TH.search(frag).group(0))
            fld = _LABELS.get(label)
            if not fld:
                continue
            val = _text(re.sub(r"<th\b.*?</th>", "", frag, flags=re.S | re.I))
            if fld == "views":
                data["views"] = int(re.sub(r"[^\d]", "", val) or 0) if val else None
            else:
                data[fld] = val or None
        return data


def schema_fields() -> list:
    from harness import schema_utils as su

    return su.fields(su.load_schema("detail"))
