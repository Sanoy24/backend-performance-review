# Performance review: evolution chain endpoint (/api/v2/evolution-chain/{id})

Scope: static read of the code path. Nothing was run. Line numbers are from the checked-out commit.

Code path: `EvolutionChainResource` (pokemon_v2/api.py:375) -> `EvolutionChainDetailSerializer` (pokemon_v2/serializers.py:3464) -> `build_chain` (3479) -> `build_evolution_tree` (3494) -> `build_chain_link_entry` (3527) -> `PokemonEvolutionSerializer` (3377).

## Summary

The detail serializer runs queries inside a recursive per-species function. The query count grows with the number of species in the chain, and each evolution row adds more queries. This is an N+1 pattern. Eevee (about 9 species, 8 evolution rows from the base form) is the worst case. For each branching species, the serializer re-queries the database instead of loading everything once.

Rough query count for a chain with S species and E evolution rows, before any fix:
- 1 query for the chain itself.
- 1 for `baby_trigger_item` (lazy FK).
- 1 for the species list.
- (S-1) queries for evolution rows, one per non-root species (3486-3539).
- Up to E queries for natures (3438), when `nature_bitmask` is set.
- Up to E queries for evolution variables (3449), when `condition_expression` is set.

Eevee is therefore at least about 11 queries, and more with natures or condition expressions. It should be 3 to 4.

## Findings, ordered by priority

### 1. HIGH: one PokemonEvolution query per species (N+1) inside the recursion
File: pokemon_v2/serializers.py, lines 3533-3540 (`build_chain_link_entry`).

For every species that has `evolves_from_species`, the code runs
`PokemonEvolution.objects.filter(evolved_species=species["id"]).select_related(*POKEMON_EVOLUTION_FK_FIELDS)` and then serializes it. The recursion visits each species in the chain once, so the cost is one query per species. Wide chains (Eevee, Tyrogue, Wurmple, Ralts) have many species, so they get the most queries.

Fix: before building the tree, load all evolution rows for the chain in a single query. Use `PokemonEvolution.objects.filter(evolved_species__evolution_chain=obj).select_related(...)`. Equivalently, filter with `evolved_species__in=<ids from the species list>`. Group the rows in Python into a dict keyed by `evolved_species_id`. In `build_chain_link_entry`, look up `evolutions_by_species.get(species["id"], [])`. Alternatively, use `Prefetch("evolved_species", queryset=PokemonEvolution.objects.select_related(...))` on the species queryset (the reverse accessor is `evolved_species` because of the related_name on models.py:1943). This turns S-1 queries into 1.

### 2. HIGH: nested queries inside PokemonEvolutionSerializer, run once per evolution row
File: pokemon_v2/serializers.py, lines 3431-3442 (`get_allowed_natures`) and 3444-3454 (`get_condition_expression`).

- `get_allowed_natures` runs `Nature.objects.filter(id__in=...)` for every row with a `nature_bitmask`.
- `get_condition_expression` runs `EvolutionVariable.objects.filter(symbol__in=tokens)` for every row with an expression.

These multiply with finding 1: E rows cause up to 2E additional queries. The Nature table has 25 rows and the EvolutionVariable table is tiny. Both are static reference data.

Fix: load each table once per request, or once per process, into a dict. For example, build `{id: Nature}` and `{symbol: EvolutionVariable}` lazily and store them in `self.context`, or use a module-level cache with `functools.lru_cache` and invalidate it on data load. Then resolve the nature IDs and tokens in Python. Also note that `symbol__in` has no index requirement since the table is tiny, so the gain is purely from avoiding the round trips.

### 3. MEDIUM: lazy foreign key on the root queryset
Files: pokemon_v2/api.py:376 (`queryset = EvolutionChain.objects.all()`) and pokemon_v2/serializers.py:3465 (`baby_trigger_item = ItemSummarySerializer()`).

The detail view loads the chain with no `select_related`, so serializing `baby_trigger_item` costs one extra query for every chain that has a baby trigger item. It is a small cost, but it is trivial to remove.

Fix: set `queryset = EvolutionChain.objects.select_related("baby_trigger_item")`. Because the list view shares the queryset and the summary serializer does not need the join, you can override `get_queryset` for the retrieve action only.

### 4. MEDIUM: over-broad select_related
File: pokemon_v2/serializers.py, lines 3468-3472 (`POKEMON_EVOLUTION_FK_FIELDS`) used at 3534-3536.

The list is built from every many-to-one and one-to-one relation on `PokemonEvolution`. That includes fields the serializer does not need as nested objects (for example `evolved_species` and `gender`, which is output only as a PK). With about 15 or more nullable FKs, each query turns into a wide LEFT JOIN, so every evolution query pays for joins that are not needed. This is not a query-count problem but adds database time per row, and it will matter more once finding 1 moves everything into one larger query.

Fix: replace the generated list with an explicit list of the relations actually rendered by `PokemonEvolutionSerializer` (version_group, evolution_item, held_item, known_move, known_move_type, party_species, party_type, trade_species, location, evolution_trigger, region, required_pokemon_form, evolved_pokemon_form, used_move). Remove the rest. Since the fields are summary serializers that only need `name` and `url`, consider `.only()` or leaving as is if the join cost is acceptable after measuring.

### 5. LOW: quadratic work in Python
File: pokemon_v2/serializers.py.

- Line 3544: `next(x for x in summary_data if x["name"] == species["name"])` is a linear scan over the species list for every node, so O(S^2). Fix: build `summary_by_name = {x["name"]: x for x in summary_data}` once, or key by id.
- Lines 3511-3519: for each species, `build_evolution_tree` searches the existing tree with a stack from the root, so building is O(S^2) too. Fix: keep a `{species_id: link}` dict and append to `by_id[evolves_from_species_id]["children"]`. The input is ordered by `order, pk`, so a parent is normally already present. Handle the missing-parent case explicitly.

For Eevee-sized chains (under 10 species) this is negligible next to the database cost, so do it only after the fixes above.

### 6. LOW: redundant serialization pass over species
File: pokemon_v2/serializers.py, lines 3480-3488.

The same species queryset is serialized twice, once with `PokemonSpeciesSummarySerializer` (name and URL) and once with `PokemonSpeciesEvolutionSerializer` (name, id, parent, is_baby). The queryset is evaluated once and cached, so this is not a second query, but it does double the serialization work and the reverse-URL generation. The summary entry could be built from the same rows (name plus the URL reverse) in one pass.

### 7. LOW / outside this endpoint: no response caching
File: config/settings.py (see CACHES around line 117).

Evolution chains are static data that change only when the database is rebuilt. If the cache backend is only local-memory or dummy, the endpoint recomputes the result for every request. Consider per-view caching (`cache_page` or a conditional-GET/ETag layer) or CDN caching keyed on the URL, with invalidation on data rebuild. This is the cheapest way to hide the cost for popular chains, but it does not remove the N+1 and should come after fixes 1 and 2.

## Suggested order of work

1. Fix 1 and 2 together (one evolution query for the whole chain, plus reference-data lookups for natures and variables). This takes Eevee from 11+ queries to about 3 or 4.
2. Fix 3 (one-line `select_related`).
3. Trim select_related (4).
4. Add a query-count test using `assertNumQueries` on a branching chain, so the count is constant rather than growing with chain size.
5. Python-side cleanups (5, 6) and caching (7) as follow-ups.
