import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException

_ITERATIONS = 200_000
credentials: dict[tuple[str, str], str] = {}
sessions: dict[str, dict] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def set_password(kind: str, user_id: str, password: str):
    credentials[(kind, user_id)] = hash_password(password)


def check_login(kind: str, user_id: str, password: str) -> bool:
    stored = credentials.get((kind, user_id))
    if stored is None:
        # Hash anyway so unknown IDs take as long as wrong passwords.
        verify_password(password, hash_password("x"))
        return False
    return verify_password(password, stored)


def new_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    sessions[token] = user
    return token


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please log in.")
    user = sessions.get(authorization.removeprefix("Bearer "))
    if user is None:
        raise HTTPException(401, "Session expired. Please log in again.")
    return user


def end_session(authorization: str | None):
    if authorization and authorization.startswith("Bearer "):
        sessions.pop(authorization.removeprefix("Bearer "), None)
