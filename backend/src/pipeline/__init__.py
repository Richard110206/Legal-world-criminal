"""Stage tool manifest resolution for criminal simulation."""

from .stage_tool_registry import (
    create_registered_stage_tool,
    get_registered_stage_tool_ids,
    is_registered_stage_tool,
)
from .stage_tool_resolver import (
    apply_stage_tool_permissions,
    build_agent_default_tools,
    describe_stage_tool_matrix,
    infer_stage_role_name,
    load_stage_tool_manifest,
    resolve_agent_type,
    resolve_configured_tool_names,
)

__all__ = [
    "apply_stage_tool_permissions",
    "build_agent_default_tools",
    "create_registered_stage_tool",
    "describe_stage_tool_matrix",
    "get_registered_stage_tool_ids",
    "infer_stage_role_name",
    "is_registered_stage_tool",
    "load_stage_tool_manifest",
    "resolve_agent_type",
    "resolve_configured_tool_names",
]
