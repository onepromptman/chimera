"""Lite verification — single-round 3-critic REFUTE panel.

The default verify gate — validates at a small fraction of the cost of a
full tournament. Survival uses the
proportional-majority rule:

    - require >=2 valid critic opinions (less is insufficient signal)
    - require strict majority unrefuted (> N/2)
    - 3 valid: need 2+ unrefuted; 2 valid: need 2 (unanimous);
      0-1 valid: fail (too thin)

Maker ≠ checker is asserted here, structurally: the panel refuses to build
if the critic model equals the maker model.
"""

from __future__ import annotations

# MakerCheckerViolation lives in agents.py (the resolution module) and is
# re-exported here — every pre-existing `lite.MakerCheckerViolation` caller
# keeps working.
from ..agents import MakerCheckerViolation, resolve_models
from ..models import AgentCall, CriticOpinion, VerifyResult

__all__ = ["MakerCheckerViolation", "assert_maker_neq_checker", "critic_calls",
           "survives", "verdict"]


def assert_maker_neq_checker(
    maker_model: str | None = None, critic_model: str | None = None
) -> None:
    """Refuse a panel whose critic model equals the maker model. Defaults
    resolve from the environment at CALL time (audit roadmap #4) — an env
    changed after import can no longer slip past the guard."""
    if maker_model is None or critic_model is None:
        models = resolve_models()
        maker_model = maker_model or models.maker
        critic_model = critic_model or models.critic
    if maker_model == critic_model:
        raise MakerCheckerViolation(
            f"critic model ({critic_model}) must differ from maker model ({maker_model})"
        )


def survives(opinions: list[CriticOpinion | None]) -> tuple[bool, int, int]:
    """Apply the proportional-majority rule. Returns (survives, valid, unrefuted)."""
    valid = [o for o in opinions if o is not None]
    unrefuted = sum(1 for o in valid if not o.refuted)
    return (len(valid) >= 2 and unrefuted > len(valid) / 2, len(valid), unrefuted)


_CRITIC_ANGLES = (
    (
        "general REFUTE",
        "Try to REFUTE this artifact. Default refuted=true on uncertainty. Examine "
        "logic, internal consistency, whether the recommendation follows from the "
        "evidence, and what concrete scenario would falsify the central claim.",
    ),
    (
        "GREP-ANCHOR check",
        "Verify every cited source. For file paths, grep/read and confirm the claim "
        "is present. For URLs, fetch and confirm. Any failed substantiation -> "
        "refuted=true. Default refuted=true if you cannot verify within reasonable effort.",
    ),
    (
        "MISSING-SOURCE check",
        "Identify evidence that is MISSING. If the artifact makes empirical claims "
        "but does not cite the obvious authoritative source -> refuted=true. Default "
        "refuted=true on absence-of-evidence.",
    ),
)


def critic_calls(
    label_prefix: str,
    payload: str,
    phase: str = "Verify",
    subagent_type: str | None = "critic",
) -> list[AgentCall]:
    """Build the 3 critic AgentCalls for the driving session to fan out.

    subagent_type defaults to "critic" so all arc verify panels get the roster
    specialist automatically. Pass None to disable (e.g. if the caller has
    already validated routing and wants to override).
    """
    assert_maker_neq_checker()
    critic_model = resolve_models().critic
    calls = []
    for i, (angle, instructions) in enumerate(_CRITIC_ANGLES, start=1):
        calls.append(
            AgentCall(
                label=f"{label_prefix}:critic{i}",
                prompt=(
                    f"Critic {i} of 3 ({angle}) for chimera.\n\n"
                    f"Artifact to refute:\n{payload}\n\n{instructions}\n\n"
                    'Return JSON: {"refuted": bool, "reason": str, '
                    '"severity": "fatal|material|cosmetic"}'
                ),
                schema_name="CriticOpinion",
                model=critic_model,
                phase=phase,
                subagent_type=subagent_type,
                selection_reason="verify panel → critic (REFUTE)" if subagent_type else None,
            )
        )
    return calls


def verdict(mode: str, opinions: list[CriticOpinion | None]) -> VerifyResult:
    assert_maker_neq_checker()
    models = resolve_models()
    passed, valid, unrefuted = survives(opinions)
    return VerifyResult(
        mode=mode,  # type: ignore[arg-type]
        passed=passed,
        maker_model=models.maker,
        critic_model=models.critic,
        opinions=[o for o in opinions if o is not None],
        valid_critic_count=valid,
        unrefuted_count=unrefuted,
    )
