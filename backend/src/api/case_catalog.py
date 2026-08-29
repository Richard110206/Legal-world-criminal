"""Case catalogue: picker metadata, case documents, report transcripts."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from src.core.file_storage_manager import FileStorageManager
from src.core.models import Sandbox
from src.core.sandbox_manager import SandboxRuntimeContext
from src.data.data_loader import DataLoader
from src.player_lawyer.run_ledger import PlayerRunLedger
from src.utils.case_progress import infer_case_state_from_artifacts

from .app_state import CASE_PICKER_METADATA_PATH, _backend_dir

logger = logging.getLogger("ws_server")


_CASE_DOCUMENT_SPECS: tuple[dict[str, str], ...] = (
    # ── 刑事文书 ──
    {
        "document_key": "DS",
        "stage": "DS",
        "document_type": "defense_opinion",
        "title": "辩护词",
        "result_filename": "DS_result.json",
        "pdf_filename": "DS_document.pdf",
    },
    {
        "document_key": "CR",
        "stage": "CR",
        "document_type": "first_instance_criminal_judgment",
        "title": "一审刑事判决书",
        "result_filename": "CR_result.json",
        "pdf_filename": "CR_document.pdf",
    },
    {
        "document_key": "CRA",
        "stage": "CRA",
        "document_type": "second_instance_criminal_judgment",
        "title": "二审刑事判决书",
        "result_filename": "CRA_result.json",
        "pdf_filename": "CRA_document.pdf",
    },
)
_CASE_DOCUMENT_SPEC_BY_KEY = {
    spec["document_key"]: spec
    for spec in _CASE_DOCUMENT_SPECS
}
def _normalize_case_identifier(case_id: str | None) -> str:
    raw_value = str(case_id or "").strip()
    if not raw_value:
        return ""
    return raw_value if raw_value.startswith("case_") else f"case_{raw_value}"


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, dict) else {}


def _load_case_picker_metadata(path: Path | None = None) -> dict[str, dict[str, str]]:
    target_path = path or CASE_PICKER_METADATA_PATH
    if not target_path.exists():
        return {}

    try:
        with target_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except Exception as exc:
        logger.warning("Failed to load case picker metadata from %s: %s", target_path, exc)
        return {}

    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for case_id, raw_item in payload.items():
        if not isinstance(raw_item, dict):
            continue
        normalized_case_id = str(case_id or "").strip()
        if not normalized_case_id:
            continue
        normalized[normalized_case_id] = {
            "training_category": str(raw_item.get("training_category") or "").strip(),
            "difficulty": str(raw_item.get("difficulty") or "").strip(),
            "raw_case_cause": str(raw_item.get("raw_case_cause") or "").strip(),
        }
    return normalized


def _get_context_selected_case_id(context: SandboxRuntimeContext | None) -> str:
    if context is None:
        return ""

    selected_case_id = _normalize_case_identifier(getattr(context, "selected_case_id", ""))
    if selected_case_id:
        return selected_case_id

    checkpoint_mgr = getattr(context, "checkpoint_mgr", None)
    session_state = checkpoint_mgr.load_session_state() if checkpoint_mgr is not None and hasattr(checkpoint_mgr, "load_session_state") else None
    return _normalize_case_identifier((session_state or {}).get("selected_case_id"))


def _persist_context_case_selection(context: SandboxRuntimeContext) -> None:
    checkpoint_mgr = getattr(context, "checkpoint_mgr", None)
    session_state = getattr(checkpoint_mgr, "_session_state", None)
    if not isinstance(session_state, dict):
        return

    session_state["selected_case_id"] = getattr(context, "selected_case_id", "") or ""
    session_state["single_case_mode"] = bool(getattr(context, "single_case_mode", False))
    saver = getattr(checkpoint_mgr, "_save_session_state", None)
    if callable(saver):
        saver()


def _set_context_case_selection(context: SandboxRuntimeContext, case_id: str) -> None:
    context.selected_case_id = _normalize_case_identifier(case_id)
    context.single_case_mode = bool(context.selected_case_id)
    _persist_context_case_selection(context)


def _clear_context_case_selection(context: SandboxRuntimeContext) -> None:
    context.selected_case_id = ""
    context.single_case_mode = False
    _persist_context_case_selection(context)


def _resolve_case_progress_state(
    *,
    storage: FileStorageManager,
    case_id: str,
    plaintiff_config: dict[str, Any],
    defendant_config: dict[str, Any],
) -> str:
    try:
        case_runtime = storage.load_case_runtime(case_id)
    except FileNotFoundError:
        case_runtime = {}
    except Exception:
        case_runtime = {}

    overall_state = str(case_runtime.get("overall_state") or "").strip()
    if overall_state:
        return overall_state

    inferred_states: list[str] = []
    for config in (plaintiff_config, defendant_config):
        if not config:
            continue
        with contextlib.suppress(Exception):
            inferred_state = str(infer_case_state_from_artifacts(storage.base_dir, config) or "").strip()
            if inferred_state:
                inferred_states.append(inferred_state)

    config_states = [
        str(config.get("case_state") or "").strip()
        for config in (plaintiff_config, defendant_config)
        if isinstance(config, dict) and config
    ]

    for state in inferred_states + config_states:
        if state not in {"", "空闲", "已结案"}:
            return state

    all_states = [state for state in inferred_states + config_states if state]
    if "已结案" in config_states and all(state in {"空闲", "已结案"} for state in all_states):
        return "已结案"
    if all_states and all(state == "已结案" for state in all_states):
        return "已结案"
    return "空闲"


def _map_case_progress_to_picker_status(
    *,
    case_id: str,
    overall_state: str,
    runtime_status: dict[str, Any] | None,
    selected_case_id: str,
) -> str:
    runtime_state = str((runtime_status or {}).get("status") or "").strip().lower()
    if runtime_state in {"running", "paused", "error"} and case_id == selected_case_id:
        return "running"
    if overall_state == "已结案":
        return "closed"
    if overall_state and overall_state != "空闲":
        return "running"
    return "idle"


def _build_dataset_fallback_candidates(dataset_path: str) -> list[str]:
    current_path = str(dataset_path or "").strip()
    data_root = _backend_dir.parent / "data"
    if not data_root.exists():
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(candidate: Path) -> None:
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)

    if current_path:
        candidate_name = Path(current_path.replace("\\", "/")).name
        if candidate_name:
            same_name_candidate = data_root / candidate_name
            if same_name_candidate.exists():
                _add(same_name_candidate)

    for item in sorted(data_root.glob("*.json")):
        if item.is_file():
            _add(item)

    return candidates


def _resolve_case_picker_case_type(config: dict[str, Any], *, fallback_name: str = "") -> str:
    dataset_path = str(config.get("dataset_path") or "").strip()
    candidate_paths: list[str] = []
    seen: set[str] = set()

    def _add(path_value: str) -> None:
        normalized = str(path_value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidate_paths.append(normalized)

    _add(dataset_path)
    for fallback_path in _build_dataset_fallback_candidates(dataset_path):
        _add(fallback_path)

    if not candidate_paths:
        return ""


    for candidate_path in candidate_paths:
        try:
            data_loader = DataLoader(candidate_path)
            case = data_loader.resolve_case_for_config(config, fallback_name=fallback_name)
            case_type = str(data_loader.extract_case_cause(case) or "").strip()
            if case_type:
                return case_type
        except Exception as exc:
            logger.warning("Failed to resolve case picker case_type from %s: %s", candidate_path, exc)

    return ""


def _build_case_picker_entries(
    *,
    storage_root: Path,
    runtime_status: dict[str, Any] | None = None,
    selected_case_id: str = "",
    metadata_path: Path | None = None,
) -> list[dict[str, str]]:
    try:
        storage: Any = FileStorageManager(base_dir=storage_root)
    except TypeError:
        class _FallbackStorage:
            def __init__(self, base_dir: Path) -> None:
                self.base_dir = base_dir

            def load_case_runtime(self, case_id: str) -> dict[str, Any]:
                raise FileNotFoundError(case_id)

        storage = _FallbackStorage(storage_root)
    entries: list[dict[str, str]] = []
    metadata_by_case_id = _load_case_picker_metadata(metadata_path)

    for plaintiff_path in sorted(storage_root.glob("cases/case_*/plaintiff/config.yaml")):
        case_dir = plaintiff_path.parent.parent
        case_id = case_dir.name
        defendant_path = case_dir / "defendant" / "config.yaml"
        plaintiff_config = _load_yaml_mapping(plaintiff_path)
        defendant_config = _load_yaml_mapping(defendant_path)

        plaintiff_name = str((plaintiff_config.get("profile") or {}).get("name") or "").strip() or "委托人"
        defendant_name = str((defendant_config.get("profile") or {}).get("name") or "").strip() or "被告人"
        is_criminal_case = True
        raw_case_cause = str(
            plaintiff_config.get("case_type")
            or defendant_config.get("case_type")
            or ""
        ).strip()
        if not raw_case_cause:
            raw_case_cause = _resolve_case_picker_case_type(plaintiff_config, fallback_name=plaintiff_name)
        if not raw_case_cause:
            raw_case_cause = _resolve_case_picker_case_type(defendant_config, fallback_name=defendant_name)
        metadata = metadata_by_case_id.get(case_id, {})
        overall_state = _resolve_case_progress_state(
            storage=storage,
            case_id=case_id,
            plaintiff_config=plaintiff_config,
            defendant_config=defendant_config,
        )
        # 教学命名：优先用权威案由（指导性案例原名，如「严某聪以危险方法危害公共安全案」）
        source_title = str(
            plaintiff_config.get("source_title")
            or defendant_config.get("source_title")
            or metadata.get("source_title")
            or ""
        ).strip()
        specific_charge = _extract_specific_charge_from_title(source_title)
        if specific_charge:
            # 案名已含当事人名（如「严某聪以危险方法危害公共安全案」），教学标准命名直接采用
            title = specific_charge
        elif defendant_name != "被告人":
            title = f"被告人{defendant_name}涉嫌{raw_case_cause}案"
        else:
            title = f"{raw_case_cause}案"
        entries.append(
            {
                "case_id": case_id,
                "title": title,
                "plaintiff_name": plaintiff_name,
                "defendant_name": defendant_name,
                "case_category": "criminal",
                "raw_case_cause": str(metadata.get("raw_case_cause") or raw_case_cause or "").strip(),
                "training_category": str(metadata.get("training_category") or "").strip(),
                "difficulty": str(metadata.get("difficulty") or "").strip(),
                "status": _map_case_progress_to_picker_status(
                    case_id=case_id,
                    overall_state=overall_state,
                    runtime_status=runtime_status,
                    selected_case_id=selected_case_id,
                ),
            }
        )

    entries.sort(key=lambda entry: int(re.sub(r"^case_", "", entry["case_id"]) or "0"))
    return entries


def _extract_specific_charge_from_title(source_title: str) -> str:
    """从权威案名提取「当事人+具体行为+案」。

    输入形如「指导性案例268号：严某聪以危险方法危害公共安全案」，
    返回「严某聪以危险方法危害公共安全案」。
    脏数据（证据描述等非案名文本、超长、含句读）返回空串回退大类命名。
    """
    import re as _re

    raw = str(source_title or "").strip()
    if not raw or len(raw) > 60:
        return ""
    body = _re.sub(r"^指导性案例\d+号[：:]\s*", "", raw).strip()
    # 「主案名——副标题（案例评析）」取主案名
    if "——" in body:
        body = body.split("——", 1)[0].strip()
    # 案名应整体为「中文（可含、，）+案」结构，不含句号/分号/书名号等正文标点
    if not body.endswith("案") or _re.search(r"[。；：]|「」《》\d{4}年", body):
        return ""
    m = _re.fullmatch(r"[一-鿿A-Za-z0-9（）()、，,]{2,32}案", body)
    return body if m else ""


def _find_case_picker_entry(entries: list[dict[str, str]], case_id: str) -> dict[str, str] | None:
    normalized_case_id = _normalize_case_identifier(case_id)
    for entry in entries:
        if _normalize_case_identifier(entry.get("case_id")) == normalized_case_id:
            return entry
    return None


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
    except Exception as exc:
        logger.warning("Failed to load JSON from %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_windows_absolute_path(path_value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\\\/]", str(path_value or "").strip()))


def _resolve_case_output_dir_for_sandbox(sandbox: Sandbox, case_id: str) -> Path:
    storage_root = Path(sandbox.storage_root).resolve()
    return (storage_root / "output" / _normalize_case_identifier(case_id)).resolve()


def _resolve_case_document_pdf_path(
    *,
    case_output_dir: Path,
    raw_pdf_path: str,
    fallback_filename: str,
) -> Path | None:
    normalized_raw_pdf_path = str(raw_pdf_path or "").strip()
    if normalized_raw_pdf_path and not _is_windows_absolute_path(normalized_raw_pdf_path):
        candidate = Path(normalized_raw_pdf_path)
        candidate = candidate.resolve() if candidate.is_absolute() else (case_output_dir / candidate).resolve()
        if candidate.exists() and _is_subpath(candidate, case_output_dir):
            return candidate

    fallback_path = (case_output_dir / fallback_filename).resolve()
    if fallback_path.exists() and _is_subpath(fallback_path, case_output_dir):
        return fallback_path
    return None


def _require_sandbox_case_entry(sandbox: Sandbox, case_id: str) -> dict[str, str]:
    entries = _build_case_picker_entries(storage_root=Path(sandbox.storage_root))
    case_entry = _find_case_picker_entry(entries, case_id)
    if case_entry is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case_entry


def _build_case_document_entry(
    *,
    sandbox: Sandbox,
    case_id: str,
    spec: dict[str, str],
) -> tuple[dict[str, Any], Path | None]:
    normalized_case_id = _normalize_case_identifier(case_id)
    case_output_dir = _resolve_case_output_dir_for_sandbox(sandbox, normalized_case_id)
    result_payload = _load_json_mapping(case_output_dir / spec["result_filename"])
    drafted_payload = result_payload.get("drafted_document_payload") or {}
    raw_pdf_path = str(
        result_payload.get("pdf_path")
        or drafted_payload.get("pdf_path")
        or ""
    ).strip()
    resolved_pdf_path = _resolve_case_document_pdf_path(
        case_output_dir=case_output_dir,
        raw_pdf_path=raw_pdf_path,
        fallback_filename=spec["pdf_filename"],
    )
    entry = {
        "document_key": spec["document_key"],
        "stage": spec["stage"],
        "document_type": spec["document_type"],
        "title": spec["title"],
        "file_name": resolved_pdf_path.name if resolved_pdf_path is not None else spec["pdf_filename"],
        "available": resolved_pdf_path is not None,
        "download_url": (
            f"/api/sandbox/cases/{normalized_case_id}/documents/{spec['document_key']}/download"
            if resolved_pdf_path is not None
            else ""
        ),
    }
    return entry, resolved_pdf_path


def _list_case_document_entries(sandbox: Sandbox, case_id: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for spec in _CASE_DOCUMENT_SPECS:
        entry, _ = _build_case_document_entry(
            sandbox=sandbox,
            case_id=case_id,
            spec=spec,
        )
        entries.append(entry)
    return entries


def _resolve_case_document_download_path(
    *,
    sandbox: Sandbox,
    case_id: str,
    document_key: str,
) -> tuple[dict[str, str], Path]:
    spec = _CASE_DOCUMENT_SPEC_BY_KEY.get(str(document_key or "").strip().upper())
    if spec is None:
        raise HTTPException(status_code=404, detail="文书类型不存在")

    _, resolved_pdf_path = _build_case_document_entry(
        sandbox=sandbox,
        case_id=case_id,
        spec=spec,
    )
    if resolved_pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF 不存在")
    return spec, resolved_pdf_path


def _stage_for_player_document_type(document_type: str) -> str:
    mapping = {
        "defense_opinion": "DS",
    }
    normalized = str(document_type or "").strip()
    return mapping.get(normalized, normalized.upper())


def _record_player_document_confirmation_to_ledger(
    *,
    storage_root: str | Path,
    draft: Any,
    document_payload: dict[str, Any],
) -> None:

    stage = str(document_payload.get("scenario_type") or "").strip()
    if not stage:
        stage = _stage_for_player_document_type(getattr(draft, "document_type", ""))
    result_path = Path(storage_root) / "output" / draft.case_id / f"{stage}_result.json"
    PlayerRunLedger(storage_root=Path(storage_root)).record_document_confirmation(
        case_id=draft.case_id,
        request_id=draft.request_id,
        stage=stage,
        document_type=draft.document_type,
        document_text=draft.document_text,
        result_json_path=str(result_path),
        pdf_path=str(getattr(draft, "pdf_path", "") or document_payload.get("pdf_path", "") or ""),
        confirmed_at=str(getattr(draft, "confirmed_at", "") or ""),
    )


def _build_case_report_transcript(storage_root: Path, case_id: str) -> list[dict[str, Any]]:
    case_output = storage_root / "output" / case_id
    transcript: list[dict[str, Any]] = []
    for path in sorted(case_output.glob("*_result.json")):
        stage = path.stem.replace("_result", "")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        histories = []
        for key in ("dialog_history", "dialogue_history", "conversation", "dialogues"):
            value = payload.get(key)
            if isinstance(value, list):
                histories = value
                break
        for item in histories:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("message") or item.get("text") or "").strip()
            if not content:
                continue
            transcript.append({
                "stage": stage,
                "speaker": str(item.get("speaker") or item.get("role") or "").strip(),
                "content": content,
            })
    return transcript
def _build_player_document_case_context(sandbox: Sandbox, case_id: str) -> dict[str, Any]:
    normalized_case_id = _normalize_case_identifier(case_id)
    output_dir = _resolve_case_output_dir_for_sandbox(sandbox, normalized_case_id)
    context: dict[str, Any] = {"case_id": normalized_case_id}
    lc_path = output_dir / "PLC_result.json"
    if not lc_path.exists():
        lc_path = output_dir / "LC_result.json"
    if lc_path.exists():
        try:
            with lc_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            dialog_history = payload.get("dialog_history", [])
            if isinstance(dialog_history, list):
                lines = []
                for entry in dialog_history[-10:]:
                    if not isinstance(entry, dict):
                        continue
                    role = str(entry.get("role", "") or "").strip()
                    content = str(entry.get("content", "") or "").strip()
                    if content:
                        lines.append(f"{role}: {content}")
                if lines:
                    context["consultation_history"] = "\n".join(lines)
        except Exception as exc:
            logger.warning("[PlayerLawyer] Failed to read LC context for %s: %s", normalized_case_id, exc)
    return context
