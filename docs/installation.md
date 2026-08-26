# Installation

How the skill is packaged, discovered, and invoked — in Claude Code and elsewhere.

---

## 1. Conventions verification record

The packaging in this repository was checked against the following sources. Recorded so a
future maintainer can tell what was verified and when, rather than guessing whether the
layout has drifted.

| Verified | Date | Source |
|:--|:--|:--|
| `SKILL.md` frontmatter fields; portable subset for non-Claude-Code distribution | 2026-08-25 | Claude Code skills documentation, `https://code.claude.com/docs/en/skills` |
| `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` schemas; skill discovery from a `skills/` directory | 2026-08-25 | Claude Code plugin marketplace documentation, `https://code.claude.com/docs/en/plugin-marketplaces` |
| Community skill-repository layout (`skills/<name>/SKILL.md`, `.claude-plugin/`, `docs/`, `scripts/`) | 2026-08-25 | `https://github.com/mattpocock/skills` |
| Manifest schemas re-verified against the installed CLI's own validator (`claude plugin validate`, v2.1.39) rather than documentation alone; full install/uninstall cycle run against the live repository | 2026-08-25 | Local `claude` CLI |
| OpenCode skill discovery paths, including its Claude-compatible fallback (`.claude/skills/<name>/SKILL.md`) | 2026-08-26 | `https://opencode.ai/docs/rules/` |
| Google Antigravity skill directory convention (`.agents/skills/<name>/SKILL.md`, project scope) | 2026-08-26 | `https://antigravity.google/docs/skills` |
| OpenAI Codex CLI skill directory convention (`.agents/skills/<name>/SKILL.md`, same path as Antigravity) | 2026-08-26 | `https://codex.danielvaughan.com/2026/03/26/writing-effective-skillmd-files/` |

The three rows above are **checked against each tool's published documentation only** — unlike
the Claude Code rows, no local install of OpenCode, Antigravity, or Codex CLI was available to
run an end-to-end cycle against this repository at the time of writing. Treat §4 below as
believed-correct, not verified-by-execution, until someone runs it and reports back (that report
would itself be a valuable contribution — see `CONTRIBUTING.md` §1).

**Re-verify before a release.** If the frontmatter schema or marketplace format has changed,
update this table with the new date and note the change in `CHANGELOG.md`.

### Frontmatter fields used, and why

```yaml
name:            backend-performance-review
description:     <trigger conditions — see below>
when_to_use:     <additional trigger phrases>
license:         MIT
compatibility:   <environment requirements>
allowed-tools:   Read, Grep, Glob, Bash(python ${CLAUDE_SKILL_DIR}/scripts/detect_stack.py *)
metadata:        {version, spec}
```

