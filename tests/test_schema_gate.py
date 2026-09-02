"""Schema gate: every payload validates against models.py before transition."""

import pytest

from chimera.verify.schema_gate import SchemaGateError, validate


def _node(id: str = "n-a", reads=None) -> dict:
    return {"id": id, "role": "researcher", "tier": "fast", "brief": "do it",
            "reads": reads or []}


def test_graph_plan_valid():
    plan = validate("GraphPlan", {
        "goal": "g", "rationale": "r",
        "phases": [{"name": "one", "nodes": [_node()]}],
    })
    assert plan.phases[0].nodes[0].id == "n-a"


def test_graph_plan_duplicate_node_ids():
    with pytest.raises(SchemaGateError, match="duplicate"):
        validate("GraphPlan", {
            "goal": "g", "rationale": "r",
            "phases": [{"name": "one", "nodes": [_node("n-a"), _node("n-a")]}],
        })


def test_graph_plan_rejects_extra_fields():
    with pytest.raises(SchemaGateError):
        validate("GraphPlan", {
            "goal": "g", "rationale": "r",
            "phases": [{"name": "one", "nodes": [_node()]}],
            "extra": 1,
        })


def test_graph_plan_width_hard_cap_is_schema_level():
    nodes = [_node(f"n-{i}") for i in "abcdefghi"]  # 9 > hard cap 8
    with pytest.raises(SchemaGateError):
        validate("GraphPlan", {
            "goal": "g", "rationale": "r",
            "phases": [{"name": "one", "nodes": nodes}],
        })


def test_graph_node_output_confidence_bounds():
    base = {"node_id": "n-a", "output": "o", "sources": [], "confidence": 101,
            "recommendation": "PROCEED"}
    with pytest.raises(SchemaGateError):
        validate("GraphNodeOutput", base)
    ok = validate("GraphNodeOutput", {**base, "confidence": 100})
    assert ok.confidence == 100


def test_unknown_schema():
    with pytest.raises(SchemaGateError, match="unknown schema"):
        validate("NotASchema", {})


def test_step_output_recommendation_literal():
    validate("StepOutput", {"confidence": 80, "recommendation": "PROCEED"})
    with pytest.raises(SchemaGateError):
        validate("StepOutput", {"confidence": 80, "recommendation": "YOLO"})
