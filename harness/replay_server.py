"""동결된 fixtures 를 로컬 HTTP 로 서빙 (README §4-2).

원본 응답의 Content-Type(=charset)까지 그대로 재현한다. 이게 어긋나면
EUC-KR 사이트에서 어느 도구가 깨지는지를 볼 수 없어 벤치마크 목적 자체가 무너진다.

    python -m harness.replay_server --port 8899
    → http://127.0.0.1:8899/f/<target_id>/<case_id>          (page.html 원본 바이트)
      http://127.0.0.1:8899/f/<target_id>/<case_id>/page.png (스크린샷, 있으면)
      http://127.0.0.1:8899/index.json                        (케이스 목록)
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

# 원본에서 그대로 재현할 헤더. 나머지(Set-Cookie, Date 등)는 재현하지 않는다.
REPLAY_HEADERS = ("content-type", "content-language", "x-ua-compatible")


def _read_meta(case_dir: Path) -> dict[str, Any]:
    meta_path = case_dir / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def case_url(case_dir_rel: str, host: str = "127.0.0.1", port: int = 8899) -> str:
    return f"http://{host}:{port}/f/{case_dir_rel}"


def iter_cases(root: Path = FIXTURES) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for page in sorted(root.glob("*/*/page.html")):
        case_dir = page.parent
        meta = _read_meta(case_dir)
        out.append({
            "target_id": case_dir.parent.name,
            "case_id": case_dir.name,
            "task": meta.get("task", "detail"),      # 채점 스키마(list/detail)를 고른다
            "path": f"{case_dir.parent.name}/{case_dir.name}",
            "origin_url": meta.get("url"),
            "encoding": meta.get("encoding"),
            "mode": meta.get("mode"),
            "bytes": page.stat().st_size,
        })
    return out


class ReplayHandler(BaseHTTPRequestHandler):
    server_version = "aicrawl-replay/1.0"
    root: Path = FIXTURES
    quiet: bool = True

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if not self.quiet:
            super().log_message(fmt, *args)

    # ---- 라우팅 -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/"):
            return self._json({"cases": len(iter_cases(self.root)), "index": "/index.json"})
        if path == "/index.json":
            return self._json(iter_cases(self.root))
        if path.startswith("/f/"):
            return self._serve_fixture(path[len("/f/"):])
        self.send_error(404, "not a fixture path")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # ---- 본체 ---------------------------------------------------------
    def _serve_fixture(self, rel: str) -> None:
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if len(parts) < 2:
            return self.send_error(404, "expected /f/<target_id>/<case_id>")
        case_dir = (self.root / parts[0] / parts[1]).resolve()
        if not str(case_dir).startswith(str(self.root.resolve())):
            return self.send_error(403, "path escape")
        if not case_dir.is_dir():
            return self.send_error(404, f"no fixture: {parts[0]}/{parts[1]}")

        asset = parts[2] if len(parts) > 2 else "page.html"
        target = case_dir / asset
        if not target.is_file():
            return self.send_error(404, f"no asset: {asset}")

        body = target.read_bytes()           # 디코딩하지 않는다. 바이트 그대로.
        meta = _read_meta(case_dir)
        headers = {k.lower(): v for k, v in (meta.get("headers") or {}).items()}

        if asset == "page.html":
            ctype = headers.get("content-type")
            if not ctype:
                enc = meta.get("encoding") or "utf-8"
                ctype = f"text/html; charset={enc}"
        elif asset.endswith(".png"):
            ctype = "image/png"
        elif asset.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        else:
            ctype = "application/octet-stream"

        self.send_response(int(meta.get("status") or 200))
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for h in REPLAY_HEADERS:
            if h != "content-type" and h in headers:
                self.send_header(h.title(), headers[h])
        # 도구 내부 캐시가 비교를 오염시키지 않도록
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class ReplayServer:
    """테스트/러너에서 with 로 쓰는 백그라운드 서버."""

    def __init__(self, root: Path = FIXTURES, host: str = "127.0.0.1", port: int = 8899,
                 quiet: bool = True, url_host: Optional[str] = None) -> None:
        """`url_host` 는 **도구가 볼 주소**다(바인드 주소와 다를 수 있다).

        컨테이너 안에서 도는 도구(셀프호스팅 Firecrawl)는 호스트의 `127.0.0.1` 을
        자기 자신으로 해석한다. 그런 도구에는 `host="0.0.0.0"` 으로 열고
        `url_host="host.docker.internal"` 을 준다. **바인드를 넓히는 것과 도구에 알려 줄
        주소는 별개**라 인자를 둘로 나눈다 — 하나로 묶으면 리플레이 서버가 LAN 에 열린 채
        127.0.0.1 을 광고하거나 그 반대가 된다.
        """
        handler = type("BoundReplayHandler", (ReplayHandler,), {"root": Path(root), "quiet": quiet})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.host, self.port = host, self.httpd.server_address[1]
        self.url_host = url_host or ("127.0.0.1" if host in ("0.0.0.0", "") else host)
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.url_host}:{self.port}"

    def url_for(self, target_id: str, case_id: str) -> str:
        return f"{self.base_url}/f/{target_id}/{case_id}"

    def __enter__(self) -> "ReplayServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="fixtures 리플레이 서버")
    ap.add_argument("--root", default=str(FIXTURES))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    handler = type("BoundReplayHandler", (ReplayHandler,),
                   {"root": Path(args.root), "quiet": not args.verbose})
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    cases = iter_cases(Path(args.root))
    print(f"[replay] {args.host}:{args.port} root={args.root} cases={len(cases)}")
    if not cases:
        print("[replay] 경고: fixtures 가 비어 있다. harness/capture.py 를 먼저 돌려라.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[replay] stop")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
