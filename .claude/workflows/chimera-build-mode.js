export const meta = {
  name: 'chimera-build-mode',
  description: 'Plan → make → test → one repair lap → diff → 3-critic REFUTE over a real target repo worktree, with a chimera ledger handoff. Pre-PR diff check only: the same two REFUTE angle NAMES as verify/lite.py, re-worded for a diff.',
  whenToUse: 'Run from a TARGET repo worktree (never chimera itself) to land a bounded code change with chimera rigor: fenced roles, maker≠checker, the same two REFUTE angle NAMES as verify/lite.py (the angle TEXTS differ — these are pointed at a diff and a test run, not at prose), and a verdict the chimera ledger can record. This panel runs BEFORE the PR exists, so it proves the diff half only; the PR half is proved by chimera\'s own verify panel at `chimera tick`. Args: {ask, targetPath, testCommand, baseBranch, sensitivity}. `sensitivity` must be "personal" — ADR-011 (e).3 keeps corp/third-party targets out of this lane entirely.',
  phases: [
    { title: 'Plan', detail: 'one read-only planner emits a bounded list of change units', model: 'opus' },
    { title: 'Make', detail: 'one maker per unit, sequential in the target worktree (Read/Grep/Glob/Write/Edit)', model: 'opus' },
    { title: 'Test', detail: 'one shell-only executor runs the test command, verbatim output + exit code' },
    { title: 'Repair', detail: 'at most ONE lap: maker fixes the failure, executor re-runs', model: 'opus' },
    { title: 'Diff', detail: 'shell-only executor captures git diff --stat and git diff vs the base branch' },
    { title: 'REFUTE', detail: 'three read-only critics on the critic tier, the verify/lite.py angle NAMES re-worded for a diff', model: 'sonnet' },
    { title: 'Verdict', detail: 'pure JS fatal-only veto rule, ported from lite.survives' },
  ],
}

// ---------------------------------------------------------------------------
// chimera build mode — the ADR-011 seam, proven as a Workflow first (audit Q4).
//
// This script drives the WORK. It does not touch the chimera ledger: workflows
// have no shell, so the driving session makes the `chimera arc submit` calls
// from the handoff printed at the end. Fences are enforced by `agentType`,
// which resolves the six role subagents written by `chimera install-agents`
// (planner read-only, maker write-no-shell-no-net, executor shell-no-write,
// critic read+web-no-write). If those roles are not installed, the first
// agent() fails loudly — which is the correct failure, not a silent unfenced
// run.
// ---------------------------------------------------------------------------

const MAX_UNITS = 6
const MAX_REPAIR_LAPS = 1

const ask = (args && args.ask) || ''
const targetPath = (args && args.targetPath) || ''
const testCommand = (args && args.testCommand) || ''
const baseBranch = (args && args.baseBranch) || 'main'
const sensitivity = (args && args.sensitivity) || ''

// Every refusal below returns the SAME null-shaped result the rest of the
// script uses for an early return, so a caller never has to tell "refused"
// apart from "ran and found nothing" by shape — `passed: false` covers both.
const REFUSED = { plan: null, diffStat: null, diff: null, testOutput: null, exitCode: null, opinions: [], passed: false }

if (!ask || !targetPath || !testCommand) {
  log('REFUSED: chimera-build-mode needs args {ask, targetPath, testCommand, baseBranch, sensitivity}.')
  return REFUSED
}

// Behavioral Rule 7 — one repo, one purpose: chimera worktrees and branches
// are for framework work only, and work aimed at another repo runs in THAT
// repo's checkout. A targetPath with a path segment named `chimera` is a
// request to make this workflow build inside the ledger it reports to, which
// is also the exact motion Security Rule 1 forbids. Refuse rather than warn:
// by the time the makers have written, the boundary is already crossed.
if (/(?:^|[/\\])chimera(?:[/\\]|$)/i.test(targetPath)) {
  log(
    `REFUSED: targetPath ${targetPath} contains a path segment named "chimera". ` +
      'Behavioral Rule 7 (one repo, one purpose) — chimera is the LEDGER for this run, never ' +
      'the target; work aimed at another repo runs in that repo\'s checkout. Re-run with a ' +
      'worktree of the actual target repo.',
  )
  return REFUSED
}

