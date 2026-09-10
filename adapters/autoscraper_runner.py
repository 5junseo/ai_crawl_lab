"""AutoScraper — 예시를 보고 규칙을 스스로 만드는 도구 (README §7 Phase 1).

**이 도구만 힌트 등급이 다르다.** 구조상 예시 없이는 아무것도 못 한다. 셀렉터를 주는 대신
'이 페이지에서 이 값들을 원한다'를 알려주면 그 값이 붙은 자리를 찾아 규칙을 만든다.
그래서 언제나 H3 이고, 다른 도구와 정확도를 나란히 놓을 때 이 점을 같이 읽어야 한다.

예시를 어디서 주나
  detail  대상마다 케이스 하나를 통째로 예시로 준다. **그 케이스는 채점하지 않는다**
          (meta.excluded). 정답을 다 알려주고 맞혔는지 세는 건 점수가 아니다.
  list    첫 케이스의 **1행만** 예시로 준다. 'AutoScraper 에 한 줄 보여주면 나머지 행을
          알아서 긁어온다'가 이 도구의 사용법 그대로다. 나머지 행과 나머지 게시판은
          채점한다 — D2B 는 NEWS01 로 배운 규칙이 NEWS02~04 에서 버티는지가 곧 시험이다.

예시 값은 **화면에 찍힌 그대로**여야 한다. gold 는 정규화된 값('2026-07-31 13:43:04')이라
페이지 표기('2026/07/31 13:43:04')와 다를 수 있다. 사람이 예시를 줄 때 화면을 보고 적는
것과 같으므로, gold 값을 페이지에서 찾을 수 있는 형태로 바꿔서 준다(_page_literal).
끝내 못 찾는 필드는 예시로 줄 수 없다 -> 그 필드는 규칙 자체가 안 만들어진다. 이것도 결과다.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests
from autoscraper import AutoScraper

from .base import Adapter, RawPage, RunResult
from harness.meter import Stopwatch

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 대상·태스크마다 예시로 쓸 케이스. 재현 가능하도록 코드에 박아 둔다.
TRAIN_CASE = {
    ("t1_d2b", "detail"): "NEWS01-6071",
    ("t1_d2b", "list"): "NEWS01-p1",
    ("t2_g2b", "detail"): "ntc-777",
    ("t2_g2b", "list"): "ntc-p1",
    ("t3_kepco_srm", "detail"): "ntc-4267",
    ("t3_kepco_srm", "list"): "ntc-p1",
}


def _variants(v: Any) -> list:
    """gold 값 하나를 페이지 표기 후보들로 펼친다."""
    if v is None or isinstance(v, bool):
        return []
    if isinstance(v, (int, float)):
        return [f"{v:,}", str(v)]
    s = str(v).strip()
    if not s:
        return []
    out = [s]
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        out += [s.replace("-", "/"), s.replace("-", "."), s[:10],
                s[:10].replace("-", "/"), s[:10].replace("-", ".")]
    return list(dict.fromkeys(out))


def _page_literal(v: Any, html: str) -> Optional[str]:
    """페이지에 실제로 있는 표기를 고른다. 없으면 None(예시로 못 준다)."""
    for cand in _variants(v):
        if cand and cand in html:
            return cand
    return None


class AutoScraperAdapter(Adapter):
    name = "autoscraper"
    supports_fetch = True
    needs_llm = False

    def setup(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self._models: dict = {}          # (target, task) -> (AutoScraper, [alias...])

    def teardown(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    # ---- 입출력 -------------------------------------------------------
    def fetch(self, url: str) -> Optional[RawPage]:
        r = self.session.get(url, timeout=30)
        ctype = r.headers.get("Content-Type") or ""
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype, re.I)
        return RawPage(url=url, body=r.content,
                       encoding=(m.group(1).lower() if m else "utf-8"), content_type=ctype)

    @staticmethod
    def _split(url: str) -> tuple:
        m = re.search(r"/f/([^/]+)/([^/?]+)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

    def _gold(self, target: str, task: str, case_id: str) -> Optional[dict]:
        p = GOLD / target / f"{task}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8")).get(case_id)

    # ---- 학습 ---------------------------------------------------------
    def _model(self, url: str, target: str, task: str):
        key = (target, task)
        if key in self._models:
            return self._models[key]

        train_case = TRAIN_CASE.get(key)
        if not train_case:
            raise KeyError(f"예시 케이스를 정해두지 않은 대상: {target}/{task}")
        train_url = re.sub(r"(/f/[^/]+/)[^/?]+", rf"\g<1>{train_case}", url)
        html = self.fetch(train_url).text
        gold = self._gold(target, task, train_case)
        if not gold:
            raise KeyError(f"예시로 쓸 gold 가 없다: {target}/{task}/{train_case}")

        wanted: dict = {}
        if task == "list":
            rows = gold.get("items") or []
            if not rows:
                raise ValueError("예시로 쓸 목록 행이 없다")
            row = rows[0]                       # 사람이 보여주는 '한 줄'
            for fld, val in row.items():
                lit = _page_literal(val, html)
                if lit:
                    wanted[fld] = [lit]
        else:
            for fld, val in gold.items():
                if isinstance(val, list):       # 첨부는 이름 하나만 예시로
                    val = val[0] if val else None
                lit = _page_literal(val, html)
                if lit:
                    wanted[fld] = [lit]

        if not wanted:
            raise ValueError("페이지에서 찾을 수 있는 예시 값이 하나도 없다")
        scraper = AutoScraper()
        scraper.build(html=html, wanted_dict=wanted)
        keep = self._pick_rules(scraper, wanted, html, task)
        self._models[key] = (scraper, keep, train_case)
        return self._models[key]

    @staticmethod
    def _norm(s: Any) -> str:
        """AutoScraper 는 결과를 NFKD 로 분해해서 돌려준다('사기' -> 'ᄉ...'). 비교 전에 되돌린다."""
        import unicodedata
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or ""))).strip()

    def _pick_rules(self, scraper, wanted: dict, html: str, task: str) -> dict:
        """별칭 하나에 규칙이 여러 개 만들어진다('관리자'가 페이지 곳곳에 있으면 7개).
        전부 합치면 열 길이가 어긋나 행이 밀린다. 그래서 **학습 페이지에서 내가 준 예시를
        그대로 재현하는 규칙**만 남긴다. AutoScraper 를 쓰는 사람이 규칙을 골라내는 그 단계다.
        고르는 근거는 예시 페이지뿐이며 채점 대상 페이지는 보지 않는다.

        반환: {alias: stack_id}
        """
        fn = (scraper.get_result_similar if task == "list" else scraper.get_result_exact)
        per = fn(html=html, grouped=True, **({"keep_order": True} if task == "list" else {})) or {}
        alias_of = {st["stack_id"]: st.get("alias") for st in scraper.stack_list}
        keep: dict = {}
        for sid, vals in per.items():
            a = alias_of.get(sid)
            if not a or a not in wanted or not vals:
                continue
            if self._norm(vals[0]) != self._norm(wanted[a][0]):
                continue                       # 예시를 첫 자리에 재현하지 못하는 규칙은 버린다
            prev = keep.get(a)
            if prev is None or len(vals) > len(per.get(prev, [])):
                keep[a] = sid                  # 같은 조건이면 더 많은 행을 뽑는 규칙
        return keep

    # ---- 적용 ---------------------------------------------------------
    def _cols(self, scraper, keep: dict, html: str, task: str) -> dict:
        """남겨둔 규칙만 적용해 {alias: [값...]} 을 만든다."""
        fn = (scraper.get_result_similar if task == "list" else scraper.get_result_exact)
        per = fn(html=html, grouped=True, **({"keep_order": True} if task == "list" else {})) or {}
        return {a: [self._norm(x) for x in (per.get(sid) or [])] for a, sid in keep.items()}

    def _detail(self, scraper, keep: dict, html: str, fields: list) -> dict:
        cols = self._cols(scraper, keep, html, "detail")
        out = {f: None for f in fields}
        for a, vals in cols.items():
            if a not in out:
                continue
            v = vals[0] if vals else None
            out[a] = ([v] if v else []) if a == "attachments" else (v or None)
        if out.get("attachments") is None:
            out["attachments"] = []
        return out

    def _list(self, scraper, keep: dict, html: str, schema: dict, fields: list) -> dict:
        from harness import schema_utils as su

        cols = self._cols(scraper, keep, html, "list")
        # AutoScraper 는 레코드가 아니라 '열'을 준다. 열마다 길이가 다르면(G2B 에서 제목은
        # 1개인데 작성자는 9개) 행을 몇 개로 볼지는 붙이는 쪽이 정해야 한다.
        # 가장 긴 열에 맞추면 없는 레코드를 지어내는 꼴이 되어 환각으로 잡힌다. 그건 도구가
        # 아니라 내가 만든 행이다. 그래서 **키 열(제목, 없으면 번호)이 뽑힌 만큼만** 레코드로 본다.
        key = su.match_key(schema)
        n = len(cols.get(key) or [])
        if not n:
            n = len(cols.get(su.fallback_key(schema)) or [])
        items = []
        for i in range(n):
            rec = {f: None for f in fields}
            for a, vals in cols.items():
                if a in rec and i < len(vals):
                    rec[a] = vals[i] or None
            items.append(rec)
        return {"items": items}

    # ---- 본체 ---------------------------------------------------------
    def extract(self, url: str, schema: dict, raw: Optional[RawPage] = None) -> RunResult:
        from harness import schema_utils as su

        case_id = self.opts.get("case_id", url.rsplit("/", 1)[-1])
        task = self.opts.get("task", "detail")
        target, _ = self._split(url)
        fields = su.fields(schema)

        with Stopwatch() as sw:
            try:
                with sw.stage("setup"):
                    scraper, keep, train_case = self._model(url, target, task)
                with sw.stage("fetch"):
                    page = raw or self.fetch(url)
                with sw.stage("parse"):
                    data = (self._list(scraper, keep, page.text, schema, fields) if task == "list"
                            else self._detail(scraper, keep, page.text, fields))
            except Exception as e:
                return self._fail(case_id, e)

        m = sw.to_metrics()
        m.setup_ms = self.setup_ms
        m.page_bytes = page.page_bytes
        meta = {"encoding": page.encoding, "task": task, "target_id": target,
                "train_case": train_case, "learned_fields": sorted(keep),
                "ts": time.time()}
        # 상세는 페이지 전체를 예시로 줬으므로 그 케이스는 채점에서 뺀다.
        # 목록은 1행만 줬으므로 채점한다(예시 행이 섞여 있다는 건 리포트에 남긴다).
        if task == "detail" and case_id == train_case:
            meta["excluded"] = "이 페이지를 예시로 학습함"
        elif task == "list" and case_id == train_case:
            meta["note"] = "1행을 예시로 줌 (나머지 행은 채점 대상)"

        return RunResult(case_id=case_id, tool=self.name, ok=True, data=data,
                         metrics=m, meta=meta)
