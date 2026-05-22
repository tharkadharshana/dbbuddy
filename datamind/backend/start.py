"""
start.py — DataMind AI backend launcher

Reads all server config from .env so you never have to remember CLI flags.

Usage:
    cd datamind/backend
    python start.py              # development (1 worker, auto-reload)
    python start.py --prod       # production (N workers from .env, no reload)

All tunables live in .env:
    UVICORN_WORKERS   number of processes (default 1)
    UVICORN_HOST      bind host          (default 127.0.0.1)
    UVICORN_PORT      bind port          (default 8000)
"""

import os
import sys
import uvicorn
from dotenv import load_dotenv

load_dotenv()

def main():
    prod = "--prod" in sys.argv

    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    host    = os.getenv("UVICORN_HOST", "127.0.0.1")
    port    = int(os.getenv("UVICORN_PORT", "8000"))

    if prod:
        # Production: multiple workers, no reload, json logs
        print(f"Starting DataMind AI (production) — {workers} workers on {host}:{port}")
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            workers=workers,
            reload=False,
            log_level="warning",   # uvicorn access log suppressed — app logger handles it
            access_log=False,
        )
    else:
        # Development: single worker, hot-reload, pretty logs
        print(f"Starting DataMind AI (development) — 1 worker on {host}:{port} with reload")
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            workers=1,
            reload=True,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
