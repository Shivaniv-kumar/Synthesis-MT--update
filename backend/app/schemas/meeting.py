"""Pydantic schemas for meeting endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class CreateMeetingRequest(BaseModel):
    title: str = Field(default="Untitled Meeting", min_length=1, max_length=512)
    source_type: Literal["paste", "file", "audio", "integration"] = "paste"
    occurred_at: Optional[datetime] = None
    attendees: list[str] = Field(default_factory=list)
    transcript_text: Optional[str] = None
    # Some frontend versions send 'transcript' instead of 'transcript_text'
    transcript: Optional[str] = None
    project_id: Optional[uuid.UUID] = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _coerce_occurred_at(cls, value: object) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            iso_value = cleaned.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(iso_value)
            except ValueError:
                pass
            for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
                try:
                    return datetime.combine(datetime.strptime(cleaned, date_format).date(), time.min)
                except ValueError:
                    continue
        return value

    @model_validator(mode="after")
    def _coerce_transcript(self) -> "CreateMeetingRequest":
        if self.transcript_text is None and self.transcript is not None:
            self.transcript_text = self.transcript
        return self


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
    created_by: Optional[uuid.UUID]
    created_at: datetime
    item_count: int = 0
    project_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class ExtractRequest(BaseModel):
    """Empty body — triggers extraction on the target meeting."""

    pass


class PaginatedMeetings(BaseModel):
    items: list[MeetingOut]
    total: int
    page: int
    size: int
