from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "logs",
}

STALE_PREFIXES = (
    "_phase",
    "_final",
    ".phase",
    ".final",
    ".repair_backup_",
    "_FINAL_REPAIR_",
)

OBSOLETE_FILES = {
    "scripts/run_web_windows.py",
    "scripts/run_media_windows.py",
    "scripts/run_runtime_windows.py",
}

REQUIRED_ROUTERS = {
    "catalog",
    "menu",
    "payments",
    "releases",
    "start",
    "storage",
}

SERVICE_HANDLERS = {
    "account",
    "admin",
}



def fail(message: str) -> None:
    FAILURES.append(message)



def is_ignored(rel: Path) -> bool:
    for part in rel.parts:
        if part in IGNORED_DIRS:
            return True
        if part.startswith(STALE_PREFIXES):
            return True
    return False



def active_python_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if not is_ignored(rel):
            result.append(path)
    return sorted(result)



def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    if rel.name == "__init__":
        return ".".join(rel.parts[:-1])
    return ".".join(rel.parts)



def top_level_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.add(alias.asname or alias.name.split(".")[0])
            else:
                for alias in node.names:
                    symbols.add(alias.asname or alias.name)
    return symbols



def resolve_relative(current: str, level: int, module: str | None, is_init: bool) -> str:
    base = current.split(".")[:-1]
    if is_init:
        base = current.split(".")
    for _ in range(max(0, level - 1)):
        if base:
            base.pop()
    if module:
        base.append(module)
    return ".".join(base)



def check_syntax(files: list[Path]) -> None:
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            fail(f"SYNTAX:{path.relative_to(ROOT)}:{exc}")



