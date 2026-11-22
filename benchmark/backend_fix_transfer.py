#!/usr/bin/env python3
"""Select real backend performance fixes and score whether a review localizes them.

The answer key is a merged pull request in an open-source backend service that its own
maintainers titled as a performance fix. Where the expert changed the code is a fact this
project had no hand in. The case is the commit before the fix; the reviewer gets that
checkout and one neutral line naming the slow operation, never the cause or the fix.

  collect  (network, via the `gh` CLI) build a repository pool from GitHub search, and for
           each repository, in SHA-256 order, snapshot its merged performance-titled pull
           requests in the window with their files and base-to-merge diff
  select   (offline) apply the fixed eligibility rules to a snapshot and order the
           candidates: repositories by SHA-256 of their name, one pull request per
           repository, the first by SHA-256 of "owner/name#number"
  verify   (network and disk) fetch each candidate's base commit alone and require that
           the skill's own stack detector routes a web framework and a datastore or cache,
           and that the checkout is not too large to review; stop at --cases passing
  check-symptom
           reject a symptom line that names a changed file or a function from a changed
           hunk's context, so the hint cannot give away where the fix is
  score    compare one review.json with one expert patch, as swefficiency_transfer.py does

A finding is a *file hit* when it names a changed source file, and a *hunk hit* when its line
falls inside a changed hunk of the base file, widened by `--window` lines. Tests, docs,
migrations, generated code, vendored code and lockfiles never count. No model is invoked here.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETECTOR = ROOT / "skills" / "backend-performance-review" / "scripts" / "detect_stack.py"

# --- Rules fixed before any pull request is read -------------------------------------------

LANGUAGES = ("Go", "Python", "Java", "Kotlin", "TypeScript", "JavaScript", "C#", "Ruby",
             "Rust", "PHP")
MERGED_WINDOW = ("2026-01-01", "2026-10-31")
MIN_STARS = 100
# The first pool, the most-starred repositories per language, was mostly libraries and tools:
# of its first 200 repositories, 4 were deployable services. The pool is now built from the
# topics maintainers use for services. Recorded as a deviation in the test's design.
POOL_TOPICS = ("self-hosted", "backend", "api", "rest-api", "saas", "microservice")
MAX_REPO_KB = 150000
PUSHED_SINCE = "2026-09-01"
LICENSES = frozenset({"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "mpl-2.0",
                      "0bsd", "unlicense"})
PERF_TITLE = re.compile(
    r"\b(perf|performance|slow(?:ness|er)?|faster|speed(?:s|ing)?[ -]?up|optimi[sz](?:e|es|ed|"
    r"ation|ing)|latency|n\s?\+\s?1|throughput|bottleneck)\b", re.IGNORECASE)
NOT_PERF_TITLE = re.compile(
    r"\b(revert|test(?:s|ing)?|benchmark(?:s)?|ci|docs?|typo|lint|deps?|bump|chore)\b",
    re.IGNORECASE)
MAX_SOURCE_FILES = 3
MAX_CHANGED_LINES = 400
MAX_REVIEW_FILES = 3000
FRAMEWORK_SIGNALS = frozenset({"rest", "graphql", "grpc"})
DATA_KINDS = frozenset({"datastore", "cache"})

SOURCE_SUFFIXES = (".go", ".py", ".java", ".kt", ".kts", ".ts", ".tsx", ".js", ".mjs", ".cjs",
                   ".cs", ".rb", ".rs", ".php", ".scala", ".ex", ".exs", ".sql")
NON_SOURCE_PARTS = ("test", "tests", "__tests__", "spec", "specs", "testdata", "fixtures",
                    "doc", "docs", "examples", "example", "bench", "benchmark", "benchmarks",
                    "vendor", "node_modules", "third_party", "migrations", "migrate",
                    "generated", "gen", "mocks", "mock", ".github", "scripts")
NON_SOURCE_NAME = re.compile(
    r"(^test_|_test\.(go|py|rb|rs)$|\.(test|spec)\.[jt]sx?$|Tests?\.(java|kt|cs)$|_spec\.rb$|"
    r"\.pb\.go$|_pb2\.py$|\.generated\.|\.min\.js$|\.d\.ts$)")

DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_source(path):
    """A file whose change could be where the slowness lived."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if not name.endswith(SOURCE_SUFFIXES) or NON_SOURCE_NAME.search(name):
        return False
    return not any(part.lower() in NON_SOURCE_PARTS for part in parts[:-1])


