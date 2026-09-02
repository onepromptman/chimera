# agents-cli cheatsheet

> **Retired-arc reference (v7).** The `gemini` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


One-screen quick reference for building Gemini/ADK agents with the sanctioned
`agents-cli` + `google-agents-cli-*` skills. Full detail lives in
`adk-cli-grounding.md`. Compacted 2026-07-29 from `google.github.io/agents-cli`.

## Setup

```bash
uvx google-agents-cli setup       # install CLI + the 7 skills into your coding agent
agents-cli login -i               # Vertex ADC auth (needed to deploy)
agents-cli login --status         # show active method + project
# local-only alt: export GEMINI_API_KEY=... in .env (works for run/eval/playground, not deploy)
```

Prereqs: Python 3.11+, `uv`, Node.js. Deploy also needs gcloud SDK + Terraform.

## The loop (never skip eval)

```
spec -> scaffold -> build -> (eval-grade  <->  fix)  x5-10+ -> deploy -> observe
```

One behavior change per lap so the grade delta is attributable. Production
traffic feeds tomorrow's evals.

## Skill per phase (activate `google-agents-cli-workflow` first, always)

| Phase | Skill |
|---|---|
| Scaffold / enhance / upgrade | `google-agents-cli-scaffold` |
| Write agent / tool / callback / state | `google-agents-cli-adk-code` |
| Author + run evals | `google-agents-cli-eval` |
| Deploy | `google-agents-cli-deploy` |
| Register in Gemini Enterprise | `google-agents-cli-publish` |
| Tracing / logging / analytics | `google-agents-cli-observability` |

## Command surface

```bash
# scaffold
agents-cli create <name> --prototype --yes        # fast prototype (no CI/CD)
agents-cli scaffold create <name> --agent adk -d agent_runtime --cicd-runner github_actions --bq-analytics
cd <name> && agents-cli install                    # == uv sync
agents-cli scaffold enhance -d cloud_run           # add deploy infra later
agents-cli scaffold upgrade --dry-run              # re-template to newer CLI

# build + test (all LOCAL, in-process)
agents-cli playground                              # web UI, localhost:8080, hot reload
agents-cli run "<prompt>" -v                       # one-off; -v = full JSON events
agents-cli lint --fix

# evaluate
agents-cli eval generate && agents-cli eval grade  # or: agents-cli eval run
agents-cli eval dataset synthesize --count 10
agents-cli eval compare prev.json latest.json
agents-cli eval analyze --eval-result latest.json
agents-cli eval metric list

# deploy + publish + observe (human-owned; the arc never runs these)
gcloud config set project <PROJECT>
agents-cli deploy [--dry-run] [--no-wait] [--status]
agents-cli infra single-project --apply            # telemetry
agents-cli publish gemini-enterprise --registration-type adk

# real deployed-surface probe (the only non-local test):
agents-cli run --url <engine> --mode adk -v
```

## Deploy targets

| Target | Select | Notes |
|---|---|---|
| Agent Runtime | `-d agent_runtime` | Managed; builds from Dockerfile; no `--image`. |
| Cloud Run | `-d cloud_run` | Builds from source; `--memory`, `--image`, `--dry-run` prints gcloud. |
| GKE | `-d gke` | Terraform + kubectl; `--cluster-name`. |

## Gotchas

- `output_schema` + `tools` on one `LlmAgent` is model-dependent (Gemini 3.0+
  supports it; many models don't). Workaround: a separate formatting sub-agent
  (ADK core docs, not the agents-cli guide).
- Tool docstring + input schema IS the contract; the model routes on it. Be
  specific.
- `playground` / `run` / `eval` are local in-process only. Only
  `run --url <engine> --mode adk|a2a` exercises the deployed surface.
- `App(name=...)` must match the agent directory name.
- Secrets by name only (`--secrets`, Secret Manager); never inline in code/.env.
- RAG is not a template: scaffold `adk`, then clone-and-study
  `github.com/google/adk-samples` (`rag-vector-search`, `rag-agent-search`).
