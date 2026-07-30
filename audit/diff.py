"""Pre/post-write field diffing for audit logging."""

from typing import Iterable, Mapping


def _norm(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    return v


def diff_dicts(
    before: Mapping,
    after: Mapping,
    fields: Iterable[str] | None = None,
) -> dict[str, tuple]:
    """Return {field: (old, new)} for fields whose normalized value changed.

    `before` may be empty (treat as INSERT) or `after` may be empty (DELETE);
    callers decide op_type. Fields default to the union of keys.
    """
    before = before or {}
    after = after or {}
    if fields is None:
        fields = set(before.keys()) | set(after.keys())

    changes: dict[str, tuple] = {}
    for f in fields:
        old = before.get(f)
        new = after.get(f)
        if _norm(old) != _norm(new):
            changes[f] = (old, new)
    return changes
