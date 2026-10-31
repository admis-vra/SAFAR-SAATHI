import os
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv(Path(__file__).parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
# Only needed for older projects that still sign tokens with the shared HS256 secret.
JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")

if not SUPABASE_URL:
    raise RuntimeError("Set SUPABASE_URL in backend/.env (see backend/.env.example)")

ISSUER = f"{SUPABASE_URL}/auth/v1"
_jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
STAFF_ROLES = {"driver", "conductor"}
ADMIN_ROLES = {"dispatcher"}


def _decode(token: str) -> dict:
    alg = jwt.get_unverified_header(token).get("alg")
    if alg == "HS256":
        if not JWT_SECRET:
            raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        key, algorithms = JWT_SECRET, ["HS256"]
    else:
        key, algorithms = _jwks.get_signing_key_from_jwt(token).key, ["RS256", "ES256"]
    return jwt.decode(token, key, algorithms=algorithms, audience="authenticated", issuer=ISSUER)


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please log in.")
    try:
        claims = _decode(authorization.removeprefix("Bearer "))
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please log in again.")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise HTTPException(401, "Invalid login. Please log in again.")

    # app_metadata can only be set by an admin, so roles come from there, never from user_metadata.
    app_meta = claims.get("app_metadata") or {}
    user_meta = claims.get("user_metadata") or {}
    role = app_meta.get("role") if app_meta.get("role") in STAFF_ROLES | ADMIN_ROLES else "passenger"
    bus_id = None
    if role in STAFF_ROLES:
        try:
            bus_id = int(app_meta.get("bus_id"))
        except (TypeError, ValueError):
            bus_id = None
    return {
        "id": claims["sub"],
        "email": claims.get("email"),
        "role": role,
        "bus_id": bus_id,
        "name": app_meta.get("name") or user_meta.get("name") or claims.get("email"),
        "user_metadata": user_meta,
    }
