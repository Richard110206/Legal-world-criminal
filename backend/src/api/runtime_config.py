"""Debug runtime configuration persistence (env file + process restart)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from fastapi import HTTPException

from . import app_state
from .schemas import RuntimeConfigRequest

logger = logging.getLogger("ws_server")


def _normalize_runtime_config(payload: RuntimeConfigRequest) -> dict[str, str]:
    prompt_profile = str(payload.prompt_profile or "").strip().lower()
    if prompt_profile not in {"test", "prod"}:
        raise HTTPException(status_code=400, detail="prompt_profile 只能是 test 或 prod")

    api_key = str(payload.api_key or "").strip() or str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    model_name = str(payload.model_name or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name 不能为空")

    api_base_url = str(payload.api_base_url or "").strip()
    if not api_base_url:
        raise HTTPException(status_code=400, detail="api_base_url 不能为空")

    return {
        "SIMLAW_PROMPT_PROFILE": prompt_profile,
        "OPENAI_API_KEY": api_key,
        "OPENAI_MODEL_NAME": model_name,
        "OPENAI_API_BASE_URL": api_base_url,
    }


def _mask_runtime_secret(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 7:
        return "*" * len(raw)
    return f"{raw[:3]}{'*' * (len(raw) - 7)}{raw[-4:]}"


def _read_runtime_config() -> dict[str, str]:
    api_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    return {
        "prompt_profile": str(os.getenv("SIMLAW_PROMPT_PROFILE", "prod") or "").strip().lower() or "prod",
        "has_api_key": bool(api_key),
        "api_key_masked": _mask_runtime_secret(api_key),
        "model_name": str(os.getenv("OPENAI_MODEL_NAME", "") or "").strip(),
        "api_base_url": str(os.getenv("OPENAI_API_BASE_URL", "") or "").strip(),
    }


def _write_runtime_config_to_env_file(env_path: Path, config: dict[str, str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated_keys: set[str] = set()
    rewritten_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rewritten_lines.append(line)
            continue

        key, _value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in config:
            rewritten_lines.append(f"{normalized_key}={config[normalized_key]}")
            updated_keys.add(normalized_key)
            continue
        rewritten_lines.append(line)

    for key in app_state.RUNTIME_CONFIG_KEYS:
        if key not in updated_keys:
            rewritten_lines.append(f"{key}={config[key]}")

    env_path.write_text("\n".join(rewritten_lines).rstrip() + "\n", encoding="utf-8")


def _apply_runtime_config(config: dict[str, str]) -> None:
    for key, value in config.items():
        os.environ[key] = value

    _write_runtime_config_to_env_file(app_state.ROOT_ENV_PATH, config)


def _restart_backend_process() -> None:
    logger.warning("Debug runtime config requested backend restart; terminating current process for container restart.")
    os.kill(os.getpid(), signal.SIGTERM)


def _schedule_backend_restart(delay_seconds: float = 0.35) -> None:
    asyncio.get_running_loop().call_later(delay_seconds, _restart_backend_process)
