#!/usr/bin/env bash
# Build three synthetic PR fixtures as real git repos with main + feature
# branches. Each fixture has cross-file dependencies designed to make
# topological-order review pay off (or to test edge cases).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
rm -rf "$ROOT/fixture-go-auth" "$ROOT/fixture-py-pipeline" "$ROOT/fixture-cycle"

git_init() {
  cd "$1"
  git init -q -b main
  git config user.email "test@test.local"
  git config user.name "Test"
  git config commit.gpgsign false
}

# --------------------------------------------------------------------
# Fixture 1: Go web service. log -> auth -> main.
# Auth changes context handling on a logger; main.go passes background ctx,
# which loses request scoping. Topo review (log first, then auth, then main)
# makes the bug stand out by the time you reach main.
# --------------------------------------------------------------------
F1="$ROOT/fixture-go-auth"
mkdir -p "$F1/pkg/util" "$F1/pkg/auth" "$F1/cmd/server"
git_init "$F1"

cat > "$F1/pkg/util/log.go" <<'EOF'
package util

import "log"

type Logger struct{ prefix string }

func New(prefix string) *Logger { return &Logger{prefix: prefix} }

func (l *Logger) Info(msg string) {
    log.Printf("[%s] %s", l.prefix, msg)
}
EOF

cat > "$F1/pkg/auth/token.go" <<'EOF'
package auth

import "example.com/svc/pkg/util"

var log = util.New("auth")

// Validate checks a token. Returns true if valid.
func Validate(token string) bool {
    log.Info("validating token")
    return token != ""
}
EOF

cat > "$F1/cmd/server/main.go" <<'EOF'
package main

import (
    "net/http"
    "example.com/svc/pkg/auth"
    "example.com/svc/pkg/util"
)

var log = util.New("server")

func main() {
    http.HandleFunc("/check", func(w http.ResponseWriter, r *http.Request) {
        tok := r.Header.Get("Authorization")
        if !auth.Validate(tok) {
            http.Error(w, "unauthorized", 401)
            return
        }
        log.Info("ok")
        w.Write([]byte("ok"))
    })
    http.ListenAndServe(":8080", nil)
}
EOF

cat > "$F1/go.mod" <<'EOF'
module example.com/svc
go 1.22
EOF

cd "$F1"
git add . && git commit -q -m "initial"
git checkout -q -b feature

# === Feature branch changes ===
# log.go: add context-aware method, keep old one (deprecated-ish)
cat > "$F1/pkg/util/log.go" <<'EOF'
package util

import (
    "context"
    "log"
)

type Logger struct{ prefix string }

func New(prefix string) *Logger { return &Logger{prefix: prefix} }

// InfoCtx logs with request scope. Use this in request handlers so logs
// can be correlated by request id (extracted from ctx).
func (l *Logger) InfoCtx(ctx context.Context, msg string) {
    rid, _ := ctx.Value(reqIDKey).(string)
    if rid != "" {
        log.Printf("[%s][%s] %s", l.prefix, rid, msg)
        return
    }
    log.Printf("[%s] %s", l.prefix, msg)
}

// Info is kept for non-request code paths (startup, shutdown).
func (l *Logger) Info(msg string) {
    log.Printf("[%s] %s", l.prefix, msg)
}

type ctxKey int
const reqIDKey ctxKey = 1

func WithRequestID(ctx context.Context, id string) context.Context {
    return context.WithValue(ctx, reqIDKey, id)
}
EOF

# auth/token.go: take ctx, missing expiry check (bug)
cat > "$F1/pkg/auth/token.go" <<'EOF'
package auth

import (
    "context"
    "strings"

    "example.com/svc/pkg/util"
)

var log = util.New("auth")

// Validate checks a token. Returns true if valid.
// Token format: "<userid>:<exp-unix>:<sig>".
func Validate(ctx context.Context, token string) bool {
    log.InfoCtx(ctx, "validating token")
    if token == "" {
        return false
    }
    parts := strings.Split(token, ":")
    if len(parts) != 3 {
        return false
    }
    // TODO: verify signature
    return true
}
EOF

# main.go: passes context.Background() instead of request ctx (bug)
cat > "$F1/cmd/server/main.go" <<'EOF'
package main

import (
    "context"
    "net/http"

    "example.com/svc/pkg/auth"
    "example.com/svc/pkg/util"
)

var log = util.New("server")

func main() {
    http.HandleFunc("/check", func(w http.ResponseWriter, r *http.Request) {
        ctx := context.Background()
        tok := r.Header.Get("Authorization")
        if !auth.Validate(ctx, tok) {
            http.Error(w, "unauthorized", 401)
            return
        }
        log.InfoCtx(ctx, "ok")
        w.Write([]byte("ok"))
    })
    http.ListenAndServe(":8080", nil)
}
EOF

