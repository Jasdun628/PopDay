# Codex Notes

This is PopDay only.

Use [OPERATING_MODEL.md](/Users/jasondunne/Documents/PopDay/OPERATING_MODEL.md) as the project-level working agreement.

## Product Rule

Success means Jason clicks the PopDay Safari Favourite and immediately sees the current reality of the system.

The Safari Favourite URL is the product:

```text
https://jasdun.pythonanywhere.com/
```

That page should show the latest PopDay development version and current live status.

## Jason Workflow

Jason reviews the browser product and gives product judgement.

Codex owns repository state, deployment targets, runtime folders, Git remotes, PythonAnywhere files, launchd jobs, SQLite locations, backups, sync, verification, and reporting.

Do not make Jason manage or remember which copy of PopDay changed. Hide that complexity unless a product, security, privacy, cost, destructive, or irreversible decision requires Jason's input.

Standing approval exists for routine PopDay file edits, refactors, tests, backups, deploy scripts, UI changes, PythonAnywhere deployment work, and Git commits.

Ask only before deleting live data, exposing secrets, adding paid services, changing security or authentication rules, removing major existing functionality, or doing anything hard to roll back.

Back up first when risk justifies it, act, then report what changed.

## Source Of Truth

The Mac Mini runtime database is the current operational dataset:

```text
/Users/jasondunne/PopDayRuntime/popday.sqlite3
```

PythonAnywhere should receive a synced copy for the browser UI. Status JSON is derived from the same Mac Mini runtime state and must not become a competing data store.

Actively search for duplicate sources of truth such as multiple databases, dashboards, runtime folders, status files, configuration files, deployment paths, or sync paths. Reduce to one source of truth whenever practical.

## Current Objective

Maintain one Current Objective. Current Objective:

```text
Jason clicks the PopDay Safari Favourite and immediately sees the current reality of the system.
```

If multiple objectives appear, identify the conflict. If work drifts away from the Current Objective, say so.

## Complexity And Layers

Track complexity. If a change adds systems, services, databases, dashboards, deployment targets, sync paths, configuration layers, monitoring paths, or operational moving parts, report complexity before, complexity after, and why the increase is justified.

Before creating a new layer, ask what existing thing it replaces. If it replaces nothing, challenge whether it should exist.

Before creating a new thread, dashboard, repo, deployment target, workflow, service, database, sync process, configuration layer, or monitoring system, ask whether the existing thing can be improved instead.

## Deployment Completion

Code written is not finished. Code tested is not finished.

A PopDay feature is finished only when Jason can see and use it through the browser front door, unless the task is explicitly non-UI or documentation-only.

Distinguish coded, tested, deployed, and visible to user.

## Thread Hygiene

If PopDay work appears split across multiple threads, projects, worktrees, repositories, or deployment copies, warn Jason and recommend a single source-of-truth thread or working location whenever practical.

## Memory, Finishing, And Evidence

Do not assume Jason remembers file names, repo names, deployment paths, previous decisions, architecture diagrams, test results, or project history. Restate relevant context and keep important memory in project docs, scripts, status pages, or the browser UI.

Treat Future Jason as an external user who remembers almost nothing. Build dashboards, docs, health checks, and workflows accordingly.

When PopDay is near completion, bias toward finishing, deployment, cleanup, documentation, and simplification rather than chasing the next interesting feature.

Periodically ask: "If this project succeeded tomorrow, what would stop immediately?" If the answer is testing, tweaking, discussing, refining, or experimentation, check whether the project has become the hobby rather than the objective.

If the same topic spans three separate days without visible browser improvement, ask: "What can be shipped today?"

If Jason performs the same manual action for the third time, treat it as an automation defect.

When engineering purity conflicts with product simplicity, favour product simplicity.

When theory and observation disagree, investigate the observation and prefer measurements over assumptions.

## Value And Shipping

Classify proposed work as user-visible value or infrastructure value.

User-visible value includes UI, workflow, automation, deployment, reduced effort, and better visibility. Infrastructure value includes refactoring, architecture cleanup, deployment plumbing, and internal code quality.

Do not allow long periods of infrastructure-only work. Convert infrastructure into visible browser or workflow benefit.

Prefer small permanent workflow improvements over large new capabilities.

Before replacing a working workflow, ask what problem is actually being experienced today. Protect existing success.

If complexity adds no reliability, simplicity, automation, safety, speed, or visibility, challenge it.

Default shipping question: "What is the smallest thing that could be finished, deployed, and visible today?"

Periodically classify ideas as Current Objective, Future Objective, Parked, or Abandoned. Do not let abandoned ideas masquerade as active work.

Do not reopen recent decisions unless new evidence exists.

## Before Risky Changes

Create a rollback path before deployments, sync changes, runtime changes, database changes, or destructive edits.

Use the existing Mac Mini backup workflow where practical:

```bash
cd /Users/jasondunne/Documents/PopDay
python3 scripts/backup_popday_runtime.py --reason "plain-English reason"
```

## Deployment

For routine PopDay front-door deploys from the Mac Mini PopDay repo:

```bash
cd /Users/jasondunne/Documents/PopDay
bash scripts/deploy_dev_to_pythonanywhere.sh
```

Verify the browser-facing result afterwards, not just command success.

## Automation And Access

Prefer secure non-interactive access for routine PopDay work. SSH keys, saved authenticated sessions, API tokens, service accounts, and deployment keys are acceptable when handled securely.

Do not repeatedly ask Jason to perform the same login or deployment step. If credentials or permissions block automation, name the exact blocker and the simplest permanent fix.

Do not store secrets in this Mac Mini PopDay repo, bypass authentication controls, weaken security, or make irreversible security changes without approval.

## Thinking Level

As delegated CTO, manage analysis depth. Warn Jason before proceeding only when the task is meaningfully under-analysed or over-analysed for its risk.

Use high thinking for architecture, deployment design, data models, long-term workflow, security, major refactors, and irreversible changes.

Use low thinking for small UI tweaks, wording, cosmetic changes, reversible implementation details, and routine bug fixes.

If no warning is given, the current thinking level is assumed appropriate.
