"""Emite un JWT de usuario de la app para probar /api/v1/billing en desarrollo.

En producción estos tokens los emite la API principal de AgeCare; este script
solo existe para dev/QA y firma con ADMIN_APP_JWT_SECRET.

Uso:
    python -m scripts.app_token --email ana@demo.cl
    python -m scripts.app_token --email ana@demo.cl --family 7c1e…  --provider 9a2b…
"""
import argparse
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


def make_token(email: str, name: str | None = None, user_id: str | None = None,
               family_ids: list[str] | None = None, provider_id: str | None = None, hours: int = 12) -> str:
    s = get_settings()
    payload = {
        "sub": user_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"agecare-user-{email}")),
        "email": email,
        "name": name or email.split("@")[0].title(),
        "family_ids": family_ids or [],
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
    }
    if provider_id:
        payload["provider_id"] = provider_id
    if s.app_jwt_audience:
        payload["aud"] = s.app_jwt_audience
    return jwt.encode(payload, s.app_jwt_secret, algorithm=s.app_jwt_algorithm)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name")
    ap.add_argument("--user-id")
    ap.add_argument("--family", action="append", default=[])
    ap.add_argument("--provider")
    a = ap.parse_args()
    print(make_token(a.email, a.name, a.user_id, a.family, a.provider))
