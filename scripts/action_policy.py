#!/usr/bin/env python3
"""Validate GitHub Action inputs and apply its verdict policy.

The composite Action intentionally delegates policy to this stdlib-only module. Shell is a
poor place to distinguish a valid false value from a typo that merely happens not to equal
"true", and a typo in `fail-on` must never turn a requested merge gate into an advisory run.

Action inputs are read from BPR_* environment variables so untrusted input values are data,
not interpolated shell source.
"""

import argparse
import os
import sys

FAIL_ON_VALUES = ("never", "fail", "warn")
VERDICT_VALUES = ("PASS", "WARN", "FAIL", "UNKNOWN")
BOOLEAN_INPUTS = {
    "upload-sarif": "BPR_UPLOAD_SARIF",
    "comment": "BPR_COMMENT",
    "include-adjacent": "BPR_INCLUDE_ADJACENT",
    "strict-verdict": "BPR_STRICT_VERDICT",
}


class ConfigurationError(ValueError):
    """An Action input is outside the documented contract."""


def validate_fail_on(value):
    if value not in FAIL_ON_VALUES:
        raise ConfigurationError(
            "input 'fail-on' must be one of %s; got %r"
            % (", ".join(FAIL_ON_VALUES), value))
    return value


def parse_boolean(name, value):
    if value not in ("true", "false"):
        raise ConfigurationError(
            "input %r must be exactly 'true' or 'false'; got %r" % (name, value))
    return value == "true"


def validate_config(environ):
    """Validate every Action setting whose typo could silently change behavior."""
    validate_fail_on(environ.get("BPR_FAIL_ON", ""))
    for name, variable in BOOLEAN_INPUTS.items():
        parse_boolean(name, environ.get(variable, ""))


def should_fail(verdict, fail_on):
    """Return whether the configured gate rejects this derived verdict."""
    validate_fail_on(fail_on)
    if verdict not in (None, "") + VERDICT_VALUES:
        raise ConfigurationError("unexpected derived verdict %r" % verdict)
    if fail_on == "never" or verdict in (None, "", "PASS", "UNKNOWN"):
        return False
    if fail_on == "fail":
        return verdict == "FAIL"
    return verdict in ("WARN", "FAIL")


def footer_for(mode, fail_on):
    """Describe the gate accurately in the pull-request comment."""
    validate_fail_on(fail_on)
    if mode != "change-scoped":
        if fail_on == "never":
            return "Advisory. This check does not block merges."
        return ("Full review: no verdict is produced, so the configured %s gate does not "
                "apply." % fail_on)
    if fail_on == "never":
        return "Advisory. This check does not block merges."
    if fail_on == "fail":
        return "Merge gate: FAIL blocks; PASS, WARN, and UNKNOWN do not."
    return "Merge gate: WARN and FAIL block; PASS and UNKNOWN do not."


def _report_error(exc):
    print("::error::Invalid Backend Performance Review Action configuration: %s" % exc,
          file=sys.stderr)
    return 2


def main(argv=None, environ=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("validate-config", "apply-verdict"))
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ

    try:
        validate_config(environ)
        if args.command == "validate-config":
            print("Backend Performance Review Action configuration is valid.")
            return 0

        verdict = environ.get("BPR_VERDICT", "")
        fail_on = environ["BPR_FAIL_ON"]
        print("Performance verdict: %s" % (verdict or "none (full review)"))
        if should_fail(verdict, fail_on):
            print("::error::Performance verdict %s is rejected by fail-on=%s"
                  % (verdict, fail_on), file=sys.stderr)
            return 1
        return 0
    except (ConfigurationError, KeyError) as exc:
        return _report_error(exc)


if __name__ == "__main__":
    sys.exit(main())
