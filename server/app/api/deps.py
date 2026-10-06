"""FastAPI dependencies: container access and authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.database import repositories as repo
from app.monitoring.logger import request_id_var
from app.services.container import Container
from app.services.security import AuthError, Principal, verify_signature, verify_token


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def _auth_fail(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


async def _hmac_principal(request: Request, c: Container) -> Principal:
    body = await request.body()
    h = request.headers
    try:
        request_id = verify_signature(
            secret=c.settings.ea_hmac_secret.get_secret_value(),
            expected_key_id=c.settings.ea_api_key_id,
            key_id=h.get("x-api-key-id"),
            timestamp=h.get("x-timestamp"),
            request_id=h.get("x-request-id"),
            signature=h.get("x-signature"),
            method=request.method,
            path=request.url.path,
            body=body,
            max_skew=c.settings.signature_max_skew_seconds,
        )
    except AuthError as exc:
        c.metrics.inc("auth_failures")
        raise _auth_fail(exc) from exc
    with c.db.session() as s:
        if not repo.record_request_id(s, request_id, request.url.path):
            c.metrics.inc("replay_rejected")
            raise HTTPException(status_code=409, detail="DUPLICATE_REQUEST_ID")
    request_id_var.set(request_id)
    request.state.request_id = request_id
    return Principal(subject=c.settings.ea_api_key_id, role="ea", method="hmac")


def _jwt_principal(request: Request, c: Container) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        return verify_token(c.settings.jwt_secret.get_secret_value(), auth[7:].strip())
    except AuthError as exc:
        c.metrics.inc("auth_failures")
        raise _auth_fail(exc) from exc


async def require_ea(request: Request, c: Container = Depends(get_container)) -> Principal:
    """EA endpoints: HMAC-signed requests only."""
    return await _hmac_principal(request, c)


async def require_reader(request: Request, c: Container = Depends(get_container)) -> Principal:
    """Read endpoints: dashboard JWT or a signed EA request."""
    principal = _jwt_principal(request, c)
    if principal is not None:
        return principal
    if request.headers.get("x-signature"):
        return await _hmac_principal(request, c)
    c.metrics.inc("auth_failures")
    raise HTTPException(status_code=401, detail="MISSING_CREDENTIALS")


async def require_admin(request: Request, c: Container = Depends(get_container)) -> Principal:
    """Control endpoints (kill switch, resets, settings): admin JWT only."""
    principal = _jwt_principal(request, c)
    if principal is None:
        c.metrics.inc("auth_failures")
        raise HTTPException(status_code=401, detail="MISSING_CREDENTIALS")
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    return principal
