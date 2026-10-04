from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select

from waferguard.api.db import ApiKey, User
from waferguard.api.security import (ROLES, Principal, create_token, current_user, hash_api_key, hash_password,
                                     new_api_key, require, verify_password)
from waferguard.api.services.observability import audit

router = APIRouter(prefix="/api/v1", tags=["auth & users"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    expires_in: int


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=8)
    role: str = "Operator"
    full_name: str | None = None


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8)
    full_name: str | None = None


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    role: str = "Operator"


def _check_role(role):
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")


def _user(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "active": u.active,
            "created_at": u.created_at.isoformat()}


@router.post("/auth/token", response_model=TokenOut, summary="Log in (OAuth2 password flow)")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    st = request.app.state.wg
    with st.db.Session() as s:
        u = s.scalar(select(User).where(User.username == form.username))
    if not u or not u.active or not verify_password(form.password, u.password_hash):
        audit(st.db, form.username, "login_failed", ip=request.client.host if request.client else None)
        raise HTTPException(401, "incorrect username or password")
    audit(st.db, Principal(u.username, u.role), "login", ip=request.client.host if request.client else None)
    minutes = st.settings.token_minutes
    return TokenOut(access_token=create_token(u.username, u.role, st.settings.jwt_secret, minutes),
                    username=u.username, role=u.role, expires_in=minutes * 60)


@router.get("/auth/me")
def me(p: Principal = Depends(current_user)):
    return {"username": p.username, "role": p.role, "via": p.via}


@router.get("/users")
def list_users(request: Request, p: Principal = Depends(require("Admin"))):
    with request.app.state.wg.db.Session() as s:
        return [_user(u) for u in s.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, p: Principal = Depends(require("Admin"))):
    _check_role(body.role)
    st = request.app.state.wg
    with st.db.Session() as s:
        if s.scalar(select(User).where(User.username == body.username)):
            raise HTTPException(409, "username already exists")
        u = User(username=body.username, full_name=body.full_name, role=body.role, password_hash=hash_password(body.password))
        s.add(u)
        s.commit()
        out = _user(u)
    audit(st.db, p, "user_created", body.username, {"role": body.role})
    return out


@router.patch("/users/{username}")
def update_user(username: str, body: UserPatch, request: Request, p: Principal = Depends(require("Admin"))):
    st = request.app.state.wg
    with st.db.Session() as s:
        u = s.scalar(select(User).where(User.username == username))
        if not u:
            raise HTTPException(404, "user not found")
        changes = {}
        if body.role is not None:
            _check_role(body.role)
            u.role = changes["role"] = body.role
        if body.active is not None:
            if username == p.username and not body.active:
                raise HTTPException(400, "you cannot disable your own account")
            u.active = changes["active"] = body.active
        if body.full_name is not None:
            u.full_name = body.full_name
        if body.password:
            u.password_hash = hash_password(body.password)
            changes["password"] = "reset"
        s.commit()
        out = _user(u)
    audit(st.db, p, "user_updated", username, changes)
    return out


@router.delete("/users/{username}")
def delete_user(username: str, request: Request, p: Principal = Depends(require("Admin"))):
    """Permanently remove a user account (Admin only).

    Inspections and audit entries that mention the username are kept; they store it as plain text.
    """
    if username == p.username:
        raise HTTPException(400, "you cannot delete your own account")
    st = request.app.state.wg
    with st.db.Session() as s:
        u = s.scalar(select(User).where(User.username == username))
        if not u:
            raise HTTPException(404, "user not found")
        if u.role == "Admin" and u.active:
            other_admins = s.scalars(select(User).where(User.role == "Admin", User.active.is_(True), User.id != u.id)).all()
            if not other_admins:
                raise HTTPException(400, "cannot delete the last active administrator")
        role = u.role
        s.delete(u)
        s.commit()
    audit(st.db, p, "user_deleted", username, {"role": role})
    return {"deleted": username}


@router.post("/auth/password")
def change_own_password(body: dict, request: Request, p: Principal = Depends(current_user)):
    st = request.app.state.wg
    old, new = body.get("old_password", ""), body.get("new_password", "")
    if len(new) < 8:
        raise HTTPException(422, "new password must be at least 8 characters")
    with st.db.Session() as s:
        u = s.scalar(select(User).where(User.username == p.username))
        if not u or not verify_password(old, u.password_hash):
            raise HTTPException(400, "current password is wrong")
        u.password_hash = hash_password(new)
        s.commit()
    audit(st.db, p, "password_changed", p.username)
    return {"ok": True}


@router.get("/api-keys")
def list_keys(request: Request, p: Principal = Depends(require("Admin"))):
    with request.app.state.wg.db.Session() as s:
        return [{"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role, "created_by": k.created_by,
                 "created_at": k.created_at.isoformat(), "revoked": k.revoked} for k in s.scalars(select(ApiKey))]


@router.post("/api-keys", status_code=201, summary="Create an API key for MES/ERP integration (shown once)")
def create_key(body: ApiKeyIn, request: Request, p: Principal = Depends(require("Admin"))):
    _check_role(body.role)
    st = request.app.state.wg
    key = new_api_key()
    with st.db.Session() as s:
        k = ApiKey(name=body.name, role=body.role, key_hash=hash_api_key(key), prefix=key[:10], created_by=p.username)
        s.add(k)
        s.commit()
        kid = k.id
    audit(st.db, p, "api_key_created", body.name, {"role": body.role})
    return {"id": kid, "name": body.name, "role": body.role, "api_key": key}


@router.delete("/api-keys/{key_id}")
def revoke_key(key_id: int, request: Request, p: Principal = Depends(require("Admin"))):
    st = request.app.state.wg
    with st.db.Session() as s:
        k = s.get(ApiKey, key_id)
        if not k:
            raise HTTPException(404, "key not found")
        k.revoked = True
        s.commit()
    audit(st.db, p, "api_key_revoked", str(key_id))
    return {"ok": True}
