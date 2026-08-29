"""DEPRECATED: kept for backward compatibility only.

The offline teaching tests now live at ``backend/tests/test_teaching_offline.py``
and run under pytest (see pyproject.toml). This wrapper simply forwards to
pytest so legacy invocations keep working:

    cd backend && ../.venv/Scripts/python.exe -X utf8 scripts/test_teaching.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        import pytest  # noqa: PLC0415
    except ImportError:
        print(
            "pytest is required (pip install pytest pytest-timeout); "
            "or run backend/tests/test_teaching_offline.py functions manually.",
            file=sys.stderr,
        )
        return 2
    return int(
        pytest.main(
            [
                "-q",
                "--timeout=120",
                str(BACKEND_ROOT / "tests" / "test_teaching_offline.py"),
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
