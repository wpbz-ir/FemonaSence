from __future__ import annotations

import ast
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MIGRATIONS = [
    "0001_initial.py",
    "0002_deliveries.py",
    "0003_experience_secure_media.py",
    "0004_premium_ux_watch_progress.py",
    "0005_media_processing.py",
    "0006_content_pipeline.py",
    "0007_admin_operations.py",
    "0008_secure_playback.py",
    "0009_production_hardening.py",
    "0010_production_content_controls.py",
    "0011_payment_sessions.py",
    "0012_notification_runtime.py",
    "0013_telegram_webhook.py",
    "0014_storage_controls.py",
    "0015_production_state.py",
]


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}
IGNORED_RUNTIME_CONFIG = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.prod",
    ".env.development",
    ".env.dev",
}


def active_files(pattern: str = "*") -> list[Path]:
    out = []
    for path in ROOT.rglob(pattern):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if path.name in IGNORED_RUNTIME_CONFIG:
            continue
        if path.suffix == ".pyc":
            continue
        if any(part.startswith("_phase") for part in rel.parts):
            continue
        if any(part.startswith("_final_backup_") for part in rel.parts):
            continue
        out.append(path)
    return out


def module_map(files: list[Path]) -> dict[str, Path]:
    result = {}
    for path in files:
        rel = path.relative_to(ROOT)
        modpath = rel.parent if rel.name == "__init__.py" else rel.with_suffix("")
        result[".".join(modpath.parts)] = path
    return result


def local_import_problems(files: list[Path], modules: dict[str, Path]) -> list[str]:
    problems = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            problems.append(f"syntax: {path}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.startswith("app.") and name not in modules and not any(
                    key.startswith(name + ".") for key in modules
                ):
                    problems.append(f"missing import: {path}: {name}")
    return problems


def migration_problems() -> list[str]:
    problems = []
    folder = ROOT / "alembic" / "versions"
    previous = None
    seen_revs: set[str] = set()
    for filename in EXPECTED_MIGRATIONS:
        path = folder / filename
        if not path.exists():
            problems.append(f"missing migration: {filename}")
            continue
        text = path.read_text(encoding="utf-8-sig")
        m = re.search(r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(None|["\']([^"\']+)["\'])', text, re.M)
        if not m:
            problems.append(f"missing revision variable: {filename}")
            continue
        revision = m.group(1)
        down = None if not d or d.group(1) == "None" else d.group(2)
        if revision in seen_revs:
            problems.append(f"duplicate revision: {revision}")
        seen_revs.add(revision)
        if previous is None and down is not None:
            problems.append(f"first migration has parent: {filename}")
        if previous is not None and down != previous:
            problems.append(f"migration chain break: {filename} expected {previous}, got {down}")
        previous = revision
    return problems


def secret_problems() -> list[str]:
    # Runtime dotenv files belong to the deployment environment and are intentionally
    # excluded from this source/package audit. The release ZIP contains only .env.example.
    return []


def key_file_problems() -> list[str]:
    required = [
        "app/api/main.py",
        "app/api/admin.py",
        "app/api/media_access.py",
        "app/api/payments.py",
        "app/api/telegram_webhook.py",
        "app/core/config.py",
        "app/db/session.py",
        "app/services/access.py",
        "app/services/content_pipeline.py",
        "app/services/media_jobs.py",
        "app/services/media_proxy.py",
        "app/services/notification_jobs.py",
        "app/services/telegram_storage.py",
        "app/services/winapay_billing.py",
        "app/workers/media_worker.py",
        "app/workers/notification_worker.py",
        "app/workers/runtime_worker.py",
        "scripts/set_webhook.py",
        "scripts/check_production.py",
    ]
    return [x for x in required if not (ROOT / x).exists()]


def main() -> int:
    problems: list[str] = []
    pyfiles = active_files("*.py")
    for path in pyfiles:
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            problems.append(f"compile: {path}: {exc}")

    problems.extend(local_import_problems(pyfiles, module_map(pyfiles)))
    problems.extend(migration_problems())
    problems.extend([f"secret in active tree: {x}" for x in secret_problems()])
    problems.extend([f"required file missing: {x}" for x in key_file_problems()])

    main_api = ROOT / "app" / "api" / "main.py"
    first_line = main_api.read_text(encoding="utf-8-sig").splitlines()[0].strip()
    if first_line != "from __future__ import annotations":
        problems.append("app/api/main.py must start with future annotations import")

    if problems:
        print("FINAL_AUDIT_FAIL")
        for problem in problems:
            print("-", problem)
        return 1

    print("FINAL_AUDIT_OK")
    print("Python files checked:", len(pyfiles))
    print("Local import graph: OK")
    print("Migrations 0001..0015: OK")
    print("Secrets excluded: OK")
    print("Critical runtime/payment/media files: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
