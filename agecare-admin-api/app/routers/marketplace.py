"""Sección 10 — Catálogos del marketplace."""
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app import models
from app.audit import audit
from app.deps import Db, require
from app.enums import CaregiverStatus, ContentStatus
from app.errors import conflict, invalid, not_found
from app.schemas.common import Page
from app.schemas.operation import (CaregiverOut, CaregiverPatchIn, ProductCreateIn, ProductOut,
                                   ProductPatchIn)
from app.security import now_utc

router = APIRouter(prefix="/marketplace", tags=["Catálogos marketplace"])


def _cg_out(c: models.CaregiverProfile, include_note: bool = False) -> CaregiverOut:
    return CaregiverOut(caregiver_id=c.id, name=c.name, zone=c.zone,
                        specialties=c.specialties or [], languages=c.languages or [],
                        certifications_count=c.certifications_count, rating_avg=c.rating_avg,
                        reviews_count=c.reviews_count, status=CaregiverStatus(c.status),
                        submitted_at=c.submitted_at, reviewed_by_name=c.reviewed_by_name,
                        internal_note=c.internal_note if include_note else None)


# ---------- 10.1 Listar cuidadoras ----------
@router.get("/caregivers", response_model=Page[CaregiverOut], dependencies=[require("marketplace")])
async def list_caregivers(db: Db,
                          status_f: CaregiverStatus | None = Query(default=None, alias="status"),
                          zone: str | None = None,
                          specialty: str | None = None,
                          q: str | None = Query(default=None, max_length=120),
                          page: int = Query(default=1, ge=1),
                          page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.CaregiverProfile)
    if status_f:
        stmt = stmt.where(models.CaregiverProfile.status == status_f)
    if zone:
        stmt = stmt.where(models.CaregiverProfile.zone.ilike(f"%{zone}%"))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.CaregiverProfile.name.ilike(like) |
                          models.CaregiverProfile.email.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.CaregiverProfile.submitted_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if specialty:  # las especialidades viven en JSON; filtro en memoria sobre la página
        rows = [r for r in rows if specialty.lower() in [s.lower() for s in (r.specialties or [])]]
    return Page(items=[_cg_out(c) for c in rows], page=page, page_size=page_size, total=total)


# ---------- 10.2 Aprobar / suspender / anotar ----------
@router.patch("/caregivers/{caregiver_id}", response_model=CaregiverOut)
async def patch_caregiver(caregiver_id: UUID, body: CaregiverPatchIn, request: Request, db: Db,
                          admin: models.AdminUser = require("marketplace", write=True)):
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is None:
        raise not_found()
    before = {"status": c.status}
    if body.status is not None:
        if body.status == CaregiverStatus.approved and not c.certifications_verified:
            raise conflict("CERTIFICATION_REQUIRED",
                           "No puede aprobarse un perfil sin certificación verificada.")
        if body.status == CaregiverStatus.suspended and not body.reason:
            raise invalid("REASON_REQUIRED", "Para suspender un perfil debes indicar el motivo.")
        c.status = body.status
        c.status_reason = body.reason
        c.reviewed_by = admin.id
        c.reviewed_by_name = admin.full_name
        # Al suspender se notificaría a la cuidadora con el motivo.
    if body.internal_note is not None:
        c.internal_note = body.internal_note
    await audit(db, request, "marketplace.caregiver_update", "caregiver_profile", c.id,
                before=before, after={"status": c.status})
    return _cg_out(c, include_note=True)


# ---------- 10.3 Listar artículos ----------
@router.get("/products", response_model=Page[ProductOut], dependencies=[require("marketplace")])
async def list_products(db: Db,
                        category: str | None = None,
                        status_f: ContentStatus | None = Query(default=None, alias="status"),
                        q: str | None = Query(default=None, max_length=120),
                        page: int = Query(default=1, ge=1),
                        page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.Product)
    if category:
        stmt = stmt.where(models.Product.category.ilike(f"%{category}%"))
    if status_f:
        stmt = stmt.where(models.Product.status == status_f)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.Product.name.ilike(like) | models.Product.vendor.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.Product.updated_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[ProductOut.model_validate(p) for p in rows],
                page=page, page_size=page_size, total=total)


# ---------- 10.4 Crear artículo ----------
@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreateIn, request: Request, db: Db,
                         admin: models.AdminUser = require("marketplace", write=True)):
    p = models.Product(name=body.name, category=body.category, vendor=body.vendor,
                       price_clp=body.price_clp, external_url=str(body.external_url),
                       image_url=str(body.image_url) if body.image_url else None)
    db.add(p)
    await db.flush()
    await db.refresh(p)
    await audit(db, request, "marketplace.product_create", "product", p.id, after={"name": p.name})
    return ProductOut.model_validate(p)


# ---------- 10.5 Editar / archivar artículo ----------
@router.patch("/products/{product_id}", response_model=ProductOut)
async def patch_product(product_id: UUID, body: ProductPatchIn, request: Request, db: Db,
                        admin: models.AdminUser = require("marketplace", write=True)):
    p = await db.get(models.Product, product_id)
    if p is None:
        raise not_found()
    before = {"status": p.status, "name": p.name}
    if body.external_url is not None:
        if body.external_url.scheme != "https":
            raise invalid("INSECURE_URL", "El enlace externo debe usar https.")
        p.external_url = str(body.external_url)
    for field in ("name", "category", "vendor", "price_clp"):
        v = getattr(body, field)
        if v is not None:
            setattr(p, field, v)
    if body.image_url is not None:
        p.image_url = str(body.image_url)
    if body.status is not None:
        p.status = body.status
    p.updated_at = now_utc()
    await db.flush()
    await db.refresh(p)
    await audit(db, request, "marketplace.product_update", "product", p.id, before=before,
                after={"status": p.status, "name": p.name})
    return ProductOut.model_validate(p)
