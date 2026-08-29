"""SimLaw Town WebSocket server entrypoint (thin wrapper).

The implementation lives in :mod:`src.api` (see its module docstring for the
layout). This file keeps the historical launch contract:

    python -m uvicorn ws_server:app --host 127.0.0.1 --port 8000
"""

from src.api import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
