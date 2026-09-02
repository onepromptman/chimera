# ADK CLI grounding (gemini arc - emit stage)

> **Retired-arc reference (v7).** The `gemini` arc no longer dispatches — it lives only in `models.RETIRED_ARCS`. This file is kept as distilled craft: the planner composes this shape as data on the one live arc (`graph`). Read arc-present-tense below as describing the shape, not a live execution surface.


Chimera-internal reference. Injected into the gemini arc's `emit` prompt for
`adk_agent` and `a2a_topology` artifacts so the driving session's specialist
(`adk-builder`) drives the **real, sanctioned `agents-cli` wrapper** and the
target project's **skeleton layout**, instead of free-writing agent code or
shelling to the bare `adk` CLI directly. Brand-agnostic: no project-, brand-,
or deliverable-specific names appear here.

The spine below is a faithful, compacted rendering of the official
`agents-cli` guide (development, lifecycle, project-structure, getting-started,
authentication, evaluation, deployment, cicd, observability, cli). Follow it as
written; the only chimera-specific layers are the record-only boundary and the
"Chimera arc contract" section. Compacted 2026-07-29 from
`google.github.io/agents-cli`.

## Record-only boundary (read first)

The arc never runs these commands and never writes target code. The **driving
session's specialist** scaffolds/edits files inside the target repo's
`skeleton/` fork and reports the paths; the arc records `emitted_paths` only.
Never run `agents-cli deploy`, `agents-cli publish`, `terraform apply`,
`gcloud`, or any live registration from an arc - shipping is the post-G2,
human-gated `/deploy` path. `agents-cli scaffold`, `agents-cli run`, and
`agents-cli playground` are scaffold-and-verify-local only. There is no
OPA/rego/conftest/Gatekeeper policy gate on this path - that governed the
retired GKE-cluster posture and does not apply to the vanilla Agent Runtime
target.

---

# The `agents-cli` development guide (compacted)

## Prerequisites & install

- **Required:** Python 3.11+, `uv`, Node.js (for skills install).
- **Optional (deployment):** Google Cloud SDK, Terraform.
- **Platforms:** macOS, Linux, Windows (WSL 2). Native Windows unsupported.

```bash
uvx google-agents-cli setup           # primary
# alternatives:
pipx install google-agents-cli && agents-cli setup
pip install google-agents-cli && agents-cli setup
npx skills add google/agents-cli
```

`setup` installs the CLI + the seven `google-agents-cli-*` skills into detected
coding agents (Antigravity CLI, Claude Code, Codex, any skills-protocol agent).
Verify with `/skills`.

## Authentication (three independent levels)

1. **Coding-agent auth** - your coding agent's own login (Anthropic account/key,
   Google account, OpenAI key). `agents-cli` does not control it.
2. **Model auth** - the agent being built calls an LLM:
   - *Gemini API key (AI Studio):* `export GEMINI_API_KEY="..."` in `.env`. No
     GCP project needed. **Local commands only** (`playground`, `run`, `eval`);
     cannot deploy.
   - *Vertex AI (GCP):* required for Vertex models, enterprise features, and any
     deploy.
     ```bash
     agents-cli login -i                 # or: gcloud auth application-default login
     gcloud config set project YOUR_PROJECT_ID
     export GOOGLE_CLOUD_LOCATION="us-east1"
     export GOOGLE_GENAI_USE_VERTEXAI=TRUE
     ```
3. **Deployment auth** - the same Vertex ADC credential from level 2B. Needs a
   GCP project with billing enabled and target-appropriate IAM.

Check with `agents-cli login --status`.

## Lifecycle - eight phases

Four core verbs rotate the loop: **scaffold -> eval -> deploy -> observe.**

| # | Phase | What you do | Key command(s) |
|---|---|---|---|
| 0 | **Spec** | Define problem, tools, constraints, success criteria in `.agents-cli-spec.md`. The whole lifecycle reads this file. | (author `.agents-cli-spec.md`) |
| 1 | **Scaffold** | Generate the project package. | `agents-cli scaffold create <name> --agent adk -d <target>` then `agents-cli install` |
| 2 | **Build** | Edit `app/agent.py`: instruction, model, tools. | `agents-cli playground`, `agents-cli run "<prompt>"` |
| 3 | **Orchestrate** | Route work to specialists; A2A is the wire format for remote peers. | (edit agent/topology) |
| 4 | **Evaluate** | Grade against a rubric; iterate. Expect 5-10+ laps. | `agents-cli eval generate` / `eval grade` |
| 5 | **Deploy** | Ship to Agent Runtime / Cloud Run / GKE. | `agents-cli deploy` |
| 6 | **Publish** | Register in the Gemini Enterprise catalog (optional). | `agents-cli publish gemini-enterprise` |
| 7 | **Observe** | Cloud Trace by default; opt-in BigQuery analytics. | `agents-cli infra single-project` |

