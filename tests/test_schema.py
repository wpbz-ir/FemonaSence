from app.db.models import Base


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
    assert required.issubset(Base.metadata.tables.keys())


def test_release_has_single_parent_constraint() -> None:
    table = Base.metadata.tables["releases"]
    checks = {constraint.name for constraint in table.constraints if constraint.name}
    assert "ck_releases_single_parent" in checks


def test_notification_dedupe_is_unique() -> None:
    table = Base.metadata.tables["notification_jobs"]
    unique_names = {constraint.name for constraint in table.constraints if constraint.name}
    assert "uq_notification_jobs_dedupe" in unique_names
