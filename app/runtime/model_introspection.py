from __future__ import annotations

from typing import Any


def first_attr(model: type, names: tuple[str, ...]):
    table_columns = getattr(model, "__table__", None)
    for name in names:
        if hasattr(model, name):
            return getattr(model, name)
        if table_columns is not None and name in table_columns.c:
            return table_columns.c[name]
    raise AttributeError(f"None of the expected attributes exist on {model!r}: {names}")


def model_values(model: type, **values: Any) -> dict[str, Any]:
    table = getattr(model, "__table__", None)
    if table is None:
        return {k: v for k, v in values.items() if hasattr(model, k)}
    columns = set(table.c.keys())
    return {k: v for k, v in values.items() if k in columns and v is not None}
