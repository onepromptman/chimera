"""Schema gate — every artifact and step payload validates before any
transition. A payload that doesn't parse never enters task state; the
submitting step is treated as returned-null."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from ..models import SCHEMA_REGISTRY


class SchemaGateError(ValueError):
    def __init__(self, schema_name: str, detail: str):
        self.schema_name = schema_name
        super().__init__(f"schema gate [{schema_name}]: {detail}")


def validate(schema_name: str, payload: Any) -> BaseModel:
    """Validate a raw payload against a named schema from models.py."""
    model_cls = SCHEMA_REGISTRY.get(schema_name)
    if model_cls is None:
        raise SchemaGateError(schema_name, f"unknown schema; known: {sorted(SCHEMA_REGISTRY)}")
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise SchemaGateError(schema_name, str(exc)) from exc