def changed_regions(patch):
    """Map each changed source file to the base-side line ranges its hunks touch."""
    regions = {}
    current = None
    for line in patch.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            current = header.group(1) if is_source(header.group(1)) else None
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk and current is not None:
            start = int(hunk.group(1))
            length = 1 if hunk.group(2) is None else int(hunk.group(2))
            end = start + length - 1 if length else start
            regions.setdefault(current, []).append((start, end))
    return regions


def changed_lines(patch):
    """Added plus removed lines in source files only."""
    count, current = 0, None
    for line in patch.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            current = header.group(1) if is_source(header.group(1)) else None
            continue
        if current and line[:1] in "+-" and not line.startswith(("+++", "---")):
            count += 1
    return count


def hunk_contexts(patch):
    """The function or type names git printed after each source hunk header."""
    names, current = set(), None
    for line in patch.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            current = header.group(1) if is_source(header.group(1)) else None
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk and current is not None:
            names.update(IDENTIFIER.findall(hunk.group(3)))
    return names


# --- select ---------------------------------------------------------------------------------

def eligible(pr):
    """Why a snapshotted pull request is excluded, or None if it is eligible."""
    title = pr.get("title") or ""
    if not PERF_TITLE.search(title):
        return "title does not state a performance change"
    if NOT_PERF_TITLE.search(title):
        return "title marks a revert, test, benchmark, docs or dependency change"
    if not pr.get("base_commit") or not pr.get("patch"):
        return "no base commit or diff"
    count = len(changed_regions(pr["patch"]))
    if not 1 <= count <= MAX_SOURCE_FILES:
        return "changes %d source files, outside 1-%d" % (count, MAX_SOURCE_FILES)
    if changed_lines(pr["patch"]) > MAX_CHANGED_LINES:
        return "changes more than %d source lines" % MAX_CHANGED_LINES
    return None


def changed_paths(patch):
    """Every path the diff touches, source or not."""
    return [m.group(1) for m in (DIFF_HEADER.match(line) for line in patch.splitlines()) if m]


BODY_CHARS = 3000


def select(snapshot, excluded_repos=(), all_prs=False):
    """Order eligible candidates by a rule fixed before any review, never by outcomes.

    By default, one pull request per repository: the first eligible by hash. With `all_prs`,
    every eligible pull request, each with its `rank` in its repository's hash order, so the
    blind fix check can move to the next one when the first is not a server-side fix.
    """
    excluded = {name.lower() for name in excluded_repos}
    chosen, reasons = [], []
    for repo in sorted(snapshot.get("repositories", []), key=lambda r: _sha(r["full_name"])):
        if repo["full_name"].lower() in excluded:
            reasons.append({"repo": repo["full_name"], "excluded": "used by an earlier case"})
            continue
        prs = sorted(repo.get("pull_requests", []),
                     key=lambda p: _sha("%s#%d" % (repo["full_name"], p["number"])))
        rank = 0
        for pr in prs:
            why = eligible(pr)
            if why:
                reasons.append({"repo": repo["full_name"], "number": pr["number"],
                                "excluded": why})
                continue
            chosen.append({"repo": repo["full_name"], "number": pr["number"], "rank": rank,
                           "title": pr["title"], "body": (pr.get("body") or "")[:BODY_CHARS],
                           "base_commit": pr["base_commit"],
                           "merge_commit": pr.get("merge_commit"),
                           "license": repo.get("license"),
                           "changed_paths": changed_paths(pr["patch"]),
                           "source_files": sorted(changed_regions(pr["patch"]))})
            rank += 1
            if not all_prs:
                break
    return chosen, reasons


