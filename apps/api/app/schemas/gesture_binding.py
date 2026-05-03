"""Gesture binding schemas — validates gesture-to-action bindings sent from Luna client.

Mirrors the TypeScript Binding type used by `apps/luna-client`. Strong validation at the
API boundary: enum-restricted action kinds and poses, list cap of 100 bindings, payload
size cap enforced both here and at the DB level via the value_json CHECK constraint.
"""
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, conlist


class Pose(str, Enum):
    OPEN_PALM = "open_palm"  # also catches the "five" geometry — see pose.rs
    FIST = "fist"
    POINT = "point"
    PEACE = "peace"
    THREE = "three"
    FOUR = "four"
    THUMB_UP = "thumb_up"
    PINCH_POSE = "pinch_pose"
    ROTATION_POSE = "rotation_pose"
    CUSTOM = "custom"


class ActionKind(str, Enum):
    MEMORY_RECALL = "memory_recall"
    MEMORY_RECORD = "memory_record"
    MEMORY_CLEAR = "memory_clear"
    NAV_CHAT = "nav_chat"
    NAV_HUD = "nav_hud"
    NAV_COMMAND_PALETTE = "nav_command_palette"
    NAV_BINDINGS = "nav_bindings"
    AGENT_NEXT = "agent_next"
    AGENT_PREV = "agent_prev"
    AGENT_OPEN = "agent_open"
    WORKFLOW_RUN = "workflow_run"
    WORKFLOW_PAUSE = "workflow_pause"
    WORKFLOW_DISMISS = "workflow_dismiss"
    APPROVE = "approve"
    DISMISS = "dismiss"
    MIC_TOGGLE = "mic_toggle"
    PTT_START = "ptt_start"
    PTT_STOP = "ptt_stop"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    SCROLL_LEFT = "scroll_left"
    SCROLL_RIGHT = "scroll_right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    CURSOR_MOVE = "cursor_move"
    CLICK = "click"
    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    CUSTOM = "custom"


class MotionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["swipe", "pinch", "rotate", "tap", "none"]
    direction: Optional[Literal["up", "down", "left", "right", "in", "out", "cw", "ccw"]] = None


class GestureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pose: Pose
    motion: Optional[MotionSpec] = None
    modifier_pose: Optional[Pose] = None


class ActionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ActionKind
    params: Optional[dict] = None


class Binding(BaseModel):
    id: str = Field(..., max_length=64, min_length=1)
    gesture: GestureSpec
    action: ActionSpec
    scope: Literal["global", "luna_only", "hud_only", "chat_only"]
    enabled: bool = True
    user_recorded: bool = False


class BindingsPayload(BaseModel):
    bindings: conlist(Binding, max_length=100)


class BindingsResponse(BaseModel):
    bindings: List[Binding]
    updated_at: Optional[str] = None
