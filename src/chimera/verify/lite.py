"""Lite verification — single-round 2-critic REFUTE panel, fatal-only veto.

The default verify gate (fire-drill evidence: validates at ~1/30th of
tournament cost). Survival (2026-09-16, evidence: the frozen run record in tests/data/ — four
panels, 12 committed opinions, 0 graded fatal, 3 runs failed on material-only
refutations whose authors wrote the conclusion survives):

    - require >=2 valid critic opinions (less is insufficient signal)
    - FAIL if any valid opinion is refuted AND graded `fatal` (the veto)
    - otherwise PASS — a `material`/`cosmetic` refutation is recorded as an
      open finding on the digest and does not fail the run

The 2026-09-11 proportional-majority rule is reversed on that evidence: across
four real runs, every refutation that killed a run was material, not fatal,
and its author's own reason text said the conclusion survived. The old
even-count tie-hole (a live hole at four valid critics under the severity-GATE
variant tried the same day) is closed structurally, not patched: non-fatal
refutations no longer participate in survival at all, so there is no majority
count left to tie. See `blocks()` and `survives()`.

Maker != checker is asserted here, structurally: the panel refuses to build if
ANY tier it dispatches — critic or research — resolves to the maker's model.
See `assert_panel_models_differ`, called at both `critic_calls()` (build time)
and `verdict()` (decide time) — the wide guard, not the narrower critic-only
check it replaced, since the executing angle rides the research tier.
"""

from __future__ import annotations

# MakerCheckerViolation lives in agents.py (the resolution module) and is
# re-exported here — every pre-existing `lite.MakerCheckerViolation` caller
# keeps working.
from ..agents import MakerCheckerViolation, resolve_models
from ..models import (
    AgentCall,
    CriticOpinion,
    MutantRun,
    PanelCredential,
    VerifyMode,
    VerifyResult,
)

__all__ = [
    "ADVISORY_SEVERITY",
    "ANGLE_ROLES",
    "assert_panel_models_differ",
    "MakerCheckerViolation",
    "MUTANT_LABEL_SUFFIX",
    "blocks",
    "credential",
    "critic_calls",
    "mutant_call",
    "survives",
    "verdict",
]


def assert_panel_models_differ(maker_model: str | None = None) -> None:
    """EVERY model the panel dispatches must differ from the maker's.

    The narrower `assert_maker_neq_checker` (deleted 2026-09-16, no production
    caller remained outside this module) compared the critic tier alone. Since
    the executing angle rides the RESEARCH tier, an operator setting
    CHIMERA_RESEARCH_MODEL to the maker's model produced an executing "checker"
    running the maker's own model, sharing its blind spots, with no guard
    raised. Verified by execution before this fix: maker=opus, research=opus
    dispatched the executing angle on opus and nothing objected. `verdict()`
    now calls this wide guard too (it used to call the narrower one), so a
    research-tier collision is caught at decide time as well as build time.

    Defaults resolve at CALL time, like the narrower guard did, so an
    environment changed after import cannot slip past. (Found by the panel,
    on itself.)
    """
    models = resolve_models()
    maker = maker_model or models.maker
    collisions = sorted(
        tier
        for tier, model in (("critic", models.critic), ("research", models.research))
        if model == maker
    )
    if collisions:
        raise MakerCheckerViolation(
            f"panel tier(s) {', '.join(collisions)} resolve to the maker model "
            f"({maker}); a checker on the maker's model is not an independent check"
        )


ADVISORY_SEVERITY = "cosmetic"


def blocks(opinion: CriticOpinion) -> bool:
    """Does this opinion stop the artifact? Only a `fatal` refutation does.

    Severity now GATES blocking (2026-09-16 evidence, frozen in tests/data/:
    four panels, 12 committed opinions, 0 graded fatal, 3 runs failed on
    material-only refutations whose own authors wrote the conclusion survives
    — every recorded kill was a false veto). This reverses the 2026-09-11 removal. That removal's stated reason
    — the exemption opened a hole at even checker counts, where a refuted
    `material` could ride out a tie — no longer applies: `survives()` no
    longer counts a majority at all, so there is nothing for a non-fatal
    refutation to tie. `material`/`cosmetic` refutations are recorded as open
    findings on the digest (`VerifyResult.open_findings`,
    `VerifyResult.advisory_count`) and change no outcome.
    """
    return opinion.refuted and opinion.severity == "fatal"


