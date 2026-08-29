"""Sandbox endpoints: case catalogue, documents, player-lawyer drafting."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from src.core.models import User
from src.player_lawyer.closing_summary import PlayerClosingSummaryService
from src.player_lawyer.document_assist import PlayerDocumentAssistService
from src.player_lawyer.run_ledger import PlayerRunLedger

from . import app_state
from .agent_status import (
    _build_sandbox_runtime_status,
    _get_sandbox_manager,
    _sandbox_context_needs_rebuild,
    _serialize_sandbox_state,
)
from .case_catalog import (
    _build_case_picker_entries,
    _build_case_report_transcript,
    _build_player_document_case_context,
    _find_case_picker_entry,
    _get_context_selected_case_id,
    _list_case_document_entries,
    _normalize_case_identifier,
    _record_player_document_confirmation_to_ledger,
    _require_sandbox_case_entry,
    _resolve_case_document_download_path,
)
from .deps import (
    _db_session_dependency,
    _get_current_user,
    _require_user_sandbox,
    _resolve_sandbox_context_by_id,
    _update_sandbox_from_runtime_status,
)
from .player_gateway_admin import (
    _ensure_player_lawyer_runtime,
    _is_player_defendant_mode,
    _publish_player_document_completion_if_unmanaged,
    reset_player_gateway,
)
from .schemas import (
    PlayerDocumentAssistRequest,
    PlayerDocumentConfirmRequest,
    PlayerDocumentFollowupRequest,
    PlayerDocumentManualConfirmRequest,
)
from .simulation_runtime import (
    _broadcast_sandbox_event,
    _cancel_sandbox_simulation_task,
    _close_sandbox_realtime_clients,
    _reset_closed_case_for_restart,
    _reset_runtime_engine_state,
)

logger = logging.getLogger("ws_server")
router = APIRouter(tags=["sandbox"])


@router.get("/api/sandbox")
async def get_current_sandbox(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = app_state.sandbox_service.get_user_sandbox(session=session, user_id=current_user.id)
    if sandbox is None:
        return _serialize_sandbox_state(None)

    runtime_status = _get_sandbox_manager().get_status(sandbox)
    return _serialize_sandbox_state(sandbox, runtime_status=runtime_status)


@router.get("/api/sandbox/cases")
async def get_sandbox_cases(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    context = _resolve_sandbox_context_by_id(sandbox.id)
    runtime_status = _build_sandbox_runtime_status(context) if context is not None else None
    selected_case_id = _get_context_selected_case_id(context)
    entries = _build_case_picker_entries(
        storage_root=Path(sandbox.storage_root),
        runtime_status=runtime_status,
        selected_case_id=selected_case_id,
    )
    return {"cases": entries}


@router.get("/api/sandbox/cases/{case_id}/documents")
async def get_sandbox_case_documents(
    case_id: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    _require_sandbox_case_entry(sandbox, normalized_case_id)
    return {
        "case_id": normalized_case_id,
        "documents": _list_case_document_entries(sandbox, normalized_case_id),
    }


@router.get("/api/sandbox/cases/{case_id}/documents/{document_key}/download")
async def download_sandbox_case_document(
    case_id: str,
    document_key: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    _require_sandbox_case_entry(sandbox, normalized_case_id)
    _, resolved_pdf_path = _resolve_case_document_download_path(
        sandbox=sandbox,
        case_id=normalized_case_id,
        document_key=document_key,
    )
    return FileResponse(
        resolved_pdf_path,
        media_type="application/pdf",
        filename=resolved_pdf_path.name,
    )


@router.get("/api/sandbox/cases/{case_id}/closing-summary")
async def get_sandbox_case_closing_summary(
    case_id: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    case_entry = _require_sandbox_case_entry(sandbox, normalized_case_id)
    documents = _list_case_document_entries(sandbox, normalized_case_id)
    service = PlayerClosingSummaryService(storage_root=Path(sandbox.storage_root))
    return service.build_summary(
        case_id=normalized_case_id,
        case_entry=case_entry,
        documents=documents,
    )


@router.post("/api/sandbox/cases/{case_id}/closing-evaluation")
async def create_sandbox_case_closing_evaluation(
    case_id: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    case_entry = _require_sandbox_case_entry(sandbox, normalized_case_id)
    documents = _list_case_document_entries(sandbox, normalized_case_id)
    service = PlayerClosingSummaryService(storage_root=Path(sandbox.storage_root))
    try:
        evaluation = await asyncio.to_thread(
            service.generate_evaluation,
            case_id=normalized_case_id,
            case_entry=case_entry,
            documents=documents,
        )
    except Exception as exc:
        logger.warning("[ClosingEvaluation] Failed for %s: %s", normalized_case_id, exc, exc_info=True)
        raise HTTPException(status_code=502, detail="结案评价生成失败，请稍后重试") from exc
    return {"success": True, "evaluation": evaluation}


@router.get("/api/sandbox/cases/{case_id}/player-run-ledger")
async def get_sandbox_case_player_run_ledger(
    case_id: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    _require_sandbox_case_entry(sandbox, normalized_case_id)
    return PlayerRunLedger(storage_root=Path(sandbox.storage_root)).load(normalized_case_id)


@router.get("/api/sandbox/cases/{case_id}/player-run-report.md")
async def download_sandbox_case_player_run_report(
    case_id: str,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    from src.player_lawyer.run_ledger import PlayerRunLedger

    sandbox = _require_user_sandbox(session, current_user)
    normalized_case_id = _normalize_case_identifier(case_id)
    case_entry = _require_sandbox_case_entry(sandbox, normalized_case_id)
    documents = _list_case_document_entries(sandbox, normalized_case_id)
    ledger = PlayerRunLedger(storage_root=Path(sandbox.storage_root))
    markdown = ledger.build_markdown_report(
        case_id=normalized_case_id,
        case_entry=case_entry,
        documents=documents,
        transcript=_build_case_report_transcript(Path(sandbox.storage_root), normalized_case_id),
        evaluation=ledger.load(normalized_case_id).get("evaluation"),
    )
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{normalized_case_id}-player-run-report.md"',
        },
    )


@router.get("/api/sandbox/player-lawyer/document-skills")
async def get_player_lawyer_document_skills(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    _ensure_player_lawyer_runtime(sandbox)
    service = PlayerDocumentAssistService(storage_root=Path(sandbox.storage_root))
    return {"skills": service.list_skills()}


@router.post("/api/sandbox/player-lawyer/document-followup")
async def create_player_lawyer_document_followup(
    payload: PlayerDocumentFollowupRequest,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    if not _is_player_defendant_mode():
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")
    message = str(payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    context, gateway = _ensure_player_lawyer_runtime(sandbox)
    req = gateway.get_request(payload.request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Unknown request_id: {payload.request_id}")
    if str(req.status.value if hasattr(req.status, "value") else req.status) != "pending":
        raise HTTPException(status_code=409, detail="当前文书任务已经结束，不能继续追问")
    if str(req.stage or "").upper() not in {"CD", "DD", "AD", "AR"}:
        raise HTTPException(status_code=409, detail="当前任务不是可追问的文书阶段")

    orchestrator = getattr(context, "orchestrator", None)
    followup_handler = getattr(orchestrator, "handle_player_document_followup", None)
    if not callable(followup_handler):
        raise HTTPException(status_code=409, detail="当前文书任务没有可追问的当事人会话")
    try:
        followup = await followup_handler(request_id=payload.request_id, message=message, request=req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


    PlayerRunLedger(storage_root=Path(sandbox.storage_root)).record_followup(
        case_id=req.case_id,
        request_id=req.request_id,
        stage=req.stage,
        question=followup.get("question", message),
        answer=followup.get("answer", ""),
    )
    return {
        "success": True,
        "request": req.to_dict(),
        "question": followup.get("question", message),
        "answer": followup.get("answer", ""),
    }


@router.post("/api/sandbox/player-lawyer/document-assist")
async def create_player_lawyer_document_draft(
    payload: PlayerDocumentAssistRequest,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    if not _is_player_defendant_mode():
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")
    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    _ensure_player_lawyer_runtime(sandbox)
    normalized_case_id = _normalize_case_identifier(payload.case_id)
    _require_sandbox_case_entry(sandbox, normalized_case_id)

    service = PlayerDocumentAssistService(storage_root=Path(sandbox.storage_root))
    try:
        draft = service.create_draft(
            sandbox_id=int(sandbox.id) if str(sandbox.id).isdigit() else 0,
            request_id=str(payload.request_id or ""),
            case_id=normalized_case_id,
            document_type=payload.document_type,
            skill_id=payload.skill_id,
            player_prompt=payload.player_prompt,
            player_draft=payload.player_draft,
            case_context=_build_player_document_case_context(sandbox, normalized_case_id),
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await _broadcast_sandbox_event(
        str(sandbox.id),
        {
            "type": "player_lawyer_document_draft_ready",
            "event": "player_lawyer_document_draft_ready",
            "data": draft.to_dict(),
        },
    )
    return {"success": True, "draft": draft.to_dict()}


@router.post("/api/sandbox/player-lawyer/documents/{draft_id}/confirm")
async def confirm_player_lawyer_document_draft(
    draft_id: str,
    payload: PlayerDocumentConfirmRequest,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    if not _is_player_defendant_mode():
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")
    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    _context, gateway = _ensure_player_lawyer_runtime(sandbox)
    service = PlayerDocumentAssistService(storage_root=Path(sandbox.storage_root))
    try:
        draft, document_payload = service.confirm_draft(
            draft_id=draft_id,
            document_text=payload.document_text,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_player_document_confirmation_to_ledger(
        storage_root=sandbox.storage_root,
        draft=draft,
        document_payload=document_payload,
    )
    if draft.request_id:
        try:
            req = gateway.resolve(draft.request_id, draft.document_text)
        except (ValueError, RuntimeError):
            req = None
        if req is not None:
            await _broadcast_sandbox_event(
                str(sandbox.id),
                {
                    "type": "player_lawyer_input_submitted",
                    "event": "player_lawyer_input_submitted",
                    "data": req.to_dict(),
                },
            )

    await _broadcast_sandbox_event(
        str(sandbox.id),
        {
            "type": "player_lawyer_document_confirmed",
            "event": "player_lawyer_document_confirmed",
            "data": {
                "draft": draft.to_dict(),
                "document_payload": document_payload,
            },
        },
    )
    asyncio.create_task(_publish_player_document_completion_if_unmanaged(_context, draft))
    return {
        "success": True,
        "draft": draft.to_dict(),
        "document_payload": document_payload,
    }


@router.post("/api/sandbox/player-lawyer/documents/confirm-manual")
async def confirm_player_lawyer_manual_document(
    payload: PlayerDocumentManualConfirmRequest,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):

    if not _is_player_defendant_mode():
        raise HTTPException(status_code=403, detail="Player-lawyer mode is not enabled")
    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    _context, gateway = _ensure_player_lawyer_runtime(sandbox)
    normalized_case_id = _normalize_case_identifier(payload.case_id)
    _require_sandbox_case_entry(sandbox, normalized_case_id)

    service = PlayerDocumentAssistService(storage_root=Path(sandbox.storage_root))
    try:
        draft, document_payload = service.confirm_manual_document(
            sandbox_id=int(sandbox.id) if str(sandbox.id).isdigit() else 0,
            request_id=str(payload.request_id or ""),
            case_id=normalized_case_id,
            document_type=payload.document_type,
            document_text=payload.document_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_player_document_confirmation_to_ledger(
        storage_root=sandbox.storage_root,
        draft=draft,
        document_payload=document_payload,
    )
    if draft.request_id:
        try:
            req = gateway.resolve(draft.request_id, draft.document_text)
        except (ValueError, RuntimeError):
            req = None
        if req is not None:
            await _broadcast_sandbox_event(
                str(sandbox.id),
                {
                    "type": "player_lawyer_input_submitted",
                    "event": "player_lawyer_input_submitted",
                    "data": req.to_dict(),
                },
            )

    await _broadcast_sandbox_event(
        str(sandbox.id),
        {
            "type": "player_lawyer_document_confirmed",
            "event": "player_lawyer_document_confirmed",
            "data": {
                "draft": draft.to_dict(),
                "document_payload": document_payload,
            },
        },
    )
    asyncio.create_task(_publish_player_document_completion_if_unmanaged(_context, draft))
    return {
        "success": True,
        "draft": draft.to_dict(),
        "document_payload": document_payload,
    }


@router.post("/api/sandbox/start")
async def start_current_sandbox(
    payload: dict[str, Any] | None = None,
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    normalized_case_id = _normalize_case_identifier((payload or {}).get("case_id"))
    if not normalized_case_id:
        raise HTTPException(status_code=400, detail="缺少 case_id")

    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    context = _resolve_sandbox_context_by_id(sandbox.id)
    runtime_status = _build_sandbox_runtime_status(context) if context is not None else None
    selected_case_id = _get_context_selected_case_id(context)
    entries = _build_case_picker_entries(
        storage_root=Path(sandbox.storage_root),
        runtime_status=runtime_status,
        selected_case_id=selected_case_id,
    )
    case_entry = _find_case_picker_entry(entries, normalized_case_id)
    if case_entry is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    if case_entry["status"] == "closed":
        _reset_closed_case_for_restart(Path(sandbox.storage_root), normalized_case_id)

    manager = _get_sandbox_manager()
    context = manager.get_or_create_context(sandbox)
    current_runtime_status = _build_sandbox_runtime_status(context)
    current_selected_case_id = _get_context_selected_case_id(context)
    if current_runtime_status["status"] == "error":
        raise HTTPException(status_code=409, detail="当前模拟处于运行异常，请先重新开始")
    if current_runtime_status["status"] in {"running", "paused"} and current_selected_case_id and current_selected_case_id != normalized_case_id:
        raise HTTPException(status_code=409, detail="当前已有其他案件在运行，请先等待其结束或重新开始")

    # 教学平台语义：一次玩完一次——「开始」永远全新开局。
    # 存在未完结旧会话（running/paused checkpoint）时，先做 restart 同款清理，
    # 不从上一次进度续跑。
    if current_runtime_status["status"] in {"running", "paused"}:
        reset_player_gateway(sandbox.id)
        cancelled = await _cancel_sandbox_simulation_task(context)
        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail="上局尚有模型调用未退出，请等待几秒后再开始",
            )
        _reset_runtime_engine_state(getattr(context, "engine", None))
        await _close_sandbox_realtime_clients(context)
        app_state.sandbox_service.reset_sandbox_storage(Path(sandbox.storage_root))
        manager.reset_context(sandbox)

    if _sandbox_context_needs_rebuild(context, Path(sandbox.storage_root)):
        await _close_sandbox_realtime_clients(context)
        manager.reset_context(sandbox)
    runtime_status = manager.start_sandbox(sandbox, payload={"case_id": normalized_case_id})
    _update_sandbox_from_runtime_status(session, sandbox, runtime_status)
    return {"success": True, "sandbox": _serialize_sandbox_state(sandbox, runtime_status=runtime_status)}


@router.post("/api/sandbox/ensure")
async def ensure_current_sandbox(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = app_state.sandbox_service.get_or_create_user_sandbox(session=session, user_id=current_user.id)
    runtime_status = None
    if _resolve_sandbox_context_by_id(sandbox.id) is not None:
        runtime_status = _get_sandbox_manager().get_status(sandbox)
    return {"success": True, "sandbox": _serialize_sandbox_state(sandbox, runtime_status=runtime_status)}


@router.post("/api/sandbox/pause")
async def pause_current_sandbox(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = _require_user_sandbox(session, current_user)
    runtime_status = _get_sandbox_manager().pause_sandbox(sandbox)
    _update_sandbox_from_runtime_status(session, sandbox, runtime_status)
    return {"success": True, "sandbox": _serialize_sandbox_state(sandbox, runtime_status=runtime_status)}


@router.post("/api/sandbox/restart")
async def restart_current_sandbox(
    current_user: User = Depends(_get_current_user),
    session=Depends(_db_session_dependency),
):
    sandbox = _require_user_sandbox(session, current_user)
    manager = _get_sandbox_manager()
    context = manager.get_or_create_context(sandbox)
    reset_player_gateway(sandbox.id)
    cancelled = await _cancel_sandbox_simulation_task(context)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail="当前仍有模型调用未退出，请先暂停并等待几秒后再重跑",
        )

    _reset_runtime_engine_state(getattr(context, "engine", None))
    await _close_sandbox_realtime_clients(context)
    app_state.sandbox_service.reset_sandbox_storage(Path(sandbox.storage_root))
    manager.reset_context(sandbox)
    runtime_status = {
        "status": "idle",
        "paused": False,
        "simulation_running": False,
        "clients_connected": 0,
        "active_cases": 0,
    }
    _update_sandbox_from_runtime_status(session, sandbox, runtime_status)
    return {
        "success": True,
        "reload_required": True,
        "sandbox": _serialize_sandbox_state(sandbox, runtime_status=runtime_status),
    }
