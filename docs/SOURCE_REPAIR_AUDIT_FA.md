# گزارش ممیزی و پاک‌سازی نهایی سورس — فمونا سنس

## منبع بررسی‌شده

فایل ورودی: `CinemaVault_SOURCE_20260924_120702.zip`

SHA-256 ورودی:
`78189225f09c87d7edfda2df09dca8398f54eaf8ff867e8b4f7a305753c79aee`

## مشکل ساختاری قطعی

فایل ورودی سورس را به‌صورت تودرتو نگه داشته بود. مسیرهای اصلی در آن به‌شکل زیر بودند:

- `app/app/...`
- `alembic/alembic/...`
- `scripts/scripts/...`
- `tests/tests/...`
- `docs/docs/...`
- `deploy/deploy/...`

علاوه بر آن، یک namespace قدیمی در `app/` با handlerها و configهای قدیمی نیز در کنار سورس جدید وجود داشت. این دو ساختار نباید هم‌زمان در ریشه پروژه فعال باشند.

## اصلاح انجام‌شده

در بسته نهایی، سورس canonical به ریشه صحیح منتقل شده است:

- `app/...`
- `alembic/...`
- `scripts/...`
- `tests/...`
- `deploy/...`
- `docs/...`

namespaceهای قدیمی و فایل‌های restore/phase تکراری حذف شده‌اند.

## اصلاح runtime مهم

`app/runtime/db.py` اصلاح شد تا `session_scope()` یک تراکنش کوتاه‌عمر واقعی با رفتار زیر فراهم کند:

1. `commit` در پایان موفق scope
2. `rollback` در صورت هر استثنا یا cancellation
3. حفظ `SessionLocal` و engine مشترک

این اصلاح برای مسیرهایی که `ensure_user`، تغییر وضعیت تماشای گروهی، maintenance و سایر عملیات را انجام می‌دهند ضروری است؛ در نسخه ورودی، `session_scope` صرفاً session را باز و بسته می‌کرد و عملیات بدون `commit` می‌توانست پایدار نشود.

## قراردادهای بررسی‌شده

- Python syntax: OK
- Local import contracts: OK
- ORM registry / FK / relationships: OK
- Bot router ownership: OK
- Callback uniqueness/ownership: OK
- Async PostgreSQL normalization: OK
- Catalog/content contracts: OK
- Windows deployment contract: OK
- Dependency contract: OK
- Alembic revisions 0001..0015 and final head: OK
- Encoding: OK
- Secrets excluded from source package: OK
- Known legacy namespace absent: OK
- Selector event-loop shim absent from runtime: OK
- HTTP route duplicate scan: 0 duplicates
- Bot exact callback duplicate scan: 0 duplicates
- `compileall` across app/scripts/tests/main.py: OK

## وضعیت نهایی دیتابیس

این بسته هیچ migration جدیدی اعمال نمی‌کند و migration موجود را تغییر نمی‌دهد. زنجیره سورس تا `0015_production_state` حفظ شده است.

Backup/Restore دیتابیس در خارج از سورس و از طریق Neon انجام می‌شود.

## محتویات عمداً حذف‌شده

- namespace قدیمی `app/config.py`, `app/handlers`, `app/keyboards`
- launcherهای قدیمی root برای bot/media/runtime
- installerهای قدیمی phase 3/4
- `bot_legacy.py`
- نسخه پشتیبان `production.ps1.bak_*`
- کپی‌های تودرتوی `alembic`, `scripts`, `tests`, `docs`, `deploy`
- اسکریپت‌های محلی `pg_dump` / `pg_restore`
- runbookهای قدیمی Phase 3/4 و `NEXT_PHASE.md`

فایل‌های `.env` و `.venv` جزو بسته نهایی نیستند و باید روی دستگاه محلی حفظ شوند.