def survives(opinions: list[CriticOpinion | None]) -> tuple[bool, int, int]:
    """The whole rule, count-agnostic:

        FAIL if valid < 2       (too thin to be signal)
        FAIL if any `blocks()`  (a fatal refutation vetoes, alone)
        else PASS

    Returns (survives, valid, unrefuted_count).

    THE VETO. One independent reader saying the central claim does not hold,
    grading the defect `fatal`, is not a vote to be outvoted, so it fails the
    run on its own at any checker count. It can only turn a pass into a fail,
    never the reverse — the safe direction for a gate.

    unrefuted_count counts `not o.refuted` directly, NOT `not blocks(o)`: a
    refuted `material`/`cosmetic` opinion is not blocking, but it IS refuted,
    and reporting it as "unrefuted" on the digest would misstate what the
    panel found. There is no majority test left to compute — PASS/FAIL is the
    veto alone — so this count is informational only.
    """
    valid = [o for o in opinions if o is not None]
    unrefuted = sum(1 for o in valid if not o.refuted)
    if len(valid) < 2:
        return (False, len(valid), unrefuted)
    if any(blocks(o) for o in valid):
        return (False, len(valid), unrefuted)
    return (True, len(valid), unrefuted)


_CRITIC_ANGLES = (
    (
        "GREP-ANCHOR check",
        "Verify every cited source. For file paths, grep/read and confirm the claim "
        "is present. For URLs, fetch and confirm. Any failed substantiation -> "
        "refuted=true. Default refuted=true if you cannot verify within reasonable effort.",
    ),
    (
        "CONFIRM check (executing)",
        "You hold a shell; the other checker does not. Do not review prose — RUN "
        "the artifact's claims and report verbatim. State the EXPECTED exit code "
        "before each command, then judge each command against its own expectation: a "
        "probe that proves an absence succeeds by exiting non-zero. Run the stated "
        "test/build/lint command if the artifact names one. If it names none, "
        "CONSTRUCT checks from its claims — grep every cited path, fetch every URL, "
        "execute the rule it says it implements and diff that against what it "
        "claims, re-measure every number it calls measured. Report exit codes and "
        "verbatim output; do not interpret, do not fix. Any command that missed its "
        "expectation -> refuted=true. "
        "If the artifact genuinely admits no executable check, say so explicitly and "
        "fall back to MISSING-SOURCE: identify evidence that is MISSING; empirical "
        "claims with no authoritative source cited -> refuted=true; default "
        "refuted=true on absence-of-evidence. "
        "DISCLOSURE RULE: a gap the artifact ITSELF names as an open question is at "
        "most `material`, never `fatal`. A gap it conceals, or asserts past, is "
        "`fatal`. Refuting a disclosed gap harder than a hidden one teaches the "
        "maker to hide its open findings, which is the opposite of the point.",
    ),
)

# Angle 2 runs on `executor` (read + shell, no write), not `critic`. This is the
# only decorrelation available at zero added cost: a shell's error distribution
# is not a reader's, so the second opinion fails differently from the first
# rather than sharing its blind spots. The dropped "general REFUTE" angle
# (2026-09-16) was the only non-executing, non-verifying angle in the old
# three — GREP-ANCHOR verifies citations and CONFIRM executes; general REFUTE
# read prose and free-associated a critique, the weakest signal of the three.
ANGLE_ROLES = _ANGLE_ROLE = ("critic", "executor")


def _angle_call(
    label: str,
    i: int,
    payload: str,
    phase: str,
    subagent_type: str | None,
) -> AgentCall:
    """ONE angle's call, built from the angle index alone.

    Extracted from `critic_calls` so the seeded-mutant control (`mutant_call`)
    dispatches through the SAME builder: the prompt, the role and the model an
    angle gets are then byte-identical whether it is grading the artifact or a
    seeded variant of it. An agent that could tell the two apart would grade
    the control instead of the panel.
    """
    models = resolve_models()
    angle, instructions = _CRITIC_ANGLES[i - 1]
    role = _ANGLE_ROLE[i - 1] if subagent_type else None
    # the executing angle rides the research tier; both are checker tiers and
    # both differ from the maker, so maker != checker still holds
    angle_model = models.research if role == "executor" else models.critic
    return AgentCall(
        label=label,
        prompt=(
            f"Critic {i} of 2 — {angle}.\n\n"
            f"Artifact to refute:\n{payload}\n\n{instructions}\n\n"
            "SEVERITY: only `fatal` fails the run — it vetoes on its own, "
            "so reserve it for a defect that changes the conclusion. "
            "`fatal`: the central claim does not hold, or a cited fact is "
            "wrong in a way that changes the conclusion. "
            "`material`: a real defect that must be fixed, but the "
            "conclusion survives. "
            "`cosmetic`: a typo, a stale count, a formatting slip. "
            "`material` and `cosmetic` refutations are recorded as open "
            "findings on the digest and do NOT fail the run. Grade "
            "honestly in both directions: do not soften a genuine "
            "`refuted` to be polite, and do not inflate severity to "
            "`fatal` to force a block when the conclusion actually "
            "survives.\n\n"
            "Return JSON matching CriticOpinion."
        ),
        schema_name="CriticOpinion",
        model=angle_model,
        phase=phase,
        subagent_type=role if subagent_type else None,
        selection_reason=(f"verify panel → {role} ({angle})" if subagent_type else None),
    )


