"""Model registry: versioned artifacts on disk + metadata in the database.

Artifacts are joblib (pickle) files. Pickle executes code on load, so only
artifacts produced by this project's training pipeline, stored in
``MODELS_DIR``, are loaded - and only after their SHA-256 matches the value
recorded at registration time.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select

from app.database.database import Database
from app.database.models import ModelRecord


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ModelMetadata:
    model_id: str
    version: str
    kind: str
    symbol: str
    created_at: datetime
    dataset_start: datetime | None
    dataset_end: datetime | None
    features: list[str]
    hyperparameters: dict[str, Any]
    validation_metrics: dict[str, Any]
    oos_metrics: dict[str, Any]
    status: str
    artifact_path: str
    artifact_sha256: str

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        for k in ("created_at", "dataset_start", "dataset_end"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


class ModelRegistry:
    def __init__(self, db: Database, models_dir: str | Path):
        self.db = db
        self.dir = Path(models_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        model: Any,
        *,
        version: str,
        kind: str = "signal",
        symbol: str = "ALL",
        dataset_start: datetime | None = None,
        dataset_end: datetime | None = None,
        features: list[str] | None = None,
        hyperparameters: dict[str, Any] | None = None,
        validation_metrics: dict[str, Any] | None = None,
        oos_metrics: dict[str, Any] | None = None,
    ) -> ModelMetadata:
        model_id = f"{kind}-{symbol.lower()}-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        path = self.dir / f"{model_id}.joblib"
        joblib.dump(model, path)
        meta = ModelMetadata(
            model_id=model_id,
            version=version,
            kind=kind,
            symbol=symbol,
            created_at=datetime.now(UTC),
            dataset_start=dataset_start,
            dataset_end=dataset_end,
            features=features or [],
            hyperparameters=hyperparameters or {},
            validation_metrics=validation_metrics or {},
            oos_metrics=oos_metrics or {},
            status="CANDIDATE",
            artifact_path=path.name,
            artifact_sha256=sha256_file(path),
        )
        (self.dir / f"{model_id}.json").write_text(json.dumps(meta.to_dict(), indent=2, default=str))
        with self.db.session() as s:
            s.add(ModelRecord(**{k: v for k, v in meta.__dict__.items()}))
        return meta

    def list(self) -> list[ModelMetadata]:
        with self.db.session() as s:
            rows = s.scalars(select(ModelRecord).order_by(ModelRecord.created_at.desc())).all()
            return [self._to_meta(r) for r in rows]

    def get(self, model_id: str) -> ModelMetadata | None:
        with self.db.session() as s:
            row = s.get(ModelRecord, model_id)
            return self._to_meta(row) if row else None

    def set_status(self, model_id: str, status: str) -> None:
        if status not in {"CANDIDATE", "ACTIVE", "RETIRED", "REJECTED"}:
            raise ValueError("invalid model status")
        with self.db.session() as s:
            row = s.get(ModelRecord, model_id)
            if row is None:
                raise KeyError(model_id)
            if status == "ACTIVE":
                if not row.oos_metrics:
                    raise ValueError("a model without out-of-sample results cannot be activated")
                for other in s.scalars(
                    select(ModelRecord).where(
                        ModelRecord.kind == row.kind, ModelRecord.symbol == row.symbol, ModelRecord.status == "ACTIVE"
                    )
                ):
                    other.status = "RETIRED"
            row.status = status

    def active(self, kind: str = "signal", symbol: str = "ALL", version: str = "") -> ModelMetadata | None:
        with self.db.session() as s:
            q = select(ModelRecord).where(ModelRecord.kind == kind, ModelRecord.symbol == symbol)
            q = q.where(ModelRecord.version == version) if version else q.where(ModelRecord.status == "ACTIVE")
            row = s.scalars(q.order_by(ModelRecord.created_at.desc())).first()
            return self._to_meta(row) if row else None

    def load(self, meta: ModelMetadata) -> Any:
        path = (self.dir / meta.artifact_path).resolve()
        if self.dir.resolve() not in path.parents:
            raise PermissionError("model artifact outside MODELS_DIR")
        if not path.exists():
            raise FileNotFoundError(path)
        if sha256_file(path) != meta.artifact_sha256:
            raise PermissionError("model artifact checksum mismatch - refusing to load")
        return joblib.load(path)

    @staticmethod
    def _to_meta(r: ModelRecord) -> ModelMetadata:
        return ModelMetadata(
            model_id=r.model_id,
            version=r.version,
            kind=r.kind,
            symbol=r.symbol,
            created_at=r.created_at,
            dataset_start=r.dataset_start,
            dataset_end=r.dataset_end,
            features=list(r.features or []),
            hyperparameters=dict(r.hyperparameters or {}),
            validation_metrics=dict(r.validation_metrics or {}),
            oos_metrics=dict(r.oos_metrics or {}),
            status=r.status,
            artifact_path=r.artifact_path,
            artifact_sha256=r.artifact_sha256,
        )
