# Backend performance review: tibiadata-api-go (case-insensitive name lookups)

## 1. Decision summary

**Overall assessment: no material performance findings in the code behind case-insensitive name lookups.** The operator report (high CPU and memory, slow responses on large lists) is not explained by anything in the source. Review confidence is Medium because nothing could be measured.

**Actions (at most three)**
1. Do not change the lookup code on the strength of this report. No reachable mechanism justifies it.
2. Capture a CPU and allocation profile of one slow case-insensitive request in staging to locate where the time actually goes.
3. If a hotspot does appear in validation, add a benchmark first (see section 5).

**Decision-changing unknowns**
1. Which endpoint is slow, and does a profile exist for it? This decides whether the cost is in validation at all.
2. Size of the creature, spell and house lists (no file states it).

**Validation commands (recommendations; none were run)**
- `go test -bench . -benchmem ./src/validation/` against a benchmark you add for `IsCreatureNameValid`. safe-on-production: yes (offline, no service).
- `go tool pprof` on a CPU/heap profile captured from staging. not-safe-on-production unless pprof is already enabled there.

## 2. Scope and method

Full review, read-only, static only. Stack from `detect_stack.py`: Go with gin (REST). The MongoDB and gRPC detections are indirect (go.mod entries only) and I found no datastore access in the request paths read. Observability inventory: no metrics, tracing or load tests found in the paths read; only unit tests and two benchmarks for unrelated parsers (`TibiaBoostableBossesOverview_test.go`, `TibiaCharactersCharacter_test.go`). So no finding could reach Confirmed.

## 3. What was examined

Request paths that match names regardless of case:
- Creature: `tibiaCreaturesCreature` in `src/webserver.go` calls `validation.IsCreatureNameValid` (`src/validation/tibia.go:156`).
- Spell: `tibiaSpellsSpell` calls `IsSpellNameOrFormulaValid` (`tibia.go:223`).
- World, town, vocation, highscore category: `WorldExists`, `TownExists`, `IsVocationValid`, `IsHighscoreCategoryValid` (`tibia.go`, `highscore.go`).
- House by ID and town: `GetHouseRaw`, `GetHouseInTown`.
- `strings.ToLower` uses in parsers (`TibiaDataUtils.go:250`, `TibiaCharactersCharacter.go:815`) are on single short strings or feed a `switch`.

## 4. Why no finding

- Every case-insensitive comparison uses `strings.EqualFold`, which compares in place and does not allocate a lowered copy. Nothing lowers the whole list per request.
- The lists live in package-level slices filled once in `Initiate` (`src/validation/validation.go`). There is no per-request rebuild, copy or JSON decode of them.
- Input is length- and character-checked before the scan (`utf8.RuneCountInString` bounds, regex check, `tibia.go:163-197`), so scan cost is O(list size) with bounded name length. Each entry costs up to three EqualFold calls.
- The scan returns early on a match and happens once per request, before an outbound HTTP fetch of the tibia.com page plus HTML parsing, which I expect to cost far more than scanning an in-memory slice. That is an inference from structure, not a measurement.
- The start-up size computations (`setCreaturesVars`, `setSpellsVars`) run once, not per request.

Counter-evidence sought: per-request lowering of list entries, regexp compilation inside handlers (the regexes found are package-level), list copies, map rebuilds, caches with unbounded growth. None found on these paths.

Considered but not reported (revisit only with a profile): the linear creature/spell scan and the house lookups. See review.json `considered_not_reported`.

## 5. What I cannot say

- I cannot confirm or refute the operators' observation. If it is real, the cost is outside the code I found: upstream fetch, HTML regex parsing (`TibiaCreaturesCreature.go` uses greedy `.*` regexes on page content) or response handling were not profiled. I did not promote the regex parsing as a finding because I have no evidence it relates to the reported symptom or its size on real pages.
- List sizes are not stated in the repository.

**Measurement plan**
1. Add a benchmark calling `IsCreatureNameValid` with a mixed-case name and a miss, with `-benchmem`. I expect 0 allocations per lookup if my reading is right; that is a hypothesis to test. safe-on-production: yes.
2. Capture a CPU and heap profile of a slow case-insensitive request in staging. not-safe-on-production unless already enabled.
3. Compare latency of an exact-case and mixed-case request for the same name to see whether case matters at all.

## 6. Findings

None.

## 7. Notes

No number in this report was measured or estimated. Adjacent (security, correctness, maintenance) issues: none recorded.