// ADR-011 (e).3 — corp/third-party targets run on-box, never on this lane, and the
// mode "refuses rather than degrades". The declaration is an operator input,
// so this is discipline made explicit (ADR-011 Consequences), not a proof:
// it makes the operator state the target class before any maker writes.
if (sensitivity !== 'personal') {
  log(
    `REFUSED: sensitivity is ${sensitivity ? JSON.stringify(sensitivity) : 'unset'}; this lane runs ` +
      'ONLY with sensitivity="personal". ADR-011 (e).3: corp/third-party targets run on-box and never ' +
      'here — this lane holds no corp credentials and bypasses the untracked pre-push guard ' +
      '(ADR-006). The mode refuses rather than degrades; there is no flag that widens it.',
  )
  return REFUSED
}

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['summary', 'units'],
  properties: {
    summary: { type: 'string', description: 'one-paragraph approach' },
    units: {
      type: 'array',
      minItems: 1,
      maxItems: MAX_UNITS,
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'title', 'files', 'instruction'],
        properties: {
          // Same pattern as models.SLUG_PATTERN, which models.GraphNodeOutput
          // .node_id enforces. The handoff maps each unit id onto a graph node
          // id, so a unit id that is not a clean kebab-case slug fails the
          // chimera schema gate at `arc submit`, after the work is already done.
          id: {
            type: 'string',
            pattern: '^[a-z0-9]+(-[a-z0-9]+)*$',
            description: 'kebab-case slug, unique in this plan; becomes the chimera graph node id',
          },
          title: { type: 'string' },
          files: { type: 'array', items: { type: 'string' }, description: 'repo-relative paths this unit may touch' },
          instruction: { type: 'string', description: 'what to change, precisely enough for a maker with no other context' },
        },
      },
    },
    risks: { type: 'array', items: { type: 'string' } },
  },
}

const MAKE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['unit_id', 'files_changed', 'summary', 'complete'],
  properties: {
    unit_id: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    complete: { type: 'boolean', description: 'false if the unit could not be finished as specified' },
    blocked_reason: { type: 'string' },
  },
}

const TEST_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['command', 'exit_code', 'output', 'passed'],
  properties: {
    command: { type: 'string', description: 'the command as actually run, verbatim' },
    exit_code: { type: 'integer' },
    output: { type: 'string', description: 'combined stdout+stderr, verbatim, never summarized' },
    passed: { type: 'boolean', description: 'exit_code === 0' },
    truncated: { type: 'boolean', description: 'true if only the tail of the output is carried' },
  },
}

const DIFF_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['diff_stat', 'diff', 'files_changed'],
  properties: {
    diff_stat: { type: 'string', description: 'git diff --stat output, verbatim' },
    diff: { type: 'string', description: 'unified diff, verbatim — never summarized or re-indented' },
    files_changed: { type: 'array', items: { type: 'string' } },
    truncated: { type: 'boolean' },
  },
}

// Identical to models.CriticOpinion so an opinion reads as one, but these are
// NOT submitted as `verify:criticN`: this panel runs before the PR exists, so
// it proves the diff half only. chimera's own verify panel (lite.critic_calls,
// printed by `chimera tick` at the verify stage, over a wrap body that carries
// the PR URL) is what fills those labels. These opinions ride INTO that wrap
// body as evidence.
const CRITIC_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['refuted', 'reason', 'severity'],
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    severity: { type: 'string', enum: ['fatal', 'material', 'cosmetic'] },
  },
}

