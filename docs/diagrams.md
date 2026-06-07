# Diagrams

Visual reference for how `pr-review-topo` works. All diagrams below are
[Mermaid](https://mermaid.js.org/) and render directly on GitHub.

## 1. End-to-end workflow

How a review request flows through the skill, from either a local branch or a
GitHub PR, to a report (and optionally inline comments).

```mermaid
flowchart TD
    A[Review request] --> B{Source?}
    B -->|local branch| C["git diff --name-only<br/>base...HEAD"]
    B -->|GitHub PR| D["gh pr checkout N<br/>gh pr view --json baseRefName"]
    C --> E[Changed files on disk]
    D --> E
    E --> F["plan_review.py<br/>build dependency graph"]
    F --> G["topological sort<br/>(Tarjan SCC + Kahn)"]
    G --> H[Ordered review batches<br/>foundation → dependent]
    H --> I[Walk each batch in order<br/>read post-change file + diff]
    I --> J{Cycle / SCC?}
    J -->|yes| K[Review the SCC<br/>as one unit]
    J -->|no| L[Review the single file<br/>with deps already understood]
    K --> M[Findings: blocker / concern / nit]
    L --> M
    M --> N{post-comments?}
    N -->|no| O[Markdown report<br/>to conversation]
    N -->|yes, user asked| P["gh api .../pulls/N/reviews<br/>summary + inline comments"]
```

## 2. Why order matters (the core idea)

A dependency graph of changed files. Edges run **foundation → dependent**.
Reviewing bottom-up (dependent first) means judging `main.go` before you know
how `log.go` and `token.go` changed. Topological order flips that.

```mermaid
flowchart LR
    log["pkg/util/log.go<br/><i>foundation</i>"]
    token["pkg/auth/token.go"]
    main["cmd/server/main.go<br/><i>top consumer</i>"]
    log --> token
    log --> main
    token --> main

    classDef found fill:#dff0d8,stroke:#3c763d;
    classDef cons fill:#f2dede,stroke:#a94442;
    class log found;
    class main cons;
```

Review order produced: **1. log.go → 2. token.go → 3. main.go**

## 3. The payoff, as a sequence

By the time the reviewer reaches `main.go`, it already knows `InfoCtx`
silently no-ops without a seeded context — so `context.Background()` is an
obvious blocker instead of an innocuous-looking line.

```mermaid
sequenceDiagram
    participant R as Reviewer
    participant L as log.go
    participant T as token.go
    participant M as main.go

    R->>L: review foundation first
    L-->>R: InfoCtx degrades silently if ctx has no request id
    Note over R: carry forward: a real request ctx is required
    R->>T: review
    T-->>R: Validate threads ctx ✓ but skips expiry/signature ✗ (blocker)
    R->>M: review top consumer
    M-->>R: passes context.Background() — empty ctx!
    Note over R: KNOWN from step 1 → correlation is dead = blocker
```

## 4. Cycle handling

When changed files import each other, no single file is foundational. The
planner detects the strongly-connected component (Tarjan) and the review
treats it as one unit.

```mermaid
flowchart LR
    user["models/user.py"]
    group["models/group.py"]
    user <--> group
    subgraph SCC["reviewed together (cycle)"]
        user
        group
    end
```

## 5. Live example

A real review produced by this skill, posted as inline comments on a GitHub PR
in topological order:

👉 https://github.com/AkshayJaitly/topo-pr-review-demo/pull/1

![PR review screenshot](pr-review-screenshot.png)
