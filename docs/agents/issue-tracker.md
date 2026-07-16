# Issue tracker: GitHub

Issues, Wayfinder maps, PRDs, and implementation tickets for this repository live
as GitHub issues. Use the `gh` CLI for all operations.

Files under `.scratch/` may hold temporary research or prototype assets, but they
are not the canonical issue tracker and must be linked from the owning GitHub
issue when they inform a decision.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`.
- **Read an issue**: `gh issue view <number> --comments`, including labels and
  relationship metadata when relevant.
- **List issues**: use `gh issue list --state open --json ...` with the labels and
  state needed for the current flow.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`.
- **Close an issue**: `gh issue close <number> --comment "..."`.

Infer the repository from `git remote -v`; `gh` resolves the current checkout.

## Pull requests as a triage surface

**PRs as a request surface: no.** External pull requests are not automatically
treated as raw feature requests by `/triage`.

GitHub shares one number space across issues and pull requests. When a bare number
is ambiguous, try `gh pr view <number>` and fall back to
`gh issue view <number>`.

## Skill operations

- When a skill says **publish to the issue tracker**, create a GitHub issue.
- When a skill says **fetch the relevant ticket**, read the full issue body,
  comments, labels, assignees, and relationships.
- Tickets created by `/to-tickets` are already agent-ready; do not send them
  through `/triage`.

## Wayfinding operations

- **Map**: one issue labelled `wayfinder:map`. It owns Destination, Notes,
  Decisions so far, Not yet specified, and Out of scope.
- **Child ticket**: create one issue labelled `wayfinder:research`,
  `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`, then attach it
  to the map with GitHub sub-issues. If sub-issues are unavailable, put `Part of
  #<map>` at the top of the child and maintain a task list on the map.
- **Blocking**: prefer GitHub native issue dependencies. Add a blocker with the
  `dependencies/blocked_by` API using the blocker's numeric database ID. If the
  endpoint is unavailable, keep a `Blocked by: #<number>` line in the ticket body.
- **Frontier**: open, unassigned child tickets with no open blocker, in map order.
- **Claim**: assign the ticket to the driving developer before working it.
- **Resolve**: add the resolution as a comment, close the ticket, then append only
  a one-line linked gist to the map's Decisions so far.

## Implementation review convention

`/code-review` compares committed `HEAD` against a fixed point, while `/implement`
asks for review before the final commit. Resolve that contract seam by creating a
reviewable WIP commit on the ticket branch, running `/code-review` against the
ticket's base commit, applying findings, and then amending or finalizing the
ticket commit. Never review an empty committed diff.
