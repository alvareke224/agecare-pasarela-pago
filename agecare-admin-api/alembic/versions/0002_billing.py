"""Pasarela de pago y suscripciones: tablas billing_*.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04

Sigue el mismo enfoque que 0001 (metadata del ORM). En una base creada con
0001 posterior a este cambio las tablas ya existen: checkfirst las omite.
El modelo físico canónico con RLS, triggers y grants para Azure PostgreSQL
está en db/agecare_billing_ddl.sql.
"""
from alembic import op

from app.billing.models import BILLING_TABLES
from app.database import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=BILLING_TABLES, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(BILLING_TABLES)), checkfirst=True)
