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
/plugin marketplace add OWNER/backend-performance-review
/plugin install backend-performance-review
```

Verify:

```
/plugin
```

The skill is then available in every project, and `/backend-performance-review` appears in
the slash-command menu.

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

## 4. Any other coding agent

The methodology is vendor-neutral Markdown with no Claude-specific content below the
frontmatter. Two options:

**Point the agent at the entry file.** Most agents accept a file reference:

> Follow the methodology in `path/to/skills/backend-performance-review/SKILL.md` and review
> this service for performance problems.

The agent then follows the relative reference paths from there. The reference tree resolves
relative to the `SKILL.md` directory.

**Vendor it into your own agent's rules directory.** Copy the skill directory wherever your
agent looks for instructions. If your agent has no notion of on-demand file loading, expect
higher context cost — the tree is designed to be loaded selectively, and loading all of it
at once defeats the design.

Either way, the YAML frontmatter is inert outside Claude Code and can be left in place.

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

Or, for a copied installation, delete the directory from `.claude/skills/` or
`~/.claude/skills/`. The skill stores no state outside its own directory.

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
