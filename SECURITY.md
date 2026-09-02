# Security policy

## What this project is

`backend-performance-review` is Markdown methodology plus one bundled script,
[`detect_stack.py`](skills/backend-performance-review/scripts/detect_stack.py). Neither
component modifies code, executes anything in the reviewed repository, makes network
calls, or touches a running system. The skill reads, reasons, and reports — see the
README's "What it does not do" section.

## Threat model for `detect_stack.py`

The script is the only executable surface here, so it is the only thing with a real
threat model:

- **Read-only.** It only ever opens files under the repository root it is pointed at, to
  read their contents. It writes nothing back to that repository.
- **Standard-library only.** No third-party packages, so no dependency supply chain to
  worry about. Two CI jobs enforce this on every push and PR — one runs it isolated with
  `python -I` and no packages installed at all.
- **No network access.** It never makes an outbound connection. There is nothing for it
  to exfiltrate to, even if it wanted to.
- **Secret files are never read.** Filenames matching common secret patterns (`.env`,
  `*.pem`, `*.key`, `credentials*`, `*.tfvars`, `id_rsa`/`id_ed25519`, etc. — the full list
  is `SECRET_PATTERNS` in the script) are recorded by path only. Their contents are never
  opened, never included in the script's output, and never surface in a review's evidence.
  This is enforced in code, not by convention — see `is_secret()` in the script.
- **Bounded reads.** Each file it does read is capped (`--max-bytes`, default 200 KB) so a
  malicious or malformed file cannot be used to exhaust memory.

The methodology (the Markdown files an agent loads) has no execution surface at all — it is
instructions for an LLM, not code, and carries the same trust level as any other prompt
content a user or maintainer contributes to a public repository.

## Supported versions

Only the latest released version is supported. See [CHANGELOG.md](CHANGELOG.md) for what's
current.

## Reporting a vulnerability

If you find a way for `detect_stack.py` to read outside the target repository, read a
secret file's contents, make a network call, or otherwise do something the threat model
above says it cannot do, please report it privately rather than opening a public issue:

- Use GitHub's [private vulnerability reporting](https://github.com/Sanoy24/backend-performance-review/security/advisories/new)
  for this repository.

Please include the smallest reproducing example you can — a minimal target directory
structure and the exact invocation is usually enough.

This is a low-severity surface by design (no network, no writes, no dependencies), but
reports are still welcome and will be acknowledged.

## What is out of scope

- The *content* of a performance review the skill produces being wrong or unhelpful — that
  is a correctness bug, not a security issue. Please [open a regular issue](.github/ISSUE_TEMPLATE)
  instead, ideally using the false-positive report template.
- Prompt-injection-style content embedded in a *target* repository being reviewed
  (a comment or file designed to manipulate the reviewing agent) is a known category for
  any agent that reads untrusted repositories, not specific to this skill. It is worth
  discussing, but track it as a regular issue rather than a private report unless you have
  a concrete exploit against this skill's own instructions.