# --- check-symptom --------------------------------------------------------------------------

CODE_STYLE = re.compile(r"[a-z][A-Z]|_|\d")


def symptom_leaks(symptom, patch):
    """What in a symptom line would point at the fix, or an empty list.

    A plain domain word ("orders") is allowed even when a changed file shares it: a symptom
    has to name the operation. What is rejected is the changed file itself, by name with its
    extension or by any directory-qualified suffix, and any code-style identifier (camelCase,
    snake_case, or with digits) that git printed as a changed hunk's context.
    """
    leaks = []
    lowered = symptom.lower().replace("\\", "/")
    for path in changed_regions(patch):
        parts = path.lower().split("/")
        suffixes = ["/".join(parts[i:]) for i in range(len(parts) - 1)] + [parts[-1]]
        if any(re.search(r"(?<![\w/.])%s\b" % re.escape(s), lowered) for s in suffixes):
            leaks.append("names changed file %s" % path)
    words = set(IDENTIFIER.findall(symptom))
    for name in sorted(hunk_contexts(patch) & words):
        if CODE_STYLE.search(name):
            leaks.append("names %s from a changed hunk's context" % name)
    return leaks


# --- collect and verify (network) -----------------------------------------------------------

TRANSIENT = ("timeout", "timed out", "connectex", "connection reset", "connection refused",
             "eof", "http 502", "http 503", "http 504", "tls handshake")


def _gh(args, accept=None):
    command = ["gh", "api", "-X", "GET"] + list(args)
    if accept:
        command += ["-H", "Accept: " + accept]
    for attempt in range(5):
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if done.returncode == 0:
            return done.stdout
        error = done.stderr.lower()
        if "rate limit" in error or "secondary" in error:
            time.sleep(30 * (attempt + 1))
            continue
        if any(word in error for word in TRANSIENT):
            time.sleep(10 * (attempt + 1))
            continue
        raise RuntimeError("gh %s failed: %s" % (" ".join(args), done.stderr.strip()))
    raise RuntimeError("gh %s: rate limited" % " ".join(args))


def repository_pool(gh=_gh, pages=1):
    pool = {}
    for topic in POOL_TOPICS:
        for language in LANGUAGES:
            query = ('topic:%s language:"%s" stars:>=%d pushed:>=%s archived:false fork:false'
                     % (topic, language, MIN_STARS, PUSHED_SINCE))
            for page in range(1, pages + 1):
                result = json.loads(gh(["search/repositories", "-f", "q=" + query, "-f",
                                        "sort=stars", "-f", "per_page=100", "-f",
                                        "page=%d" % page]))
                for item in result.get("items", []):
                    spdx = ((item.get("license") or {}).get("spdx_id") or "").lower()
                    if spdx in LICENSES and (item.get("size") or 0) <= MAX_REPO_KB:
                        pool.setdefault(item["full_name"], {
                            "full_name": item["full_name"], "license": spdx,
                            "language": language, "topic": topic,
                            "stars": item.get("stargazers_count")})
                time.sleep(2.5)
    return [pool[name] for name in sorted(pool, key=_sha)]


def repository_prs(full_name, gh=_gh):
    query = ("repo:%s is:pr is:merged merged:%s..%s perf OR performance OR slow OR faster OR "
             "optimize" % (full_name, MERGED_WINDOW[0], MERGED_WINDOW[1]))
    result = json.loads(gh(["search/issues", "-f", "q=" + query, "-f", "per_page=100"]))
    time.sleep(2.5)
    prs = []
    for item in result.get("items", []):
        if not PERF_TITLE.search(item.get("title") or ""):
            continue
        number = item["number"]
        pull = json.loads(gh(["repos/%s/pulls/%d" % (full_name, number)]))
        merge = pull.get("merge_commit_sha")
        if not merge:
            continue
        commit = json.loads(gh(["repos/%s/commits/%s" % (full_name, merge)]))
        parents = commit.get("parents") or []
        if not parents:
            continue
        base = parents[0]["sha"]
        patch = gh(["repos/%s/compare/%s...%s" % (full_name, base, merge)],
                   accept="application/vnd.github.diff")
        prs.append({"number": number, "title": item["title"], "body": pull.get("body") or "",
                    "merged_at": pull.get("merged_at"), "base_commit": base,
                    "merge_commit": merge, "patch": patch})
    return prs


