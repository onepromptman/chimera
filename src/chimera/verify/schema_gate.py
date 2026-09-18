"""Schema gate — every artifact and step payload validates before any
transition. A payload that doesn't parse never enters task state; the
submitting step is treated as returned-null (workflow.js v2.1 semantics)."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..models import SCHEMA_REGISTRY

_M = TypeVar("_M", bound=BaseModel)


class SchemaGateError(ValueError):
    def __init__(self, schema_name: str, detail: str):
        self.schema_name = schema_name
        super().__init__(f"schema gate [{schema_name}]: {detail}")


def validate_as(model_cls: type[_M], payload: Any) -> _M:
    """Validate a raw payload against a registered schema from models.py.

    The model class names the schema (it must be registered) and the caller
    gets that class back — no cast at the arc call sites, and an unregistered
    model is refused, not silently accepted. The untyped `validate(name,
    payload)` twin was deleted 2026-09-03: nothing but its own tests called
    it."""
    name = model_cls.__name__
    if SCHEMA_REGISTRY.get(name) is not model_cls:
        raise SchemaGateError(
            name, f"model is not in the schema registry; known: {sorted(SCHEMA_REGISTRY)}"
        )
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise SchemaGateError(name, str(exc)) from exc