def critic_calls(
    label_prefix: str,
    payload: str,
    phase: str = "Verify",
    subagent_type: str | None = "critic",
) -> list[AgentCall]:
    """Build the 2 critic AgentCalls for the driving session to fan out.

    subagent_type defaults to "critic" so all arc verify panels get the roster
    specialist automatically — which is also where the REFUTE discipline and
    the CriticOpinion shape live (agents.ROSTER["critic"]); a call carries its
    angle, the payload, and the schema name it owes. Pass None to disable (e.g.
    if the caller has already validated routing and wants to override).
    """
    # the panel dispatches two tiers, so check both — not just the critic tier
    assert_panel_models_differ()
    return [
        _angle_call(f"{label_prefix}:critic{i}", i, payload, phase, subagent_type)
        for i in range(1, len(_CRITIC_ANGLES) + 1)
    ]


MUTANT_LABEL_SUFFIX = "mutant1"


def mutant_call(
    label_prefix: str,
    payload: str,
    angle: int,
    phase: str = "Verify",
    subagent_type: str | None = "critic",
) -> AgentCall:
    """ONE control call on *angle*, carrying a seeded variant of the artifact.

    The payload differs (verify/mutants.py seeded exactly one defect into it);
    nothing else does. The same wide maker != checker guard the panel builds
    under applies here — a control graded by the maker's own model measures
    nothing. The opinion this returns is recorded as a credential and NEVER
    joins the panel's opinions: see `verdict()`.
    """
    assert_panel_models_differ()
    return _angle_call(
        f"{label_prefix}:{MUTANT_LABEL_SUFFIX}", angle, payload, phase, subagent_type
    )


def credential(mutant: MutantRun | None) -> PanelCredential:
    """What this run's control says about the PANEL (never about the artifact).

    No mutant scheduled -> `panel-uncredentialed`: the honest statement that
    nothing was measured. Every non-terminal or failed state — including a
    `pending` control that never landed — reads `mutant-not-measured`, never a
    pass. Nothing here can change `VerifyResult.passed`."""
    if mutant is None:
        return "panel-uncredentialed"
    if mutant.status == "caught":
        return "mutant-caught"
    if mutant.status == "missed":
        return "mutant-missed"
    return "mutant-not-measured"


def verdict(
    mode: VerifyMode,
    opinions: list[CriticOpinion | None],
    mutant: MutantRun | None = None,
) -> VerifyResult:
    assert_panel_models_differ()
    models = resolve_models()
    passed, valid, unblocked = survives(opinions)
    kept = [o for o in opinions if o is not None]
    return VerifyResult(
        mode=mode,
        # computed from the two real critics ALONE. The mutant below rides the
        # record; it is not an input to `survives` and cannot move this bit in
        # either direction (C15: the credential never gates).
        passed=passed,
        maker_model=models.maker,
        critic_model=models.critic,
        opinions=kept,
        valid_critic_count=valid,
        unrefuted_count=unblocked,
        # informational only: refutations the critic itself called cosmetic.
        # They weigh the same as any other refuted-but-non-fatal opinion; this
        # is for the digest, so a reader can see what kind of refutations were
        # filed.
        advisory_count=sum(1 for o in kept if o.refuted and o.severity == ADVISORY_SEVERITY),
        # every VALID opinion refuted but not fatal — the run survived them,
        # but they are still open, recorded on the digest for a human to read.
        # severity is now schema-required when refuted=True (models.py), but
        # a record written before that gate existed can still carry None —
        # match arcs/graph.py's record_verify_notes convention rather than
        # rendering the literal "[None]".
        open_findings=[
            f"[{o.severity or 'unrated'}] {o.reason}"
            for o in kept
            if o.refuted and o.severity != "fatal"
        ],
        # the control's own opinion stays HERE, on its own field: it refutes a
        # defect chimera seeded, so counting it among the panel's findings
        # would put a manufactured refutation on the artifact's record.
        panel_credential=credential(mutant),
        mutant=mutant,
    )
