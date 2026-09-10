"""실사이트 1회 호출 -> fixtures 동결 (README §4-2 fetch 벤치).

여기서만 외부 네트워크를 친다. 이후 모든 도구 비교는 replay_server 가 서빙하는
동일 스냅샷 위에서 돈다. 사이트에 부하를 주지 않으려는 것이 첫 번째 이유이고,
매 실행마다 페이지가 달라져 비교가 무너지는 걸 막는 것이 두 번째 이유다.

  python -m harness.capture --target t1_d2b                      # list + detail 전부
  python -m harness.capture --target t1_d2b --task list          # 목록 페이지만
  python -m harness.capture --target t1_d2b --case NEWS01-6071   # 특정 케이스만
  python -m harness.capture --target t2_g2b --task list --browser

산출물: fixtures/<target_id>/<case_id>/{page.html, page.png, meta.json}
  page.html 은 '응답 바이트 그대로'. 디코딩해서 저장하면 인코딩 비교가 무의미해진다.
  meta.json 의 task 가 채점 스키마(list/detail)를 고른다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
TARGETS = ROOT / "targets"

DEFAULT_DELAY_S = 3.0          # 사이트 예의. 줄이지 말 것.
DEFAULT_TIMEOUT_S = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)
TASKS = ("list", "detail")


def load_target(target_id: str) -> dict:
    import yaml

    p = TARGETS / f"{target_id}.yaml"
    if not p.exists():
        raise SystemExit(f"targets/{target_id}.yaml 없음")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def target_cases(target: dict, task: str) -> list:
    """[{id, url, ...}, ...] — id 외의 키(row 등)는 그대로 넘긴다."""
    rows = ((target.get("cases") or {}).get(task)) or []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("id"):
            c = dict(r)
            c["id"] = str(r["id"])
            c.setdefault("url", None)
            out.append(c)
    return out


def sniff_encoding(body: bytes, content_type: Optional[str]) -> str:
    if content_type:
        m = re.search(r"charset\s*=\s*([A-Za-z0-9_\-]+)", content_type, re.I)
        if m:
            return m.group(1).lower()
    m2 = _META_CHARSET.search(body[:4096])
    if m2:
        return m2.group(1).decode("ascii", "ignore").lower()
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "euc-kr"   # 국내 사이트 기본 추정. meta.json 에 남으니 나중에 교정 가능.


def _write(case_dir: Path, body: bytes, meta: dict, png: Optional[bytes] = None) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "page.html").write_bytes(body)
    if png:
        (case_dir / "page.png").write_bytes(png)
    meta["sha256"] = hashlib.sha256(body).hexdigest()
    meta["bytes"] = len(body)
    meta["frozen_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (case_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 정적 취득
def capture_static(url: str, *, method: str = "GET", data: Optional[dict] = None,
                   headers: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    import requests

    t0 = time.perf_counter()
    resp = requests.request(method, url, data=data, timeout=timeout,
                            headers={"User-Agent": UA, **(headers or {})})
    fetch_ms = (time.perf_counter() - t0) * 1000
    body = resp.content
    ctype = resp.headers.get("Content-Type")
    return {
        "body": body,
        "meta": {
            "url": url,
            "final_url": resp.url,
            "method": method,
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items()
                        if k.lower() in ("content-type", "content-language")},
            "encoding": sniff_encoding(body, ctype),
            "fetch_ms": round(fetch_ms, 2),
            "mode": "static",
        },
    }


# ------------------------------------------------------------------ 렌더 값 고정
# 브라우저는 폼 값을 DOM 프로퍼티(el.value)로 들고 있고, page.content() 는 그걸
# 직렬화하지 않는다. 한전 상세가 여기 걸렸다 — 화면에는 본문이 보이는데 동결된 HTML
# 에는 <textarea></textarea> 만 남아 어떤 도구도 값을 못 가져간다.
# 동결 직전에 프로퍼티를 마크업으로 되박아 '화면에 보이는 것 = 스냅샷' 을 맞춘다.
# 값을 만들어 넣는 게 아니라 이미 있는 값을 옮겨 적는 것이다. meta 에 표시로 남긴다.
_INLINE_VALUES_JS = """() => {
  let n = 0;
  for (const el of document.querySelectorAll('input')) {
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (el.checked) { el.setAttribute('checked', 'checked'); n++; }
      else el.removeAttribute('checked');
    } else if (el.value !== '' && el.value != null) {
      el.setAttribute('value', el.value); n++;
    }
  }
  for (const el of document.querySelectorAll('textarea')) {
    if (el.value !== '' && el.value != null) { el.textContent = el.value; n++; }
  }
  for (const el of document.querySelectorAll('select')) {
    for (const o of el.options) {
      if (o.selected) { o.setAttribute('selected', 'selected'); n++; }
      else o.removeAttribute('selected');
    }
  }
  return n;
}"""


def _inline_form_values(page) -> int:
    """모든 프레임에서 폼 값을 마크업에 고정한다. 실패해도 캡처는 계속한다."""
    total = 0
    for f in page.frames:
        try:
            total += int(f.evaluate(_INLINE_VALUES_JS) or 0)
        except Exception:
            pass
    return total


# ------------------------------------------------------------------ 메뉴 진입 취득
def _pick_frame(page, hint: Optional[str]):
    """frame 힌트(URL 부분 문자열)로 프레임을 고른다. 없으면 메인 프레임."""
    if not hint:
        return page.main_frame
    for f in page.frames:
        if hint in (f.url or "") or hint in (f.name or ""):
            return f
    raise ValueError(f"frame '{hint}' 없음 (현재: {[f.url[:60] for f in page.frames]})")


def _close_popups(ctx, keep, layer_sel: str = ".w2window_close") -> int:
    """keep 이외의 창을 닫고, keep 안의 레이어 팝업도 닫는다.

    G2B 는 진입할 때마다 '나라장터 공지사항' 레이어가 여러 겹 뜨고, 한전은 담합신고·
    피싱주의 창이 별도 window 로 뜬다. 둘 다 화면을 덮어 다음 클릭을 가로챈다.
    사람이 하는 첫 동작이 '팝업 닫기' 이므로 그대로 따라 한다.
    """
    n = 0
    for p in list(ctx.pages):
        if p is not keep:
            try:
                p.close(); n += 1
            except Exception:
                pass
    for _ in range(10):                      # 레이어는 닫으면 다음 겹이 드러난다
        try:
            loc = keep.locator(layer_sel)
            cnt = loc.count()
        except Exception:
            break
        hit = False
        for i in range(cnt):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.click(timeout=3000); n += 1; hit = True
                    keep.wait_for_timeout(400)
                    break
            except Exception:
                pass
        if not hit:
            break
    return n


def _run_steps(ctx, page, steps: list, *, timeout: int) -> Any:
    """진입 절차를 순서대로 실행하고, 최종 '대상 페이지'를 돌려준다.

    step 종류
      {wait: ms}                                   대기
      {close_popups: true}                         부수 창 + 레이어 팝업 정리
      {click: <셀렉터>, frame: <url 부분문자열>,    클릭. expect_popup 이면 새 창을 대상으로
       expect_popup: bool}
    """
    cur = page
    for st in steps or []:
        if st.get("wait"):
            cur.wait_for_timeout(int(st["wait"]))
        if st.get("close_popups"):
            _close_popups(ctx, cur, st.get("layer_selector") or ".w2window_close")
        sel = st.get("click")
        if sel:
            fr = _pick_frame(cur, st.get("frame"))
            if st.get("expect_popup"):
                with ctx.expect_page(timeout=timeout * 1000) as pop:
                    fr.click(sel, timeout=timeout * 1000)
                cur = pop.value
                cur.wait_for_load_state("networkidle", timeout=timeout * 1000)
            else:
                fr.click(sel, timeout=timeout * 1000)
    return cur


def capture_nav(nav: dict, *, task: str = "list", case: Optional[dict] = None,
                timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    """진입 URL -> 절차 실행 -> (상세면 행 클릭) -> 렌더 완료 DOM 동결.

    G2B 와 한전 SRM 은 목록/상세를 URL 로 지목할 수 없다(2026-09-08 실측).
      - G2B: 목록 화면(.../R23AB00000134L_01/)은 직접 열리지만 **상세는 URL 이 없다**.
             행을 클릭하면 같은 URL 위에서 화면만 바뀐다.
      - 한전: 메인의 공지 위젯은 제목이 잘린 티저다. iframe 안의 '더보기'를 눌러야
             전체 목록이 뜨고, 상세도 같은 화면 위에서 열린다.
    그래서 '사람이 하는 순서'를 steps 로 적고 그대로 밟는다.

    nav.list_api 가 지정되면 그 XHR 응답 본문을 가로채 meta.api_capture 에 넣는다.
    이건 '도구가 무엇을 놓쳤나' 를 보는 참조용이며 채점 입력이 아니다.
    """
    from playwright.sync_api import sync_playwright

    entry = nav.get("entry")
    if not entry:
        raise ValueError("nav.entry 가 없다")
    api = nav.get("list_api")
    case = case or {}
    captured: list = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1600, "height": 1200})
        page = ctx.new_page()

        def on_response(resp):
            if api and api.split("?")[0] in resp.url:
                try:
                    captured.append({"url": resp.url, "status": resp.status,
                                     "body": resp.text()[:200000]})
                except Exception:
                    pass                       # 본문을 못 읽는 응답은 조용히 넘긴다
        ctx.on("response", on_response)

        t0 = time.perf_counter()
        page.goto(entry, timeout=timeout * 1000, wait_until="networkidle")
        target = _run_steps(ctx, page, nav.get("steps"), timeout=timeout)

        row_used = None
        if task == "detail":
            sel = nav.get("row_selector")
            if not sel and nav.get("row_selector_js"):
                sel = target.evaluate(nav["row_selector_js"])   # 런타임 계산
            if not sel:
                browser.close()
                raise ValueError("detail 캡처에는 nav.row_selector 또는 row_selector_js 가 "
                                 "필요하다 (렌더된 목록을 보고 확정할 것)")
            row = int(case.get("row") or 1)                     # yaml 은 1부터
            loc = target.locator(sel).nth(row - 1)
            cell = nav.get("row_cell")
            if cell is not None:
                loc = loc.locator(nav.get("cell_selector") or ".x-grid-cell, td").nth(int(cell))
            loc.click(timeout=timeout * 1000)
            row_used = row
            target = _run_steps(ctx, target, nav.get("detail_steps"), timeout=timeout)

        target.wait_for_timeout(int(nav.get("wait_after_click_ms") or 2000))
        inlined = _inline_form_values(target)
        fetch_ms = (time.perf_counter() - t0) * 1000
        html = target.content()
        png = target.screenshot(full_page=False)
        final_url = target.url
        browser.close()

    return {
        "body": html.encode("utf-8"),
        "png": png,
        "meta": {
            "url": final_url,
            "entry_url": entry,
            "status": 200,
            "method": "GET",
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "encoding": "utf-8",
            "fetch_ms": round(fetch_ms, 2),
            "mode": "rendered_dom",
            "nav": {"steps": len(nav.get("steps") or []), "task": task, "row": row_used},
            "inlined_form_values": inlined,
            "api_capture": captured[:5],
            "note": "절차 진입 후 렌더된 DOM. 원본 바이트가 아니므로 ENCODING 판정 제외. "
                    "api_capture 는 참조용이며 채점 입력이 아니다.",
        },
    }


# ------------------------------------------------------------------ 렌더 취득
def capture_browser(url: str, *, wait_selector: Optional[str] = None,
                    wait_ms: int = 0, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    """렌더 후 DOM 동결. G2B·한전처럼 HTML 만으로는 값이 비는 사이트용."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(user_agent=UA)
        t0 = time.perf_counter()
        resp = page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=timeout * 1000)
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        inlined = _inline_form_values(page)
        fetch_ms = (time.perf_counter() - t0) * 1000
        html = page.content()
        png = page.screenshot(full_page=True)
        status = resp.status if resp else None
        ctype = resp.headers.get("content-type") if resp else None
        browser.close()

    # 렌더 결과는 항상 UTF-8 문자열이다. 원본 charset 은 meta 에 따로 남긴다.
    return {
        "body": html.encode("utf-8"),
        "png": png,
        "meta": {
            "url": url,
            "status": status,
            "method": "GET",
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "origin_content_type": ctype,
            "encoding": "utf-8",
            "fetch_ms": round(fetch_ms, 2),
            "mode": "rendered_dom",
            "inlined_form_values": inlined,
            "note": "렌더 후 DOM. 원본 바이트가 아니므로 ENCODING 실패모드 판정에는 쓰지 않는다.",
        },
    }