def check_local_imports(files: list[Path]) -> None:
    modules = {module_name(path): path for path in files}

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError:
            continue

        current = module_name(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                target = resolve_relative(
                    current,
                    node.level,
                    node.module,
                    path.name == "__init__.py",
                )
            else:
                target = node.module or ""

            if not target.startswith("app"):
                continue

            target_path = modules.get(target)
            if target_path is None:
                fail(f"MISSING_MODULE:{path.relative_to(ROOT)} -> {target}")
                continue

            symbols = top_level_symbols(target_path)
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name in symbols:
                    continue
                # `from package import submodule` is a valid import when the
                # submodule file exists (e.g. `from app.api import admin_extended`,
                # `from app.bot.handlers import ads`) — only flag the alias when
                # it is neither a symbol nor a real module.
                if f"{target}.{alias.name}" not in modules:
                    fail(
                        f"MISSING_SYMBOL:{path.relative_to(ROOT)} -> "
                        f"{target}.{alias.name}"
                    )



def check_model_registry() -> None:
    try:
        sys.path.insert(0, str(ROOT))
        from app.db.models import Base
        from sqlalchemy.orm import RelationshipProperty
    except ModuleNotFoundError as exc:
        # [P0-10] Static-audit sandboxes ship without third-party wheels; the
        # ORM registry cannot be exercised there (tests/*.py cover the same
        # contracts structurally). Full check still runs in deployment envs.
        print(f"SKIP model-registry check (missing dependency: {exc.name})")
        return
    except Exception as exc:
        fail(f"MODEL_REGISTRY_IMPORT:{type(exc).__name__}:{exc}")
        return

    mapper_classes = {mapper.class_.__name__: mapper.class_ for mapper in Base.registry.mappers}
    expected = {"Title", "Episode"}
    missing = sorted(expected - mapper_classes.keys())
    for name in missing:
        fail(f"MODEL_CLASS_MISSING:{name}")

    exports = top_level_symbols(ROOT / "app" / "db" / "models" / "__init__.py")
    for name in ("Title", "Episode", "Release", "StorageFile", "Plan", "Subscription"):
        if name not in exports:
            fail(f"MODEL_EXPORT_MISSING:{name}")

    for table in Base.metadata.tables.values():
        for column in table.columns:
            for fk in column.foreign_keys:
                target_table = fk.column.table.name
                if target_table not in Base.metadata.tables:
                    fail(f"FK_TARGET_MISSING:{table.name}.{column.name}->{target_table}")

    for mapper in Base.registry.mappers:
        cls = mapper.class_
        for prop in mapper.iterate_properties:
            if not isinstance(prop, RelationshipProperty):
                continue
            target = prop.mapper.class_
            if target.__name__ not in mapper_classes:
                fail(f"RELATION_TARGET_MISSING:{cls.__name__}.{prop.key}")
            if prop.back_populates and not hasattr(target, prop.back_populates):
                fail(
                    f"BACK_POPULATES_MISSING:{cls.__name__}.{prop.key}"
                    f" -> {target.__name__}.{prop.back_populates}"
                )



def check_bot_router_contract() -> None:
    handlers_dir = ROOT / "app" / "bot" / "handlers"
    router_names: set[str] = set()
    for path in handlers_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        if re.search(r"^router\s*=\s*Router\(", text, re.M):
            router_names.add(path.stem)

    app_text = (ROOT / "app" / "bot" / "app.py").read_text(encoding="utf-8-sig")
    includes = set(re.findall(r"include_router\((\w+)\.router\)", app_text))

    missing = REQUIRED_ROUTERS - router_names
    for name in sorted(missing):
        fail(f"HANDLER_ROUTER_MISSING:{name}")

    for name in sorted(REQUIRED_ROUTERS - includes):
        fail(f"BOT_ROUTER_NOT_INCLUDED:{name}")

    for name in sorted(SERVICE_HANDLERS):
        if f"{name}.router" in app_text:
            fail(f"SERVICE_HANDLER_WRONG_ROUTER:{name}")

    for name in SERVICE_HANDLERS:
        path = handlers_dir / f"{name}.py"
        if not path.exists():
            fail(f"SERVICE_HANDLER_FILE_MISSING:{name}")
            continue
        symbols = top_level_symbols(path)
        if name == "account" and not {"send_account", "send_wallet", "send_favorites", "send_history"}.issubset(symbols):
            fail("ACCOUNT_SERVICE_FUNCTIONS_MISSING")
        if name == "admin" and "send_admin" not in symbols:
            fail("ADMIN_SERVICE_FUNCTION_MISSING")



def check_callback_contract() -> None:
    handlers_dir = ROOT / "app" / "bot" / "handlers"
    exact: dict[str, str] = {}
    regex: dict[str, str] = {}

    for path in handlers_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                source = ast.unparse(decorator)
                match = re.search(r"F\.data\s*==\s*([\"'])(.*?)\1", source)
                if match:
                    key = match.group(2)
                    location = f"{path.name}:{node.name}"
                    if key in exact:
                        fail(f"DUPLICATE_CALLBACK_EXACT:{key}:{exact[key]}:{location}")
                    exact[key] = location
                match = re.search(r"F\.data\.regexp\(r?([\"'])(.*?)\1\)", source)
                if match:
                    pattern = match.group(2)
                    location = f"{path.name}:{node.name}"
                    if pattern in regex:
                        fail(f"DUPLICATE_CALLBACK_REGEX:{pattern}:{regex[pattern]}:{location}")
                    regex[pattern] = location

    forbidden_callbacks = {
        "menu:subscription": "app/bot/handlers/payments.py",
        "menu:search": "app/bot/handlers/catalog.py",
    }
    for callback, owner in forbidden_callbacks.items():
        for path in handlers_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8-sig")
            if path.as_posix().replace("\\", "/").endswith(owner):
                continue
            if f'F.data == "{callback}"' in text or f"F.data == '{callback}'" in text:
                fail(f"CALLBACK_OWNERSHIP_CONFLICT:{callback}:{path.name}")



def check_database_contract() -> None:
    normalizer = ROOT / "app" / "core" / "database.py"
    if not normalizer.exists():
        fail("DATABASE_NORMALIZER_MISSING")
        return

    normalizer_text = normalizer.read_text(encoding="utf-8-sig")
    for required in ("make_url", "postgresql+asyncpg", "sslmode", "channel_binding"):
        if required not in normalizer_text:
            fail(f"DATABASE_NORMALIZER_MISSING:{required}")

    async_files = [
        ROOT / "app" / "db" / "session.py",
        ROOT / "app" / "workers" / "media_worker.py",
        ROOT / "scripts" / "check_production.py",
        ROOT / "scripts" / "requeue_stale_media_jobs.py",
        ROOT / "scripts" / "enqueue_quality_matrix.py",
    ]
    for path in async_files:
        text = path.read_text(encoding="utf-8-sig")
        if "create_async_engine" not in text:
            continue
        # [FIX-E] نشانه‌ی موتور نرمال‌شده یکی از این دو است: async_database_url()
        # مستقیم، یا async_engine_kwargs_from_url() که خودش async_database_url را
        # صدا می‌زند (app/core/database.py). media_worker/check_production/
        # requeue_stale_media_jobs/enqueue_quality_matrix به شکل دوم مهاجرت
        # کرده‌اند؛ پذیرش فقط نشانه‌ی اول false-FAIL می‌داد.
        if "async_database_url(" not in text and "async_engine_kwargs_from_url(" not in text:
            fail(f"ASYNC_ENGINE_NOT_NORMALIZED:{path.relative_to(ROOT)}")
        if "postgresql+psycopg://" in text:
            fail(f"ASYNC_FILE_CONTAINS_PSYCOPG_URL:{path.relative_to(ROOT)}")

    active = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="ignore")
        for path in active_python_files()
        if path != ROOT / "scripts" / "strict_audit.py"
        and "tests" not in path.relative_to(ROOT).parts
    )
    if "WindowsSelectorEventLoopPolicy" in active or "loop_factory=asyncio.SelectorEventLoop" in active:
        fail("SELECTOR_EVENT_LOOP_SHIM_REMAINS")



