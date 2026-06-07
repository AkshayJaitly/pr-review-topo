#!/usr/bin/env python3
"""Plan a PR review by topologically sorting changed files.

Given a list of changed files (post-change contents on disk), build a
language-agnostic dependency graph from import-like references and emit a
review plan in topological order so foundational files come before files that
depend on them.

Usage:
    plan_review.py --files file1 file2 ...
    plan_review.py --files-from list.txt
    plan_review.py --diff-base origin/main   # auto-detects changed files via git

Output (stdout, JSON):
    {
      "groups": [
         {"order": 1, "files": ["pkg/util/log.go"], "depended_on_by": ["cmd/main.go"]},
         {"order": 2, "files": ["pkg/auth/token.go"], "depended_on_by": [...]},
         ...
      ],
      "edges": [["pkg/util/log.go", "cmd/main.go"], ...],
      "cycles": [["a.go", "b.go"]]   # SCCs of size > 1, if any
    }

Edges run foundation -> dependent (A -> B means B depends on A; review A first).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path


# Patterns that look like a reference to another module/file. Language-agnostic
# on purpose: the goal is decent recall, not perfect precision. False positive
# edges just shuffle order; they don't break the review.
IMPORT_PATTERNS = [
    # Go single-line `import "x"`, Python/Java/Rust `import x.y.z`
    re.compile(r'^\s*import\s+(?:"([^"]+)"|([\w\.\_/]+))', re.MULTILINE),
    # Python `from x.y import z`
    re.compile(r'^\s*from\s+([\w\.\_]+)\s+import', re.MULTILINE),
    # JS/TS `import ... from 'x'` and bare `import 'x'`
    re.compile(r'''^\s*import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]''', re.MULTILINE),
    re.compile(r'''require\(\s*['"]([^'"]+)['"]\s*\)''', re.MULTILINE),
    # C/C++
    re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE),
    # Ruby
    re.compile(r'''^\s*require(?:_relative)?\s+['"]([^'"]+)['"]''', re.MULTILINE),
    # Catch-all for path-like quoted strings that look like import paths.
    # Useful for Go's parenthesized `import (...)` block, where the quoted
    # paths are on their own lines and don't have the `import` keyword in
    # front of them. False positives are filtered out downstream by the
    # token-suffix match against actual changed files.
    re.compile(r'''["']([\w\.\-]+(?:/[\w\.\-]+)+)["']'''),
]

# Files we never treat as code for graph purposes. They can still appear in the
# review (as leaves), but we don't try to parse imports out of them.
BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.zip', '.lock', '.bin', '.ico'}


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except (OSError, IsADirectoryError):
        return ''


def extract_refs(text: str) -> set[str]:
    """Pull out everything that looks like a module/path reference."""
    refs: set[str] = set()
    for pat in IMPORT_PATTERNS:
        for m in pat.finditer(text):
            for g in m.groups():
                if g:
                    refs.add(g)
    return refs


def file_tokens(path: str) -> set[str]:
    """Tokens that another file could plausibly use to reference `path`.

    For pkg/auth/token.go we contribute: 'pkg/auth/token', 'auth/token',
    'token', 'pkg/auth', 'auth'. A ref matches if it ends with any token.
    """
    p = Path(path)
    stem = p.stem  # 'token'
    parts = list(p.with_suffix('').parts)  # ['pkg', 'auth', 'token']
    tokens: set[str] = set()
    if stem and stem not in ('__init__', 'index', 'mod'):
        tokens.add(stem)
    # progressive path suffixes: full, then drop leading components
    for i in range(len(parts)):
        suffix = '/'.join(parts[i:])
        if suffix:
            tokens.add(suffix)
        # also the parent dir, for package-style imports
        parent_suffix = '/'.join(parts[i:-1])
        if parent_suffix:
            tokens.add(parent_suffix)
    return tokens


def normalize_ref(ref: str) -> str:
    """Strip extensions and './' so refs compare against path tokens cleanly."""
    r = ref.strip()
    # turn dots into slashes for python-style 'a.b.c'
    if '/' not in r and '.' in r and not r.endswith(('.h', '.hpp', '.c', '.cpp')):
        r = r.replace('.', '/')
    r = r.lstrip('./')
    for ext in ('.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs', '.py', '.go'):
        if r.endswith(ext):
            r = r[: -len(ext)]
    return r


def build_graph(files: list[str]) -> tuple[dict[str, set[str]], list[tuple[str, str]]]:
    """Return (adj, edges). adj[u] = set of v where u -> v (v depends on u)."""
    files = sorted(set(files))
    # token -> set of files that contribute that token. Multiple files can
    # share a token (e.g. all files in the same package), and the dependent
    # plausibly relies on any of them, so we don't pick a winner here.
    token_index: dict[str, set[str]] = defaultdict(set)
    for f in files:
        for tok in file_tokens(f):
            token_index[tok].add(f)

    adj: dict[str, set[str]] = {f: set() for f in files}
    edges: list[tuple[str, str]] = []
    for f in files:
        if Path(f).suffix.lower() in BINARY_EXTS:
            continue
        text = read_text(f)
        if not text:
            continue
        refs = {normalize_ref(r) for r in extract_refs(text)}
        for ref in refs:
            # Find the longest token that is a suffix of `ref`; that's the
            # most specific match. Then add edges from every file that
            # contributes that token (handles multi-file packages).
            best_len = -1
            best_token: str | None = None
            for tok in token_index:
                if ref == tok or ref.endswith('/' + tok):
                    if len(tok) > best_len:
                        best_len, best_token = len(tok), tok
            if best_token is None:
                continue
            for target in token_index[best_token]:
                if target == f:
                    continue
                if f not in adj[target]:
                    adj[target].add(f)
                    edges.append((target, f))
    return adj, edges


def tarjan_scc(adj: dict[str, set[str]]) -> list[list[str]]:
    """Find strongly connected components (Tarjan's). Returns SCCs in
    reverse-topological order, which is what we want for grouped Kahn's."""
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = defaultdict(bool)
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in adj.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack[w]:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                component.append(w)
                if w == v:
                    break
            result.append(sorted(component))

    # iterative-ish via recursion; PRs rarely have thousands of files so depth
    # is fine in practice. If you hit recursion limits, raise sys.setrecursionlimit.
    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for v in adj:
        if v not in index:
            strongconnect(v)
    return result


def topo_groups(files: list[str], adj: dict[str, set[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """Group files into review batches. Each SCC is one batch; batches are
    ordered so foundations come before dependents. Cycles (SCCs of size > 1)
    are returned separately so the caller can flag them."""
    sccs = tarjan_scc(adj)  # reverse-topological order
    # Build condensation DAG
    node_to_scc: dict[str, int] = {}
    for i, comp in enumerate(sccs):
        for n in comp:
            node_to_scc[n] = i
    cond_adj: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
    cond_indeg: dict[int, int] = {i: 0 for i in range(len(sccs))}
    for u, vs in adj.items():
        for v in vs:
            iu, iv = node_to_scc[u], node_to_scc[v]
            if iu != iv and iv not in cond_adj[iu]:
                cond_adj[iu].add(iv)
                cond_indeg[iv] += 1
    # Kahn's on the condensation
    queue: deque[int] = deque(sorted([i for i, d in cond_indeg.items() if d == 0]))
    order: list[list[str]] = []
    while queue:
        i = queue.popleft()
        order.append(sccs[i])
        for j in sorted(cond_adj[i]):
            cond_indeg[j] -= 1
            if cond_indeg[j] == 0:
                queue.append(j)
    cycles = [comp for comp in sccs if len(comp) > 1]
    return order, cycles


def discover_changed_files(base: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ['git', 'diff', '--name-only', '--diff-filter=ACMR', base + '...HEAD'],
            text=True,
        )
    except subprocess.CalledProcessError:
        # fall back to working-tree diff
        out = subprocess.check_output(
            ['git', 'diff', '--name-only', '--diff-filter=ACMR', base],
            text=True,
        )
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--files', nargs='+', help='Explicit list of changed files.')
    src.add_argument('--files-from', help='File containing one path per line.')
    src.add_argument('--diff-base', help='Git ref to diff against (e.g. origin/main).')
    ap.add_argument('--root', default='.', help='Repo root; paths are resolved relative to this.')
    args = ap.parse_args()

    os.chdir(args.root)
    if args.files:
        files = args.files
    elif args.files_from:
        files = [l.strip() for l in Path(args.files_from).read_text().splitlines() if l.strip()]
    else:
        files = discover_changed_files(args.diff_base)

    files = [f for f in files if Path(f).exists()]
    if not files:
        print(json.dumps({'groups': [], 'edges': [], 'cycles': []}, indent=2))
        return 0

    adj, edges = build_graph(files)
    order, cycles = topo_groups(files, adj)

    groups = []
    for i, comp in enumerate(order, start=1):
        depended_on_by = sorted({v for n in comp for v in adj.get(n, ()) if v not in comp})
        groups.append({'order': i, 'files': comp, 'depended_on_by': depended_on_by})

    print(json.dumps({'groups': groups, 'edges': edges, 'cycles': cycles}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