# ------------------------------------------------------------------ main
def capture_one(target: dict, target_id: str, task: str, case: dict, *,
                use_browser: bool, timeout: int, force: bool, dry_run: bool) -> str:
    """'ok' | 'skip' | 'fail'"""
    case_dir = FIXTURES / target_id / case["id"]
    nav = target.get("nav") or {}
    # url 이 없으면 nav(메뉴 진입) 경로로 캡처한다 — G2B·한전이 이 경우다.
    use_nav = not case.get("url") and bool(nav.get("entry"))

    if case_dir.joinpath("page.html").exists() and not force:
        print(f"    {case['id']:<20} skip (이미 동결됨)")
        return "skip"
    if not case.get("url") and not use_nav:
        print(f"    {case['id']:<20} SKIP url 도 nav 도 없음 - targets yaml 을 채워라")
        return "fail"
    if dry_run:
        where = case.get("url") or ("nav: " + str(nav.get("entry")))
        print(f"    {case['id']:<20} -> {where}")
        return "skip"
    try:
        if use_nav:
            got = capture_nav(nav, task=task, case=case, timeout=timeout)
        elif use_browser:
            got = capture_browser(case["url"],
                                  wait_ms=int(nav.get("wait_after_click_ms") or 0),
                                  timeout=timeout)
        else:
            got = capture_static(case["url"],
                                 method=(target.get("fetch") or {}).get("method", "GET"),
                                 timeout=timeout)
        meta = got["meta"]
        meta.update({"target_id": target_id, "case_id": case["id"], "task": task})
        _write(case_dir, got["body"], meta, got.get("png"))
        print(f"    {case['id']:<20} ok {meta['bytes']:>7}B enc={meta['encoding']:<7} "
              f"{meta['fetch_ms']:.0f}ms")
        return "ok"
    except Exception as e:                       # 한 건 실패로 전체를 멈추지 않는다
        print(f"    {case['id']:<20} FAIL {type(e).__name__}: {e}")
        return "fail"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="실사이트 1회 호출 -> fixtures 동결")
    ap.add_argument("--target", required=True)
    ap.add_argument("--task", choices=[*TASKS, "all"], default="all")
    ap.add_argument("--case", action="append", default=[], help="케이스 id 필터 (반복 가능)")
    ap.add_argument("--browser", action="store_true", help="playwright 렌더 후 동결")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--limit", type=int, help="태스크당 케이스 수 상한")
    ap.add_argument("--force", action="store_true", help="이미 있는 fixture 도 다시 받는다")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    target = load_target(args.target)
    tasks = TASKS if args.task == "all" else (args.task,)
    use_browser = args.browser or bool((target.get("fetch") or {}).get("needs_browser"))

    plan = {}
    for t in tasks:
        cases = target_cases(target, t)
        if args.case:
            cases = [c for c in cases if c["id"] in args.case]
        if args.limit:
            cases = cases[:args.limit]
        plan[t] = cases
    total = sum(len(v) for v in plan.values())
    if not total:
        print("케이스가 없다. targets yaml 의 cases.list / cases.detail 을 채워라.")
        return 2

    print(f"[capture] target={args.target} cases={total} "
          f"mode={'browser' if use_browser else 'static'} delay={args.delay}s")
    counts = {"ok": 0, "skip": 0, "fail": 0}
    done = 0
    for t, cases in plan.items():
        if not cases:
            continue
        print(f"  [{t}] {len(cases)}건")
        for c in cases:
            counts[capture_one(target, args.target, t, c, use_browser=use_browser,
                               timeout=args.timeout, force=args.force,
                               dry_run=args.dry_run)] += 1
            done += 1
            if done < total and not args.dry_run:
                time.sleep(args.delay)

    print(f"[capture] ok {counts['ok']} / skip {counts['skip']} / fail {counts['fail']}"
          f"  -> fixtures/{args.target}")
    print(f"[capture] 다음: python -m harness.gold_init --target {args.target}")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
