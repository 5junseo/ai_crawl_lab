"""gold 초안 생성용 **참조 추출기**. 벤치마크 대상 도구가 아니다.

왜 있나
  gold 는 사람이 확정해야 하지만(README §4-1), 22건 × 필드 10개 + 한전 목록 52행을
  맨손으로 옮겨 적으면 오타가 정답이 된다. 그래서 사이트별 구조를 알고 쓴 추출기로
  **초안**을 만들고 사람이 그 위에서 확인·수정한다.

지켜야 할 선
  1. 이 추출기는 **비교 대상 7종과 무관하다.** 여기서 나온 값을 도구 점수에 쓰지 않는다.
     반대로 도구 출력을 보고 이 파일을 고치면 벤치마크가 무의미해진다. 절대 금지.
  2. 사이트별 지식(셀렉터, 컬럼 순서, id 규칙)을 마음껏 쓴다. 공정성 제약이 없다.
     도구들은 hint_level(H0~H3) 안에서만 움직이지만 이 파일은 정답을 만드는 쪽이다.
  3. **이미 사람이 채운 칸은 건드리지 않는다.** __TODO__ 인 칸만 채운다.
  4. 채운 케이스는 gold/<target>/_provenance.json 에 ref_draft 로 남는다.
     사람이 확인하면 human 으로 바꾼다. --check 가 ref_draft 를 미확인으로 센다.

  python -m harness.gold_ref --target t1_d2b            # __TODO__ 칸을 초안으로 채움
  python -m harness.gold_ref --target t1_d2b --dry-run  # 뭘 채울지만 보여준다
  python -m harness.gold_ref --confirm t1_d2b NEWS01-p1 # 사람이 확인했다고 표시
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema_utils as su   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
GOLD = ROOT / "gold"
TODO = su.GOLD_TODO


# ------------------------------------------------------------------ 공용 도우미
# 인라인 태그를 공백으로 지우면 '2026<span>년</span>' 이 '2026 년' 이 된다.
# 에디터가 글자마다 span 을 두르는 사이트(G2B)에서 본문이 통째로 어긋난다.
# 블록 태그만 줄바꿈으로 바꾸고 나머지는 빈 문자열로 지운다.
_BLOCK = re.compile(r"</?(?:br|p|div|tr|li|h[1-6]|table|blockquote)\b[^>]*>", re.I)


def strip_tags(s: str) -> str:
    s = _BLOCK.sub("\n", s)
    return re.sub(r"<[^>]+>", "", s)


def txt(s: str) -> str:
    """태그 제거 + 공백 정리 (한 줄)."""
    s = strip_tags(s).replace("\xa0", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def block(s: str) -> str:
    """태그 제거 + 줄바꿈 유지 (본문용)."""
    s = strip_tags(s).replace("\xa0", " ").replace("&nbsp;", " ")
    s = unescape(s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def unescape(s: str) -> str:
    import html as _h
    return _h.unescape(s)


def as_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    m = re.search(r"-?[\d,]+", str(s))
    return int(m.group(0).replace(",", "")) if m else None


def as_dt(s: Any) -> Optional[str]:
    """'2026/07/31 13:43:04' / '2026-08-28' -> ISO 비슷한 문자열. 정밀도는 유지한다."""
    if not s:
        return None
    v = str(s).strip().replace(".", "-").replace("/", "-")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", v)
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    out = f"{y}-{mo:02d}-{d:02d}"
    if m.group(4):
        out += f" {int(m.group(4)):02d}:{m.group(5)}"
        if m.group(6):
            out += f":{m.group(6)}"
    return out


def rows_of(table_html: str) -> list:
    return re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)


def cells_of(row_html: str, tag: str = "td") -> list:
    return re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", row_html, re.S)


# ------------------------------------------------------------------ D2B (정적)
D2B_LIST_MAP = {"번호": "seq", "제목": "title", "첨부": "has_attachment",
                "작성자": "author", "작성일자": "posted_at", "등록일자": "posted_at",
                "조회": "views", "조회수": "views"}


def d2b_list(html: str) -> list:
    """게시판마다 컬럼 수가 다르다. 양끝을 먼저 고정하고 가운데를 맞춘다.

    NEWS04 는 헤더에 '작성자' 가 있는데 본문 행에는 없다(th 6 / td 5).
    위치로만 맞추면 작성일자를 작성자로 읽는다 — 그 함정을 여기서 피한다.
    """
    th = [txt(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
    th = [t for t in th if t]
    trs = [r for r in rows_of(html) if "fn_detail" in r]
    if not th or not trs:
        return []
    head, tail = th[:3], th[-2:]              # 번호/제목/첨부 · 작성일자/조회
    mid_hdr = th[3:-2]
    out = []
    for r in trs:
        td = cells_of(r)
        n_mid = len(td) - 5
        mid = mid_hdr[-n_mid:] if n_mid > 0 else []      # 모자라면 앞쪽 헤더가 빠진 것
        names = head + mid + tail
        rec = {f: None for f in ("seq", "title", "author", "posted_at", "views",
                                 "has_attachment")}
        for name, cell in zip(names, td):
            key = D2B_LIST_MAP.get(name)
            if key == "has_attachment":
                rec[key] = "ico_file" in cell
            elif key == "views":
                rec[key] = as_int(txt(cell))
            elif key == "posted_at":
                rec[key] = as_dt(txt(cell))
            elif key:
                rec[key] = txt(cell) or None
        out.append(rec)
    return out


def d2b_detail(html: str) -> dict:
    m = re.search(r"<table[^>]*>\s*<caption>[^<]*첨부파일[^<]*</caption>.*?</table>", html, re.S)
    seg = m.group(0) if m else html
    rec = {f: None for f in su.fields(su.load_schema("detail"))}
    t = re.search(r"<th[^>]*colspan=\"4\"[^>]*>(.*?)</th>", seg, re.S)
    rec["title"] = txt(t.group(1)) if t else None
    for lab, val in re.findall(r"<th[^>]*scope=\"row\"[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                               seg, re.S):
        lab, raw = txt(lab), val
        if lab == "작성자":
            rec["author"] = txt(raw) or None
        elif lab in ("부서명", "부대명", "담당부서"):
            rec["dept"] = txt(raw) or None
        elif lab == "구분":
            rec["category"] = txt(raw) or None
        elif lab == "게시기간":
            p = [as_dt(x) for x in re.split(r"~", txt(raw))]
            rec["period_start"] = p[0] if p else None
            rec["period_end"] = p[1] if len(p) > 1 else None
        elif lab == "첨부파일":
            rec["attachments"] = [re.sub(r"\s*\([^)]*\)\s*$", "", txt(a)).strip()
                                  for a in re.findall(r"<a[^>]*>(.*?)</a>", raw, re.S)]
    # 본문 담는 방식이 두 가지다. 오래된 글은 textarea(CLOB), 최근 글은 td.td_noti 에
    # HTML 을 그대로 넣는다. 하나만 보면 게시판 절반이 본문 없음으로 잡힌다.
    b = re.search(r"<textarea[^>]*>(.*?)</textarea>", seg, re.S)
    if not b:
        b = re.search(r'<td[^>]*class="[^"]*td_noti[^"]*"[^>]*>(.*?)</td>', seg, re.S)
    rec["body"] = (block(b.group(1)) or None) if b else None
    if rec.get("attachments") is None:
        rec["attachments"] = []
    return rec                                 # posted_at / views 는 상세에 없다 -> null


# ------------------------------------------------------------------ G2B (WebSquare)
def g2b_list(html: str) -> list:
    hd = re.search(r'<table[^>]*class="gridHeaderTableDefault"[^>]*>(.*?)</table>', html, re.S)
    names = [txt(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", hd.group(1), re.S)] if hd else []
    i = html.find('id="mf_wfm_container_grdPst_body_table"')
    if i < 0:
        return []
    body = html[i:i + 400000]
    out = []
    for r in rows_of(body):
        td = cells_of(r)
        if len(td) != len(names) or not txt(td[0]):
            continue
        d = dict(zip(names, td))
        # 게시판분류 셀에는 필터 드롭다운의 전체 옵션이 들어 있다. selected 옵션만 값이다.
        out.append({
            "seq": txt(d.get("게시물번호", "")) or None,
            "title": txt(d.get("제목", "")) or None,
            "author": txt(d.get("작성자", "")) or None,
            "posted_at": as_dt(txt(d.get("작성일시", ""))),
            "views": as_int(txt(d.get("조회수", ""))),
            "has_attachment": None,            # 목록에 첨부 컬럼이 없다
        })
    return out


_G2B_IBX = {
    "ibxPstNm": "title", "ibxBbsClsfPritm": "category", "ibxPstInqCnt": "views",
    "ibxOdn1Col": "dept", "ibxInptDt": "posted_at",
}


def g2b_detail(html: str) -> dict:
    rec = {f: None for f in su.fields(su.load_schema("detail"))}
    for key, field in _G2B_IBX.items():
        m = re.search(rf'<input[^>]*id="mf_wfm_container_{key}"[^>]*value="([^"]*)"', html)
        if not m:
            continue
        v = unescape(m.group(1)).strip()
        rec[field] = as_int(v) if field == "views" else (as_dt(v) if field == "posted_at"
                                                         else (v or None))
    # 공지기간: '사용안함' 이면 값이 없다는 뜻이다
    m = re.search(r'<input[^>]*id="mf_wfm_container_ibxNtcPrd"[^>]*value="([^"]*)"', html)
    if m and "~" in m.group(1):
        a, b = [x.strip() for x in unescape(m.group(1)).split("~", 1)]
        rec["period_start"], rec["period_end"] = as_dt(a), as_dt(b)
    # 작성자 항목은 상세 화면에 없다(기관명만 있다) -> null
    m = re.search(r'id="mf_wfm_container_tbxPstCn"[^>]*>(.*?)</div><div id="wq_uuid', html, re.S)
    if m:
        body = block(m.group(1))
        # 본문이 이미지 1장뿐이면 텍스트 값이 없는 것이다
        rec["body"] = body if len(re.sub(r"\W", "", body)) >= 5 else None
    rec["attachments"] = _g2b_attachments(html)
    return rec


_FNAME = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def _g2b_attachments(html: str) -> list:
    """첨부 그리드에서 파일명 셀만 고른다.
    앞의 uuid(wq_uuid_2091)는 캡처마다 바뀌므로 id 접미사로 잡는다."""
    m = re.search(r'<table[^>]*id="[^"]*_grdFile_body_table"[^>]*>.*?</table>', html, re.S)
    if not m:
        return []
    out = []
    for r in rows_of(m.group(0)):
        for c in cells_of(r):
            v = txt(c)
            if v and _FNAME.search(v):
                out.append(v)
                break
    return out


# ------------------------------------------------------------------ 한전 SRM (ExtJS)
KEPCO_COLS = ["공지번호", "제목", "첨부", "업무구분", "공고시작일", "공고종료일",
              "공동이용사", "품목구분", "등록자", "등록일자"]


def kepco_list(html: str) -> list:
    """그리드가 locked/normal 두 패널로 쪼개져 한 행이 DOM 에 두 번 나온다.
    셀 10개를 다 가진 쪽(normal)만 쓴다. 그리드 id 는 캡처마다 바뀌므로 셀 수로 고른다."""
    best: list = []
    for vid in sorted(set(re.findall(r'id="(gridview-\d+)"', html))):
        rows = re.findall(
            rf'data-boundview="{vid}"[^>]*data-recordindex="(\d+)"(.*?)</table>', html, re.S)
        got = []
        for _idx, chunk in rows:
            cells = [txt(c) for c in re.findall(
                r'class="x-grid-cell-inner[^"]*"[^>]*>(.*?)</div>', chunk, re.S)]
            if len(cells) == len(KEPCO_COLS):
                got.append(dict(zip(KEPCO_COLS, cells)))
        if len(got) > len(best):
            best = got
    return [{
        "seq": d["공지번호"] or None,
        "title": d["제목"] or None,
        "author": d["등록자"] or None,
        "posted_at": as_dt(d["등록일자"]),
        "views": None,                          # 목록에 조회수 컬럼이 없다
        "has_attachment": (as_int(d["첨부"]) or 0) > 0,
    } for d in best]


def _kepco_fields(html: str) -> dict:
    """라벨과 다음 라벨 사이를 그 필드의 영역으로 본다."""
    i = html.find("상세정보")
    seg = html[i:] if i >= 0 else html
    # ExtJS 는 한 필드 안에 라벨 없는 하위 위젯을 또 넣는다(공지기간 = 날짜 2개).
    # 그래서 '다음 <label>' 이 아니라 '다음 이름 있는 라벨' 까지를 그 필드 영역으로 본다.
    marks = [(m.group(1).strip(), m.end())
             for m in re.finditer(r">([^<>]{2,10})</span></label>", seg)]
    out = {}
    for k, (lab, pos) in enumerate(marks):
        win = seg[pos:marks[k + 1][1] if k + 1 < len(marks) else len(seg)]
        out[lab] = {
            "inputs": [unescape(v).strip() for v in
                       re.findall(r'<input[^>]*value="([^"]*)"', win) if v.strip()],
            "textarea": [unescape(t) for t in
                         re.findall(r"<textarea[^>]*>(.*?)</textarea>", win, re.S) if t.strip()],
        }
    return out


def kepco_detail(html: str) -> dict:
    rec = {f: None for f in su.fields(su.load_schema("detail"))}
    f = _kepco_fields(html)

    def first(lab, idx=0):
        v = (f.get(lab) or {}).get("inputs") or []
        return v[idx] if len(v) > idx else None

    rec["title"] = first("제목")
    rec["category"] = first("업무구분")
    rec["author"] = first("등록자")
    rec["period_start"] = as_dt(first("공지기간", 0))
    rec["period_end"] = as_dt(first("공지기간", 1))
    ta = (f.get("내용") or {}).get("textarea") or []
    rec["body"] = block(ta[0]) if ta else None
    rec["attachments"] = _kepco_attachments(html)
    return rec                                 # dept / posted_at / views 는 화면에 없다 -> null


def _kepco_attachments(html: str) -> list:
    """첨부 그리드는 ExtJS 업로더가 그린다. 파일명 셀에만 uploader-file 클래스가 붙는다."""
    i = html.find("상세정보")
    seg = html[i:] if i >= 0 else html
    j = seg.find(">첨부파일<")
    win = seg[j:] if j >= 0 else seg
    out = [txt(m) for m in re.findall(
        r'class="[^"]*uploader-file[^"]*"[^>]*>(.*?)</div>', win, re.S)]
    return [v for v in out if v]


# ------------------------------------------------------------------ 등록
REF = {
    "t1_d2b": {"list": d2b_list, "detail": d2b_detail},
    "t2_g2b": {"list": g2b_list, "detail": g2b_detail},
    "t3_kepco_srm": {"list": kepco_list, "detail": kepco_detail},
}


def prov_path(target_id: str) -> Path:
    return GOLD / target_id / "_provenance.json"


def load_prov(target_id: str) -> dict:
    p = prov_path(target_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_prov(target_id: str, d: dict) -> None:
    p = prov_path(target_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fill(target_id: str, *, dry_run: bool = False) -> int:
    if target_id not in REF:
        raise SystemExit(f"{target_id} 용 참조 추출기가 없다")
    prov = load_prov(target_id)
    changed = 0
    for task in ("list", "detail"):
        gp = GOLD / target_id / f"{task}.json"
        if not gp.exists():
            continue
        gold = json.loads(gp.read_text(encoding="utf-8"))
        for case_id in sorted(gold):
            page = FIXTURES / target_id / case_id / "page.html"
            if not page.exists():
                continue
            cur = gold[case_id]
            key = f"{task}/{case_id}"
            if prov.get(key) == "human":
                print(f"  {case_id:<14} 사람 확정 - 건드리지 않음")
                continue
            html = page.read_text(encoding="utf-8", errors="replace")
            got = REF[target_id][task](html)
            if task == "list":
                if cur != TODO and isinstance(cur, dict) and cur.get("items") not in (None, TODO):
                    # 값은 그대로 두되, 사람이 확정한 적이 없으면 미확인으로 남긴다.
                    prov.setdefault(key, "ref_draft")
                    print(f"  {case_id:<14} 이미 값 있음 - 건드리지 않음 "
                          f"({'사람 확정' if prov[key] == 'human' else '미확인'})")
                    continue
                gold[case_id] = {"items": got}
                print(f"  {case_id:<14} list  {len(got)}행")
            else:
                base = cur if isinstance(cur, dict) else {}
                merged, filled = dict(base), []
                for k, v in got.items():
                    if base.get(k, TODO) == TODO:
                        merged[k] = v
                        filled.append(k)
                gold[case_id] = merged
                print(f"  {case_id:<14} detail 채움 {len(filled)}칸 "
                      f"{'' if len(filled) == len(got) else '(나머지는 기존 값 유지)'}")
            # 이번에 채웠으면 초안. 아무것도 안 채웠으면 기존 표시를 그대로 두되,
            # 표시가 없으면 미확인으로 본다 (사람이 확정한 기록이 없으므로).
            if task == "list" or filled:
                prov[key] = "ref_draft"
            else:
                prov.setdefault(key, "ref_draft")
            changed += 1
        if not dry_run:
            gp.write_text(json.dumps(gold, ensure_ascii=False, indent=2, sort_keys=True),
                          encoding="utf-8")
    if not dry_run:
        save_prov(target_id, prov)
    return changed


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="gold 초안 생성 (참조 추출기)")
    ap.add_argument("--target")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", nargs=2, metavar=("TARGET", "CASE"),
                    help="사람이 확인했다고 표시한다 (task/case 또는 case)")
    a = ap.parse_args(argv)

    if a.confirm:
        tid, case = a.confirm
        prov = load_prov(tid)
        hit = [k for k in prov if k.endswith("/" + case) or k == case]
        if not hit:
            print(f"{case} 항목이 없다"); return 1
        for k in hit:
            prov[k] = "human"
        save_prov(tid, prov)
        print("확정:", ", ".join(hit))
        return 0

    if not a.target:
        ap.error("--target 또는 --confirm 이 필요하다")
    print(f"[gold_ref] {a.target}{' (dry-run)' if a.dry_run else ''}")
    n = fill(a.target, dry_run=a.dry_run)
    print(f"[gold_ref] {n}건 초안. **사람 확인 전에는 정답이 아니다.**")
    print(f"[gold_ref] 확인 뒤: python -m harness.gold_ref --confirm {a.target} <case_id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