**Phase 0 spec** (`.agents-cli-spec.md`) captures: a `## Tools` table
(tool -> backing service), numbered `## Constraints` (prohibited/required
actions), and measurable `## Success criteria`.

**Phase 1 scaffold** - full form and prototype form:

```bash
# full project (~72 files: agent, tests, eval, Terraform, CI/CD, manifests)
agents-cli scaffold create <name> \
  --agent adk \
  --deployment-target agent_runtime \   # agent_runtime | cloud_run | gke | none
  --cicd-runner github_actions \        # github_actions | google_cloud_build | skip
  --bq-analytics
cd <name> && agents-cli install         # == uv sync

# rapid prototype (no CI/CD, no Terraform); add infra later
agents-cli create <name> --prototype --yes
agents-cli scaffold enhance -d cloud_run
```

Optional scaffold flags: `--session-type agent_platform_sessions` (managed
session storage vs the `in_memory` default), `--iap` (gate Cloud Run behind
Workspace SSO), `--agent-identity` (per-agent service account), `--adk`
(quickstart = adk + agent_runtime + prototype).

## Skills and the development loop (best practice)

Drive the lifecycle through the sanctioned `google-agents-cli-*` skills, not
hand-rolled probes, venvs, or scripts. Activate `google-agents-cli-workflow`
first (it carries the lifecycle, the "shortcuts to resist", and the systematic
debugging rules), then activate the phase's skill before each phase:

| Phase / task | Skill to activate |
|---|---|
| Any ADK work (lifecycle spine, always on) | `google-agents-cli-workflow` |
| Scaffold / enhance / upgrade a project | `google-agents-cli-scaffold` |
| Write agent / tool / callback code, state | `google-agents-cli-adk-code` |
| Author and run evals | `google-agents-cli-eval` |
| Deploy (Agent Runtime / Cloud Run / GKE) | `google-agents-cli-deploy` |
| Register in Gemini Enterprise | `google-agents-cli-publish` |
| Tracing / logging / analytics | `google-agents-cli-observability` |

**The loop is a loop, not a line:**

```
spec -> scaffold -> build -> (eval-grade  <->  fix)  x5-10+ -> deploy -> observe
                                    ^                                        |
                                    +-- production traffic feeds evals ------+
```

Rules: never skip eval; iterate on the instruction, tools, or logic (not by
re-running the same input); change one behavior per lap so a grade delta is
attributable; budget 5-10+ grade laps before deploy. Because the arc is
record-only it does NOT run this loop, it RECORDS it: the emit specialist notes
the loop and which skill drives each step as the required post-emit workflow the
human owns.

## Templates

The only template is **`adk`** - a ReAct agent using ADK, which **serves the
A2A protocol by default** (agent card + JSON-RPC routes mounted automatically;
interoperates with agents on any framework, no hand-written A2A code).

**RAG is not a template** - it is a clone-and-study recipe: scaffold a base
`adk` project, then adapt a sample from `github.com/google/adk-samples`:
- `rag-vector-search` - Vertex AI Vector Search 2.0 + custom ingestion pipeline.
- `rag-agent-search` - Agent Platform Search (Discovery Engine) + managed GCS
  data connector.
Sample setup uses `make setup-infra` and `make data-ingestion`.

## Project structure

```
my-agent/
├── app/
│   ├── __init__.py            # registers/exports `app`
│   ├── agent.py               # agent definition: instruction, model, tools
│   ├── fast_api_app.py        # FastAPI server: telemetry, feedback/A2A routes
│   └── app_utils/{services,a2a,typing}.py
├── tests/
│   ├── eval/datasets/basic-dataset.json   # default eval cases
│   ├── eval/eval_config.yaml              # metrics config
│   ├── integration/test_agent.py
│   └── unit/test_dummy.py
├── pyproject.toml             # config + deps (google-adk[gcp]>=2.0.0,<3.0.0)
├── agents-cli-manifest.yaml   # agents-cli config (agent_directory, create_params)
├── GEMINI.md                  # guidance file auto-read by coding agents
├── Makefile                   # make dev / make eval shortcuts
├── .env                       # GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
└── uv.lock
```

