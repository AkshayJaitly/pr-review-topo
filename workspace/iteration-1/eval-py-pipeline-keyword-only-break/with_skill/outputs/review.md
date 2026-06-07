# PR Review: pipeline — keyword-only IO options + CLI args

## Summary

**Recommendation: request changes.** The refactor to make `read_data`'s `sep`
keyword-only is reasonable, but `transform.py` was not updated to match and
will now `TypeError` on every call. Topological order: `io.py` →
`transform.py` → `main.py`.

1 blocker, 1 nit.

## Findings by file (topological order)

### 1. pipeline/io.py  *(depended on by: pipeline/transform.py)*

**Note carried forward:** `read_data` signature changed from
`read_data(path, sep=',')` to `read_data(path, *, sep=',', encoding='utf-8')`.
The `*` makes `sep` and `encoding` **keyword-only** — any positional
second-arg call to `read_data` will now raise `TypeError` at call time. We
must verify every caller passes `sep` by name when checking downstream files.

The change itself is a good API improvement (explicit `encoding` beats
relying on the platform default, and forcing kwargs prevents
`read_data(path, 'utf-8')`-style mix-ups). The docstring captures the intent.

No issues to file against this file in isolation. The dependent callers are
where this needs verification.

### 2. pipeline/transform.py  *(depended on by: pipeline/main.py)*

- **pipeline/transform.py:4 — blocker.** `raw = read_data(path, ',')` passes
  `sep` positionally, but io.py just made it keyword-only. This raises
  `TypeError: read_data() takes 1 positional argument but 2 were given` the
  first time `load_and_clean` is called. The cosmetic refactor (docstring +
  variable rename `rows` → `raw`) touched the function body but missed the
  semantic break in its dependency.

  **Fix:** `raw = read_data(path, sep=',')` (or just drop the arg, since
  `','` is the default).

### 3. pipeline/main.py

- **pipeline/main.py:7 — nit.** The CLI now exposes an `--encoding` flag, but
  it isn't passed through to `load_and_clean` → `read_data`. So setting
  `--encoding latin-1` has no effect. Either thread the value down or drop
  the flag until it's wired up.

## Cross-cutting notes

- No tests touched. The blocker in transform.py would have been caught by a
  single smoke test that calls `load_and_clean` on any tiny CSV.
- This PR illustrates exactly why topological-order review matters: by the
  time we reach `transform.py`, the io.py review has already flagged the
  keyword-only constraint, so the broken positional call jumps out
  immediately. Without that context, the `read_data(path, ',')` line looks
  unchanged and gets skipped.
