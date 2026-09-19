#!/usr/bin/env python3
"""Prepare and score paired reference ablations without running model calls.

Manifest shape (paths are relative to this checkout):
  {"model": "model-id", "prompt": "benchmark/prompt.txt",
   "prompt_sha256": "64 hex digits", "cases": [{
    "id": "case-id", "truth": "benchmark/ground-truth/case.json",
    "references": {"common": [...], "category": [...], "technology": [...], "full": [...]},
    "trials": [{"id": "repeat-1", "order": ["category_only",
      "category_technology", "full_routed"], "arms": {
      "category_only": {"review": "...json", "audited_by": "name",
        "unsupported_claims": [], "context_tokens": 1, "reference_tokens": 1,
        "elapsed_seconds": 1, "cost_usd": 0.01, "prompt_sha256": "...",
        "bundle_sha256": "...", "tokenizer": "model tokenizer",
        "usage_source": "run usage record", "cost_source": "billing record"},
      "category_technology": {...}, "full_routed": {...}}}]}]}

`--plan-only` freezes each arm's reference paths and SHA-256 hashes before runs exist.
Curate those tiers before seeing outputs: common skill/methodology stays fixed; category
adds technology-agnostic references; technology adds only matching engine references;
full adds the remaining routed context. Give all arms
the same pinned repository, prompt, model, output format, and scope; restrict access to
unlisted references and ground truth. Repeat and rotate execution order. Record actual
usage/cost sources and have a human audit unsupported claims in every arm.
The default mode requires all three arms and uses the existing ground-truth scorer. It
reports a net supported-finding score (expected matches - false positives - manually
adjudicated unsupported claims); acceptable findings remain neutral. That score is only
compared within a paired case. Context tokens, elapsed time, and actual cost stay separate
so document size cannot masquerade as quality. Unmatched findings or ambiguous matching
withhold the score until the ground truth is adjudicated. No model is invoked here.
"""

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "scoring"))
sys.path.insert(0, str(ROOT / "scripts"))

import score as scorer  # noqa: E402
import json_schema_lite as schema_lite  # noqa: E402
import validate_review  # noqa: E402


ARMS = ("category_only", "category_technology", "full_routed")
TIERS = ("common", "category", "technology", "full")
PAIRS = (("category_only", "category_technology"),
         ("category_technology", "full_routed"))
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AblationError(ValueError):
    pass


def _path(root, raw, prefix=None):
    if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw:
        raise AblationError("invalid relative path %r" % raw)
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AblationError("invalid relative path %r" % raw)
    basename = parts[-1].lower()
    if (basename == ".env" or basename.startswith((".env.", "credentials"))
            or basename.endswith((".pem", ".key", ".tfvars"))):
        raise AblationError("secret file must not be read: %s" % raw)
    if prefix and not raw.startswith(prefix):
        raise AblationError("reference %r must be under %s" % (raw, prefix))
    path = (root / raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise AblationError("path %r escapes the checkout" % raw)
    if not path.is_file():
        raise AblationError("file does not exist: %s" % raw)
    return path


def _load(root, raw):
    path = _path(root, raw)
    if path.suffix != ".json":
        raise AblationError("expected a JSON artifact: %s" % raw)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AblationError("cannot parse %s: %s" % (raw, exc))


def _references(root, case):
    tiers = case.get("references")
    if not isinstance(tiers, dict) or set(tiers) != set(TIERS):
        raise AblationError("%s: references must declare %s" % (case.get("id"), TIERS))
    paths = []
    for tier in TIERS:
        files = tiers[tier]
        if not isinstance(files, list) or not files:
            raise AblationError("%s: %s references must be a nonempty list"
                                % (case.get("id"), tier))
        for raw in files:
            path = _path(root, raw, "skills/backend-performance-review/")
            if path.suffix not in (".md", ".yaml"):
                raise AblationError("reference is not Markdown or routing YAML: %s" % raw)
            paths.append(raw)
            if tier == "technology" and "/technology/" not in raw:
                raise AblationError("technology tier contains non-technology reference %s"
                                    % raw)
            if tier == "category" and "/technology/" in raw:
                raise AblationError("category tier contains technology reference %s" % raw)
    if len(set(paths)) != len(paths):
        raise AblationError("%s: reference tiers overlap" % case.get("id"))
    bundled = {}
    selected = []
    for tier, arm in zip(("category", "technology", "full"), ARMS):
        if tier == "category":
            selected.extend(tiers["common"])
        selected.extend(tiers[tier])
        files = [{
            "path": raw,
            "sha256": hashlib.sha256(_path(root, raw).read_bytes()).hexdigest(),
        } for raw in selected]
        bundled[arm] = {
            "files": files,
            "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")
                                     ).hexdigest(),
        }
    return bundled


