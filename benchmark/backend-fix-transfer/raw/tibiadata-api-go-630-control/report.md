# Performance review: case-insensitive name lookups

Scope: static review only. Nothing was run. Paths are relative to the repository root.

## Summary

Case-insensitive lookups for creatures, spells, worlds, towns and houses all go through `src/validation/tibia.go`. Each one does a linear scan over an in-memory list, and every comparison uses `strings.EqualFold`. There is no index, no pre-normalized key and no cache. Cost per request is therefore O(N) in the list size, multiplied by the number of fields compared per entry. This matches the report that large lists hurt. The biggest costs are the creature and spell scans, which compare 3 fields per entry. Nothing in the code builds a lookup map once at startup, even though the data is static after `Initiate`.

## Findings, in priority order

### 1. Creature lookup: linear scan, three EqualFold calls per entry (High)
`src/validation/tibia.go:205-211` (`IsCreatureNameValid`, called from `src/webserver.go:397`).
For every request the loop walks all of `val.Creatures` and evaluates up to `EqualFold(name, Endpoint) || EqualFold(name, Name) || EqualFold(name, PluralName)`. A miss, which is the worst case and the one a bad or random name triggers, does 3 x N Unicode-aware folds. Some of the 3 x N calls short-circuit on length, but the cost is still linear.
Fix: at the end of `Initiate` (`src/validation/validation.go:99`, next to `setVars`) build a `map[string]string` from `strings.ToLower` of each Endpoint, Name and PluralName to the endpoint. The lookup becomes `m[strings.ToLower(name)]`, which is O(1) with a single allocation for the lowered key. If the input is known to be ASCII (the regex at line 21 already restricts it to `[a-zA-Z\s']`), ToLower is cheap. Keep first-match-wins semantics when two keys collide (do not overwrite an existing entry).

### 2. Spell lookup: same pattern (High)
`src/validation/tibia.go:272-278` (`IsSpellNameOrFormulaValid`, called from `src/webserver.go:1027`). Same 3-field EqualFold scan over `val.Spells`. Spell lists are usually larger than creature lists, so this is likely the worst offender. Fix: same lowercase map keyed on Name, Formula and Endpoint.

### 3. World and town existence checks scan on every request (Medium)
`src/validation/tibia.go:306-310` (`WorldExists`) and `:337-341` (`TownExists`).
`WorldExists` is called from at least 6 handlers (`src/webserver.go:497, 564, 646, 707, 766, 1093`), so it sits on the hot path for most world-scoped endpoints. The lists are small today (dozens of entries), but each call is still O(N) with a fold per entry. `TownExists` also allocates a new string through `strings.ReplaceAll` on every call (line 334), even when there is no `+`.
Fix: build `map[string]struct{}` sets of lowercased worlds and towns at init. In `TownExists`, replace `+` with a space and lowercase once, then do a single map lookup. Skip `ReplaceAll` when `strings.IndexByte(town, '+') < 0`.

### 4. House lookups scan the full house list, and do the town check twice (Medium)
- `src/validation/tibia.go:367-371` (`GetHouseRaw`) is a linear scan by integer ID. It is used by `HouseExistsRaw` (called at `src/webserver.go:658`) and by `src/TibiaHousesHouse.go:98`. So one house request scans the list at least twice (existence check, then fetch).
- `src/validation/tibia.go:387-408` (`GetHouseInTown`) first calls `TownExists` (scan of towns, with an allocation), then scans all houses with `h.ID == houseID && strings.EqualFold(h.Town, town)`. Note the town string passed to `EqualFold` here is the raw input, not the `+`-normalized one, which is also a correctness bug for multi-word towns written with `+`.
- `HouseExistsRaw` and `HouseExistsInTown` call the getter and compare a struct to the zero value, which does the full scan and copy just to answer a boolean.
Fix: build `map[int]House` at init for ID lookup. For town scoping, compare the stored house's town against the normalized input after the O(1) fetch. Keep a pre-lowered `TownKey` field on each house so no fold is needed at request time.

### 5. Vocation validation scans a slice with EqualFold (Low)
`src/validation/tibia.go:53-60` (`IsVocationValid`) loops over 12 entries with EqualFold. Negligible in absolute terms, but it is easy to make a switch on `strings.ToLower(vocation)` or a map. Also `src/TibiaDataUtils.go:250` lowercases again for the same value, so the input is folded twice per request.

### 6. Repeated allocation and re-folding on the request path (Low to Medium, scales with traffic)
- `src/TibiaDataUtils.go:80` and `src/TibiaHighscores.go:166` call `cases.Title(language.English).String(world)`. `cases.Title` builds a new Caser (with its internal state) on each call. A caser is not safe to share across goroutines, but it can be pooled, or the title-cased world name can come from the validator's canonical world list (store the canonical spelling in the map from finding 3 and return it, which removes the Title call entirely).
- The validators are not on a read lock (`locker` in `src/validation/validation.go:44` guards only `Initiate`). That is fine for performance because it adds no contention, but note it when moving to maps: build the maps fully before setting `initiated = true`, since readers do not synchronize.

### 7. Getter functions hand out the internal slices (Informational)
`GetWorlds`, `GetTowns`, `GetHouses`, `GetCreatures` (`src/validation/tibia.go:288, 316, 347, 419`) return the backing slices with no copy. Not a CPU problem, but callers that iterate them repeat the linear-scan pattern. If any caller does its own case-insensitive matching on these, move it to the maps.

## Recommended plan

1. In `src/validation/validation.go`, add index fields to `validator`, populated by a new `buildIndexes()` called right after `json.Unmarshal` and before `initiated = true`: `creatureByKey`, `spellByKey` (lowercased key to endpoint), `worldSet`, `townSet`, `houseByID`.
2. Rewrite the functions in findings 1 to 4 to use them. Preserve current behavior: first match wins, `+` becomes a space for towns, and the error values stay the same.
3. Optionally return the canonical world name from the world map to drop `cases.Title` on the hot path.
4. Add benchmarks (`go test -bench`) for the old and new lookups with a realistic list size, with both hit and miss inputs, and confirm allocations per op with `-benchmem`.

## Caveats
- The actual list sizes come from the downloaded `data.min.json` mapping (see `src/tibiamapping`), so I could not measure them. The severity ranking assumes creatures and spells are in the hundreds to low thousands and worlds and towns are in the tens.
- The review did not cover upstream fetching/caching of tibia.com pages, which is a separate and probably larger latency source for the same endpoints.
