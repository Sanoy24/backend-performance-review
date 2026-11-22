# Performance Review: arcane (backend service, project list path)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the code behind the project list page (`ListProjects`)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

## 1. Decision summary

### Overall assessment

The list path is already carefully built: one Docker call for all containers, batched tag and update-record queries, bounded concurrency (8 workers), and an mtime-validated metadata cache. One defect defeats part of that work for GitOps-managed projects: the compose-file resolver runs a `gitops_syncs` query per project, twice per request, before any cache is consulted. This matches the reported symptom (database load growing with the number of Git-managed projects). Review confidence is Medium: static reading only, no measurements.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Resolve each project's compose file once per request, with the GitOps `compose_path` values loaded in one `IN` query, and pass the result to both the metadata and update-summary steps. | It is the only mechanism found that scales DB queries with project count, and it is cheap to remove. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Page size and number of GitOps-managed projects in real use | Whether PERF-001 stays High or drops to Medium | Ask operators; count `projects` rows where `gitops_managed_by` is set. |
| Whether DB, filesystem, or the Docker call dominates list latency | Whether fixing PERF-001 resolves "slow" or only the DB-load part | Trace one list request with per-step timing. |

### Validation commands

No repository-local command is justified. Procedure instead:

| Finding | Safety | Procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | not-safe-on-production (use a staging copy) | Enable DB statement logging and count `SELECT compose_path FROM gitops_syncs WHERE id = ?` statements for one project-list request with N GitOps projects, before and after the change. | Confirms the 2 x N shape (falsifier: count independent of N). |

## 2. Scope and method

**Reviewed:** `backend/internal/project` (`project_listing.go`, `project.go`, `project_details.go`, `project_tags.go`, `model.go`), `backend/pkg/projects` (`cache.go`, `env.go`), pool settings in `backend/internal/database/database.go`, the `gitops_syncs` migration.
**Not reviewed:** frontend, Docker daemon call internals, image-update service internals, GitOps sync jobs, CLI, other endpoints.

**Evidence available:** Uninstrumented for this purpose (no benchmark, trace, or load test was supplied).
**Ranking method:** Structural signals only.
**Reference depth:** Go, Postgres, SQLite, REST at deep tier. The repository has 6 detected services; only `backend` was in scope.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | The list request path and the helpers it calls; nothing else. |
| **Critical paths** | 1 / 1 (project list) |
| **Shared resources** | 3 / 3 (DB connection pool, Docker client, in-memory metadata/compose caches) |
| **Technology support** | Go deep, Postgres deep, SQLite deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Real project counts, page size, GitOps share | No evidence exists | Operator answer or a `COUNT` on the projects table |
| Latency split between DB, filesystem, and Docker | No evidence exists | Per-step trace of one list request |
| Which database the affected deployment uses | No evidence exists | Deployment config; pool limits are set in `database.go:109-113` |

## 3. Architecture overview

| Component | Technology | Support tier | Role |
|:--|:--|:--|:--|
| Backend API | Go | deep | `ProjectHandler.ListProjects` calls `ProjectService.ListProjects` |
| Datastore | Postgres or SQLite via GORM | deep | `projects`, `gitops_syncs`, tags, update records |
| Docker engine | Docker client | n/a | one global compose-container list per request |

**Shared resources:** DB connection pool (`SetMaxOpenConns` 20 or 50 by branch), Docker client, in-memory `metaCache` / `parsedCompose` caches, local filesystem.

## 4. Workload model

**Known**
- The list handler paginates; on the derived-filter path limit is clamped to 100 and `-1` means "all" (`project_listing.go:343-366`).
- Status, updates, or label filters, or sorting by status, load every project row and build DTOs for all of them (`project_listing.go:264-269`, `368-389`).
- Per-project fan-out is bounded to 8 concurrent workers (`project.go:201`, `project_listing.go:964-975`).

**Assumed**
- A meaningful number of projects are GitOps-managed (the operator report says so). Affects PERF-001.

**Unknown**
- Page size, project count, GitOps share.

**Derived**
- `gitops_syncs` queries per list request = 2 x (GitOps-managed projects in the result set), from the two call sites in PERF-001.

**Measured:** none.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | high | How many projects are listed per request, and how many are GitOps-managed? | severity | PERF-001 High vs Medium |

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | Project list (`ListProjects`) | yes | 1 page query, 1 tag query, 1 update-record query, plus per-project `gitops_syncs` lookups | page limit, or all rows on `-1` / derived filters | debug logs only | PERF-001 |

**Amplification points:** `gitops_syncs` lookup x2 per GitOps project; filesystem reads (`.env.global`, `.env`) and cache-validation stats per project, bounded by 8 workers.

**Paths not analyzed in depth:** image-update service, Docker listing internals.

## 6. Layer analysis

### 6.1 Application
Fan-out uses a bounded errgroup and a semaphore; panics are recovered.

### 6.3 Data access and datastore
Tags and update records are fetched in one query each over the project ID set. The compose-path lookup is the exception (PERF-001). `gitops_syncs.id` is the primary key, so each lookup is a single-row probe, cheap individually.

### 6.4 Cache
`metaCache` and `parsedCompose` are keyed by project ID and validated by file mtimes. They are consulted only after `ResolveProjectComposeFile`, which does the DB and env-file work the cache was meant to avoid.

### 6.7 Observability
No metrics or tracing around the list path were found in the files reviewed; only debug logs. This caps how far any finding can be confirmed.