// The three angles of src/chimera/verify/lite.py::_CRITIC_ANGLES, pointed at a
// diff + a test run instead of prose. Same wording discipline: default
// refuted=true on uncertainty.
const ANGLES = [
  {
    name: 'general REFUTE',
    instructions:
      'Try to REFUTE this change. Default refuted=true on uncertainty. Examine the logic of the ' +
      'diff, its internal consistency, whether it actually accomplishes the ask, whether it ' +
      'breaks an adjacent behaviour, and what concrete scenario would falsify the claim that this ' +
      'change is correct.',
  },
  {
    name: 'GREP-ANCHOR check',
    instructions:
      'Verify every claim against the repository itself. Read each file the diff touches and ' +
      'confirm the hunks are actually present and do what the summary says. Confirm the test ' +
      'command in the reported output matches the command that was asked for, and that the ' +
      'reported exit code is consistent with the output text. Any failed substantiation -> ' +
      'refuted=true. Default refuted=true if you cannot verify within reasonable effort.',
  },
  {
    name: 'MISSING-SOURCE check',
    instructions:
      'Identify what is MISSING. A behaviour change with no test exercising it, an error path the ' +
      'diff introduces and never covers, a caller of a changed signature left unupdated, a config ' +
      'or doc the change invalidates -> refuted=true. Default refuted=true on absence-of-evidence.',
  },
]

// Port of verify/lite.py::survives — fatal-only veto (2026-09-16). The
// proportional-majority rule this used to port is gone from the engine too:
// four real runs showed every refutation that ever killed a run was graded
// `material`, never `fatal`. Only a fatal refutation fails the run now;
// every other refutation survives and is left for the reader to weigh.
function survives(opinions) {
  const valid = opinions.filter(Boolean)
  const unrefuted = valid.filter((o) => !o.refuted).length
  const fatal = valid.some((o) => o.refuted && o.severity === 'fatal')
  return {
    passed: valid.length >= 2 && !fatal,
    valid: valid.length,
    unrefuted,
  }
}

function fence(role) {
  return (
    `You are a chimera graph ${role} node. Operate INSIDE ${targetPath} — every path you ` +
    `touch is relative to that root, and you never read or write outside it. You never run ` +
    `git commit, git push, or gh pr create: the driving session owns those.`
  )
}

// ---------------------------------------------------------------------------

log(`chimera build mode — target ${targetPath} (sensitivity: ${sensitivity}), base ${baseBranch}, tests \`${testCommand}\``)
log(`Change units are capped at ${MAX_UNITS}; repair laps at ${MAX_REPAIR_LAPS}. A plan that needs more must be split into two tasks.`)

// ----- Plan ----------------------------------------------------------------
phase('Plan')

const plan = await agent(
  `${fence('planner')} You hold read-only tools; you produce the plan, you never make the change.\n\n` +
    `THE ASK:\n${ask}\n\n` +
    `Read the relevant code in ${targetPath} first, then decompose the ask into AT MOST ${MAX_UNITS} ` +
    `independent change units. A unit is one coherent edit a single maker can complete with no ` +
    `context beyond its own instruction and the files it names. Units must not overlap on files — ` +
    `two units editing the same file is a planning error, merge them. Name real repo-relative ` +
    `paths that exist (or are deliberately new). If the ask genuinely needs more than ${MAX_UNITS} ` +
    `units, return the ${MAX_UNITS} that unblock the rest and say so in \`risks\`.`,
  { label: 'plan', phase: 'Plan', model: 'opus', agentType: 'planner', schema: PLAN_SCHEMA },
)

if (!plan || !plan.units || !plan.units.length) {
  log('Plan stage returned nothing — halting before any edit. Nothing was changed in the target.')
  return REFUSED
}

log(`Plan: ${plan.units.length} change unit(s) — ${plan.units.map((u) => u.id).join(', ')}`)
if (plan.risks && plan.risks.length) log(`Planner risks: ${plan.risks.join(' | ')}`)

// ----- Make ----------------------------------------------------------------
// SEQUENTIAL, deliberately. `isolation: 'worktree'` exists and would let these
// run concurrently, but each maker would then land its edits in a SEPARATE
// worktree with no merge-back available to the script API — the Test and Diff
// stages would see an incomplete tree. One shared worktree, one maker at a
// time, is the only shape where the composite change is what gets tested.
phase('Make')

