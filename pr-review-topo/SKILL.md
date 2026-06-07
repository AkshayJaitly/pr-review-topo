---
name: pr-review-topo
description: Review a pull request in topological order — foundational/dependency files first, dependents last — so each file is reviewed with full context of what it builds on. Use this whenever the user asks for a PR review, code review, diff review, "review my changes", or asks to look over uncommitted/staged work. Works on local git diffs against a base branch AND on GitHub PRs via `gh`. Prefer this over ad-hoc file-by-file review whenever the changeset touches more than ~2 files, since the topo ordering catches "this dependent broke because of how its foundation changed" issues that alphabetical or random ordering misses.
---

# PR Review in Topological Order

## Why topological order

A code review's job is to decide whether each change is correct. A change in
file B is hard to judge without first knowing how its dependency A changed —
if you review B first, you have to guess, then re-judge when you finally see
A. That's wasted work and a real source of missed bugs.

This skill builds a dependency graph from the changed files, topologically
sorts it, and walks the review from foundations (leaves of the dependency
tree — files nothing in the diff depends on going *into* them) to dependents.
By the time you reach a file, you've already understood everything it builds
on within this changeset.

## Workflow

### 1. Identify the source

Figure out which PR / diff to review based on the user's prompt:

- **Local branch** (no PR number, user says "review my changes" / "review
  this branch"): use `git diff --name-only --diff-filter=ACMR <base>...HEAD`.
  Ask for the base if it's not obvious (`main`, `master`, `origin/main`).
- **GitHub PR** (user gives a PR number, URL, or says "review PR #123"):
  - Ensure you're in the right repo (or `gh -R owner/repo`).
  - Check out the PR locally so the post-change files exist on disk:
    `gh pr checkout <num>`. If the user doesn't want a checkout, you can
    instead `gh pr diff <num> --name-only` for the file list and
    `gh pr view <num> --json baseRefName -q .baseRefName` for the base, then
    fetch each file via `git show <head>:<path>` into a temp dir. Checkout
    is simpler — prefer it unless the user objects.

If neither path is clear, ask the user one short question to disambiguate.

### 2. Build the review plan

Run the bundled planner:

```bash
python3 <skill-path>/scripts/plan_review.py --diff-base <base> --root <repo>
```

Or pass an explicit list with `--files a.go b.go ...`.

It outputs JSON with `groups` (ordered review batches), `edges` (the inferred
dependency edges), and `cycles` (any strongly-connected components — files
that import each other and must be reviewed together).

The graph is built with **language-agnostic heuristics**: regex over
`import` / `from` / `require` / `#include` lines, matched against the changed
files' path tokens. It will have false positives (an import string that
coincidentally shares a name with a changed file) and false negatives (a
language whose import syntax we don't catch, dynamic imports, codegen). That
is fine — the ordering exists to help you, not constrain you. If the plan
looks obviously wrong for a particular PR, ignore it and review in the order
that makes sense.

### 3. Review each group in order

For each group in `groups` (in order):

1. State which file(s) you're reviewing and which downstream files depend on
   them (from `depended_on_by`). This frames why this file matters.
2. Read the **post-change** file (Read tool), then read the **diff hunks** for
   it (`git diff <base>...HEAD -- <path>` or `gh pr diff <num> -- <path>`).
   Reading both lets you judge the change in the context of the surrounding
   code, not just the patch.
3. Apply the review checklist in `references/review_checklist.md`. Carry
   forward what you learned from earlier groups — if foundation A changed its
   error semantics, the dependent's review should explicitly check whether
   the dependent handles the new semantics.
4. Record findings as you go. Use this structure for each finding:
   - **File:line** — short title
   - Severity: `blocker` / `concern` / `nit`
   - What's wrong + why it matters (one or two sentences)
   - Suggested fix (concrete; a code snippet if it's clearer than prose)

### 4. Handle cycles

If `cycles` is non-empty, the files in each cycle form a strongly-connected
component (they reference each other). Review the entire SCC as one unit —
read all of its files and diffs before forming opinions, since no single
member is "foundational" relative to the others. Briefly call out the cycle
in your report so the user knows the ordering inside that group was
arbitrary.

### 5. Produce the report

Default output is a markdown report. Structure:

```markdown
# PR Review: <title or branch>

## Summary
- Overall take in 2-3 sentences. Recommend approve / request-changes / comment.
- Topological order used: <group 1 files> → <group 2 files> → ...
- N findings: X blockers, Y concerns, Z nits.

## Findings by file (topological order)

### 1. pkg/util/log.go  *(depended on by: pkg/auth/token.go, cmd/main.go)*
- **pkg/util/log.go:42** — blocker: ...
- **pkg/util/log.go:58** — nit: ...

### 2. pkg/auth/token.go  *(depended on by: cmd/main.go)*
...

### 3. cmd/main.go
...

## Cross-cutting notes
Anything that doesn't belong to a single file (architectural concerns,
missing tests, doc gaps).
```

### Never echo secrets into the report

Finding a leaked secret — an API key, token, password, private key,
connection string — is a high-value result. But the report, the PR comment,
and the conversation are all places the secret would get *re-published* if you
quote the offending line verbatim. That defeats the point: you'd be copying a
live credential into one more place (and PR comments and logs are hard to
scrub).

So when a finding involves a secret:

- **Redact the value.** Cite the file and line and describe what kind of
  secret it is, but mask the value: `AKIA****************` or
  `<redacted 40-char hex token>`. Show just enough for the author to locate
  it, never the full string.
- **Don't paste the raw diff hunk** that contains it into the report. Describe
  it in prose instead.
- **Treat it as a blocker** and tell the author to rotate the credential, not
  just delete the line — once a secret is in git history it's compromised.

This applies to anything that looks live: long random-looking strings near
words like `key`, `token`, `secret`, `password`, `-----BEGIN`. When in doubt,
redact — a false positive costs nothing, a leaked key costs a lot.

Print the report to the conversation. If the user passed `--post-comments`
(or said so in their prompt), also post each finding as an inline review
comment on the GitHub PR:

```bash
gh pr review <num> --comment -b "<summary>"
# inline comments via gh api repos/:owner/:repo/pulls/<num>/comments
# with path, line, body — see `gh api --help`
```

Only post comments when the user explicitly asked. Posting is visible to
others and hard to undo, so default to "report only" and confirm before
posting if it's ambiguous.

## What to look for in each file

See `references/review_checklist.md` for the full checklist. The short
version: correctness bugs first, then security / data integrity, then
simplification / reuse, then style. Don't pad a review with style nits when
there's a real bug somewhere — call the bug clearly and let the rest go.

## When this skill is not a good fit

- Single-file PRs: just review the file, no need to invoke the planner.
- PRs that are pure renames or pure config/data changes with no code logic:
  topological order is meaningless. Read the diff and judge it directly.
- Generated-file-heavy PRs (lockfiles, snapshot tests, vendored deps):
  skip the generated files in the planner (`--files` excluding them) so
  the graph isn't drowned in noise.
