# PR Review: feat — add request-scoped logging and ctx-aware auth

## Summary

The branch introduces context-aware logging (`InfoCtx`) with request-ID correlation and
updates `auth.Validate` to accept a `context.Context`. The design of the `util` package
is solid and the auth token parsing is a useful improvement over the bare empty-string
check. However, `cmd/server/main.go` passes `context.Background()` instead of
`r.Context()` throughout the handler — this defeats the entire purpose of the
context-scoped logging and means request-ID correlation will never work in production.
Additionally, `Validate` accepts a well-structured token but never checks its expiry
field, creating a security hole where expired tokens are silently accepted. **Request
changes** on both blockers before merging.

Topological order used: `pkg/util/log.go` → `pkg/auth/token.go` → `cmd/server/main.go`

5 findings: 2 blockers, 1 concern, 2 nits.

---

## Findings by file (topological order)

### 1. `pkg/util/log.go`  *(depended on by: `pkg/auth/token.go`, `cmd/server/main.go`)*

This file is the foundation for the whole context-propagation effort. The implementation
is clean: `InfoCtx` gracefully falls back to plain logging when no request ID is present,
`WithRequestID` is a standard `context.WithValue` helper, and the unexported key type
prevents key collisions across packages.

- **pkg/util/log.go:6** — nit: `reqIDKey` is package-private but callers in other
  packages must call `WithRequestID` to set it; that is fine and is the intended
  encapsulation. However, the `type ctxKey int` declaration could be placed before the
  constants that use it rather than after, which matches the conventional Go declaration
  order and is easier to read when someone follows the `const reqIDKey ctxKey = 1`
  reference.

- **pkg/util/log.go (general)** — nit: `Info` is described in a comment as "kept for
  non-request code paths (startup, shutdown)" but carries no `Deprecated` godoc tag
  indicating callers should prefer `InfoCtx`. If the intent is for all new request-path
  callers to use `InfoCtx`, consider adding a standard `// Deprecated:` comment so
  `gopls` and `staticcheck` surface the guidance automatically.

No correctness or security issues in this file.

---

### 2. `pkg/auth/token.go`  *(depended on by: `cmd/server/main.go`)*

The signature change from `Validate(token string) bool` to
`Validate(ctx context.Context, token string) bool` is consistent with the new logging
convention and is the right call given that auth events should be correlated by request.
The token-format parsing (`"<userid>:<exp-unix>:<sig>"`) is a genuine improvement over
the old empty-string check.

- **pkg/auth/token.go:~18** — **blocker**: missing expiry check — expired tokens are
  silently accepted.

  The token's second field is documented as `<exp-unix>` (a Unix timestamp), but the
  code only checks that the field exists — it never parses or compares the value against
  the current time. Any token with the right structural shape will pass validation
  indefinitely, regardless of how old it is. This is a security hole: a stolen or leaked
  token remains valid forever.

  Fix: parse the expiry field and reject tokens whose expiry is in the past.

  ```go
  import (
      "context"
      "strconv"
      "strings"
      "time"

      "example.com/svc/pkg/util"
  )

  func Validate(ctx context.Context, token string) bool {
      log.InfoCtx(ctx, "validating token")
      if token == "" {
          return false
      }
      parts := strings.Split(token, ":")
      if len(parts) != 3 {
          return false
      }
      exp, err := strconv.ParseInt(parts[1], 10, 64)
      if err != nil || time.Now().Unix() > exp {
          return false
      }
      // TODO: verify signature
      return true
  }
  ```

- **pkg/auth/token.go:~20** — concern: `// TODO: verify signature` — no signature
  verification is implemented. This is not a blocker on its own if the branch is an
  intentional incremental step, but without signature verification any caller can forge
  a structurally valid token with any `<userid>` and a future expiry and it will be
  accepted. The TODO should be tracked in an issue and linked here so it does not get
  forgotten. If signature verification cannot land in the same PR, this function should
  not be merged in its current form as the sole auth gate on production traffic.

---

### 3. `cmd/server/main.go`

By the time we reach this file we know:
- `util.InfoCtx` extracts a request ID from the context and includes it in logs.
- `auth.Validate` passes the context through to `InfoCtx` for the same correlation.

The file compiles and the handler wiring is otherwise correct, but the context threading
is broken at the source.

- **cmd/server/main.go:12** — **blocker**: `context.Background()` used instead of
  `r.Context()` — request-scoped correlation is permanently broken.

  ```go
  // WRONG — all auth and handler logs are emitted with no request ID:
  ctx := context.Background()

  // RIGHT — the HTTP framework already attaches cancellation and can carry
  // request-scoped values (e.g. injected by middleware that calls
  // util.WithRequestID):
  ctx := r.Context()
  ```

  With `context.Background()`, `InfoCtx` will never find a request ID in the context,
  so every log line falls back to the non-correlated format `[server] ok`. The entire
  context-propagation feature — the stated purpose of this PR — is effectively dead
  on arrival. Changing to `r.Context()` is a one-line fix.

  Note: for correlation to actually appear in logs, some middleware layer must call
  `util.WithRequestID(r.Context(), <id>)` and store the enriched context back on the
  request before the handler runs. That plumbing is not present in this diff; it should
  either land here or be tracked as a follow-up issue.

---

## Cross-cutting notes

- **No tests.** None of the three changed files has a corresponding `_test.go` file in
  the diff. The two blockers above — missing expiry check and wrong context — are exactly
  the kind of regression that unit and integration tests would catch immediately. At
  minimum, `pkg/auth/token_test.go` should cover: empty token, wrong number of fields,
  expired token, future token (valid structure, no sig yet), and the context-logging path
  (that `InfoCtx` is called with the provided ctx rather than a new one). A handler test
  for `/check` would catch the `context.Background()` bug by asserting that the request
  context flows through.

- **`http.ListenAndServe` error not handled.** This is pre-existing (not introduced by
  this branch), but since `main.go` was touched, it is worth noting: `ListenAndServe`
  only returns on error, and the returned error is discarded. The idiomatic fix is
  `log.Fatal(http.ListenAndServe(...))` or, for the new style, `if err :=
  http.ListenAndServe(...); err != nil { log.Fatal(err) }`.

- **`w.Write` return value ignored.** Also pre-existing. `w.Write([]byte("ok"))` returns
  an error that is silently dropped; in practice HTTP write errors at this stage are
  uncommon but not impossible. Low priority, but worth noting since the file was touched.

- **Architectural note — request ID injection.** The `WithRequestID` helper in `log.go`
  is only useful if something calls it before the handler. As noted above, the diff
  contains no middleware that injects a request ID. Without that, `InfoCtx` is
  functionally equivalent to `Info` for all request-path code. Consider adding a
  small middleware (even a `uuid.New()` stub) in the same PR so the feature can actually
  be exercised end-to-end.
