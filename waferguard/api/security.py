"""Authentication and role-based access control.

Roles are hierarchical: Operator < Engineer < Manager < Admin.
  Operator  run inspections, use cameras, view results
  Engineer  + review/relabel, batch jobs, SPC, exports, acknowledge alerts
  Manager   + audit trail, alert configuration, summary reports
  Admin     + users, API keys, model promotion / A-B configuration

Humans log in with username/password (OAuth2 password flow -> JWT). MES/ERP
integrations use API keys (header ``X-API-Key``) which carry a role.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from waferguard.api.db import ApiKey, User

ROLES = ["Operator", "Engineer", "Manager", "Admin"]
_ITER = 200_000

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITER)
    return f"pbkdf2_sha256${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, digest = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(it))
    return hmac.compare_digest(dk.hex(), digest)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "wg_" + secrets.token_urlsafe(32)


def create_token(username: str, role: str, secret: str, minutes: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + dt.timedelta(minutes=minutes)},
                      secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"])


def role_at_least(role: str, minimum: str) -> bool:
    return ROLES.index(role) >= ROLES.index(minimum)


class Principal:
    def __init__(self, username: str, role: str, via: str = "jwt"):
        self.username, self.role, self.via = username, role, via

    def __repr__(self):  # pragma: no cover
        return f"<Principal {self.username} {self.role}>"


def _unauthorized(detail="not authenticated"):
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def resolve_principal(request: Request, token: str | None) -> Principal:
    state = request.app.state.wg
    api_key = request.headers.get("X-API-Key")
    if api_key:
        with state.db.Session() as s:
            row = s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key), ApiKey.revoked.is_(False)))
        if not row:
            raise _unauthorized("invalid API key")
        return Principal(f"apikey:{row.name}", row.role, "api_key")
    if not token:
        raise _unauthorized()
    try:
        claims = decode_token(token, state.settings.jwt_secret)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("token expired")
    except jwt.PyJWTError:
        raise _unauthorized("invalid token")
    with state.db.Session() as s:
        user = s.scalar(select(User).where(User.username == claims["sub"]))
    if not user or not user.active:
        raise _unauthorized("user disabled")
    return Principal(user.username, user.role)


def current_user(request: Request, token: str | None = Depends(oauth2)) -> Principal:
    return resolve_principal(request, token)


def require(minimum: str):
    def dep(p: Principal = Depends(current_user)) -> Principal:
        if not role_at_least(p.role, minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {minimum} or higher")
        return p
    return dep
