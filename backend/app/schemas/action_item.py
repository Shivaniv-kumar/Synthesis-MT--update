"""Pydantic schemas for action-item endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


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
    reviewed_by: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # Denormalized for display — populated on list; null on single-item responses
    meeting_title: Optional[str] = None
    project_name: Optional[str] = None
    reviewed_by_name: Optional[str] = None

    model_config = {"from_attributes": True}


class UpdateItemRequest(BaseModel):
    status: Optional[Literal["Open", "In progress", "Done"]] = None
    owner_user_id: Optional[uuid.UUID] = None
    owner_label: Optional[str] = Field(default=None, max_length=255)
    priority: Optional[Literal["High", "Medium", "Low"]] = None
    due_date: Optional[date] = None
    due_text: Optional[str] = Field(default=None, max_length=255)
    task: Optional[str] = Field(default=None, min_length=1)
    context: Optional[str] = None
    needs_review: Optional[bool] = None


class DraftItem(BaseModel):
    """A single draft item submitted from the review screen."""

    meeting_id: uuid.UUID
    task: str = Field(..., min_length=1)
    owner_user_id: Optional[uuid.UUID] = None
    owner_label: str = ""
    priority: Literal["High", "Medium", "Low"] = "Medium"
    due_date: Optional[date] = None
    due_text: str = ""
    status: Literal["Open", "In progress", "Done"] = "Open"
    context: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_review: bool = True


class BulkSaveRequest(BaseModel):
    items: list[DraftItem] = Field(..., min_length=1)


class ItemFilters(BaseModel):
    status: Optional[Literal["Open", "In progress", "Done"]] = None
    priority: Optional[Literal["High", "Medium", "Low"]] = None
    owner_user_id: Optional[uuid.UUID] = None
    meeting_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    needs_review: Optional[bool] = None
    search: Optional[str] = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=200)
    # Accept page_size as alias for size (frontend sends page_size)
    page_size: Optional[int] = Field(default=None, ge=1, le=200)

    @property
    def effective_size(self) -> int:
        return self.page_size if self.page_size is not None else self.size


class PaginatedItems(BaseModel):
    items: list[ActionItemOut]
    total: int
    page: int
    size: int
    has_next: bool = False
    has_prev: bool = False
