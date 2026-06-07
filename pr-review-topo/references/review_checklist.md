# Review checklist

A focused checklist applied per file as you walk the topological order. Don't
mechanically tick every box — use it as a prompt to make sure you didn't skip
a category.

## Correctness (highest priority)

- **Logic**: Does the change do what it claims? Is the diff complete, or are
  there obvious gaps (callers not updated, missing cases)?
- **Error handling**: Are errors checked and propagated correctly? In Go,
  every returned error should be handled or explicitly ignored with a reason.
  Is the new code swallowing errors that used to surface?
- **Concurrency**: Any new shared state? Goroutines / threads / async tasks
  without obvious synchronization? Map writes from multiple goroutines?
- **Boundary conditions**: Off-by-one, empty input, nil/None, very large
  input, negative numbers, unicode in strings, timezones in dates.
- **Backwards compatibility**: If this file is a foundation that other
  changed files depend on, does the dependent code still work given the new
  signature / semantics? (You'll verify this when you reach the dependent in
  topo order — note any concerns here so you remember to check.)

## Security

- Unvalidated user input flowing into SQL, shell, file paths, HTML, redirects.
- Secrets in logs, error messages, or committed files. If you find a committed
  credential, **redact its value in your report** (cite file:line, mask the
  value) rather than copying the live secret into the review — see the
  "Never echo secrets" section in SKILL.md. Flag it as a blocker and tell the
  author to rotate it.
- Tokens / credentials being logged: a logging change is a classic place a
  secret starts leaking into stdout. When a diff touches logging *and* handles
  auth data, check that tokens aren't being printed.
- AuthN / authZ checks: missing, in the wrong layer, or trivially bypassable.
- Crypto: don't roll your own; correct algorithm + correct mode + correct
  random source.

## Data integrity

- Database migrations: forward+backward safe? Locking a hot table?
- Schema changes: do consumers know how to read the new shape?
- Transactions: is the unit of work right? Partial writes possible?

## Simplification / reuse

- Is there an existing helper this should use? (Especially relevant when the
  PR touches a foundation file you've already reviewed — the dependent might
  reimplement something the foundation now exposes.)
- Dead code / unreachable branches.
- Premature abstraction or premature generalization.

## Tests

- Are the new code paths covered? If not, is there a stated reason?
- Do existing tests still exercise the same behavior, or were they
  weakened to make new code pass?
- Are tests testing behavior (good) or implementation details (brittle)?

## Style (lowest priority)

- Naming: is it consistent with the rest of the file / package?
- Comments: do they explain *why*, or just restate *what*? Restating-what
  comments are noise; flag them only if they actively mislead.
- Formatting: don't bother — the formatter handles it.

## Severity rubric

- **blocker**: ship-stopping. Wrong behavior, security hole, data loss.
- **concern**: should be fixed before merge, but the author might
  reasonably push back with context.
- **nit**: small / subjective. Author can take it or leave it.

If you find yourself writing more than ~3 nits per file, you're padding.
Drop the weakest ones.
