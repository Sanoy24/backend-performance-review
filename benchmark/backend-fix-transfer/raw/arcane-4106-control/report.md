# Performance review: project list with many Git-managed projects

Paths are relative to the repository root. Line numbers are in the checked-out commit. Nothing was run; this is from reading the code.

## Summary

The list endpoint (`ListProjects`, `backend/internal/project/project_listing.go:253`) does a per-project database lookup for every GitOps-managed project, and it does so twice per request. Both lookups are inside `ResolveProjectComposeFile` (`backend/internal/project/project.go:553`).

- The first lookup (finding 1) happens before the metadata cache is consulted, so the cache cannot prevent it.
- The second lookup (finding 2) comes from the update-info enrichment. It has the same shape and is not cached at all.

Because the query count is 2 x (GitOps projects on the page) per request, load time and DB load grow linearly with project count. With limit -1 ("all"), or with the status/updates/label filters or sort-by-status, every project is processed on every request.

## 1. (High) A `gitops_syncs` query per GitOps project before the metadata cache check

- `backend/internal/project/project_listing.go:1080-1100` (`ProjectMetadata`): line 1083 calls `s.ResolveProjectComposeFile(ctx, &p)` first, and the cache lookup (`s.metaCache.Get`) comes only afterwards at about line 1100.
- `backend/internal/project/project.go:574-583`: for a project with `GitOpsManagedBy` set, `ResolveProjectComposeFile` runs `SELECT compose_path FROM gitops_syncs WHERE id = ? LIMIT 1` with `First(&syncRecord)`.
- The comments at `project.go:61-65` and `project_listing.go:1074` say the cache is meant to avoid exactly this cost ("a gitops_syncs query for GitOps-managed projects"), but the lookup runs before the cache is consulted, so every request pays it even when the cache would hit.
- The same call also does filesystem work for every project: `GetProjectsDirectory` (a settings read plus path resolution), then `projects.ComposeFileEnvSelection` (`backend/pkg/projects/env.go:856`). That function parses `.env.global` and the project's `.env` from disk and merges COMPOSE env files. Non-GitOps projects pay this too.
- Cost: N queries plus N or more file reads per request, issued by up to 8 concurrent workers (`maxConcurrentComposeReads`, `project.go:201`, used at `project_listing.go:967`).
- The pool allows 20 to 50 connections (`backend/internal/database/database.go:110-113`). With 8 workers each issuing queries this pattern saturates the pool under concurrent page loads, and it is the "hammers the database" symptom.

Fix, in order of preference:
1. Move the cache check ahead of the compose-file resolution. Validate the cache by mtime or fingerprint of the previously resolved compose file and its env files. Store the resolved compose path inside the cache entry, so that a hit costs no DB query and no env parsing.
2. Batch the lookup. In `fetchProjectStatusConcurrently` (`project_listing.go:914`), collect the distinct `GitOpsManagedBy` IDs from `projectsList`, run one `SELECT id, compose_path FROM gitops_syncs WHERE id IN (...)`, and pass the resulting map through `projectMetadataEnvInternal` (or an argument to `ResolveProjectComposeFile`). That gives 1 query per request instead of N.
3. Optionally denormalize `compose_path` onto the project row, or cache sync compose paths in memory and invalidate them on sync create, update and delete.

## 2. (High) The same per-project lookup again in update-info enrichment, with no cache in front