With a deployment target (or after `scaffold enhance`): add
`deployment/terraform/{dev,staging,prod,variables.tf}` and CI/CD under
`.github/workflows/` (github_actions) or `.cloudbuild/` (google_cloud_build),
each with `pr_checks.yaml`, `staging.yaml`, `deploy-to-prod.yaml`.

### `app/agent.py` convention

```python
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

MODEL = "gemini-2.5-flash"   # change the model via this constant

def get_weather(query: str) -> str:
    """Docstring IS the tool contract - the model routes on it. Be specific."""
    ...

root_agent = Agent(
    name="root_agent",
    model=Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=3)),
    instruction="You are a helpful AI assistant.",
    tools=[get_weather],
)

app = App(root_agent=root_agent, name="app")   # name MUST match the agent dir
```

Four required components: tool functions (plain Python + docstrings), the
`Agent`, the `App` (`name` matches the directory), and the model. The agent body
is ~30 lines; real work lives in tools. ADK supports Gemini plus Anthropic Claude
and OpenAI GPT via Model Garden. For conversation state across restarts, wire a
session/memory service (e.g. `VertexAiMemoryBankService`).

**ADK note (ADK core docs, not the agents-cli guide):** using `output_schema`
together with `tools=[...]` on an `LlmAgent` is model-dependent - Gemini 3.0+
supports it; on many other models the tool calls may not fire reliably. When the
combination is unsupported, drop `output_schema` on the tool-using agent and let
a separate sub-agent handle structured output formatting (ADK's recommended
workaround).

`agents-cli-manifest.yaml` keys: `name`, `agent_directory` (where agent code
lives), `create_params: {deployment_target, session_type}` (preserved so
`agents-cli scaffold upgrade` can re-template).

## Command surface

Use the sanctioned `agents-cli` wrapper, never the bare `adk` binary.

| Group | Command | Notes |
|---|---|---|
| Scaffold | `scaffold create <name> --agent adk -d <target>` / `create <name> --prototype --yes` | `create` == `scaffold create`. `--adk` = quickstart bundle. |
| Enhance | `scaffold enhance [.] -d <target> [--dry-run]` | Add deploy/CI-CD to a prototype without re-creating. |
| Upgrade | `scaffold upgrade [--dry-run] [-y]` | Re-template to a newer `agents-cli`; preview drift first. |
| Install | `install [--clean] [--locked]` | `uv sync`; `--clean` repairs the venv; `--locked` asserts lockfile matches. |
| Run | `run "<prompt>" [-v] [-f FILE] [--session-id ID] [--start-server]` | One-off local test; `-v` prints full JSON events (tool calls, silent failures). |
| Playground | `playground [--port 8080] [--no-reload_agents]` | Web dev UI at `localhost:8080`, hot reload. |
| Lint | `lint [--fix] [--mypy]` | ruff + codespell + ty (+ mypy). |
| Eval | `eval generate` / `eval grade` / `eval run` (chains both) | Plus `dataset synthesize`, `compare`, `analyze`, `optimize`, `metric list`, `submit`, `results`. |
| Deploy | `deploy [-d agent_runtime\|cloud_run\|gke] [--dry-run] [--no-wait] [--status] [--list]` | Reads `deployment_target` from the manifest if `-d` omitted. **Arc: record intent, never run.** |
| Infra | `infra single-project [--project ID] [--apply]` / `infra setup-cicd ...` | Provision telemetry / CI-CD (default is plan-only; `--apply` to execute). |
| Publish | `publish gemini-enterprise [--registration-type adk\|a2a]` | Register in the catalog. **Arc: out of scope.** |
| Login | `login -i` / `login --status` | Interactive auth / show active method + project. |
| Info | `cmd-info [--json]` | Project config, paths, CLI version. |

Prefer `scaffold create` to establish the package, then edit generated files;
do not hand-roll the layout the CLI already produces.

## Evaluate

Local behavioral gate - run before any deploy. Never skip it; production merges
require passing rubric scores.