def collect(limit_repos, gh=_gh, log=sys.stderr, offset=0, pool=None):
    """Snapshot pull requests for pool repositories [offset, offset + limit), in hash order.

    Pass the frozen `pool` from the first batch to every later one: GitHub search results
    drift as star counts change, so rebuilding the pool could reorder it between batches.
    """
    pool = repository_pool(gh) if pool is None else pool
    repositories = []
    for repo in pool[offset:offset + limit_repos]:
        try:
            repo = dict(repo, pull_requests=repository_prs(repo["full_name"], gh))
        except RuntimeError as error:
            repo = dict(repo, pull_requests=[], error=str(error))
        repositories.append(repo)
        print("%s: %d pull requests" % (repo["full_name"], len(repo["pull_requests"])),
              file=log)
    return {"rules": rules(), "pool_size": len(pool), "offset": offset,
            "pool_sha256": _sha(json.dumps(pool, sort_keys=True)),
            "pool": [r["full_name"] for r in pool], "repositories": repositories}


def rules():
    return {"languages": list(LANGUAGES), "merged_window": list(MERGED_WINDOW),
            "min_stars": MIN_STARS, "pool_topics": list(POOL_TOPICS), "max_repo_kb": MAX_REPO_KB, "pushed_since": PUSHED_SINCE,
            "licenses": sorted(LICENSES), "perf_title": PERF_TITLE.pattern,
            "not_perf_title": NOT_PERF_TITLE.pattern, "max_source_files": MAX_SOURCE_FILES,
            "max_changed_lines": MAX_CHANGED_LINES, "max_review_files": MAX_REVIEW_FILES,
            "framework_signals": sorted(FRAMEWORK_SIGNALS), "data_kinds": sorted(DATA_KINDS)}


def detected_signals(detection):
    """{kind: {signal, ...}} from detect_stack.py's `detected` mapping."""
    found = {}
    for kind, items in (detection.get("detected") or {}).items():
        found[kind] = {item.get("signal") for item in items if isinstance(item, dict)}
    return found


def routes_backend(detection):
    """Whether the skill's detector found a web framework and a datastore or cache."""
    found = detected_signals(detection)
    frameworks = found.get("framework", set()) & FRAMEWORK_SIGNALS
    return bool(frameworks) and any(found.get(kind) for kind in DATA_KINDS)


def count_files(checkout):
    return sum(1 for path in Path(checkout).rglob("*")
               if path.is_file() and ".git" not in path.parts and is_source(
                   path.relative_to(checkout).as_posix()))


class FetchError(RuntimeError):
    """The base commit could not be fetched."""


def fetch(full_name, commit, destination, attempts=3):
    """Fetch one commit alone; reuse a checkout already at that commit."""
    destination = Path(destination)
    run = lambda *a: subprocess.run(["git", "-C", str(destination)] + list(a), check=True,
                                    capture_output=True, text=True)
    if (destination / ".git").is_dir():
        try:
            if run("rev-parse", "HEAD").stdout.strip() == commit:
                return destination
        except subprocess.CalledProcessError:
            pass
    else:
        destination.mkdir(parents=True, exist_ok=True)
        run("init", "-q")
        run("remote", "add", "origin", "https://github.com/%s.git" % full_name)
    for attempt in range(attempts):
        try:
            run("fetch", "-q", "--depth", "1", "origin", commit)
            run("checkout", "-q", "FETCH_HEAD")
            return destination
        except subprocess.CalledProcessError as error:
            last = (error.stderr or "").strip()
            time.sleep(5 * (attempt + 1))
    raise FetchError("could not fetch %s@%s: %s" % (full_name, commit, last))


