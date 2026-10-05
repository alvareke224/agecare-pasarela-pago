"""Sección 9 — Curación de contenido."""
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app import models
from app.audit import audit
from app.deps import Db, require
from app.enums import ContentStatus, ContentType
from app.errors import conflict, invalid, not_found
from app.schemas.common import Page
from app.schemas.operation import ContentCreateIn, ContentItemOut, ContentPatchIn
from app.security import now_utc

router = APIRouter(prefix="/content", tags=["Curación de contenido"])


def _out(i: models.ContentItem) -> ContentItemOut:
    return ContentItemOut(id=i.id, type=ContentType(i.type), title=i.title, body=i.body,
                          audio_available=i.tts_ready, tags=i.tags or [],
                          status=ContentStatus(i.status), publish_at=i.publish_at,
                          published_at=i.published_at, created_by_name=i.created_by_name,
                          updated_at=i.updated_at)


async def _get(db, item_id: UUID) -> models.ContentItem:
    item = await db.get(models.ContentItem, item_id)
    if item is None or item.deleted_at is not None:
        raise not_found()
    return item


# ---------- 9.1 Listar ----------
@router.get("/items", response_model=Page[ContentItemOut], dependencies=[require("content")])
async def list_items(db: Db,
                     type_f: ContentType | None = Query(default=None, alias="type"),
                     status_f: ContentStatus | None = Query(default=None, alias="status"),
                     q: str | None = Query(default=None, max_length=120),
                     page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.ContentItem).where(models.ContentItem.deleted_at.is_(None))
    if type_f:
        stmt = stmt.where(models.ContentItem.type == type_f)
    if status_f:
        stmt = stmt.where(models.ContentItem.status == status_f)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.ContentItem.title.ilike(like) | models.ContentItem.body.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.ContentItem.updated_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[_out(i) for i in rows], page=page, page_size=page_size, total=total)


# ---------- 9.2 Crear ----------
@router.post("/items", response_model=ContentItemOut, status_code=201)
async def create_item(body: ContentCreateIn, request: Request, db: Db,
                      admin: models.AdminUser = require("content", write=True)):
    if body.publish_at is not None and body.publish_at <= now_utc():
        raise invalid("PUBLISH_AT_PAST", "La fecha de publicación programada debe ser futura.")
    item = models.ContentItem(type=body.type, title=body.title, body=body.body,
                              tags=body.tags, publish_at=body.publish_at,
                              created_by=admin.id, created_by_name=admin.full_name,
                              tts_ready=True)  # demo: la cola TTS real marcaría esto al terminar
    db.add(item)
    await db.flush()
    await db.refresh(item)
    await audit(db, request, "content.create", "content_item", item.id, after={"title": item.title})
    return _out(item)


# ---------- 9.3 Editar ----------
@router.patch("/items/{item_id}", response_model=ContentItemOut)
async def patch_item(item_id: UUID, body: ContentPatchIn, request: Request, db: Db,
                     admin: models.AdminUser = require("content", write=True)):
    item = await _get(db, item_id)
    before = {"title": item.title}
    if body.publish_at is not None and body.publish_at <= now_utc():
        raise invalid("PUBLISH_AT_PAST", "La fecha de publicación programada debe ser futura.")
    if body.body is not None and body.body != item.body:
        item.body = body.body
        item.tts_ready = True  # demo: en producción se re-encolaría la generación TTS
    if body.title is not None:
        item.title = body.title
    if body.tags is not None:
        item.tags = body.tags
    if body.publish_at is not None:
        item.publish_at = body.publish_at
    await db.flush()
    await db.refresh(item)
    await audit(db, request, "content.update", "content_item", item.id, before=before,
                after={"title": item.title})
    return _out(item)


# ---------- 9.4 Publicar ----------
@router.post("/items/{item_id}/publish", response_model=ContentItemOut)
async def publish_item(item_id: UUID, request: Request, db: Db,
                       admin: models.AdminUser = require("content", write=True)):
    item = await _get(db, item_id)
    if ContentStatus(item.status) == ContentStatus.published:
        raise conflict("ALREADY_PUBLISHED", "El contenido ya está publicado.")
    if not item.tts_ready:
        raise conflict("TTS_PENDING", "El audio del contenido aún se está generando. Intenta en unos segundos.")
    item.status = ContentStatus.published
    item.published_at = now_utc()
    item.publish_at = None
    await db.flush()
    await db.refresh(item)
    await audit(db, request, "content.publish", "content_item", item.id)
    return _out(item)


# ---------- 9.5 Retirar ----------
@router.post("/items/{item_id}/unpublish", response_model=ContentItemOut)
async def unpublish_item(item_id: UUID, request: Request, db: Db,
                         admin: models.AdminUser = require("content", write=True)):
    item = await _get(db, item_id)
    if ContentStatus(item.status) != ContentStatus.published:
        raise conflict("NOT_PUBLISHED", "Solo puede retirarse contenido publicado.")
    item.status = ContentStatus.archived
    await db.flush()
    await db.refresh(item)
    await audit(db, request, "content.unpublish", "content_item", item.id)
    return _out(item)


# ---------- 9.6 Eliminar ----------
@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: UUID, request: Request, db: Db,
                      admin: models.AdminUser = require("content", write=True)):
    item = await _get(db, item_id)
    if ContentStatus(item.status) == ContentStatus.published:
        raise conflict("ITEM_PUBLISHED", "Retira el contenido del feed antes de eliminarlo.")
    item.deleted_at = now_utc()
    await audit(db, request, "content.delete", "content_item", item.id)
    return Response(status_code=204)
