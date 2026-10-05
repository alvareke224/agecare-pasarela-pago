"""Fixtures: BD SQLite en memoria + cliente HTTP + seed mínimo."""
import asyncio
import os

os.environ["ADMIN_DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["ADMIN_JWT_SECRET"] = "secreto-de-test"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.database as database
from app import models
from app.database import Base
from app.main import app as fastapi_app
from app.security import hash_password


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                              connect_args={"check_same_thread": False})
    database._engine = eng
    database._session_factory = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def seeded(engine):
    from scripts.seed import seed
    await seed()
    return True


@pytest_asyncio.fixture
async def client(seeded):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_headers(client):
    r = await client.post("/api/v1/admin/auth/login",
                          json={"email": "admin@wellq.co.uk", "password": "Admin123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def analyst_headers(client):
    r = await client.post("/api/v1/admin/auth/login",
                          json={"email": "analista@wellq.co.uk", "password": "Analista123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------- facturación
@pytest_asyncio.fixture
async def fake_gw():
    """Simulador de Transbank limpio para cada test."""
    from app.billing.gateway import FakeGateway, set_gateway
    gw = FakeGateway()
    set_gateway(gw)
    yield gw
    set_gateway(None)


@pytest.fixture
def app_headers():
    """Fábrica de cabeceras con JWT de usuario de la app (único por test si no se indica correo)."""
    import uuid as _uuid
    from scripts.app_token import make_token

    def make(email: str | None = None, family_ids: list[str] | None = None, provider_id: str | None = None,
             user_id: str | None = None) -> dict:
        email = email or f"user-{_uuid.uuid4().hex[:10]}@demo.cl"
        return {"Authorization": f"Bearer {make_token(email, user_id=user_id, family_ids=family_ids, provider_id=provider_id)}"}
    return make


async def _login(client, email: str, password: str) -> dict:
    r = await client.post("/api/v1/admin/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def support_headers(client):
    return await _login(client, "soporte@wellq.co.uk", "Soporte123!")


@pytest_asyncio.fixture
async def editor_headers(client):
    return await _login(client, "editora@wellq.co.uk", "Editora123!")


@pytest_asyncio.fixture
async def session(seeded):
    async with database.get_session_factory()() as s:
        yield s
