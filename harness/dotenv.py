"""`.env` 를 os.environ 에 얹는다. 외부 의존 없음.

키는 `.env` 에만 둔다(`.gitignore` 대상). 이 파일이 없으면 조용히 넘어간다 —
LLM 을 안 쓰는 도구는 키 없이도 돌아야 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: str | Path | None = None, *, override: bool = False) -> int:
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if override or k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def proxy() -> tuple[str, str]:
    """LiteLLM 프록시 주소와 키. 도구는 벤더 API 를 직접 치지 않는다 —
    직접 치면 그 비용이 리포트에서 통째로 사라진다(README §4-3)."""
    load_env()
    base = (os.environ.get("LITELLM_BASE_URL") or os.environ.get("OPENAI_API_BASE")
            or "http://127.0.0.1:4000")
    key = (os.environ.get("LITELLM_MASTER_KEY") or os.environ.get("OPENAI_API_KEY_PROXY") or "")
    if not key:
        raise RuntimeError(
            "LiteLLM 프록시 키가 없다. .env 에 LITELLM_MASTER_KEY 를 넣어라 "
            "(.env.example 참고). 벤더 키를 어댑터에 직접 주지 말 것.")
    return base.rstrip("/"), key
