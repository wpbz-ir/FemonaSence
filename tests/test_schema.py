"""ORM schema contract tests.

[P0-10] test_release_has_single_parent_constraint used to expect
"ck_releases_single_parent", but the ORM-level CheckConstraint in
app/db/models/media.py is named "single_parent" — the production database
constraint name comes from the migrations instead (alembic/versions/
0001_initial.py creates CONSTRAINT ck_releases_single_parent). This test now
asserts the ORM reality and pins the migration-side name alongside it.

Environment note: the metadata assertions run when SQLAlchemy is importable.
In the static-audit sandbox (no third-party wheels) the same contracts are
verified structurally over the model sources via ast. No live PostgreSQL is
required in either mode.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "app" / "db" / "models"

try:  # full environment: SQLAlchemy installed
    from app.db.models import Base

    _STATIC_FALLBACK = False
except ModuleNotFoundError as exc:  # static sandbox: no third-party deps
    if exc.name != "sqlalchemy":
        raise
    _STATIC_FALLBACK = True


def _model_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8-sig") for p in sorted(MODELS_DIR.glob("*.py"))}


def _static_tables_and_constraints() -> tuple[set[str], set[str]]:
    """Collect __tablename__ values and named constraint names from the model sources."""
    tables: set[str] = set()
    constraints: set[str] = set()
    for source in _model_sources().values():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if (
                        isinstance(stmt, (ast.Assign, ast.AnnAssign))
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                        and (
                            (isinstance(stmt, ast.Assign) and any(
                                isinstance(t, ast.Name) and t.id == "__tablename__"
                                for t in stmt.targets
                            ))
                            or (isinstance(stmt, ast.AnnAssign)
                                and isinstance(stmt.target, ast.Name)
                                and stmt.target.id == "__tablename__")
                        )
                    ):
                        tables.add(stmt.value.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
                "CheckConstraint", "UniqueConstraint", "Index", "PrimaryKeyConstraint",
            }:
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(
                        kw.value.value, str
                    ):
                        constraints.add(kw.value.value)
    return tables, constraints


def test_expected_tables_exist() -> None:
    required = {
        "users",
        "titles",
        "series",
        "seasons",
        "episodes",
        "genres",
        "collections",
        "releases",
        "storage_files",
        "plans",
        "subscriptions",
        "orders",
        "payment_attempts",
        "payments",
        "wallets",
        "wallet_ledger_entries",
        "notification_jobs",
        "referrals",
        "analytics_events",
        "audit_logs",
    }
    if _STATIC_FALLBACK:
        tables, _ = _static_tables_and_constraints()
        assert required.issubset(tables)
        return
    assert required.issubset(Base.metadata.tables.keys())


def test_release_has_single_parent_constraint() -> None:
    # ORM truth: app/db/models/media.py names the CheckConstraint "single_parent".
    # The production DB name comes from the migrations
    # (ck_releases_single_parent in alembic/versions/0001_initial.py), so both
    # layers are pinned here.
    if _STATIC_FALLBACK:
        _, constraints = _static_tables_and_constraints()
        assert "single_parent" in constraints
    else:
        table = Base.metadata.tables["releases"]
        checks = {constraint.name for constraint in table.constraints if constraint.name}
        assert "single_parent" in checks
    migration = (ROOT / "alembic" / "versions" / "0001_initial.py").read_text(encoding="utf-8-sig")
    assert "ck_releases_single_parent" in migration


def test_notification_dedupe_is_unique() -> None:
    if _STATIC_FALLBACK:
        _, constraints = _static_tables_and_constraints()
        assert "uq_notification_jobs_dedupe" in constraints
        return
    table = Base.metadata.tables["notification_jobs"]
    unique_names = {constraint.name for constraint in table.constraints if constraint.name}
    assert "uq_notification_jobs_dedupe" in unique_names
