"""Pydantic schemas for authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    # M1: max_length=128 prevents bcrypt CPU-exhaustion DoS — bcrypt processes the
    # full input before truncating at 72 bytes, so an unbounded password allows
    # an attacker to peg a CPU core by submitting multi-megabyte passwords.
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: str
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
