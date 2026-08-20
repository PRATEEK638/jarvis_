"""Launch the local control interface: `python -m jarvis.web`."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("JARVIS_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_WEB_PORT", "8731"))
    print(f"JARVIS control interface: http://{host}:{port}")
    uvicorn.run("jarvis.web.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
