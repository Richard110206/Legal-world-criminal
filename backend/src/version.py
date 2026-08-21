"""Backend release version metadata."""

from datetime import datetime
from zoneinfo import ZoneInfo

BACKEND_VERSION = "0.1.22"
# 启动时刻（实时）——每次进程启动刷新
BACKEND_VERSION_TIME = datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
    "%Y-%m-%d %H:%M CST"
)
BACKEND_VERSION_LABEL = f"v{BACKEND_VERSION} · {BACKEND_VERSION_TIME} 启动"
