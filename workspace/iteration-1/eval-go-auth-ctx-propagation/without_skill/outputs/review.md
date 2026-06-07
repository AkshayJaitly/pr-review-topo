# Code Review — feature branch (Go web service)

Three files changed: `cmd/server/main.go`, `pkg/auth/token.go`,
`pkg/util/log.go`.

## cmd/server/main.go

The handler now creates a `context.Background()` and passes it to
`auth.Validate` and `log.InfoCtx`. Switched from `log.Info` to `log.InfoCtx`.

Otherwise unchanged.

Question: is there a reason to make a fresh background context here rather
than using `r.Context()`? If the handler is meant to be request-scoped, the
request's own context is the right thing to pass — and the new logger
machinery seems designed for exactly that. Worth checking.

## pkg/auth/token.go

`Validate` now takes a `context.Context` and parses the token format
`<userid>:<exp>:<sig>`. Returns false on empty token or wrong shape.

The function returns `true` for any structurally-valid token regardless of
signature — there's a `// TODO: verify signature` comment in place of the
actual check. That's a meaningful security regression if this is the
production path: the previous version was also weak (only checked
non-empty), but at least nothing claimed to be a real validator.

Also, the parsed expiry component isn't checked against `time.Now()`. Easy
to miss.

## pkg/util/log.go

New `InfoCtx(ctx, msg)` that extracts a request ID from the context value
and prefixes the log line with it. Falls back to the unscoped format if no
request id is present.

`WithRequestID(ctx, id)` helper is provided to inject the id. The `ctxKey`
private type is the right pattern.

Notable: nothing in this diff actually calls `WithRequestID`. So unless a
middleware does it elsewhere, the request id field will always be empty and
`InfoCtx` will silently behave like `Info`. Worth confirming the middleware
exists.

`Info` is kept for non-request paths. Good.

## Overall

- Security blocker: token signature isn't verified; expiry isn't checked.
- The ctx plumbing is half-built — needs middleware to set the request id,
  and main.go should pass `r.Context()`.

**Recommendation: request changes.**