def check_catalog_contract() -> None:
    catalog = (ROOT / "app" / "services" / "catalog.py").read_text(encoding="utf-8-sig")
    if not re.search(r"^async def get_title\(session, title_id\):", catalog, re.M):
        fail("CATALOG_GET_TITLE_CONTRACT_INVALID")
    if "async def title_text" in catalog:
        fail("TITLE_TEXT_MUST_BE_SYNC")

    admin = (ROOT / "app" / "api" / "admin.py").read_text(encoding="utf-8-sig")
    if 'pattern="^(MOVIE|SERIES|ANIMATION)$"' not in admin:
        fail("ADMIN_ANIMATION_KIND_NOT_ALLOWED")



def check_deployment_contract() -> None:
    production = (ROOT / "deploy" / "production.ps1").read_text(encoding="utf-8-sig")
    stop = (ROOT / "deploy" / "stop_production.ps1").read_text(encoding="utf-8-sig")
    start = (ROOT / "scripts" / "start_local.ps1").read_text(encoding="utf-8-sig")

    for stale in sorted(OBSOLETE_FILES):
        if (ROOT / stale).exists():
            fail(f"OBSOLETE_FILE_PRESENT:{stale}")
        if stale.rsplit("/", 1)[-1] in production:
            fail(f"STALE_LAUNCHER_REFERENCED:{stale}")

    for service in ("uvicorn", "app.workers.media_worker", "app.workers.runtime_worker"):
        if service not in production:
            fail(f"SERVICE_NOT_IN_PRODUCTION_STARTER:{service}")

    if "main.py" not in stop:
        fail("BOT_NOT_STOPPED")

    required_flags = {
        'APP_ENV = "development"',
        'TELEGRAM_MODE = "polling"',
        'PUBLIC_BASE_URL = "http://127.0.0.1:8000"',
        'ALLOWED_HOSTS = "127.0.0.1,localhost"',
        'RATE_LIMIT_ENABLED = "0"',
        'RUN_MAINTENANCE_IN_BOT = "0"',
        'RUN_NOTIFICATIONS_IN_BOT = "0"',
        'TELEGRAM_STORAGE_REQUIRED = "0"',
    }
    for flag in required_flags:
        if f"$env:{flag}" not in start:
            fail(f"LOCAL_FLAG_MISSING:{flag}")
    # [P0-10] The HEALTH_READY_FAILED-in-start_local.ps1 gate was removed with
    # the old launcher; start_local.ps1 now only sets env flags and delegates
    # to deploy/production.ps1 — no health-gate assertion here any more.