def prepare(manifest, root=ROOT):
    """Freeze the three reference bundles without claiming an ablation result."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("model"), str) or not manifest["model"].strip():
        raise AblationError("manifest must name the model used in every arm")
    if not SHA256.fullmatch(str(manifest.get("prompt_sha256", ""))):
        raise AblationError("manifest must freeze the common prompt_sha256")
    prompt = _path(root, manifest.get("prompt"))
    if hashlib.sha256(prompt.read_bytes()).hexdigest() != manifest["prompt_sha256"]:
        raise AblationError("common prompt does not match prompt_sha256")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AblationError("manifest needs at least one pinned case")
    result = []
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise AblationError("each case needs an id")
        case_id = case["id"]
        if case_id in seen:
            raise AblationError("duplicate case id %s" % case_id)
        seen.add(case_id)
        truth = _load(root, case.get("truth"))
        errors = schema_lite.validate_file(truth, root / "schemas" / "ground-truth.schema.json")
        if errors:
            raise AblationError("%s: invalid ground truth: %s" % (case_id, errors[0]))
        repository = truth["repository"]
        if not repository.get("commit"):
            raise AblationError("%s: ground truth has no pinned commit" % case_id)
        result.append({
            "id": case_id, "repository": repository["name"],
            "commit": repository["commit"],
            "ground_truth_method": truth.get("annotation", {}).get("method", "unreported"),
            "bundles": _references(root, case),
        })
    return {"status": "plan_only", "model": manifest["model"],
            "prompt": manifest["prompt"], "prompt_sha256": manifest["prompt_sha256"],
            "cases": result}


def _nonnegative_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _arm_result(root, run, truth, model, prompt_hash, bundle_hash):
    if not isinstance(run, dict):
        raise AblationError("each arm needs a run record")
    if run.get("prompt_sha256") != prompt_hash or run.get("bundle_sha256") != bundle_hash:
        raise AblationError("arm run prompt/bundle hash does not match the frozen plan")
    for metric in ("context_tokens", "reference_tokens", "elapsed_seconds", "cost_usd"):
        if not _nonnegative_number(run.get(metric)):
            raise AblationError("arm run requires nonnegative measured %s" % metric)
    if run["reference_tokens"] > run["context_tokens"]:
        raise AblationError("reference_tokens cannot exceed context_tokens")
    for source in ("tokenizer", "usage_source", "cost_source"):
        if not isinstance(run.get(source), str) or not run[source].strip():
            raise AblationError("arm run requires a named %s" % source)
    if not isinstance(run.get("audited_by"), str) or not run["audited_by"].strip():
        raise AblationError("unsupported claims require a named human audit")
    claims = run.get("unsupported_claims")
    if not isinstance(claims, list) or not all(
            isinstance(c, dict) and isinstance(c.get("finding_id"), str)
            and isinstance(c.get("reason"), str) and c["reason"].strip()
            for c in claims):
        raise AblationError("unsupported_claims must cite finding IDs and audit reasons")
    claim_ids = [claim["finding_id"] for claim in claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise AblationError("unsupported_claims contains a duplicate finding ID")

    review = _load(root, run.get("review"))
    errors = validate_review.validate(review, root / "schemas")
    if errors:
        raise AblationError("invalid review %s: %s" % (run.get("review"), errors[0]))
    reproducibility = review["reproducibility"]
    if reproducibility.get("model") != model:
        raise AblationError("review model does not match the paired manifest")
    source = reproducibility["repository"]
    pinned = truth["repository"]
    if source.get("name") != pinned["name"] or source.get("commit") != pinned["commit"]:
        raise AblationError("review repository and commit must match ground truth")
    finding_ids = {item["id"] for item in review["findings"]}
    if not set(claim_ids) <= finding_ids:
        raise AblationError("unsupported_claims references a missing finding")

    scored = scorer.score(truth, review)
    counts = scored["counts"]
    assignments = scored["matching"]["assignments"]
    expected = {item["item"]: item["finding"] for item in assignments["expected"]}
    findings_by_id = {item["id"]: item for item in review["findings"]}
    expected_details = {
        item_id: {
            "finding_id": finding_id,
            "severity": findings_by_id[finding_id]["severity"],
            "confidence": findings_by_id[finding_id]["confidence"],
            "priority": findings_by_id[finding_id]["priority"],
            "recommendation": findings_by_id[finding_id]["recommendation"],
        }
        for item_id, finding_id in expected.items()
    }
    needs_adjudication = bool(counts["unanticipated"] or scored["matching"]["ambiguities"])
    quality = None if needs_adjudication else (
        counts["true_positives"] - counts["false_positives"] - len(claims))
    return {
        "quality_score": quality,
        "needs_adjudication": needs_adjudication,
        "expected_matches": expected,
        "expected_details": expected_details,
        "counts": counts,
        "unsupported_claims": claims,
        "recommendation_accuracy": scored["recommendation_accuracy"],
        "context_tokens": run["context_tokens"],
        "reference_tokens": run["reference_tokens"],
        "elapsed_seconds": run["elapsed_seconds"],
        "cost_usd": run["cost_usd"],
    }


def compare(manifest, root=ROOT):
    """Compare complete three-arm trials; withhold scores until adjudication is complete."""
    plan = prepare(manifest, root)
    output = []
    for case, planned in zip(manifest["cases"], plan["cases"]):
        truth = _load(root, case["truth"])
        trials = case.get("trials")
        if not isinstance(trials, list) or not trials:
            raise AblationError("%s: no trials; run --plan-only first" % case["id"])
        records = []
        trial_ids = set()
        for trial in trials:
            if not isinstance(trial, dict) or not isinstance(trial.get("id"), str):
                raise AblationError("%s: trial needs an id" % case["id"])
            if trial["id"] in trial_ids:
                raise AblationError("%s: duplicate trial id %s" % (case["id"], trial["id"]))
            trial_ids.add(trial["id"])
            if not isinstance(trial.get("order"), list) or set(trial["order"]) != set(ARMS) or len(trial["order"]) != len(ARMS):
                raise AblationError("%s/%s: record the three-arm execution order"
                                    % (case["id"], trial["id"]))
            arms = trial.get("arms")
            if not isinstance(arms, dict) or set(arms) != set(ARMS):
                raise AblationError("%s/%s: all three arms are required"
                                    % (case["id"], trial["id"]))
            results = {arm: _arm_result(
                root, arms[arm], truth, manifest["model"], manifest["prompt_sha256"],
                planned["bundles"][arm]["sha256"])
                       for arm in ARMS}
            deltas = {}
            for before, after in PAIRS:
                left, right = results[before], results[after]
                old, new = set(left["expected_matches"]), set(right["expected_matches"])
                changed = sorted(item for item in old & new
                                 if left["expected_details"][item] != right["expected_details"][item])
                deltas[before + "_to_" + after] = {
                    "quality_score": (right["quality_score"] - left["quality_score"]
                                      if left["quality_score"] is not None
                                      and right["quality_score"] is not None else None),
                    "expected_gained": sorted(new - old),
                    "expected_lost": sorted(old - new),
                    "expected_changed": changed,
                    "context_tokens": right["context_tokens"] - left["context_tokens"],
                    "reference_tokens": right["reference_tokens"] - left["reference_tokens"],
                    "elapsed_seconds": right["elapsed_seconds"] - left["elapsed_seconds"],
                    "cost_usd": round(right["cost_usd"] - left["cost_usd"], 6),
                }
            records.append({"id": trial["id"], "order": trial["order"],
                            "arms": results, "deltas": deltas})
        output.append({"id": case["id"], "repository": planned["repository"],
                       "commit": planned["commit"],
                       "ground_truth_method": planned["ground_truth_method"],
                       "trials": records})

    transitions = {}
    for before, after in PAIRS:
        label = before + "_to_" + after
        values = [trial["deltas"][label]["quality_score"]
                  for case in output for trial in case["trials"]]
        transitions[label] = {
            "paired_trials": len(values),
            "mean_quality_delta": (round(statistics.mean(values), 3)
                                   if all(value is not None for value in values) else None),
        }
    return {"status": "scored" if all(
        value["mean_quality_delta"] is not None for value in transitions.values())
        else "needs_adjudication", "model": manifest["model"],
        "cases": output, "transitions": transitions,
        "caveat": "This measures reference-tier contribution, not individual file quality; "
                  "run targeted leave-one-reference-out trials before removing a file. "
                  "Treatment-derived ground truth is exploratory, not held-out evidence."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--checkout", type=Path, default=ROOT,
                        help="checkout containing the references and review artifacts")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = (prepare(manifest, args.checkout) if args.plan_only
                  else compare(manifest, args.checkout))
    except (OSError, UnicodeError, json.JSONDecodeError, AblationError) as exc:
        print("Reference ablation: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] != "needs_adjudication" else 1


if __name__ == "__main__":
    raise SystemExit(main())