const made = []
for (let i = 0; i < plan.units.length; i++) {
  const unit = plan.units[i]
  const result = await agent(
    `${fence('maker')} You hold Read/Grep/Glob/Write/Edit — no shell, no network. You author the ` +
      `change; you do not test it and you do not judge it.\n\n` +
      `TASK GOAL:\n${ask}\n\n` +
      `OVERALL APPROACH (context only — do NOT implement other units):\n${plan.summary}\n\n` +
      `YOUR UNIT (${unit.id} — ${i + 1} of ${plan.units.length}):\n${unit.title}\n${unit.instruction}\n\n` +
      `FILES YOU MAY TOUCH: ${unit.files.join(', ')}\n\n` +
      `Match the surrounding code's conventions. Make the change and stop — do not refactor ` +
      `adjacent code, do not touch files outside your list, do not add unrelated tests. If the ` +
      `unit cannot be completed as specified, set complete=false and say exactly why rather than ` +
      `improvising a different change.`,
    { label: `make:${unit.id}`, phase: 'Make', model: 'opus', agentType: 'maker', schema: MAKE_SCHEMA },
  )
  if (result) made.push(result)
  else log(`Unit ${unit.id} returned null (skipped or failed) — it is NOT in the change set.`)
}

const incomplete = made.filter((m) => m.complete === false)
if (incomplete.length) log(`Incomplete units carried forward as findings: ${incomplete.map((m) => m.unit_id).join(', ')}`)
if (made.length < plan.units.length) log(`Coverage: ${made.length} of ${plan.units.length} units landed a result.`)

// ----- Test ----------------------------------------------------------------
phase('Test')

const TEST_PROMPT =
  `${fence('executor')} You hold Bash only — you run commands and report, you never edit a file.\n\n` +
  `Run EXACTLY this command in ${targetPath}, once:\n\n    ${testCommand}\n\n` +
  `Report its combined stdout+stderr VERBATIM and its real exit code. Do not summarize, ` +
  `re-order, or clean up the output — a paraphrased test run is not evidence. If the output ` +
  `exceeds roughly 400 lines, carry the LAST 400 lines and set truncated=true. Never invent an ` +
  `exit code: if the command did not run, report the shell's failure verbatim with its code.`

let test = await agent(TEST_PROMPT, {
  label: 'test',
  phase: 'Test',
  agentType: 'executor',
  schema: TEST_SCHEMA,
})

// COMMAND DRIFT: the executor reports the command it says it ran. If that is
// not the command the operator asked for, every exit code below is evidence
// about a DIFFERENT run. Log it loudly and carry it to the panel (the evidence
// block states the requested command); never fail the run on it — the
// GREP-ANCHOR critic decides whether the drift is refutable.
function commandDrift(reported) {
  if (!reported) return null
  return reported.trim() === testCommand.trim() ? null : reported.trim()
}

if (!test) log('Test stage returned null — exit code unknown; the panel will be told so.')
else {
  log(`Test: exit ${test.exit_code}${test.truncated ? ' (output tail only)' : ''}`)
  const drift = commandDrift(test.command)
  if (drift) log(`COMMAND DRIFT: executor reported \`${drift}\` but the operator asked for \`${testCommand}\` — the exit code below is evidence about a different command.`)
}

