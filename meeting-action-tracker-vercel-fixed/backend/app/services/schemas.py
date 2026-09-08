"""Shared Pydantic schemas used across services and API layers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ExtractedItemRaw(BaseModel):
    """Shape of a single action item returned by the Claude extraction call.

    Fields are validated strictly so bad model output is caught here rather
    than propagating into the database.  H9: use model_validate() at call sites.
    """

    task: str = Field(..., min_length=1)
    owner: str = Field(default="Unassigned")
    priority: str = Field(default="Medium")
    due: str = Field(default="")
    context: str = Field(default="")
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v: object) -> str:
        """Accept any casing; fall back to 'Medium' for unknown values."""
        if isinstance(v, str):
            title = v.strip().capitalize()
            if title in ("High", "Medium", "Low"):
                return title
        return "Medium"


class ActionItemOut(BaseModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    task: str
    owner_user_id: Optional[uuid.UUID]
    owner_label: str
    priority: Literal["High", "Medium", "Low"]
    due_date: Optional[date]
    due_text: str
    status: Literal["Open", "In progress", "Done"]
    context: str
    confidence: float
    needs_review: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    source_type: Literal["paste", "file", "audio", "integration"]
    occurred_at: Optional[datetime]
    attendees: list[str]
    transcript_ref: Optional[str]
    status: Literal["draft", "extracting", "extracted", "reviewed"]
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}
