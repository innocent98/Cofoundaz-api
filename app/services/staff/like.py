def contains_pattern(term: str) -> str:
    """`%term%` for ILIKE with `%`, `_` and the escape character taken literally (escape="\\")."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