// ----- Repair (at most one lap) --------------------------------------------
let repairLaps = 0
if (test && test.exit_code !== 0) {
  phase('Repair')
  repairLaps += 1
  log(`Tests failed (exit ${test.exit_code}) — spending the single repair lap.`)

  const fix = await agent(
    `${fence('maker')} You hold Read/Grep/Glob/Write/Edit — no shell, no network.\n\n` +
      `TASK GOAL:\n${ask}\n\n` +
      `The change below was made and its test run FAILED. This is a BOUNDED repair lap: address ` +
      `exactly what the failure reports and nothing else. Do not weaken, skip, delete or ` +
      `xfail a test to make it pass — that is a refutable change, not a fix. If the correct fix ` +
      `is out of scope, set complete=false and explain.\n\n` +
      `CHANGE UNITS MADE:\n${made.map((m) => `- ${m.unit_id}: ${m.summary}`).join('\n')}\n\n` +
      `COMMAND: ${test.command}\nEXIT CODE: ${test.exit_code}\n\nFAILING OUTPUT:\n${test.output}`,
    { label: 'repair:fix', phase: 'Repair', model: 'opus', agentType: 'maker', schema: MAKE_SCHEMA },
  )
  // The repair maker's `unit_id` is whatever it chose to return — it answers
  // no unit in `plan.units`. Left as-is it collides with a real unit id (the
  // handoff would submit two payloads to one `node:<unit-id>` label) or fails
  // the slug pattern. Pin it: `repair-lap` is a clean slug, is not a planned
  // unit, and reads correctly in the evidence block and the handoff mapping.
  if (fix) made.push({ ...fix, unit_id: 'repair-lap' })

  const retest = await agent(
    `${TEST_PROMPT}\n\nThis is the re-run after one bounded repair lap. Report what the command ` +
      `does NOW — never carry the previous run's output forward.`,
    { label: 'test:rerun', phase: 'Repair', agentType: 'executor', schema: TEST_SCHEMA },
  )
  if (retest) {
    test = retest
    log(`Repair lap complete: exit ${test.exit_code}. No further laps — a still-failing run goes to the panel as it is.`)
    const drift = commandDrift(test.command)
    if (drift) log(`COMMAND DRIFT (re-run): executor reported \`${drift}\` but the operator asked for \`${testCommand}\`.`)
  } else {
    log('Repair re-run returned null — carrying the pre-repair failure to the panel.')
  }
}

// ----- Diff ----------------------------------------------------------------
phase('Diff')

const diffResult = await agent(
  `${fence('executor')} You hold Bash only — you never edit a file.\n\n` +
    `In ${targetPath}, run these three commands in order and report the last two outputs ` +
    `verbatim:\n\n` +
    `    git add -A -N\n    git diff --stat ${baseBranch}\n    git diff ${baseBranch}\n\n` +
    `The first command is intent-to-add: nothing in this run is committed, so a file the makers ` +
    `CREATED is untracked and \`git diff\` would not show it at all — \`-N\` registers the path so ` +
    `the diff includes it, while staging no content and making no commit.\n\n` +
    `The unified diff is the artifact the verification panel judges, so carry it EXACTLY as git ` +
    `printed it: no summarizing, no re-indenting, no re-wrapping, no eliding of hunks. If the ` +
    `diff exceeds roughly 2,000 lines, carry the first 2,000 lines and set truncated=true so the ` +
    `panel knows it judged a prefix. Also list the changed paths. If the diff is EMPTY, say so ` +
    `plainly with empty strings — an empty diff is a real finding, not an error to paper over.`,
  { label: 'diff', phase: 'Diff', agentType: 'executor', schema: DIFF_SCHEMA },
)

const diffStat = diffResult ? diffResult.diff_stat : null
const diff = diffResult ? diffResult.diff : null
if (!diffResult) log('Diff stage returned null — the panel will judge without a diff, which is itself refutable.')
else if (!diff) log('Diff is EMPTY — nothing changed in the target worktree.')
else log(`Diff captured${diffResult.truncated ? ' (prefix only — panel is told)' : ''}: ${(diffResult.files_changed || []).length} file(s)`)

// DIFF FIDELITY — the one cross-check the script can make on its own.
// Everything above is a MODEL TRANSCRIPTION of git's output, and the whole
// verify-by-diff claim rests on it being verbatim. The script holds three
// independent counts of the same fact: `+++ ` headers in the diff text, the
// "N files changed" line of the stat, and the length of files_changed. They
// are produced by different parts of the executor's answer, so a summarized
// or elided diff shows up as a disagreement. This NEVER fails the run — a
// truncated prefix disagrees legitimately, and it is the critics' job to
// weigh it. It is logged and appended to the evidence block so the panel sees
// it too, rather than a silent number nobody reads.
function diffFidelity(text, stat, listed, truncated) {
  if (!text) return null
  const hunkFiles = (text.match(/^\+\+\+ /gm) || []).length
  const statMatch = (stat || '').match(/(\d+)\s+files?\s+changed/)
  const statFiles = statMatch ? Number(statMatch[1]) : null
  const listedFiles = (listed || []).length
  const counts = [hunkFiles, statFiles, listedFiles].filter((n) => typeof n === 'number')
  if (new Set(counts).size <= 1) return null
  return (
    `${hunkFiles} '+++' header(s) in the diff text, ` +
    `${statFiles === null ? 'no parsable count' : statFiles + ' file(s)'} in git diff --stat, ` +
    `${listedFiles} path(s) in files_changed` +
    (truncated ? ' — the diff is a TRUNCATED PREFIX, so some disagreement is expected' : '') +
    '. These describe the same change and should agree; a mismatch means the diff carried here ' +
    'may not be what git printed. Judge the diff accordingly.'
  )
}

