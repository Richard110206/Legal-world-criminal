"""Request schemas for the REST API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel


class AuthRequest(BaseModel):
    email: str
    password: str


class RuntimeConfigRequest(BaseModel):
    prompt_profile: str
    api_key: str | None = None
    model_name: str
    api_base_url: str


class SandboxStartRequest(BaseModel):
    case_id: str


class PlayerDocumentAssistRequest(BaseModel):
    request_id: str | None = None
    case_id: str
    document_type: str
    skill_id: str
    player_prompt: str = ""
    player_draft: str = ""


class PlayerDocumentFollowupRequest(BaseModel):
    request_id: str
    message: str


class PlayerDocumentConfirmRequest(BaseModel):
    document_text: str


class PlayerDocumentManualConfirmRequest(BaseModel):
    request_id: str | None = None
    case_id: str
    document_type: str
    document_text: str


@dataclass(slots=True)
class _CaseLaunchRequest:
    case_id: str
    launch: Callable[[], Awaitable[bool | None]]
    post_launch_delay: float = 0.0
