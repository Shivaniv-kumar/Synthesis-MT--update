"""Projects API — CRUD for project folders.

GET    /api/projects/         — list active projects for the workspace
POST   /api/projects/         — create a new project
PATCH  /api/projects/{id}     — rename / recolour a project
DELETE /api/projects/{id}     — soft-archive a project (is_active=False)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.database import get_db
from app.models import User
from app.models.project import Project

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    is_active: Optional[bool] = None


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    color: Optional[str]
    is_active: bool
    created_at: datetime
    created_by: Optional[uuid.UUID]

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_project_or_404(
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession,
) -> Project:
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
            Project.workspace_id == workspace_id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProjectOut], include_in_schema=False)
@router.get("/", response_model=list[ProjectOut], summary="List projects for the workspace")
async def list_projects(
    include_archived: bool = False,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ProjectOut]:
    q = select(Project).where(
        Project.tenant_id == ctx.tenant_id,
        Project.workspace_id == ctx.workspace_id,
    )
    if not include_archived:
        q = q.where(Project.is_active.is_(True))
    q = q.order_by(Project.name)
    result = await db.execute(q)
    return [ProjectOut.model_validate(p) for p in result.scalars().all()]


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post(
    "/",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project folder",
)
async def create_project(
    body: ProjectCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ProjectOut:
    project = Project(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        created_by=ctx.user_id,
        name=body.name,
        description=body.description,
        color=body.color,
        is_active=True,
    )
    db.add(project)
    await db.flush()
    logger.info(
        "projects.created",
        project_id=str(project.id),
        name=project.name,
        tenant_id=str(ctx.tenant_id),
    )
    return ProjectOut.model_validate(project)


# ---------------------------------------------------------------------------
# PATCH /{project_id}
# ---------------------------------------------------------------------------


@router.patch("/{project_id}", response_model=ProjectOut, summary="Update a project")
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ProjectOut:
    project = await _get_project_or_404(project_id, ctx.tenant_id, ctx.workspace_id, db)

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.color is not None:
        project.color = body.color
    if body.is_active is not None:
        project.is_active = body.is_active

    await db.flush()
    return ProjectOut.model_validate(project)


# ---------------------------------------------------------------------------
# DELETE /{project_id}  (soft-archive — meetings are kept, project is hidden)
# ---------------------------------------------------------------------------


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a project (soft-delete)",
)
async def archive_project(
    project_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    project = await _get_project_or_404(project_id, ctx.tenant_id, ctx.workspace_id, db)
    project.is_active = False
    await db.flush()
    logger.info(
        "projects.archived",
        project_id=str(project_id),
        tenant_id=str(ctx.tenant_id),
    )