git add . && git commit -q -m "feat: add request-scoped logging and ctx-aware auth"
git checkout -q main
echo "Built $F1"

# --------------------------------------------------------------------
# Fixture 2: Python pipeline. io.py -> transform.py -> main.py.
# io.py makes `sep` keyword-only; transform still calls positionally → broken.
# Topo review catches it because io.py is reviewed first.
# --------------------------------------------------------------------
F2="$ROOT/fixture-py-pipeline"
mkdir -p "$F2/pipeline"
git_init "$F2"

cat > "$F2/pipeline/__init__.py" <<'EOF'
EOF
cat > "$F2/pipeline/io.py" <<'EOF'
import csv

def read_data(path, sep=','):
    with open(path) as f:
        return list(csv.reader(f, delimiter=sep))
EOF
cat > "$F2/pipeline/transform.py" <<'EOF'
from pipeline.io import read_data

def load_and_clean(path):
    rows = read_data(path, ',')
    return [r for r in rows if any(r)]
EOF
cat > "$F2/pipeline/main.py" <<'EOF'
import sys
from pipeline.transform import load_and_clean

def main():
    print(load_and_clean(sys.argv[1]))

if __name__ == '__main__':
    main()
EOF

cd "$F2"
git add . && git commit -q -m "initial"
git checkout -q -b feature

cat > "$F2/pipeline/io.py" <<'EOF'
import csv

def read_data(path, *, sep=',', encoding='utf-8'):
    """Read a delimited file. `sep` and `encoding` are keyword-only so callers
    are explicit about format choices."""
    with open(path, encoding=encoding) as f:
        return list(csv.reader(f, delimiter=sep))
EOF

# transform.py: small refactor (added docstring + variable rename) but the
# call to read_data is still positional — broken now that sep is keyword-only.
# This is the bug a topo-ordered review catches: by the time you reach
# transform.py you've just noted that sep became keyword-only in io.py.
cat > "$F2/pipeline/transform.py" <<'EOF'
from pipeline.io import read_data

def load_and_clean(path):
    """Read a CSV and drop empty rows."""
    raw = read_data(path, ',')
    return [row for row in raw if any(row)]
EOF

cat > "$F2/pipeline/main.py" <<'EOF'
import argparse
from pipeline.transform import load_and_clean

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--encoding', default='utf-8')
    args = ap.parse_args()
    print(load_and_clean(args.path))

if __name__ == '__main__':
    main()
EOF

git add . && git commit -q -m "feat: keyword-only IO options + CLI args"
git checkout -q main
echo "Built $F2"

# --------------------------------------------------------------------
# Fixture 3: Cycle. models/user.py <-> models/group.py.
# user.py renames `groups` field to `group_ids`. group.py still references the
# old name. Skill should flag the cycle and review both together.
# --------------------------------------------------------------------
F3="$ROOT/fixture-cycle"
mkdir -p "$F3/models"
git_init "$F3"

cat > "$F3/models/__init__.py" <<'EOF'
EOF
cat > "$F3/models/user.py" <<'EOF'
from models.group import Group

class User:
    def __init__(self, uid):
        self.uid = uid
        self.groups = []  # list[Group]

    def add_group(self, g: Group):
        self.groups.append(g)
EOF
cat > "$F3/models/group.py" <<'EOF'
from models.user import User

class Group:
    def __init__(self, gid):
        self.gid = gid
        self.members = []  # list[User]

    def members_with_group(self):
        return [u for u in self.members if self in u.groups]
EOF

cd "$F3"
git add . && git commit -q -m "initial"
git checkout -q -b feature

cat > "$F3/models/user.py" <<'EOF'
from models.group import Group

class User:
    def __init__(self, uid):
        self.uid = uid
        self.group_ids = []  # list[int] — was: list[Group]

    def add_group(self, g: Group):
        self.group_ids.append(g.gid)
EOF
# group.py: cosmetic refactor (renamed local var, added docstring) but
# still references u.groups — which no longer exists on User. Bug that
# topo review of the cycle should catch by reviewing both together.
cat > "$F3/models/group.py" <<'EOF'
from models.user import User

class Group:
    """A group of users."""

    def __init__(self, gid):
        self.gid = gid
        self.members = []  # list[User]

    def members_with_group(self):
        """Return members who still list this group in their record."""
        result = [user for user in self.members if self in user.groups]
        return result
EOF

git add . && git commit -q -m "refactor: store group ids on User instead of refs"
git checkout -q main
echo "Built $F3"

echo "All fixtures built under $ROOT"