## 7. Findings

### PERF-001 - Per-project, uncached GitOps compose-path lookup, executed twice per list request

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `backend/internal/project/project.go:553` (`ResolveProjectComposeFile`, query at 574-581) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
For every GitOps-managed project, `ResolveProjectComposeFile` runs a `gitops_syncs` lookup by ID and re-reads `.env.global` and the project `.env`. It is called once from `ProjectMetadata` and again from `getCachedComposeProjectInternal`, each time before the respective cache is checked. The comment on `metaCache` (`project.go:61-65`) and on `ProjectMetadata` (`project_listing.go:1066-1075`) describes the cache as avoiding a per-project `gitops_syncs` lookup, but the lookup precedes the cache read.

**Performance principle**
Work with a stable answer should not be repeated per item per request; a cache must be consulted before the work it exists to avoid; lookups for a set of items should be batched.

**Evidence** (static; no runtime evidence)
- `project_listing.go:1083`: `ProjectMetadata` calls `ResolveProjectComposeFile`; the cache `Get` is at line 1099-1101, after it.
- `project.go:574-581`: per-project GORM `First` on `gitops_syncs` when `GitOpsManagedBy` is set.
- `project.go:568`: `ComposeFileEnvSelection` parses `.env.global` and project `.env` on every call (`pkg/projects/env.go:856-884`).
- `project.go:636`: `getCachedComposeProjectInternal` calls the resolver again before `LoadCachedComposeProject`; it is reached per project from `resolveProjectUpdateServicesInternal` (`project_details.go`).
- Both callers iterate the same project list inside `ListProjects` (`project_listing.go:914-960`, `project_details.go:504-560`).

**Impact**
Position: critical path (user-facing list). Frequency: per item, 2 lookups per GitOps project per request (derivation from the two call sites). Growth: O(n) in listed GitOps projects; O(all projects) when `-1`, a status/updates/label filter, or status sort is used. Blast radius: endpoint, plus shared DB pool pressure (pool limit 20 or 50 open connections, `database.go:109-113`).

**Conditions**
Matters when many projects are GitOps-managed and the list is requested often (auto-refresh, several users) or unpaginated or with derived filters. Assumed and unmeasured; negligible for small project counts.

**Counter-evidence**
- Each lookup is a primary-key probe, individually cheap (bounds impact; High rather than Critical).
- Tags and update records are already batched; metadata and compose caches avoid YAML re-parsing on hits (`pkg/projects/cache.go:47-57`).
- Searched for a request-scoped or sync-record cache around the resolver: none found.

**Why this might not matter**
Single-row primary-key reads on a local database are cheap and the page query is capped; with tens of projects, Docker and filesystem time likely dominate.

**Recommendation**
Resolve the compose file once per project per request: before fan-out, collect the distinct `GitOpsManagedBy` IDs on the page, load `id, compose_path` with one `IN` query, and pass the resolved path into both `ProjectMetadata` and the update-summary step. This removes the repeated work rather than masking it and makes this step's DB queries O(1) in project count.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A - batch-load compose paths once per request, pass resolved paths down | **preferred** | Removes the work; no staleness or invalidation. |
| B - join `gitops_syncs` into the project page query | | Also O(1) but changes the page query and pagination. |
| C - in-memory cache of `compose_path` by sync ID | | Needs invalidation on sync edits; adds staleness. |

**Trade-offs**
Larger function signatures; env-file selection still runs per project unless also hoisted; option C adds consistency risk.

**Validation**
Baseline: count `gitops_syncs` statements per list request with N GitOps projects on a staging copy (`not-safe-on-production`). Expectation: count falls from 2N to 1. Falsifier: count is already constant in N. Guard: a service test asserting a bounded statement count for N GitOps projects.

---

### Considered and not reported

- `mapProjectToDto` spawns a goroutine to persist `service_count` when the DB value is 0 (`project_listing.go`, near the `ServiceCount == 0` branch): self-healing after the first write. Revisit if counts stay at 0 for projects with running containers.
- Cache validation does `stat` + `open` per tracked file per project on every hit (`pkg/projects/cache.go:104-125`): bounded by 8 workers. Revisit if projects live on slow network storage.
- Derived-filter listing builds all DTOs before paginating: intentional for derived filters; same per-item cost, covered by PERF-001 conditions.

## 8. Prioritized action plan

### P1 - High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P1 | Small | Only per-project DB amplification found on the list path |

**If only one thing is done:** batch and hoist the `gitops_syncs` compose-path lookup out of the per-project loop.

## 9. Validation plan

### PERF-001
- **Baseline:** statement count per list request versus N GitOps projects. `not-safe-on-production`.
- **Change:** resolve compose paths once per request.
- **Measurement:** DB statement log count for `gitops_syncs`; list endpoint timing at fixed N.
- **Expectation:** statements drop from 2N to 1; latency direction down, magnitude not derivable from code.
- **Falsifier:** statement count is already constant in N.
- **Guard:** test asserting bounded query count.

### Instrumentation gaps to close first
Add per-step timing (DB, filesystem, Docker) to the list request so the latency split can be measured.

## 10. Machine-readable output

See `review.json` in the same directory.

## 11. Notes on this review

- No runtime metric was measured or estimated; "2 x N" is a derivation from two call sites.
- All findings rest on static evidence, so none is `Confirmed`.
