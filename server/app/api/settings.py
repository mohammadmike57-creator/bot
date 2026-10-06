"""Effective settings (non-secret), runtime overrides and model management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, require_admin, require_reader
from app.api.schemas import RuntimeSettingsIn
from app.services.container import Container
from app.services.security import Principal

router = APIRouter()


@router.get("/v1/settings")
def get_settings(c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> dict[str, Any]:
    return c.public_settings()


@router.post("/v1/settings")
def update_settings(body: RuntimeSettingsIn, c: Container = Depends(get_container),
                    who: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Runtime overrides may only tighten env limits; looser values are refused."""
    s = c.settings
    values = body.model_dump(exclude_none=True)
    for key, env_value in (("risk_per_trade", s.risk_per_trade), ("max_positions", s.max_positions),
                           ("max_portfolio_risk", s.max_portfolio_risk)):
        if key in values and values[key] > env_value:
            raise HTTPException(status_code=422, detail=f"{key} cannot exceed the configured limit {env_value}")
    if "confidence_threshold" in values and values["confidence_threshold"] < s.confidence_threshold:
        raise HTTPException(status_code=422, detail="confidence_threshold cannot be lowered below the configured value")
    if "enabled_symbols" in values:
        unknown = [x for x in values["enabled_symbols"] if x not in c.resolver.logical_symbols()]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown symbols: {unknown}")
    c.set_overrides({**c.overrides.values, **values}, who.subject)
    return c.public_settings()


@router.get("/v1/model/status")
def model_status(c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> dict[str, Any]:
    status = c.inference.status()
    status["registry"] = [m.to_dict() for m in c.registry.list()[:20]]
    return status


@router.post("/v1/model/reload")
def model_reload(c: Container = Depends(get_container), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    c.inference.reload()
    return c.inference.status()