def verify(candidates, workdir, cases, detect=None, fetcher=fetch):
    """Check candidates in order until `cases` pass; record why each failed."""
    detect = detect or (lambda path: json.loads(subprocess.run(
        [sys.executable, "-I", str(DETECTOR), str(path)], capture_output=True, text=True,
        check=True).stdout))
    passed, records = [], []
    for candidate in candidates:
        if len(passed) >= cases:
            break
        slug = candidate["repo"].replace("/", "__")
        try:
            checkout = fetcher(candidate["repo"], candidate["base_commit"],
                               Path(workdir) / slug)
        except FetchError as error:
            records.append(dict(candidate, review_files=None, detected=[],
                                excluded="base commit could not be fetched",
                                fetch_error=str(error)[:300]))
            continue
        detection = detect(checkout)
        files = count_files(checkout)
        record = dict(candidate, review_files=files,
                      detected=sorted(signal for signals in detected_signals(detection).values()
                                      for signal in signals))
        if not routes_backend(detection):
            record["excluded"] = "detector routes no web framework with a datastore or cache"
        elif files > MAX_REVIEW_FILES:
            record["excluded"] = "more than %d source files to review" % MAX_REVIEW_FILES
        else:
            passed.append(record)
        records.append(record)
    return passed, records


# --- blind service classification -----------------------------------------------------------
#
# The detector reads every dependency declaration, including dev dependencies, examples and
# docs, so frameworks, test libraries and CLIs that mention a database pass it. In the first
# batch, about three of ten detector passes were deployable services. A separate model now
# labels each detector-passing repository from a packet holding only its README excerpt and
# top-level listing: never the pull request, its title, its diff, or any score.

README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md", "Readme.md")
README_CHARS = 4000
SERVICE_QUESTION = (
    "Is this repository a deployable backend service: an application its maintainers run or "
    "self-host as a server that handles network requests and keeps data in a datastore or "
    "cache? Answer no for a library, framework, SDK, CLI, desktop or mobile app, developer "
    "tool, test helper, or static-analysis tool, even if it can serve HTTP.")


def service_packet(checkout, repo):
    """What the classifier may see about one repository, and nothing else."""
    checkout = Path(checkout)
    readme = ""
    for name in README_NAMES:
        path = checkout / name
        if path.is_file():
            readme = path.read_text(encoding="utf-8", errors="replace")[:README_CHARS]
            break
    listing = sorted(p.name + ("/" if p.is_dir() else "") for p in checkout.iterdir()
                     if p.name != ".git")
    return {"repo": repo, "question": SERVICE_QUESTION, "top_level": listing,
            "readme_excerpt": readme}


def packets(verified, workdir):
    """Packets for every detector-passing record, in the order verify checked them."""
    return [service_packet(Path(workdir) / record["repo"].replace("/", "__"), record["repo"])
            for record in verified if "excluded" not in record]


def accept(verified, decisions, cases):
    """Keep detector passes the classifier called services, in order, up to `cases`."""
    labels = {d["repo"]: d for d in decisions}
    accepted, records = [], []
    for record in verified:
        if "excluded" in record:
            continue
        decision = labels.get(record["repo"])
        if decision is None:
            raise ValueError("no service decision for %s" % record["repo"])
        if decision.get("service") is not True:
            records.append(dict(record, excluded="classifier: not a deployable service",
                                classifier_reason=decision.get("reason")))
            continue
        if len(accepted) < cases:
            accepted.append(dict(record, classifier_reason=decision.get("reason")))
    return accepted, records


# --- blind fix check ------------------------------------------------------------------------
#
# Title keywords also catch features and frontend changes: in the first pool, a chart option
# matched on "latency" and a service-worker image cap on "perf". A separate model now reads
# each eligible pull request of a service repository (its title, description and changed
# paths, never any review or score) and answers whether it fixes a server-side performance
# problem. The first yes per repository, in hash order, becomes the case.

