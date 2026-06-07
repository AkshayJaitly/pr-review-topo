# PR Review: feature — store group ids on User instead of refs

## Summary

The branch refactors `User` to store plain integer group IDs (`group_ids: list[int]`)
instead of `Group` object references, but leaves `Group.members_with_group` querying
the old attribute name (`user.groups`) and checking membership with the old type (`self in
user.groups`). This is a ship-stopping inconsistency: the method will always raise
`AttributeError` at runtime, and even if the name were patched the containment check
would silently return the wrong answer. **Request changes.**

Topological order used: `models/group.py` ↔ `models/user.py` (cycle — reviewed as one unit)

1 finding: 1 blocker, 0 concerns, 0 nits.

---

## Cycle note

`models/group.py` and `models/user.py` import each other directly
(`group.py → User`, `user.py → Group`). The planner detected this as a
strongly-connected component (SCC), so both files are reviewed together as a
single unit rather than in a strict sequence. No single file is "foundational"
relative to the other within this changeset.

---

## Findings (cycle: models/group.py + models/user.py)

### models/group.py + models/user.py *(circular dependency — reviewed as one unit)*

- **models/group.py:12** — **blocker**: stale attribute name + type mismatch in `members_with_group`

  `user.py` renamed `self.groups` (a `list[Group]`) to `self.group_ids` (a `list[int]`)
  and updated `add_group` to store `g.gid` (an `int`) instead of `g` (a `Group`).
  `group.py` was not updated to match: `members_with_group` still reads `user.groups`
  (which no longer exists — will raise `AttributeError`), and the containment test
  `self in user.groups` compared a `Group` object against a list of integers — a check
  that could never return `True` even if the attribute name were corrected.

  The method must be updated to use the new attribute and compare by ID:

  ```python
  def members_with_group(self):
      """Return members who still list this group in their record."""
      return [user for user in self.members if self.gid in user.group_ids]
  ```

---

## Cross-cutting notes

- The circular import between `group.py` and `user.py` is pre-existing (not introduced
  by this branch) but is worth noting: it means Python must finish executing one module
  before the other imports resolve, which can produce subtle `ImportError` or
  `AttributeError` issues at startup depending on import order. Consider breaking the
  cycle by passing raw IDs across the boundary (which this refactor partially moves
  toward) and deferring the type annotation to a `TYPE_CHECKING` guard or a forward
  reference string.

- No tests are present in the diff. The bug in `members_with_group` would have been
  caught immediately by a test exercising the round-trip `add_group → members_with_group`.
  Adding even a minimal unit test is strongly recommended before merging.
