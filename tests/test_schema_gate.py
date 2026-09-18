"""Schema gate: every payload validates against models.py before transition."""

import pytest
from pydantic import BaseModel, ConfigDict

from chimera.models import SCHEMA_REGISTRY, GraphNodeOutput, GraphPlan, TaskSpec
from chimera.verify.schema_gate import SchemaGateError, validate_as


def _node(id: str = "n-a", reads=None) -> dict:
    return {"id": id, "role": "researcher", "tier": "fast", "brief": "do it", "reads": reads or []}


def test_graph_plan_valid():
    plan = validate_as(
        GraphPlan,
        {
            "goal": "g",
            "rationale": "r",
            "phases": [{"name": "one", "nodes": [_node()]}],
        },
    )
    assert plan.phases[0].nodes[0].id == "n-a"


def test_graph_plan_duplicate_node_ids():
    with pytest.raises(SchemaGateError, match="duplicate"):
        validate_as(
            GraphPlan,
            {
                "goal": "g",
                "rationale": "r",
                "phases": [{"name": "one", "nodes": [_node("n-a"), _node("n-a")]}],
            },
        )


def test_graph_plan_rejects_extra_fields():
    with pytest.raises(SchemaGateError):
        validate_as(
            GraphPlan,
            {
                "goal": "g",
                "rationale": "r",
                "phases": [{"name": "one", "nodes": [_node()]}],
                "extra": 1,
            },
        )


def test_graph_plan_width_hard_cap_is_schema_level():
    nodes = [_node(f"n-{i}") for i in "abcdefghi"]  # 9 > hard cap 8
    with pytest.raises(SchemaGateError):
        validate_as(
            GraphPlan,
            {
                "goal": "g",
                "rationale": "r",
                "phases": [{"name": "one", "nodes": nodes}],
            },
        )


def test_graph_node_output_confidence_bounds():
    base = {
        "node_id": "n-a",
        "output": "o",
        "sources": [],
        "confidence": 101,
        "recommendation": "PROCEED",
    }
    with pytest.raises(SchemaGateError):
        validate_as(GraphNodeOutput, base)
    ok = validate_as(GraphNodeOutput, {**base, "confidence": 100})
    assert ok.confidence == 100


# --- the registry guard is the hot path for arc acceptance -------------------
# The arc routes GraphPlan / GraphNodeOutput / GraphArtifact / CriticOpinion
# through validate_as, so the registry check is what stops an unregistered (or
# impostor) model class from being accepted as a contract surface. It is
# load-bearing, so it gets its own tests.


def test_validate_as_accepts_a_registered_model():
    out = validate_as(
        GraphNodeOutput,
        {
            "node_id": "n-a",
            "output": "o",
            "sources": [],
            "confidence": 80,
            "recommendation": "PROCEED",
        },
    )
    assert isinstance(out, GraphNodeOutput)
    assert out.node_id == "n-a"


def test_validate_as_refuses_an_unregistered_model():
    """TaskSpec is a real, valid model that is deliberately NOT a schema-gate
    payload: the registry guard must refuse it, not silently accept it."""
    assert TaskSpec.__name__ not in SCHEMA_REGISTRY  # the premise of this test
    with pytest.raises(SchemaGateError, match="not in the schema registry"):
        validate_as(
            TaskSpec,
            {
                "id": "20260610-demo-task",
                "slug": "demo-task",
                "ask": "demo ask",
                "arc": "graph",
            },
        )


def test_validate_as_refuses_an_impostor_wearing_a_registered_name():
    """The guard is identity, not name: a class whose __name__ matches a
    registered schema is still refused unless it IS the registered class."""

    class _Impostor(BaseModel):
        model_config = ConfigDict(extra="forbid")

        smuggled: str

    _Impostor.__name__ = "GraphNodeOutput"
    assert SCHEMA_REGISTRY["GraphNodeOutput"] is GraphNodeOutput
    assert SCHEMA_REGISTRY["GraphNodeOutput"] is not _Impostor

    with pytest.raises(SchemaGateError, match="not in the schema registry"):
        validate_as(_Impostor, {"smuggled": "a payload the real schema would refuse"})