```bash
agents-cli eval dataset synthesize --count 10   # optional cold-start dataset
agents-cli eval generate                        # run inference over eval cases
agents-cli eval grade                            # score vs metrics (repeat)
agents-cli eval compare prev.json latest.json    # confirm a fix helped
agents-cli eval analyze --eval-result latest.json # cluster failure modes
agents-cli eval optimize                         # auto-tune prompts (GEPA)
agents-cli eval metric list                      # list built-in metrics
```

**Eval-fix loop:** write 1-2 core cases -> `generate` -> `grade` -> read failures
-> fix instruction/tools/logic -> re-run -> expand coverage. Budget 5-10+ laps.

**Built-in metrics** (`eval metric list` for the live set):

| Metric | Purpose |
|---|---|
| `general_quality` | Overall response quality (auto content criteria). |
| `text_quality` | Fluency, coherence, grammar. |
| `instruction_following` | Adherence to constraints/instructions. |
| `tool_use_quality` | Tool selection, params, step sequence (single-turn). |
| `multi_turn_tool_use_quality` | Tool correctness across turns. |
| `multi_turn_trajectory_quality` | Sequential logic, efficiency, error recovery. |
| `multi_turn_task_success` | Goal fulfillment across the conversation. |
| `final_response_quality` / `_reference_free` / `_match` | Final answer (with/without golden ref). |
| `hallucination` | Claims vs tool-returned context. |
| `grounding` | Factuality/consistency vs context. |
| `safety` | PII, hate, dangerous content, harassment, sexual. |

Metric picks: custom-tool agents -> `tool_use_quality` (or the multi-turn pair);
RAG -> `grounding` + `hallucination` + `safety`; conversational -> `general_quality`;
goal-oriented -> `multi_turn_task_success`.

`tests/eval/eval_config.yaml` declares `metrics_to_run` plus `custom_metrics` -
either code-execution (`custom_function` with `def evaluate(instance) -> dict`,
local by default; `"execution": "remote"` for Vertex sandbox) or LLM-as-judge
(`prompt_template`, optional `judge_model`, `judge_model_sampling_count` 1-32).
Default dataset: `tests/eval/datasets/basic-dataset.json`. Legacy
`tests/eval/evalsets/*.evalset.json` needs migration.

## Deploy

`agents-cli infra` **provisions** cloud resources (service accounts, IAM, APIs,
telemetry); `agents-cli deploy` **runs** agent code on that infrastructure.
`deploy` reads `deployment_target` from `agents-cli-manifest.yaml` when `-d` is
omitted. Set the project first: `gcloud config set project YOUR_DEV_PROJECT_ID`.

| Target | Selection | Traits | Notable flags |
|---|---|---|---|
| **Agent Runtime** | `-d agent_runtime` | Fully managed; builds from the scaffolded `Dockerfile`; no cluster/service to manage. | `--build-args K=V`, `--port`, `--no-wait`, `--status`. Prebuilt `--image` not supported. |
| **Cloud Run** | `-d cloud_run` | Builds a container from source, deploys a service. | `--memory 8Gi`, `--image gcr.io/...:v1`, `--dry-run` prints the full gcloud command. |
| **GKE** | `-d gke` | Terraform + kubectl; needs a cluster. | `--cluster-name my-cluster`. |

Common deploy flags: `--dry-run` (preview), `--no-wait` + `--status` (async),
`--list`, `--secrets ENV=SECRET,...`, `--update-env-vars K=V,...`,
`--agent-identity` (per-agent SA), `--iap` (Cloud Run SSO gate),
`--no-confirm-project`. Verify: `agents-cli deploy --list` / `--status`. Change
target on an existing project with `agents-cli scaffold enhance -d <target>`.

**Straightforward single-project path:** `login -i` -> set project -> `deploy`
(reads target from manifest) -> `agents-cli infra single-project --apply` for
telemetry -> `publish gemini-enterprise`.

## CI/CD (full projects)

Three stages: **CI** on PR (unit + integration tests) -> **staging CD** on `main`
merge (build image -> Artifact Registry -> deploy staging -> load test) ->
**production** (manual approval -> deploy the tested image).

```bash
agents-cli infra setup-cicd \
  --staging-project my-staging \
  --prod-project my-prod \
  [--dev-project ...] [--cicd-project ...] [--repository-owner ...] \
  [--repository-name ...] [--create] [--cicd-runner github_actions] [--apply]
```

