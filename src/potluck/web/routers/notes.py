"""Notes router — CRUD for KnowledgeNote entities."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from starlette.responses import Response

from potluck.models.notes import KnowledgeNote
from potluck.web.dependencies import get_db

router = APIRouter(prefix="/notes", tags=["notes"])

NOTE_TYPES = ["knowledge", "task", "reminder", "observation"]


@router.get("", response_class=HTMLResponse)
async def notes_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query(default="", description="Filter notes"),
    page: int = Query(default=1, ge=1),
) -> Response:
    """Render notes list page."""
    per_page = 20

    stmt = select(KnowledgeNote).order_by(col(KnowledgeNote.updated_at).desc())

    if q.strip():
        like_q = f"%{q.strip()}%"
        stmt = stmt.where(col(KnowledgeNote.content).ilike(like_q))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    notes = list(result.scalars().all())

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/notes.html",
        {
            "active_page": "notes",
            "notes": notes,
            "total": total,
            "page": page,
            "per_page": per_page,
            "q": q,
            "note_types": NOTE_TYPES,
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_note(
    request: Request,
    db: AsyncSession = Depends(get_db),
    content: str = Form(...),
    note_type: str = Form(default="knowledge"),
) -> RedirectResponse:
    """Create a new KnowledgeNote."""
    note = KnowledgeNote(
        content=content.strip(),
        content_hash=hashlib.sha256(content.strip().encode()).hexdigest(),
        note_type=note_type if note_type in NOTE_TYPES else "knowledge",
        created_by="web",
    )
    db.add(note)
    await db.commit()
    return RedirectResponse(url="/notes", status_code=303)


@router.post("/{note_id}/edit", response_class=HTMLResponse)
async def edit_note(
    request: Request,
    note_id: UUID,
    db: AsyncSession = Depends(get_db),
    content: str = Form(...),
    note_type: str = Form(default=""),
) -> RedirectResponse:
    """Update a KnowledgeNote."""
    stmt = select(KnowledgeNote).where(col(KnowledgeNote.id) == note_id)
    result = await db.execute(stmt)
    note = result.scalar_one_or_none()

    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    note.content = content.strip()
    note.content_hash = hashlib.sha256(content.strip().encode()).hexdigest()
    if note_type and note_type in NOTE_TYPES:
        note.note_type = note_type
    db.add(note)
    await db.commit()

    return RedirectResponse(url="/notes", status_code=303)


@router.post("/{note_id}/delete")
async def delete_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Delete a KnowledgeNote."""
    stmt = select(KnowledgeNote).where(col(KnowledgeNote.id) == note_id)
    result = await db.execute(stmt)
    note = result.scalar_one_or_none()

    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    await db.delete(note)
    await db.commit()

    return RedirectResponse(url="/notes", status_code=303)