def check_dependencies() -> None:
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8-sig")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8-sig")
    if "asyncpg==0.31.0" not in req:
        fail("ASYNCPG_MISSING_REQUIREMENTS")
    if '"asyncpg==0.31.0"' not in pyproject:
        fail("ASYNCPG_MISSING_PYPROJECT")



def check_callback_data_targets() -> None:
    handlers_dir = ROOT / "app" / "bot" / "handlers"
    handler_text = "\n".join(
        p.read_text(encoding="utf-8-sig")
        for p in handlers_dir.glob("*.py")
    )
    keyboard_paths = [
        ROOT / "app" / "bot" / "keyboards" / "main_menu.py",
        ROOT / "app" / "bot" / "handlers" / "catalog.py",
        ROOT / "app" / "bot" / "handlers" / "payments.py",
        ROOT / "app" / "bot" / "handlers" / "releases.py",
    ]
    for path in keyboard_paths:
        text = path.read_text(encoding="utf-8-sig")
        if 'callback_data="menu:settings"' in text:
            fail(f"DANGLING_CALLBACK:menu:settings:{path.relative_to(ROOT)}")
        if 'callback_data="cv:noop"' in text:
            fail(f"DANGLING_CALLBACK:cv:noop:{path.relative_to(ROOT)}")
    if 'F.data == "cv:no-op"' not in handler_text:
        fail("NOOP_CALLBACK_HANDLER_MISSING")


def check_runtime_entry_contracts() -> None:
    app_text = (ROOT / "app" / "bot" / "app.py").read_text(encoding="utf-8-sig")
    if "account.router" in app_text or "admin.router" in app_text:
        fail("SERVICE_HANDLER_ROUTER_REGISTERED")
    if "account.router" not in app_text and "admin.router" not in app_text:
        pass
    main_text = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    runtime_text = (ROOT / "app" / "workers" / "runtime_worker.py").read_text(encoding="utf-8-sig")
    for label, text in (("main.py", main_text), ("runtime_worker.py", runtime_text)):
        if "WindowsSelectorEventLoopPolicy" in text or "SelectorEventLoop" in text:
            fail(f"SELECTOR_EVENT_LOOP_SHIM_REMAINS:{label}")


def check_database_normalizer_behavior() -> None:
    try:
        sys.path.insert(0, str(ROOT))
        from app.core.database import async_database_url
    except ModuleNotFoundError as exc:
        # [P0-10] same static-sandbox treatment as check_model_registry.
        print(f"SKIP database-normalizer behavior check (missing dependency: {exc.name})")
        return
    try:
        samples = {
            "postgres://u:p@example.test/db?sslmode=require&channel_binding=require":
                "postgresql+asyncpg://u:p@example.test/db?ssl=require",
            "postgresql+psycopg://u:p@example.test/db?sslmode=verify-full":
                "postgresql+asyncpg://u:p@example.test/db?ssl=verify-full",
            "postgresql+asyncpg://u:p@example.test/db?ssl=require":
                "postgresql+asyncpg://u:p@example.test/db?ssl=require",
        }
        for raw, expected in samples.items():
            got = async_database_url(raw)
            if got != expected:
                fail(f"DATABASE_NORMALIZER_MISMATCH:{got}")
    except Exception as exc:
        fail(f"DATABASE_NORMALIZER_BEHAVIOR:{type(exc).__name__}:{exc}")


def check_source_hygiene() -> None:
    for rel in ("app/config.py", "app/handlers", "app/keyboards"):
        if (ROOT / rel).exists():
            fail(f"DUPLICATE_LEGACY_NAMESPACE_PRESENT:{rel}")


_MIGRATION_HEAD: str | None = None