Runner is auto-detected: **GitHub Actions** (from `wif.tf`, uses Workload
Identity Federation - no service-account keys in the repo) or **Cloud Build**.
Terraform variables include `project_name`, `prod_project_id`,
`staging_project_id`, `cicd_runner_project_id`, `region` (default `us-west1`),
`repository_name`, `repository_owner`, `app_sa_roles`, `cicd_roles`.

## Publish

```bash
agents-cli publish gemini-enterprise --registration-type adk   # or a2a
```

Lists the agent in the Gemini Enterprise catalog for discovery. **ADK mode**
publishes the deployed Agent Runtime instance (native `:streamQuery`) - the
default and recommended mode on Agent Runtime. **A2A mode** publishes an
A2A-compatible HTTP endpoint - the only mode on Cloud Run/GKE (no reasoning
engine).

## Observe

Cloud Trace is automatic on every deployed target (spans per invocation: tool
calls, model generation, sub-agent handoffs), with **message content excluded by
default**. Content logging is opt-in and separate:

| Surface | Env / flag |
|---|---|
| Prompt-response logging (GCS JSONL + BigQuery `completions`) | `LOGS_BUCKET_NAME` set (on when Terraform-provisioned; off for bare `deploy`). |
| Content in traces/events | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (default `NO_CONTENT`). |
| Content in spans | `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`. |
| BigQuery Agent Analytics | scaffold with `--bq-analytics`. |

Provision telemetry with `agents-cli infra single-project`. Production traffic
feeds tomorrow's eval dataset - scores recompute continuously for regression
detection.

## Do / don't (from the guide)

- **Do** start from `scaffold create`; edit generated files rather than
  hand-rolling layout. **Do** keep the model behind the `MODEL` constant and
  change it surgically.
- **Do** gate every deploy on passing evals; expect 5-10+ grade laps.
- **Do** write tool docstrings/schemas as the contract - specific and grounded.
- **Model-gated:** `output_schema` + `tools` in one request works on Gemini 3.0+
  but not reliably on many other models; use a formatting sub-agent as the
  workaround (ADK core docs, not the agents-cli guide).
- **Don't** put secrets in code or `.env` values that get committed - reference
  them by name; use `--secrets` / Secret Manager on deploy.
- **Don't** treat local `playground`/`run`/`eval` as proof of the deployed
  surface (see below).

---

# Chimera arc contract

## Skeleton layout the arc emits into

The target repo carries a `skeleton/` fork; emit into the matching subtree per
artifact type (paths are the record-only pointers the arc stores):

| Artifact type | Emit into |
|---|---|
| `adk_agent` | `skeleton/agent/` (agent.py, config.py, prompts/system.md, tools/) |
| `a2a_topology` | `skeleton/a2a_topology/orchestrator/` (agent.py, remote_peers.py), `a2a_peers/`, `tools/mcp_toolset.py` |
| `mcp_connector` | `skeleton/mcp-servers/<server>/` (server.py, tools.yaml, Dockerfile, pyproject.toml) |
| `data_store` / `rag_corpus` / `grounding` | `skeleton/infra-templates/` (Terraform + REST spec, record-only) - see the `data-store-grounding` reference for what these surfaces return. |

Tooling contract: **tool descriptions and input schemas ARE the contract** -
the model routes on them. Prefer **MCP** (`McpToolset`) to reach external
tools/data; reserve **A2A** (`RemoteA2aAgent` + `remote_peers`) for genuine
remote / independently-owned / separately-scaled agents; use in-process
`sub_agents` + `transfer_to_agent` for tight-loop delegation. In ADK, routing IS
prompt text - the orchestrator instruction and each sub-agent / remote-peer
`{description, instruction}` are the orchestration logic (the arc's converged
`A2APromptSet` fills exactly these fields).

## Real-surface testing is a recorded requirement

`playground`, bare `run "prompt"`, and `eval` all execute the agent **locally
in-process** - no network hop, no reasoning-engine adapter, no A2A layer. Only
`agents-cli run --url <deployed-engine> --mode adk|a2a` makes a real HTTP call
against a deployed agent; for Agent Runtime this is the closest proxy for what
Gemini Enterprise itself calls (the `:streamQuery` contract). Since the arc
never deploys, the emit-stage specialist **records** in its `EmitManifest`/build
report that local verification is necessary-but-not-sufficient and that
real-surface verification (`run --url ... --mode adk`, post-deploy, post-G2) is
a required follow-up the human owns.
