# Second reference ablation — does `dotnet.md` earn its place?

**Status: pre-registered, not yet run.** This design was committed before any run started.
Results will be appended below the line at the end, without editing anything above it except
this status line.

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

*Not yet run.*