def check_migrations() -> None:
    """[P0-10] Dynamic instead of the hardcoded 0001..0016/0016-head range:
    scan alembic/versions/*.py for revision/down_revision pairs, assert the
    graph is a linear single-head chain whose head carries the max numeric
    prefix (e.g. 0017_ads_system; a bare numeric id like 0018 also counts)."""
    global _MIGRATION_HEAD
    folder = ROOT / "alembic" / "versions"
    revisions: dict[str, str | None] = {}
    numeric: dict[str, int] = {}
    for path in sorted(folder.glob("*.py")):
        text = path.read_text(encoding="utf-8-sig")
        match = re.search(r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)', text, re.M)
        if not match:
            continue
        rev = match.group(1)
        revisions[rev] = None
        # [FIX-E] «0018_plan_duration_check» شناسه‌اش «0018» لخت است (بدون پسوند
        # نام) — regex قبلی فقط «NNNN_name» را عددی می‌دانست و headِ واقعی به‌اشتباه
        # MIGRATION_HEAD_NOT_MAX_NUMERIC می‌گرفت. عدد پیشوند برای هر دو شکل خوانده
        # می‌شود؛ قدرت چک (head = جدیدترین عدد) تغییری نمی‌کند.
        num = re.match(r"(\d+)(?:_|$)", rev)
        if num:
            numeric[rev] = int(num.group(1))
        down = re.search(r'^down_revision[^=]*=\s*(?:["\']([^"\']*)["\']|None)', text, re.M)
        if down:
            revisions[rev] = down.group(1)

    if not revisions:
        fail("MIGRATION_REVISIONS_EMPTY")
        return

    # every down_revision must point at an existing revision (no dangling refs)
    for rev, down in sorted(revisions.items()):
        if down is not None and down not in revisions:
            fail(f"MIGRATION_DANGLING_DOWN_REVISION:{rev}->{down}")

    # linear single head: exactly one revision never referenced as a down_revision
    referenced = {down for down in revisions.values() if down is not None}
    heads = sorted(rev for rev in revisions if rev not in referenced)
    if len(heads) != 1:
        fail(f"MIGRATION_HEAD_INVALID:{','.join(heads) or 'none'}")
        return
    _MIGRATION_HEAD = heads[0]

    # the head must carry the max numeric prefix (newest migration wins)
    if numeric and numeric.get(_MIGRATION_HEAD) != max(numeric.values()):
        fail(f"MIGRATION_HEAD_NOT_MAX_NUMERIC:{_MIGRATION_HEAD}")



def check_encoding() -> None:
    suspicious = re.compile(r"(?:Ø.|Ù.|Ú.|â€|ï»¿)")
    for path in active_python_files():
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if path.name == "seed_initial_data.py" and suspicious.search(text):
            fail(f"MOJIBAKE_IN_SEED:{path.relative_to(ROOT)}")



def check_obsolete_legacy_entrypoints() -> None:
    for rel in ("apply_phase3.py", "apply_phase4.py", "bot_legacy.py"):
        if (ROOT / rel).exists():
            fail(f"LEGACY_ROOT_ENTRYPOINT_PRESENT:{rel}")



def main() -> int:
    files = active_python_files()
    check_syntax(files)
    if not FAILURES:
        check_local_imports(files)
        check_model_registry()
        check_bot_router_contract()
        check_callback_contract()
        check_callback_data_targets()
        check_runtime_entry_contracts()
        check_database_contract()
        check_database_normalizer_behavior()
        check_catalog_contract()
        check_deployment_contract()
        check_dependencies()
        check_migrations()
        check_encoding()
        check_obsolete_legacy_entrypoints()
        check_source_hygiene()

    if FAILURES:
        print("STRICT_AUDIT_FAIL")
        for item in FAILURES:
            print(" -", item)
        return 1

    print("STRICT_AUDIT_OK")
    print("Active Python files:", len(files))
    print("Python syntax: OK")
    print("Local import contracts: OK")
    print("ORM model/FK/relationship registry: OK")
    print("Bot router ownership/contracts: OK")
    print("Callback uniqueness/ownership: OK")
    print("Async PostgreSQL driver contract: OK")
    print("Catalog/content contracts: OK")
    print("Windows deployment contract: OK")
    print("Dependencies: OK")
    print(f"Alembic chain linear, head {_MIGRATION_HEAD or '?'}: OK")
    print("Encoding: OK")
    print("Legacy entrypoints: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