const fidelityNote = diffResult
  ? diffFidelity(diff, diffStat, diffResult.files_changed, diffResult.truncated)
  : null
if (fidelityNote) log(`DIFF FIDELITY: ${fidelityNote}`)

// ----- REFUTE --------------------------------------------------------------
// Critics run on the critic tier, distinct from the makers' tier by
// construction — this is maker≠checker, the invariant verify/lite.py refuses
// to build a panel without. They are read-only and never edit what they judge.
//
// SCOPE, and this is the half that matters: nothing is committed yet and no PR
// exists, so this panel proves the DIFF half of ADR-011's acceptance criterion
// and nothing else. The PR half — a URL fetched and matched against the diff —
// can only be proved by a panel that runs AFTER the driving session opens the
// PR, which is chimera's own verify panel at `chimera tick` (lite.critic_calls
// over the wrap body, which carries the PR URL). These opinions are therefore
// EVIDENCE pasted into that wrap body; they are NOT a replay to submit as
// `verify:criticN`. See the runbook §5 and ADR-011's acceptance criteria.
phase('REFUTE')

const evidence =
  `THE ASK:\n${ask}\n\n` +
  `TARGET WORKTREE: ${targetPath} (base branch ${baseBranch})\n\n` +
  `CHANGE UNITS CLAIMED:\n${made.length ? made.map((m) => `- ${m.unit_id}: ${m.summary}${m.complete === false ? ' [INCOMPLETE: ' + (m.blocked_reason || 'unstated') + ']' : ''}`).join('\n') : '(none landed)'}\n\n` +
  `REPAIR LAPS SPENT: ${repairLaps} of ${MAX_REPAIR_LAPS}\n\n` +
  // The GREP-ANCHOR angle asks whether the command that ran is the command
  // that was asked for. Without this line the critic can only compare the
  // executor's report against itself, which always agrees.
  `COMMAND REQUESTED BY THE OPERATOR: ${testCommand}\n\n` +
  `TEST RUN:\n${test ? `command: ${test.command}\nexit code: ${test.exit_code}${test.truncated ? '\n[output tail only]' : ''}\n\n${test.output}` : '(the test executor returned nothing — there is NO evidence the tests were run)'}\n\n` +
  `DIFF --STAT:\n${diffStat || '(none)'}\n\n` +
  `UNIFIED DIFF${diffResult && diffResult.truncated ? ' [TRUNCATED PREFIX]' : ''}:\n${diff || '(none)'}` +
  (fidelityNote ? `\n\nDIFF FIDELITY: ${fidelityNote}` : '')

const opinions = (
  await parallel(
    ANGLES.map((angle, i) => () =>
      agent(
        `Critic ${i + 1} of 3 (${angle.name}) for chimera build mode. You hold read + web tools ` +
          `and NO write tools: you refute, you never edit what you judge. You may read anything ` +
          `under ${targetPath} to check a claim.\n\n` +
          `SCOPE: nothing here is committed and NO pull request exists yet. Judge the working ` +
          `tree, the diff and the test run only. Do not ask for, fetch, or treat as missing any ` +
          `PR, branch or commit — a later panel judges those.\n\n` +
          `${evidence}\n\n${angle.instructions}\n\n` +
          `Return JSON: {"refuted": bool, "reason": str, "severity": "fatal|material|cosmetic"}. ` +
          `Name specific evidence — a file+line from the diff, a line of the test output, a path ` +
          `you read. A verdict that names no specific evidence is hand-waving.`,
        { label: `verify:critic${i + 1}`, phase: 'REFUTE', model: 'sonnet', agentType: 'critic', schema: CRITIC_SCHEMA },
      ),
    ),
  )
).filter(Boolean)

