# A2A Orchestration Prompt Kit + Rubric

> **Retired-arc reference (v7).** The `gemini` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


For tuning the prompts of an ADK multi-agent topology (orchestrator + in-process
sub-agents + remote A2A peers). In ADK, **routing is prompt text** — there is no
separate router config. The orchestrator decides `transfer_to_agent` purely from
what the prompts say, so these fields *are* the orchestration logic.

Two halves: a **fill-in template** (what to write) and a **critic rubric** (how
the `gemini` arc's topology-and-prompts stage scores it). Use the template to
draft, the rubric to converge. Convergence: **`min >= 8` across all 7 axes**.

Applies to any hub-and-spoke ADK topology — an `a2a_topology/` with an
`orchestrator/`, in-process `sub_agents/`, and remote `a2a_peers/`.

---

## The four fields you are tuning

| Field | Where | Controls |
|---|---|---|
| orchestrator `instruction` | `orchestrator/agent.py` | routing policy: when to delegate to whom, when to self-handle, when to go remote, how to merge returns |
| sub-agent `description` | `orchestrator/sub_agents/*.py` | the **routing signal** the orchestrator matches against — this is what picks the delegate |
| sub-agent `instruction` | same file | behavior once delegated |
| remote-peer `description` + `instruction` | `a2a_peers/*/agent.py` (+ `remote_peers.py`) | cross-network routing: the peer's `description` becomes its AgentCard skill, advertised to the orchestrator |

### Single-source rule for a peer's description (drift gotcha)

A remote peer's `description` appears in **three** places:
`a2a_peers/<peer>/agent.py` (source of the auto-derived AgentCard),
`orchestrator/remote_peers.py` (the `RemoteA2aAgent` the orchestrator reads),
and the served `/.well-known/agent-card.json`. If they diverge, the orchestrator
routes on stale text. **Write the description once, copy it verbatim to the
`RemoteA2aAgent`, and let `to_a2a` derive the card from the agent.** The rubric's
Agent Card Fidelity axis fails any mismatch.

---

## Part 1 — Prompt template (fill-in)

### Orchestrator `instruction`

```
You are <role>, the coordinator of a <N>-agent team. You do not do the work
yourself; you route each request to exactly one specialist and synthesize what
comes back.

Delegate as follows:
- <subagent_one>: route here when <specific trigger condition>.
- <subagent_two>: route here when <specific trigger condition>.
- <peer_one> (remote): route here when <specific trigger condition>.
Handle yourself ONLY when: <the explicit self-handle case, or "never — always delegate">.
If a request matches two specialists, prefer <tie-break rule>.
If none match, <fallback: ask a clarifying question / decline / default route>.

After a specialist returns, <how to synthesize>: <e.g. "return its answer
verbatim" / "summarize into one paragraph" / "combine peer_one's data with
subagent_two's formatting">. Never re-do a specialist's work.
```

### Sub-agent `description` (the routing signal — be specific, be disjoint)

```
<Verb-first, one line: the exact task class this agent owns and the boundary of
what it does NOT own.>  e.g.
"Parses uploaded CSV exports into structured records. Does not aggregate,
rank, or transmit them."
```

### Sub-agent `instruction` (behavior)

```
You are <name>. Your job: <single responsibility>.
Input you receive: <what the orchestrator hands you>.
Do: <steps / constraints>.
Return to the orchestrator: <exact shape of what you hand back>.
Out of scope (hand back to orchestrator, do not attempt): <boundaries>.
```

### Remote peer `description` (becomes the AgentCard skill)

```
<One line advertising the skill as a capability another org's orchestrator
could discover: what it does, what it needs as input, what it returns.>
```

Fill every `<...>`. A remaining `TODO` or generic description auto-fails the
rubric.

---

## Part 2 — The 7-axis rubric

Each axis 0–10. The critic emits per-axis scores; converge at `min >= 8`.

### 1. routing_coverage
Every plausible user-intent branch maps to exactly one delegate or an explicit
self-handle. No dead branches, no "it depends" gaps.
- 0–3: whole intent classes have no route
- 4–6: common cases routed, edges unhandled
- 7–8: all branches routed, fallback defined
- 9–10: fallback + tie-break + clarifying-question path all explicit

### 2. description_disjointness
No two delegate `description`s overlap enough to cause mis-transfer. This is the
highest-leverage axis — most mis-routing is two vague descriptions competing.
- 0–3: descriptions are near-duplicates or generic ("handles requests")
- 4–6: overlap on some inputs; orchestrator could reasonably pick wrong
- 7–8: boundaries stated; each owns a distinct task class
- 9–10: each description names what it does AND what it does not own

### 3. delegation_discipline
The orchestrator delegates and synthesizes; it does not re-implement a
specialist's job in its own instruction. Sub-agents do not re-route work that is
theirs to finish.
- ≤5 if the orchestrator instruction contains task logic that belongs in a sub-agent.

### 4. return_contract
Each sub-agent states the exact shape it returns, and the orchestrator states
how it merges those returns into a final answer. No "figure it out" seams.
- ≤5 if any agent's return shape is unstated or the merge step is missing.

### 5. boundary_fit (in-process vs remote)
Work is on the right side of the network boundary. Tight-loop or shared-session
work stays in-process (`transfer_to_agent`); domain-bound, independently-scaled,
or separately-owned work goes remote (A2A peer). Wrong side = needless latency or
coupling.
- ≤5 if a chatty tight-loop step is a remote peer, or a heavy independent domain
  is crammed in-process.

### 6. agent_card_fidelity (remote peers)
The peer's advertised skill (`description` → AgentCard) matches what it actually
delivers, and the description is identical across all three locations (see the
single-source rule). Advertised skills the peer cannot do = fail.
- ≤5 on any drift between `agent.py`, `remote_peers.py`, and the served card.

### 7. register_fit (delegation depth + persona)
The orchestrator's voice is a coordinator, not a doer; each specialist's persona
fits its narrow job. No over-delegation (bouncing a one-line answer through three
hops) and no under-delegation (orchestrator hoarding work it should hand off).
- ≤5 if a single request needs 3+ hops for a task one agent owns.

---

## Forbidden moves (any sighting → relevant axis ≤5)

- A `description` left as `TODO` or generic ("handles user requests", "does
  tasks") — description_disjointness ≤3.
- Two sub-agents whose descriptions could both match the same input — mis-routing
  by construction.
- Orchestrator `instruction` that contains the actual task steps of a sub-agent
  (the orchestrator "helpfully" doing the work) — delegation_discipline.
- Peer description that differs between `agent.py` and `remote_peers.py` —
  agent_card_fidelity.
- Remote peer for a step that shares tight session state with the orchestrator —
  boundary_fit.
- Missing fallback: nothing said about what happens when no delegate matches —
  routing_coverage.
- Synthesis step unstated: sub-agents return, orchestrator's merge behavior is
  silent — return_contract.
- Advertising a skill in the AgentCard the peer's instruction cannot deliver.
- ALL-CAPS or emphatic prose standing in for a precise routing rule.

---

## Scoring honesty

- Descriptions that "look fine" usually overlap. Push on axis 2 — construct the
  ambiguous input that would mis-route, and see if the descriptions resolve it.
- A 9–10 requires you to trace at least 3 concrete requests end-to-end and show
  each lands at exactly one agent and returns cleanly.
- Multiple critics independently; disagreement on which agent an input routes to
  IS the disjointness signal — do not smooth it over.

---

## Convergence and escalation

- `min >= 8` across all 7 axes → converged → ship the topology prompts.
- `min < 8` → iterate; the next pass fixes the bottom axes only (usually
  description_disjointness), surgically — do not rewrite the whole topology.
- Iteration 3+ with avg drift `<0.3` → escalate (the drafts aren't responding).
- Iteration 5 without convergence → escalate to the operator (the topology itself may be
  wrong — too many agents, or boundaries drawn in the wrong place).
