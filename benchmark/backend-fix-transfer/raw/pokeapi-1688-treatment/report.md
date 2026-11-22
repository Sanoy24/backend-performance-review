# Performance Review: pokeapi-1688-treatment (evolution-chain detail path)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the code behind `GET /evolution-chain/{id}`
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

## 1. Decision summary

### Overall assessment

The operators' report is confirmed by the source. The detail serializer fetches evolution details one species at a time while walking the chain tree, so query count grows with chain size; branching chains such as Eevee's pay the most. The effect is real but bounded (chains are small, fixed game data), and there is no runtime evidence, so the main finding is P2 rather than higher. Review confidence: Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P2)** | Replace the per-species `PokemonEvolution` query with one chain-scoped query grouped in memory; load natures and variables once per request | Matches the reported symptom directly; removes work proportional to chain size |
| 2 | **PERF-002 (P3)** | Add `select_related('baby_trigger_item')` on the detail queryset | One-line quick win |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is the detail response cached by a CDN or proxy in production? | Whether PERF-001 is felt at all | Deployment config or cache hit-rate metrics |
| Measured query count and latency for the largest chain | Confidence (to Confirmed) and priority | Query log or `assertNumQueries` probe in a test environment |
| Request rate on this endpoint | Severity | Access logs / APM |

### Validation commands

No repository-local command is justified without running code, which this review did not do. Procedure: capture the SQL count for one request to the largest chain (9 species in the seed CSV) in a non-production environment with Django's query logging, before and after the change (not-safe-on-production if enabling debug logging; safe on a staging copy).

## 2. Scope and method

**Reviewed:** `pokemon_v2/api.py` (`EvolutionChainResource`, viewset mixins), `pokemon_v2/serializers.py` (`EvolutionChainDetailSerializer`, `PokemonEvolutionSerializer`, summary serializers), `pokemon_v2/models.py` (evolution models), `config/settings.py` and `config/local.py` (database, cache), `data/v2/csv/pokemon_species.csv` and `pokemon_evolution.csv` (row counts only).
**Not reviewed:** all other endpoints, the data build, GraphQL, migrations and indexes, deployment and CDN configuration.

**Evidence available:** Uninstrumented. No metrics, traces, or query-count tests exist for this path (no `assertNumQueries` in `pokemon_v2`).
**Ranking method:** Structural signals only.
**Reference depth:** Django/DRF and PostgreSQL deep; Redis conceptual.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | The evolution-chain request path only |
| **Critical paths** | 2 / 2 (detail and list of evolution chains) |
| **Shared resources** | 2 / 2 (PostgreSQL connection, Redis cache config) |
| **Technology support** | Deep / Deep / Conceptual |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production caching in front of the API | No evidence exists | Deployment config, CDN metrics |
| Actual latency and query count | No evidence exists | Query log or APM trace |
| Index on `evolved_species_id` in production | Not examined | Migrations / `pg_indexes` |

## 3. Architecture overview

| Component | Technology | Role |
|:--|:--|:--|
| API | Django + DRF `ReadOnlyModelViewSet`, gunicorn | Serves `EvolutionChainResource` |
| Database | PostgreSQL, `CONN_MAX_AGE` 30 | Primary store |
| Cache | Redis via django-redis configured; DummyCache in local config | Configured, but not used by this code path |

**Shared resources:** the PostgreSQL connections held by gunicorn workers and threads.

## 4. Workload model

**Known**
- Detail serialization queries `PokemonEvolution` once per species that has `evolves_from_species` (`pokemon_v2/serializers.py:3534`).
- Largest chain in `pokemon_species.csv` has 9 species, 8 evolving; 19 `pokemon_evolution.csv` rows reference them.

**Assumed**
- Each ORM query is one database round trip. Affects PERF-001.

**Unknown**
- Request rate, upstream caching, measured latency.

**Derived**
- Largest chain: 1 chain lookup + 1 species query + 8 per-species evolution queries = 10 queries, plus 1 if `baby_trigger_item` is set, plus 1 per evolution row with `nature_bitmask` or `condition_expression` (none in that chain per the CSV). Inputs: CSV counts and `serializers.py:3479-3545`.

**Measured:** none.

## 5. Critical path analysis

| # | Path | Datastore ops | Bounded | Instrumented |
|:--|:--|:--|:--|:--|
| 1 | GET evolution-chain detail | 3 + one per evolving species + per-row extras | By chain size (small, fixed data) | No |
| 2 | GET evolution-chain list | One paginated query (page size 20), url-only serializer | Yes | No |

**Amplification points:** per-species evolution query; per-row nature and variable queries.
**Not analyzed in depth:** list path (no per-item queries found).

## 6. Layer analysis

### 6.3 Data access
`build_chain` runs two queries for species, then `build_chain_link_entry` recurses and issues one `select_related` query per evolving species. `PokemonEvolutionSerializer.get_allowed_natures` and `get_condition_expression` each issue a query per evolution row that has the relevant field set.