Every one of these is in the portable [Agent Skills](https://agentskills.io) subset except
`when_to_use`, which Claude Code supports and other consumers ignore harmlessly.

Two notes on specific fields:

- **`description` is the most important line in the repository.** It is what a model reads to
  decide whether to load the skill at all. It is written as *trigger conditions* — the
  situations in which the skill should fire — rather than as a summary of what the skill is.
  A description reading "A skill for backend performance reviews" would be correct and
  useless. `description` and `when_to_use` are truncated together at 1,536 characters in the
  skill listing, so the key use cases come first.
- **`allowed-tools`** pre-approves the read-only tools the review needs plus the bundled
  detection script, so a review does not generate a permission prompt per file. The grant
  lasts for the invoking turn only. `${CLAUDE_SKILL_DIR}` expands to the installed skill
  directory in both the frontmatter rule and the skill body, so the rule matches the exact
  command the body tells the agent to run.

The skill deliberately does **not** set `disable-model-invocation`, because automatic
invocation on a relevant question is the primary way it is meant to be used.

---

## 2. Claude Code — plugin install

The repository is a single-plugin marketplace, so it can be added directly:

```
/plugin marketplace add Sanoy24/backend-performance-review
/plugin install backend-performance-review
```

Verify:

```
/plugin
```

The skill is then available in every project, and `/backend-performance-review` appears in
the slash-command menu.

**Verified end-to-end**, not just checked against documentation: `claude plugin validate .`
was run against both manifests (catching two real gaps — `marketplace.json` needs
`metadata.description`, `plugin.json` needs `author` — both now fixed), then
`claude plugin marketplace add Sanoy24/backend-performance-review` and
`claude plugin install backend-performance-review` were run for real against the live
repository, and `claude plugin list` confirmed the plugin installed and enabled at the correct
version. This is the exact flow above, actually exercised, not inferred from the schema.

## 3. Claude Code — copy the skill directory

If you would rather not use the plugin system, copy the skill directory. Nothing else in the
repository is required at runtime.

**Project scope** — checked into your repo, shared with your team:

```bash
mkdir -p .claude/skills
cp -r backend-performance-review/skills/backend-performance-review .claude/skills/
```

**Personal scope** — available in all your projects:

```bash
mkdir -p ~/.claude/skills
cp -r backend-performance-review/skills/backend-performance-review ~/.claude/skills/
```

The directory name becomes the command name, so keep it as `backend-performance-review`.

## 4. Other agents

The methodology is vendor-neutral Markdown with no Claude-specific content below the
frontmatter, and several other tools have converged on the same `SKILL.md` directory
convention Claude Code uses. Where that is true, no repackaging is required — copy or
symlink the same directory.

### OpenCode

OpenCode discovers skills at `.opencode/skills/<name>/SKILL.md` (project) and
`~/.config/opencode/skills/<name>/SKILL.md` (global), but it also reads the **Claude Code
paths directly** as a compatibility fallback: `.claude/skills/<name>/SKILL.md` and
`~/.claude/skills/<name>/SKILL.md`. This means the exact copy step in §3 above — installing
into `.claude/skills/` — already makes the skill available in OpenCode with no change.

### Google Antigravity

Antigravity looks for project-scoped skills at `.agents/skills/<name>/SKILL.md`, and for a
global scope shared across projects. Copy the directory there:

```bash
mkdir -p .agents/skills
cp -r backend-performance-review/skills/backend-performance-review .agents/skills/
```

### OpenAI Codex CLI

Codex CLI uses the same `.agents/skills/<name>/SKILL.md` convention as Antigravity —
repository-scoped under `.agents/skills/`, personal-scoped under `$HOME/.agents/skills/`. The
command above installs it for Codex CLI at the same time as Antigravity; no separate step is
needed.

### Supporting more than one tool at once

`.claude/skills/` and `.agents/skills/` are the two conventions seen so far. Rather than
maintaining two copies that can drift apart, symlink the second at the first:

```bash
mkdir -p .agents/skills
ln -s ../../.claude/skills/backend-performance-review .agents/skills/backend-performance-review
```

### Any other agent

**Point the agent at the entry file.** Most agents accept a file reference:

> Follow the methodology in `path/to/skills/backend-performance-review/SKILL.md` and review
> this service for performance problems.

The agent then follows the relative reference paths from there. The reference tree resolves
relative to the `SKILL.md` directory.

**Vendor it into your own agent's rules directory.** Copy the skill directory wherever your
agent looks for instructions. If your agent has no notion of on-demand file loading, expect
higher context cost — the tree is designed to be loaded selectively, and loading all of it
at once defeats the design.

Either way, the YAML frontmatter is inert outside Claude Code and can be left in place — every
tool checked so far ignores fields it does not recognize rather than rejecting the file.

---

## 5. The detection script

`skills/backend-performance-review/scripts/detect_stack.py` is an accelerator, not a
dependency. If Python is unavailable or the script errors, the skill falls back to manual
inspection as described in `methodology/discovery.md`.

```bash
python skills/backend-performance-review/scripts/detect_stack.py /path/to/repo --pretty
```

Properties worth knowing before you run it on someone else's code:

- Python 3.8+, standard library only. No third-party packages, no network access.
- **Read-only.** It creates, modifies, and deletes nothing.
- **It never reads files matching secret patterns** — `.env`, `*.pem`, `*.key`,
  `credentials*`, `*.tfvars`, `secrets.*`, SSH private keys, `.netrc`. Their paths are
  reported so the reviewer knows they exist; their contents are not read and never appear in
  output.
- It skips vendor and build directories, caps per-file and total bytes read, and caps file
  count. On a very large repository the scan may be partial, and it says so in `warnings`.

Output is JSON on stdout; diagnostics go to stderr.

---

## 6. How the skill is discovered and invoked

Three paths, in decreasing order of how often they happen:

1. **Automatic.** The model reads the `description` and `when_to_use` fields from the skill
   listing and loads the skill when a request matches — "why is this endpoint slow", "review
   this service for performance", "will this scale".
2. **Explicit.** You type `/backend-performance-review`.
3. **Preloaded into a subagent**, if you configure one for review work.

Once loaded, the skill body stays in context for the rest of the session, which is why
`SKILL.md` is kept tight and the detailed knowledge lives in reference files that are read
only when the detected stack calls for them.

### What the skill will do first

It will ask you up to seven workload questions, once, in a single message. Answering
materially improves the ranking of findings. Declining is fine — the review proceeds, caps
its own confidence on workload-dependent findings, and states in the report which conclusions
would change if you had answered.

---

## 7. Uninstalling

```
/plugin uninstall backend-performance-review
```

Or, for a copied installation, delete the directory from `.claude/skills/`, `~/.claude/skills/`,
`.agents/skills/`, or wherever else it was copied or symlinked per §4. The skill stores no state
outside its own directory.

---

## 8. Troubleshooting

**The skill never activates automatically.** Check that it appears in `/plugin` or in your
skills directory listing. If it is installed but not firing, phrase the request in terms the
`description` covers — latency, throughput, bottleneck, performance review, scalability.

**Permission prompts on every file read.** The `allowed-tools` grant covers the invoking turn
only. For a long review, approve the read tools for the session.

**`detect_stack.py` reports no matches.** Expected on unusual layouts. The skill falls back to
manual inspection; the `warnings` field says the scan found nothing and points at
`methodology/discovery.md`.

**The report is shorter than expected.** That may be correct. Returning few or zero findings
is an explicitly valid outcome — the report should then carry the unknowns and the
measurements that would resolve them. If it does neither, that is a bug worth reporting.
