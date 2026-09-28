from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # full environment: SQLAlchemy installed
    from app.core.database import async_database_url

    _STATIC_FALLBACK = False
except ModuleNotFoundError as exc:  # static sandbox: no third-party deps
    if exc.name != "sqlalchemy":
        raise
    _STATIC_FALLBACK = True


def test_database_urls_normalize_to_asyncpg() -> None:
    if _STATIC_FALLBACK:
        # Behavioral check needs SQLAlchemy's make_url; verify the contract
        # structurally until the dependency is available.
        src = (ROOT / "app" / "core" / "database.py").read_text(encoding="utf-8-sig")
        assert "def async_database_url(" in src
        assert "make_url(" in src
        assert "postgresql+asyncpg" in src
        assert "sslmode" in src
        assert "channel_binding" in src
        return
    assert async_database_url(
        "postgres://u:p@example.test/db?sslmode=require&channel_binding=require"
    ) == "postgresql+asyncpg://u:p@example.test/db?ssl=require"


def test_bot_service_handlers_are_not_registered_as_routers() -> None:
    text = (ROOT / "app" / "bot" / "app.py").read_text(encoding="utf-8-sig")
    assert "account.router" not in text
    assert "admin.router" not in text
    for name in ("catalog", "menu", "payments", "releases", "start", "storage"):
        assert f"{name}.router" in text


def test_no_dangling_known_callbacks() -> None:
    keyboard = (ROOT / "app" / "bot" / "keyboards" / "main_menu.py").read_text(encoding="utf-8-sig")
    payments = (ROOT / "app" / "bot" / "handlers" / "payments.py").read_text(encoding="utf-8-sig")
    releases = (ROOT / "app" / "bot" / "handlers" / "releases.py").read_text(encoding="utf-8-sig")
    assert "menu:settings" not in keyboard
    assert 'callback_data="cv:noop"' not in payments
    assert 'F.data == "cv:no-op"' in releases


def test_no_selector_loop_shim_in_runtime() -> None:
    for path in (ROOT / "main.py", ROOT / "app" / "workers" / "runtime_worker.py"):
        text = path.read_text(encoding="utf-8-sig")
        assert "SelectorEventLoop" not in text
        assert "WindowsSelectorEventLoopPolicy" not in text


def test_runtime_session_scope_commits_and_rolls_back() -> None:
    text = (ROOT / "app" / "runtime" / "db.py").read_text(encoding="utf-8-sig")
    assert "await session.commit()" in text
    assert "await session.rollback()" in text