### 6.4 Cache
Redis is configured in `config/settings.py:117`, but nothing in `pokemon_v2` or `config` uses it for responses or querysets.

### 6.7 Observability
No query-count assertions, metrics, or traces for this path.

## 7. Findings

### PERF-001 — Per-species evolution queries while walking the chain

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `pokemon_v2/serializers.py:3534` (`EvolutionChainDetailSerializer.build_chain_link_entry`) |
| **Tags** | quick-win, scalability-risk |

**Problem**
`build_chain_link_entry` runs a `PokemonEvolution` query for each non-root species as it recurses, and `PokemonEvolutionSerializer` adds further queries per evolution row. Query count scales with chain size.

**Performance principle**
Per-item work against a data store should be batched into per-request work; round trips should not scale with result size.

**Evidence**
- `PokemonEvolution.objects.filter(evolved_species=species["id"]).select_related(...)` inside the recursive method (`serializers.py:3534`). Static.
- `EvolutionVariable.objects.filter(symbol__in=tokens)` per row with an expression (`serializers.py:3445`). Static.
- `Nature.objects.filter(id__in=...)` per row with a bitmask (`serializers.py:3432`). Static.
- Derived count for the largest seed chain: 8 evolution queries on top of the chain and species queries. No runtime evidence exists.

**Impact**
Position: critical path. Frequency: per evolving species. Growth: O(n) in chain size. Blast radius: endpoint.

**Conditions**
Detail requests for chains with many evolving species (9 species at most in the seed data) not served from an upstream cache. Chain size is fixed game data, so the multiplier is small; request rate is unknown.

**Counter-evidence**
- Searched for response or queryset caching in `pokemon_v2` and `config`: Redis is configured but unused here; upstream caching is invisible to the repository (lowers confidence in real-world impact).
- Each per-species query is one join query via `select_related`, not a nested N+1 (bounds impact).
- Tree building and the `next(...)` summary lookup are quadratic but in-memory over at most 9 entries (bounds impact).

**Why this might not matter**
Chains are small, queries are simple lookups on a persistent connection, and a CDN may absorb most traffic. Per-request cost could be a few milliseconds.

**Recommendation**
Fetch all `PokemonEvolution` rows for the chain in one query (`evolved_species__evolution_chain=obj` with the existing `select_related`), group by `evolved_species_id` in memory, and pass groups into `build_chain_link_entry`. Load `Nature` (at most 25 rows by the serializer's range) and `EvolutionVariable` once per request and reuse them. This removes the round trips instead of hiding them.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Single chain-scoped query, in-memory grouping, per-request lookup dicts | **preferred** | Query count constant in chain size |
| `Prefetch` on the species queryset | | Also constant, but the serializer is invoked manually so it needs reshaping anyway |
| Cache the serialized response | | Masks the work; needs invalidation after data rebuilds; consider only if measurement shows hot chains |

**Trade-offs**
A little more serializer code and a per-request dictionary; ordering of `evolution_details` must be preserved so output is unchanged.

**Validation**
Metric: DB query count for the largest chain detail. Expectation: independent of chain size (direction only; magnitude unmeasured). Falsifier: query count already constant, or latency not database-dominated. Safety: safe-on-production (read-only measurement on a staging copy preferred). Guard: `assertNumQueries` test on a branching chain.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-002 | Low | High | P3 | `pokemon_v2/api.py:376` | Detail queryset lacks `select_related('baby_trigger_item')`; one extra query when the item is set |

### Considered and not reported

- In-memory tree building and summary lookup (`serializers.py:3490`): quadratic but over at most 9 entries, no I/O. Revisit if chains grow by an order of magnitude.

## 8. Prioritized action plan

### P2
### P3

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P2 | Small | Matches the reported symptom |
| 2 | PERF-002 | P3 | Trivial | Quick win, sequenced early without changing its priority |

**If only one thing is done:** PERF-001.

## 9. Validation plan

### PERF-001
- **Baseline:** query count for the largest chain on a staging copy with query logging (not-safe-on-production if debug logging is enabled).
- **Change:** single chain-scoped evolution query plus per-request nature/variable lookups.
- **Measurement:** number of SQL statements for one detail request.
- **Expectation:** constant in chain size.
- **Falsifier:** count already constant, or latency dominated elsewhere.
- **Guard:** `assertNumQueries` test.
- **Commands:** none justified without running code.

### PERF-002
- Same measurement on a chain with a baby trigger item; expect one fewer query.

### Instrumentation gaps to close first
No query-count tests or endpoint latency metrics exist; add a query-count test before changing the serializer.

## 11. Notes on this review

- No runtime metric in this report was measured. The 10-query figure for the largest chain is a derivation from the seed CSVs and the code, labelled as such.
- Machine-readable output: `review.json` in the same directory.