- `backend/internal/project/project_details.go:504-560` (`enrichProjectsWithUpdateInfoInternal`) starts one goroutine per project, bounded by a semaphore of 8. Each calls `resolveProjectUpdateServicesInternal` (`project_details.go:~583`), which calls `getCachedComposeProjectInternal` (`project.go:630-640`), which calls `ResolveProjectComposeFile` again at line 636.
- That is a second `gitops_syncs` query plus env parsing per project, per request. The compose-parse cache (`LoadCachedComposeProject`, `backend/pkg/projects/cache.go:129`) is keyed on the already-resolved path, so it also cannot skip the resolve step.
- Note that `ListProjects` runs this enrichment (line 291 of `project_listing.go`) for all rows on the page. `CountProjectsWithPendingUpdates` (`project_listing.go:827`) runs it for every non-archived project, with all rows loaded by `Find(&allProjects)` at line 844. Wherever that count is used (dashboard or counts endpoints), it adds another 2N lookups per call.
- Fix: the same batching as in finding 1. Resolve each project's compose file once per request and pass it to both the metadata step and the update step. A per-request map from project ID to resolved path is enough. Better still, make `ResolveProjectComposeFile` cache its result keyed on project ID plus env file mtimes.

## 3. (Medium) Derived filters and sort load and enrich the entire project table

- `backend/internal/project/project_listing.go:253-300` and `343-390`. When `status`, `updates` or `label` filters, or `sort=status`, are used, `filterProjectsWithDerivedFiltersInternal` calls `query.Find(&projectsArray)` (line 377) with no limit. It then runs the full DTO build, tag enrichment, update enrichment and discovered-project merge for all rows, and paginates in memory (`SearchOrderAndPaginate`).
- This makes findings 1 and 2 apply to all N projects rather than one page, and it makes the cost independent of page size. The frontend's status sort and filters trigger it.
- Fix: push status down to the DB where possible. The `status` column exists, and `pagination.ApplyFilter(query, "status", ...)` is already used on the simple path. Alternatively, compute derived status from the single Docker container list first and paginate IDs before doing per-project enrichment. At minimum, skip metadata and update enrichment for rows that are filtered out.

## 4. (Medium) Repeated full-table scans and a goroutine write per list

- `GetProjectStatusCounts` (`project_listing.go:194-250`, `Find(&projectsList)` at line 198) and `CountProjectsWithPendingUpdates` (line 844) each load all project rows, and `buildKnownComposeProjectNameSetInternal` (lines 436-462) selects all names. Each list or dashboard call can issue several full scans. This is linear but cheap compared with findings 1 and 2. Fix: use `SELECT COUNT(*) ... GROUP BY status, is_archived`, and cache the name set.
- `project_listing.go:~1058-1066` spawns an unbounded `go func` DB update when `ServiceCount == 0 && len(services) > 0`. It uses a background context and runs on every list until the row is persisted. A burst of such rows can add concurrent writes against the pool. Fix: batch these, or bound them with a worker or queue and de-duplicate by project ID.

## 5. (Low) Container grouping fallback is quadratic

- `lookupProjectContainersInternal` (`project_listing.go:65-95`): when the name-based lookups miss, which is common for stopped or imported projects, it loops over every container of every project to compare the working-dir label. That is O(projects x containers) per request.
- Fix: build a `workingDir -> projectName` index once in `groupComposeContainersByProjectInternal` and look up by cleaned path.

## 6. (Low) Smaller per-project costs

- `GetProjectsDirectory` and settings reads inside `ResolveProjectComposeFile` (`project.go:553-572`) are repeated per project, even though `fetchProjectStatusConcurrently` already resolves the directory once. Pass the resolved directory in.
- `projects.CheckProjectEnvAccess` is called per project in `mapProjectToDto` (`project_listing.go:~996`), adding another filesystem access per project. It could share the env parsing with the compose resolution.

## Suggested priority

1. Resolve compose files once per request and batch the `gitops_syncs` lookup, or put the metadata cache check before it (findings 1 and 2). This should remove most of the DB load and make the cost close to constant in DB queries.
2. Stop the derived filter and sort paths from enriching all rows (finding 3).
3. Replace the full scans with aggregate queries and bound the background writes (finding 4).
4. Clean up the quadratic container matching and the per-project filesystem checks (findings 5 and 6).

An easy way to verify the fix is to count DB statements per request in a test with, for example, 200 GitOps projects. After the fix the count should be constant rather than around 2N.
