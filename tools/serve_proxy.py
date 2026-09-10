"""LiteLLM 프록시 기동 래퍼.

    conda activate aicrawl
    python tools/serve_proxy.py            # 포트 4000

맨손으로 `litellm --config ...` 을 띄우면 매번 세 가지를 빠뜨린다.

  1. `PYTHONUTF8=1` — 없으면 한국어 윈도우에서 config 의 한글 주석을 cp949 로 읽다 죽는다.
  2. `.env` 로딩 — litellm CLI 가 항상 `.env` 를 읽어 주지는 않는다. 벤더 키가
     `os.environ` 에 **실제로** 들어 있어야 하는 코드 경로가 있다(예: `/v1/responses`
     브리지. 여기서 키를 못 찾아 500 이 났다 — 2026-09-09 실측).
  3. `AICRAWL_MAX_USD` — 안 주면 코드 기본값 $5 가 걸린다. 상한은 `harness/ledger.py`
     의 프로젝트 누적치와 비교되므로 프록시를 재시작해도 이어서 센다.

키는 **화면에 찍지 않는다.** 어떤 이름이 채워졌는지만 알린다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.dotenv import load_env          # noqa: E402


def main(argv: list[str]) -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONPATH", str(ROOT))
    n = load_env()
    names = [k for k in os.environ
             if k.endswith(("_API_KEY", "_MASTER_KEY")) and os.environ.get(k)]
    print(f"[proxy] .env 에서 {n}개 로드. 채워진 키: {sorted(names)}")

    from harness import ledger
    t = ledger.totals()
    print(f"[proxy] 누적 지출 ${t['usd']:.6f} / 상한 ${ledger.max_usd():.6g} "
          f"(토큰 ${t['llm_usd']:.6f} + 서비스 ${t['service_usd']:.6f})")

    port = argv[argv.index("--port") + 1] if "--port" in argv else "4000"
    args = ["litellm", "--config", str(ROOT / "harness" / "litellm_config.yaml"),
            "--port", str(port)] + [a for a in argv if a not in ("--port", port)]
    from litellm.proxy.proxy_cli import run_server
    sys.argv = args
    run_server(standalone_mode=False)        # click 커맨드
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
