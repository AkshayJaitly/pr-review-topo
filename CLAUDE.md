# CLAUDE.md — pr-review-topo

Guidance for Claude (and humans) working in this repo. This is a **Claude
skill** that reviews a pull request in topological (dependency) order. The
skill itself lives in `pr-review-topo/`; everything else is test scaffolding.

## Repo map

```
pr-review-topo/                  # THE SKILL (this is what gets installed)
├── SKILL.md                     # workflow + trigger description (the contract)
├── scripts/plan_review.py       # dependency graph + topological sort
├── references/review_checklist.md
└── evals/evals.json             # test prompts + assertions
workspace/
├── fixtures/build_fixtures.sh   # regenerates 3 example PR repos (git repos w/ main+feature)
└── iteration-1/                 # graded eval outputs: with_skill vs without_skill
```

The generated `workspace/fixtures/fixture-*/` repos are `.gitignore`d — they
are reproducible by running `build_fixtures.sh`.

## Low-level design: `plan_review.py`

The planner turns a set of changed files into ordered review batches. The
whole thing is intentionally a **heuristic over text**, not a compiler — it
needs decent recall, not soundness, because a wrong edge only reshuffles
review order; it never changes correctness.

### Pipeline

1. **Input resolution** (`main`) — one of `--files`, `--files-from`, or
   `--diff-base <ref>` (which shells out to `git diff --name-only
   --diff-filter=ACMR <base>...HEAD`, falling back to a working-tree diff).
   Non-existent paths are dropped so deleted files don't poison the graph.

2. **Reference extraction** (`extract_refs`) — runs `IMPORT_PATTERNS` (regex
   for Go/Python/JS/TS/C/C++/Ruby imports + a catch-all for quoted path-like
   strings, which is how Go's parenthesized `import (...)` blocks get caught)
   over each file's text. Returns the raw import strings.

3. **Token model** (`file_tokens`, `normalize_ref`) — each changed file
   contributes the path suffixes another file might use to reference it:
   `pkg/auth/token.go` → `{token, auth/token, pkg/auth/token, auth, pkg/auth}`.
   Refs are normalized (strip extensions, `./`, turn dotted `a.b.c` into
   `a/b/c`) so they compare cleanly against tokens.

4. **Graph build** (`build_graph`) — `token_index` maps each token to the set
   of files contributing it (a token can be shared, e.g. all files in one
   package). For each ref in a file, find the **longest** token that is a
   suffix of the ref (longest = most specific, beats bare-stem coincidences),
   then add an edge from every file contributing that token to the current
   file. **Edge direction: `foundation → dependent`** (A→B means "B imports A,
   review A first").

5. **Cycle handling** (`tarjan_scc`) — Tarjan's algorithm finds strongly
   connected components. An SCC of size > 1 is an import cycle; its members
   must be reviewed together because none is "foundational" relative to the
   others.

6. **Ordering** (`topo_groups`) — build the condensation DAG (one node per
   SCC), then Kahn's algorithm over it yields SCCs in foundation→dependent
   order. Output `groups` (ordered batches, each with `depended_on_by`),
   `edges`, and `cycles`.

### Invariants / gotchas

- **Edges only exist between changed files.** If coupling runs through a file
  the PR didn't touch, the graph can't see it — the topo benefit shrinks
  accordingly. This is the single biggest limitation; don't oversell ordering
  when a PR's real dependencies are mostly in untouched code.
- **Binary/asset files** (`BINARY_EXTS`) are kept as leaves but never parsed.
- Tarjan is recursive; `sys.setrecursionlimit` is bumped. Fine for
  PR-sized inputs (tens to low hundreds of files), not for whole-repo graphs.
- Output is deterministic: files and groups are sorted before emit.

## Guardrails

These are non-negotiable when running or modifying the skill.

1. **Never echo secrets into a review.** Finding a leaked credential is a
   valid result, but the report / PR comment / conversation are all places
   the secret would get *re-published*. Redact the value (cite file:line,
   mask it), describe it in prose, flag as a blocker, and tell the author to
   **rotate** (not just delete — git history keeps it). See the "Never echo
   secrets" section in `SKILL.md`.

2. **Don't post to GitHub without explicit consent.** Inline PR comments are
   outward-facing and hard to undo. Default to a report printed to the
   conversation. Only post (`gh pr review` / `gh api .../comments`) when the
   user explicitly asks (`--post-comments` or in their prompt). When it's
   ambiguous, confirm first.

3. **Use `COMMENT`, not `REQUEST_CHANGES`/`APPROVE`, on your own PR.** GitHub
   rejects formal verdicts on a self-authored PR (422). On a teammate's PR,
   `REQUEST_CHANGES` is appropriate when there are blockers.

4. **The plan is advisory.** If the topo order is obviously wrong for a given
   PR (heuristic false edges, unsupported language, codegen), say so and
   review in the order that makes sense. Never withhold a finding because the
   ordering "didn't get there."

5. **Scope large/noisy diffs.** Exclude generated files, lockfiles, and
   vendored deps from the planner (`--files` without them) so the graph isn't
   drowned in noise. Reviewing every file in a 60-file PR also burns context —
   prefer per-package passes.

6. **Don't mutate the user's working tree silently.** `git checkout`,
   `gh pr checkout` change branch state. Restore the prior branch when done,
   and don't run these on a repo with uncommitted changes without flagging it.

## Dev workflow

```bash
# regenerate example fixtures (creates git repos with main + feature branches)
bash workspace/fixtures/build_fixtures.sh

# see the topo plan for a fixture
cd workspace/fixtures/fixture-go-auth && git checkout feature
python3 ../../../pr-review-topo/scripts/plan_review.py --diff-base main
git checkout main
```

Evals live in `pr-review-topo/evals/evals.json`; graded outputs from the last
run are in `workspace/iteration-1/` (each eval has `with_skill` and
`without_skill` outputs + `grading.json`). The skill was built with the
skill-creator workflow; it is not yet description-optimized or packaged.

## Conventions

- Keep `plan_review.py` dependency-free (stdlib only) — it must run anywhere
  Python 3 does, with no install step.
- Comments explain *why* (the heuristic tradeoffs), not *what*. Match the
  existing density.
- `SKILL.md` is the contract: its `description` frontmatter drives triggering,
  and its body is loaded whenever the skill fires. Keep it under ~500 lines
  and push detail into `references/`.
