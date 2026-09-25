from pathlib import Path

from app.core.database import async_database_url

ROOT = Path(__file__).resolve().parents[1]


def test_database_urls_normalize_to_asyncpg() -> None:
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
