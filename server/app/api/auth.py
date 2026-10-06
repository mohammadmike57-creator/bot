"""Dashboard authentication: exchange the admin API key for a short-lived JWT."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container
from app.api.schemas import AuthRequest, AuthResponse
from app.database import repositories as repo
from app.services.container import Container
from app.services.security import AuthError, check_api_key, issue_token

router = APIRouter()


@router.post("/v1/auth", response_model=AuthResponse)
def authenticate(body: AuthRequest, c: Container = Depends(get_container)) -> AuthResponse:
    if not check_api_key(c.settings.api_key.get_secret_value(), body.api_key):
        c.metrics.inc("auth_failures")
        with c.db.session() as s:
            repo.add_system_event(s, "auth", "LOGIN_FAILED", "invalid api key", "WARNING")
        raise HTTPException(status_code=401, detail="INVALID_API_KEY")
    try:
        token, exp = issue_token(c.settings.jwt_secret.get_secret_value(), "dashboard", "admin",
                                 c.settings.jwt_ttl_minutes)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    return AuthResponse(access_token=token, expires_at=exp)