FIX_QUESTION = (
    "Does this pull request fix a performance problem in server-side code, code that runs "
    "while the service handles requests or background jobs (latency, throughput, memory, CPU, "
    "or database or cache load)? Answer no for browser or frontend code, build, CI or test "
    "speed, documentation, a new feature or configuration option, or a refactor that states "
    "no performance problem.")


def fix_packets(candidates, service_repos):
    """One packet per eligible pull request of each service repository, in order."""
    services = set(service_repos)
    return [{"repo": c["repo"], "number": c["number"], "question": FIX_QUESTION,
             "title": c["title"], "body": c.get("body", ""),
             "changed_paths": c.get("changed_paths", [])}
            for c in candidates if c["repo"] in services]


def accept_fixes(service_records, candidates, decisions, cases):
    """Per service repository, in order, the first pull request judged a server-side fix."""
    labels = {(d["repo"], d["number"]): d for d in decisions}
    by_repo = {}
    for candidate in sorted(candidates, key=lambda c: c.get("rank", 0)):
        by_repo.setdefault(candidate["repo"], []).append(candidate)
    accepted, rejected = [], []
    for record in service_records:
        chosen = None
        for candidate in by_repo.get(record["repo"], []):
            decision = labels.get((candidate["repo"], candidate["number"]))
            if decision is None:
                raise ValueError("no fix decision for %s#%d"
                                 % (candidate["repo"], candidate["number"]))
            if decision.get("server_side_fix") is True:
                chosen = dict(candidate, review_files=record.get("review_files"),
                              detected=record.get("detected"),
                              classifier_reason=record.get("classifier_reason"),
                              fix_reason=decision.get("reason"))
                break
            rejected.append({"repo": candidate["repo"], "number": candidate["number"],
                             "excluded": "fix check: not a server-side performance fix",
                             "fix_reason": decision.get("reason")})
        if chosen is not None and len(accepted) < cases:
            accepted.append(chosen)
    return accepted, rejected


# --- score ----------------------------------------------------------------------------------

def _normalize(path, known):
    cleaned = str(path or "").replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned in known:
        return cleaned
    for candidate in known:
        if cleaned.endswith("/" + candidate):
            return candidate
    return cleaned


def localize(patch, review, window=20):
    regions = changed_regions(patch)
    indexed = list(enumerate(review.get("findings") or []))
    indexed.sort(key=lambda item: (PRIORITY_ORDER.get(item[1].get("priority"), 9), item[0]))
    findings = [finding for _, finding in indexed]
    file_hits, hunk_hits = [], []
    for finding in findings:
        location = finding.get("location") or {}
        path = _normalize(location.get("file"), regions)
        if path not in regions:
            continue
        file_hits.append(finding.get("id"))
        line = location.get("line")
        if isinstance(line, int) and any(start - window <= line <= end + window
                                         for start, end in regions[path]):
            hunk_hits.append(finding.get("id"))
    top = findings[0] if findings else None
    return {"source_files": sorted(regions), "window": window, "findings": len(findings),
            "file_hit": bool(file_hits), "hunk_hit": bool(hunk_hits),
            "file_hits": file_hits, "hunk_hits": hunk_hits,
            "top_finding": top.get("id") if top else None,
            "top_finding_file_hit": bool(top) and top.get("id") in file_hits}