if (opinions.length < ANGLES.length) log(`${ANGLES.length - opinions.length} critic(s) returned null — the fatal-only veto is applied over the ${opinions.length} valid opinion(s), and fewer than 2 fails by rule.`)

// ----- Verdict (pure JS) ---------------------------------------------------
phase('Verdict')

const v = survives(opinions)
log(`Verdict: ${v.passed ? 'SURVIVES' : 'REFUTED'} — ${v.unrefuted} of ${v.valid} valid opinions unrefuted (min 2 valid; any single fatal refutation vetoes alone).`)

// ----- chimera handoff -----------------------------------------------------
// This workflow has no shell and never calls chimera. The driving session
// makes these calls from the returned object.
//
// The ask is printed on its OWN LINE, unquoted and untruncated: building a
// shell command around it here would need quote escaping the script cannot get
// right for every ask, and a 120-char clamp silently filed a DIFFERENT task
// than the one that was built. The operator quotes it once, in their shell.
const shape = plan.units.length > 1 ? 'pipeline' : 'straight'
log(
  [
    'CHIMERA HANDOFF — run these from the chimera checkout (NOT this worktree).',
    'Write each payload to a file and use --file; --json chokes on a diff.',
    'Open the PR FIRST (git add -A && git commit -m "..." && git push -u ...; gh pr create),',
    'then file the task — the wrap body has to carry the PR URL.',
    '',
    `0. The ask, verbatim — quote it yourself when you paste it:`,
    `     ${ask}`,
    `1. chimera new --shape ${shape} "<the ask above>" --context "target: ${targetPath}; PR: <url>"`,
    `     ${plan.units.length} change unit(s) -> shape ${shape}: graph.py::_check_shape refuses`,
    '       "straight" when ANY phase width > 1, and refuses "pipeline"/"diamond"',
    '       when NO phase fans out. One unit is straight; two or more is pipeline.',
    '2. chimera tick                                  # claims the task, lists the `plan` call',
    '3. chimera arc submit <id> plan --file plan.json',
    '     GraphPlan from `plan`: phase 1 = one maker node per unit (id = unit id),',
    '     phase 2 = one executor node `test` reading ALL the maker nodes (the',
    '       fan-in guard), phase 3 = one executor node `diff` reading `test`.',
    '     If the repair lap fired, `made` also holds a unit_id `repair-lap`; fold it',
    '       into the node it repaired — it is evidence, not a fourth planned unit.',
    '4. chimera arc submit <id> node:<unit-id> --file node-<unit-id>.json',
    '     GraphNodeOutput per unit from `plan.units` + the make summaries;',
    '     then node:test (test output + exit code) and node:diff (diffStat + diff).',
    '5. chimera arc submit <id> wrap --file wrap.json',
    '     GraphArtifact. Until ADR-011 lands the diff/pr_url fields, the diff, the',
    '     PR URL and these three opinions go in the body VERBATIM as evidence —',
    '     the wrap merges, it never authors.',
    '6. chimera tick   # prints the REAL verify panel: 3 critic calls built by',
    '     verify/lite.py::critic_calls over that wrap body. RUN THOSE CALLS and',
    '     submit their results to verify:critic1|2|3. Do NOT replay the opinions',
    '     above into those labels: this workflow judged the diff BEFORE the PR',
    '     existed, so a replay can never satisfy ADR-011 (ii) — the PR URL fetched',
    '     and matched against the diff. Only the chimera-side panel sees the URL.',
    '7. chimera approve <id> --by <operator> --token <token>   # G2, after reading',
    '     the diff. The token is required whenever .g2-operator.sha256 is present',
    '     (gates.g2_enforcement == "token"); --token, $CHIMERA_OPERATOR_TOKEN and',
    '     ~/.config/chimera/operator-token are the three ways to supply it.',
    '',
    'If a critic returned null, submit that label with --null — a degraded critic is',
    'a recoverable null, never a new halt class.',
  ].join('\n'),
)

return {
  plan,
  diffStat,
  diff,
  testOutput: test ? test.output : null,
  exitCode: test ? test.exit_code : null,
  opinions,
  passed: v.passed,
}
