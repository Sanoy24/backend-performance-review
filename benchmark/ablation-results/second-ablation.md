# Second reference ablation — does `dotnet.md` earn its place?

**Status: run on 2026-10-10; results below.** This design was committed before any run started.
Results are appended below the line at the end, without editing anything above it except this
status line.

## Why this run exists

The [first ablation](first-ablation.md) found no measurable difference between reference tiers
on the FastAPI template. Every issue there was visible in the code, so the category references
were enough. The harness says the next step, before shortening or removing any reference, is a
targeted leave-one-out comparison on a case chosen because a technology reference should matter.

This is that comparison. On the ASP.NET Core RealWorld app, two of the four expected issues are
blocking calls in async code: the synchronous EF Core `BeginTransaction()`, `Commit()` and
`Count()` inside async handlers, where `*Async` equivalents exist (`GT-001` and `GT-003`). The
generic category reference `application/async-and-blocking.md`, present in both arms, already
covers "a synchronous datastore or HTTP client in an async handler". What `technology/dotnet.md`
adds is the .NET-specific consequence: a blocked call holds a thread-pool thread, the pool grows
only slowly, and latency degrades progressively under load (thread-pool starvation). Neither
file names the EF Core methods. Of the pinned cases, this is the one where a technology
reference has the most to add, so it is the fairest test of whether one does.

The question: **with every other routed reference present, does adding `dotnet.md` change the
real issues found?**

## Design