# --- CLI ------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    c = commands.add_parser("collect")
    c.add_argument("--repos", type=int, required=True, help="pool repositories to snapshot")
    c.add_argument("--offset", type=int, default=0, help="first pool position to snapshot")
    c.add_argument("--pool", type=Path, required=True,
                   help="frozen pool file: read if it exists, else built once and written")
    c.add_argument("--out", type=Path, required=True)
    s = commands.add_parser("select")
    s.add_argument("--snapshot", type=Path, required=True)
    s.add_argument("--exclude", type=Path, help="JSON list of repository names to skip")
    s.add_argument("--all", action="store_true", help="every eligible PR per repository")
    v = commands.add_parser("verify")
    v.add_argument("--candidates", type=Path, required=True)
    v.add_argument("--workdir", type=Path, required=True)
    v.add_argument("--cases", type=int, default=10)
    p = commands.add_parser("packets", help="blind classifier packets for detector passes")
    p.add_argument("--verified", type=Path, required=True, help="verify's `checked` records")
    p.add_argument("--workdir", type=Path, required=True)
    a = commands.add_parser("accept", help="keep detector passes classified as services")
    a.add_argument("--verified", type=Path, required=True)
    a.add_argument("--decisions", type=Path, required=True)
    a.add_argument("--cases", type=int, default=10)
    f = commands.add_parser("fix-packets", help="blind fix-check packets for service repos")
    f.add_argument("--candidates", type=Path, required=True, help="select --all candidates")
    f.add_argument("--services", type=Path, required=True, help="accept's `cases`")
    g = commands.add_parser("accept-fixes", help="first server-side fix per service repo")
    g.add_argument("--services", type=Path, required=True)
    g.add_argument("--candidates", type=Path, required=True)
    g.add_argument("--decisions", type=Path, required=True)
    g.add_argument("--cases", type=int, default=10)
    k = commands.add_parser("check-symptom")
    k.add_argument("--patch", type=Path, required=True)
    k.add_argument("--symptom", required=True)
    r = commands.add_parser("score")
    r.add_argument("--patch", type=Path, required=True)
    r.add_argument("--review", type=Path, required=True)
    r.add_argument("--window", type=int, default=20)
    args = parser.parse_args(argv)

    if args.command == "collect":
        if args.pool.exists():
            pool = json.loads(args.pool.read_text(encoding="utf-8"))
        else:
            pool = repository_pool()
            args.pool.write_text(json.dumps(pool, indent=2) + "\n", encoding="utf-8")
        snapshot = collect(args.repos, offset=args.offset, pool=pool)
        args.out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        return 0
    if args.command == "select":
        excluded = json.loads(args.exclude.read_text(encoding="utf-8")) if args.exclude else ()
        chosen, reasons = select(json.loads(args.snapshot.read_text(encoding="utf-8")),
                                 excluded, all_prs=args.all)
        result = {"candidates": chosen, "excluded": reasons}
    elif args.command == "verify":
        passed, records = verify(json.loads(args.candidates.read_text(encoding="utf-8")),
                                 args.workdir, args.cases)
        result = {"cases": passed, "checked": records}
    elif args.command == "packets":
        result = packets(json.loads(args.verified.read_text(encoding="utf-8")), args.workdir)
    elif args.command == "accept":
        accepted, rejected = accept(json.loads(args.verified.read_text(encoding="utf-8")),
                                    json.loads(args.decisions.read_text(encoding="utf-8")),
                                    args.cases)
        result = {"cases": accepted, "rejected": rejected}
    elif args.command == "fix-packets":
        services = json.loads(args.services.read_text(encoding="utf-8"))
        result = fix_packets(json.loads(args.candidates.read_text(encoding="utf-8")),
                             [record["repo"] for record in services])
    elif args.command == "accept-fixes":
        accepted, rejected = accept_fixes(
            json.loads(args.services.read_text(encoding="utf-8")),
            json.loads(args.candidates.read_text(encoding="utf-8")),
            json.loads(args.decisions.read_text(encoding="utf-8")), args.cases)
        result = {"cases": accepted, "rejected": rejected}
    elif args.command == "check-symptom":
        leaks = symptom_leaks(args.symptom, args.patch.read_text(encoding="utf-8"))
        result = {"symptom": args.symptom, "leaks": leaks}
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if leaks else 0
    else:
        result = localize(args.patch.read_text(encoding="utf-8"),
                          json.loads(args.review.read_text(encoding="utf-8")), args.window)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
