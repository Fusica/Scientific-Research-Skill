# Codex plugin and workflow hook

The repository is both a Skill collection and a Codex plugin. Plugin installation is the default because skill-only installation cannot activate the shared workflow guard.

Node.js must be available to the Codex hook runner. A missing runtime is reported as a Hook failure rather than silently disabling the mandatory guard.

## Hook lifecycle

`hooks/hooks.json` registers two command hooks that call the same read-only script:

- `SessionStart` on startup, resume, clear, and compact loads the authority boundary plus parsed project ID, stage, Gate, overview-version, and explicitly active planning metadata.
- `UserPromptSubmit` adds a shorter reminder before each user request so non-trivial research work cannot silently bypass the planning/scientific-state boundary.

The hook constrains actions and state handling. It does not request or expose private chain-of-thought.

The activation marker is `.research/project-state.yaml`. The hook walks upward from the event working directory and emits `{}` when no marker exists, so installing the plugin globally does not inject research policy into ordinary code repositories. Initializing project state opts that repository into the shared guard.

## Authority model

```text
.planning/<task-id>  --verified promotion-->  .research/**
       execution state                         scientific record
                                                    |
                                                    v
                                      project-state.yaml (Gate authority)

project-overview.md = derived navigation only
```

The hook never creates, edits, or approves project files. The agent performs any authorized initialization in the normal task flow, and tests assert that hook execution is read-only. It does not inject raw overview, state, or task-plan prose. It parses a fixed allowlist of navigation metadata, reads active task IDs only from `project-overview.md`, and verifies each referenced task status before reporting it. The agent reads the actual files later as ordinary repository data.

## Install

From a clone of this repository:

```bash
codex --version
codex plugin marketplace add "$PWD"
codex plugin add scientific-research-skill@scientific-research-skill
```

Codex treats command hooks as security-sensitive. Explicitly trust the `SessionStart` and `UserPromptSubmit` entries in the Codex hook manager, then start a new thread. Do not bypass trust by hand-writing guessed config entries.

The legacy `scripts/install_codex.py` path installs only the eight Skill directories. It is intentionally available for compatibility, but it does not activate the hook and therefore is not the default installation mode.

## Verify during development

Run repository and hook tests:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
node --test tests/hooks.test.js
```

Directly inspect `SessionStart` output for an initialized research project without changing project files:

```bash
PROJECT_ROOT=/path/to/initialized/research-project
printf '{"cwd":"%s"}' "$PROJECT_ROOT" |
  PLUGIN_DATA="$(mktemp -d)" \
  CLAUDE_PLUGIN_ROOT="$PWD" \
  node hooks/research-workflow-hook.js SessionStart
```

For a non-research repository, the same command should return `{}`. If `codex --version` fails, fix the CLI `PATH` or use the Codex application plugin manager before following the CLI commands; do not infer installation success from copied files.

For an installed plugin, verify all three levels: the plugin is installed/enabled, both hook entries are enabled and trusted in Codex, and a new-thread run receives `SCIENTIFIC-RESEARCH-WORKFLOW:DEFAULT` plus the project context. File presence alone is not an end-to-end verification.