| | |
|:--|:--|
| Case | `gothinkster/aspnetcore-realworld-example-app` @ `a397d1197b22edeffa4d2563fa5f4f7f11d0b254` (C#, ASP.NET Core, EF Core, SQLite and SQL Server) |
| References | Exactly the 11 references `detect_stack.py` routes for this commit, plus the shared methodology and rubrics. The router also routes `infrastructure/resources.md`; the harness only allows technology files in the technology tier, so it goes in the category tier |
| Arms run | **Without** `dotnet.md`: the harness's `category_technology` arm (shared + category + `docker.md`, `rest.md`, `sqlite.md`, `sqlserver.md`; 25 files, about 63.8k tokens). **With** `dotnet.md`: the `full_routed` arm (the same plus `technology/dotnet.md`; 26 files, about 67.0k). Token counts are characters ÷ 4, an approximation |
| Arm not run | `category_only` is exported, because the harness always builds three arms, but is not run. It is not part of the question, and leaving it out saves three runs |
| Bundles | Frozen by SHA-256 before any run, as printed by `--plan-only`:<br>`category_only` `a7d4a2817254bde8035449ffe81a2ddbaa4475d701ef497709c906392c7ac640` (not run)<br>`category_technology` `a2a3b3c09b863987052feca4daace8e2431cff62d4599dce8920d06416b6711d`<br>`full_routed` `f902ebbe62a69c6441adc7ea5810097ba1ab6467213dab5cd7842bf916eb0f87`<br>prompt `efdd77aacd05c600a113220c8ac1ac32b892068b56a7df07f63ebfa13a2d5f5f` |
| Manifest | [`second-ablation-manifest.json`](second-ablation-manifest.json). The prompt, [`second-ablation-prompt.txt`](second-ablation-prompt.txt), is a byte copy of the first ablation's |
| Trials | Three repeats, the harness's rotated orders with `category_only` skipped: (without, with), (with, without), (without, with) |
| Model | `claude-sonnet-5-5` in every run, read from each run's transcript metadata. If the six runs did not all use one model, the ablation is void |
| Skill | `main` at `7835069`, exported by `reference_ablation.py --export-dir`. Every slot carries the same helpers, including the bundled validator (#120) and the stack detector with its registry (#124) |
| Isolation | Each reviewer receives only its slot directory and a separate read-only checkout of the target. No slot contains ground truth, another arm's references, or the slot-to-arm mapping. Each run's transcript is checked afterwards for reads, and now also writes, outside its slot, its output files and the target |
| Order | Sequential, in slot order within each repeat |

The ASP.NET Core app was also used in the fifth A/B comparison. That does not favour either arm:
both run the same skill on the same code and differ by one file.

## What is measured

- **Expected issues (primary):** which of `GT-001`–`GT-004` each run reports as a finding,
  matched by mechanism and location with the rules used in the A/B comparisons.
- **The blocking-in-async issues:** `GT-001` and `GT-003`, counted separately.
- **The .NET framing (descriptive, outside the decision rules):** whether a run's findings for
  `GT-001` or `GT-003` name the thread-pool starvation consequence.
- **Breadth:** distinct real performance issues per run, adjudicated against source as in the
  first ablation. A claim checked and found not to be a real performance issue is rejected for
  both arms. Instrumentation-only findings are not counted.
- **Discipline:** unsourced performance estimates (every unit-bearing number extracted with
  `score.py`'s pattern and adjudicated). The case has no `forbidden` entries.
- **Cost:** total tokens per run, as reported by the agent runtime, and elapsed time.
- **Publishable output:** whether each `review.json` passes the bundled validator.

The adjudicator knows which arm wrote which report; it is not blind. To limit that, findings are
read from each run's `review.json`, and every rejection is applied to both arms, with the source
fact that settled it written down.

### What this run cannot produce

As in the first ablation, there is no named human audit and no billing-sourced cost, so the
harness's official reference-quality score is reported as **withheld**. The six reviews are kept
so a human auditor can complete it.

## Decision rules, fixed now

**`dotnet.md` earns its place on this case** if, compared with the arm without it, the arm with it:

- reports `GT-001` or `GT-003` in **at least 2 of 3** runs where the other arm reports that
  issue in **at most 1 of 3**; or
- has a mean count of distinct real issues higher by **at least 1.0**;

and in either case makes no more unsourced estimates.

**Otherwise:** no measurable benefit from `dotnet.md` on this case at n = 3. Because this is the
targeted comparison the harness asks for, that result would justify reviewing `dotnet.md` for
overlap with the category references, especially `application/async-and-blocking.md`. It would
not by itself justify removing or shortening the file.

If the arm **without** `dotnet.md` does better by the same thresholds, that is reported with the
same prominence, as a sign that the file may distract.

n = 3 per arm, one case, one model. Directional, not a general result.

---

## Results

**Answer: on this case, `dotnet.md` made no measurable difference.** Both blocking-in-async issues
were found as often with it as without it: `GT-001` in 2 of 3 runs in each arm, and `GT-003` in
3 of 3 in each. The mean count of distinct real issues was 8.67 with it and 8.33 without, a
difference of 0.33, under the 1.0 threshold. Neither "earns its place" rule is met, and the arm
without `dotnet.md` did not do better either. As pre-registered, this justifies reviewing
`dotnet.md` for overlap with `application/async-and-blocking.md`. It does not by itself justify
removing or shortening the file.

### Runs

All six runs used `claude-sonnet-5-5` on every model call, read from each run's transcript
metadata. Every `review.json` passed the bundled validator. Each transcript was checked for reads
and writes outside the run's slot, its two output files and the target: none were found. The
reports are in [`raw-second/`](raw-second/). `category_only` was exported and not run, as
pre-registered.

| Repeat | Without `dotnet.md` | With `dotnet.md` |
|:--|:--|:--|
| 1 | `r1-s2` | `r1-s3` |
| 2 | `r2-s3` | `r2-s1` |
| 3 | `r3-s1` | `r3-s2` |

### Summary by arm

| | Without `dotnet.md` | With `dotnet.md` |
|:--|:--|:--|
| `GT-001` synchronous transaction on every request | 2 of 3 | 2 of 3 |
| `GT-002` every favorite row loaded to count it | 3 of 3 | 3 of 3 |
| `GT-003` synchronous `Count()` in an async handler | 3 of 3 | 3 of 3 |
| `GT-004` a round-trip pair per new tag | 2 of 3 | 3 of 3 |
| Expected issues per run | 4, 4, 2 (mean 3.33) | 3, 4, 4 (mean 3.67) |
| Distinct real issues per run | 8, 10, 7 (mean 8.33) | 8, 9, 9 (mean 8.67) |
| Findings reported per run | 8, 9, 5 | 7, 8, 7 |
| Unsourced performance estimates | 0 | 0 |
| Async findings that name thread-pool starvation or slow thread injection | 1 of 3 runs | 1 of 3 runs |
| Validator passed | 3 of 3 | 3 of 3 |
| Total tokens per run | 134,339; 139,385; 114,417 (mean 129,380) | 125,902; 121,842; 139,556 (mean 129,100) |
| Elapsed per run | 6.8, 5.2, 3.9 min (mean 5.3) | 4.5, 4.5, 4.5 min (mean 4.5) |

Runs are listed in repeat order. Tokens are the agent runtime's total per run. Cost from billing
was not available, so the official reference-quality score stays **withheld**, as pre-registered.

### The distinct real issues

Each was checked against the source at `a397d11`. A mechanism counts as a distinct issue when at
least one run reports it as the subject of its own finding; it is then credited in any run whose
finding names it, in either arm. Sub-points that no run reported on their own (offset paging, the
article body loaded and then nulled, the slug probe loop, comment creation loading every comment)
are credited as part of the issue they appeared under, not separately.

| Issue | Source fact | Without | With |
|:--|:--|:-:|:-:|
| No index on `Slug`, `Username`, `Email` | No `HasIndex` anywhere; `Program.cs:124` uses `EnsureCreated`, which indexes keys only | 3 | 3 |
| `GT-002` favorites loaded to count | `GetAllData` includes `ArticleFavorites` and `ArticleTags` in one query | 3 | 3 |
| Follow collections loaded for a membership check | `ProfileReader.cs` includes `Following` and `Followers`, then calls `Any` | 3 | 1 |
| Comments list unbounded | `Comments/List.cs` loads the article with every comment and author, tracked | 3 | 3 |
| Article `limit` uncapped | `Articles/List.cs:109` passes `Take(message.Limit ?? 20)` with no maximum | 2 | 3 |
| `GT-003` synchronous `Count()` | `Articles/List.cs:133` | 3 | 3 |
| `GT-001` synchronous transaction per request | `DBContextTransactionPipelineBehavior` calls synchronous `BeginTransaction`/`Commit` | 2 | 2 |
| Verbose logging to a synchronous console sink | `ServicesExtensions.cs:114` sets `MinimumLevel.Verbose()` unconditionally | 2 | 3 |
| `GT-004` per-tag round trips | `Articles/Create.cs`: `FindAsync` plus `SaveChangesAsync` per new tag | 2 | 3 |
| Tags list unbounded | `Tags/List.cs` reads every tag on each call | 2 | 2 |

No claim was rejected. No run attributed the backend to anything but .NET.

### What the runs did with `GT-001`

Each arm missed `GT-001` once, and in the same way: `r3-s1` (without) and `r1-s3` (with) both saw the
per-request transaction and listed it under `considered_not_reported`. Every run that did report it
rated it Low and P3. The answer key rates it Critical; that key is blind-pass-derived, which
`benchmark/README.md` calls weaker evidence. `GT-003` was always reported, at Low to Medium
severity against the key's High. `dotnet.md` describes how blocked threads starve the pool under
load, but runs with it rated these issues no higher than runs without it. Whether the skill
under-rates blocking calls in async code, or the key over-rates them, is a separate question this
run cannot settle.

### Where the starvation framing came from

One run in each arm named the thread-pool consequence: "starvation" without `dotnet.md`, "thread-pool
injection" with it. Without `dotnet.md`, it came from a falsifier line, and `application/async-and-blocking.md`, present in both arms, also mentions
starvation. So the one idea `dotnet.md` was expected to add appeared equally often without it.

### Cost

Mean tokens per run were within 0.3k of each other. `dotnet.md` adds about 3.2k tokens to the
bundle (characters ÷ 4) but did not raise the runtime total, consistent with the first ablation:
reviewers read references as they need them.

### Deviations and notes

- **Directory listing.** One reviewer (`r1-s3`) ran `ls` on the shared output directory while
  checking that it existed, which showed one other run's file names. The names do not reveal arms,
  and it opened none of them.
- **A false alarm in the scope check.** The transcript check flagged the word "coordinator" in one
  run's own report text. It was not a read of the coordinator file.
- **Adjudication was not blind.** As pre-registered, the adjudicator knew the mapping. Findings were
  read from each `review.json`, and the counting rule above was applied to both arms alike.

### What this does and does not show

On the one pinned case where a technology reference had the most to add, with one model and three
runs per arm, `dotnet.md` did not change which real issues were found, nor how they were rated.
The generic async reference was enough for the reviewer to spot the blocking calls. This is
evidence for reviewing `dotnet.md` for overlap with `application/async-and-blocking.md`, and for
checking whether its distinct content (thread-pool starvation, the `.Result` deadlock, GC and
container settings) is reaching reviews at all. It is not evidence that technology references are
useless in general: a case whose issues lie in runtime configuration rather than in visible code
could still show a difference.
