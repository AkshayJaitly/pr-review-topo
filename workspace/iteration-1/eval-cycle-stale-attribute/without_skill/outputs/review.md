# Code Review: `feature` vs `main`

**Files changed:** `models/user.py`, `models/group.py`

---

## Summary of Changes

The feature branch partially refactors the `User` model to store group IDs (`int`) instead of `Group` object references:

- `User.groups: list[Group]` is renamed to `User.group_ids: list[int]`
- `User.add_group(g)` now appends `g.gid` instead of `g`
- `Group.members_with_group()` gains a docstring and a cosmetic rename of loop variables

---

## Critical Bug: Stale Attribute Reference in `Group.members_with_group()`

**File:** `models/group.py`, line 12

```python
# feature branch (broken)
result = [user for user in self.members if self in user.groups]
```

`Group.members_with_group()` still reads `user.groups`, but `User.groups` no longer exists on the feature branch. The attribute was renamed to `user.group_ids` in `models/user.py`.

Beyond the name mismatch, the comparison is also semantically wrong:

- `user.group_ids` holds `int` values (GIDs), not `Group` objects.
- The membership test `self in user.group_ids` would check whether the `Group` object itself is in a list of integers, which will always be `False`.

**Effect:** `members_with_group()` always returns an empty list on the feature branch, silently, with no exception on Python versions where the attribute happens to exist via inheritance or `__getattr__` magic. In plain usage it raises `AttributeError: 'User' object has no attribute 'groups'`.

**Required fix:**

```python
result = [user for user in self.members if self.gid in user.group_ids]
```

This checks whether the group's own ID is present in the user's list of integer IDs, which is consistent with the refactored `User` model.

---

## Non-critical Observations

### Docstring and rename are cosmetic only
The added class docstring (`"""A group of users."""`) and the loop variable rename (`u` -> `user`) are fine style improvements but do not affect correctness.

### `User.add_group` type annotation is now misleading
```python
def add_group(self, g: Group):
    self.group_ids.append(g.gid)
```
The parameter is annotated as `Group` but only `g.gid` (an `int`) is used. The annotation is still technically accurate (the caller passes a `Group`), but a reader might wonder why the method takes a full object when it only needs the ID. Consider accepting `gid: int` directly, or at minimum adding a brief comment explaining the indirection.

### Circular import is unchanged
Both files import each other at module level (`group.py` imports `User`, `user.py` imports `Group`). This pre-existing circular import is not introduced by this PR but also not resolved. It works at runtime only because of the specific module initialization order. Any refactor that changes import order could break it. This is worth a follow-up ticket.

---

## Verdict

**Do not merge.** The rename of `User.groups` to `User.group_ids` is not propagated into `Group.members_with_group()`, which breaks the only public method on `Group`. The fix is a one-line change in `group.py`.
