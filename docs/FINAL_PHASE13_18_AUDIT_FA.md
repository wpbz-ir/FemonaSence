# ممیزی نهایی Phase 13 تا 18 — فمونا سنس

## مبنای بررسی
Snapshot واقعی پروژه با ساختار مسیر کامل بررسی شد. Baseline ورودی `0009_production_hardening` بود. Overlay نهایی فقط روی فایل‌های موردنیاز Phase 13 تا 18 اعمال می‌شود؛ `.env`، `.venv`، `.git`، cacheها و Backupهای فازهای قبلی وارد Overlay نمی‌شوند.

## Phase 13 — Production Configuration
- `app/core/config.py`: اعتبارسنجی سختگیرانه Production، HTTPS، Secretها، Webhook، Redis، Storage و Rights Gate.
- `.env.example`: پارامترهای Production و WinaPay/Telegram/Media.
- `Title.rights_verified` و `rights_reference`: جلوگیری از Publish ناخواسته محتوای فاقد مجوز ثبت‌شده.
- Admin API Token با مقایسه constant-time و حداقل طول Production.

## Phase 14 — Runtime Infrastructure
- Runtime Worker مستقل از Bot.
- Notification Worker مستقل.
- Notification claim با `FOR UPDATE SKIP LOCKED`.
- Lease و recovery برای اعلان‌های گیرکرده.
- Reminderهای 7 روز و 1 روز با dedupe key.
- Service heartbeat.
- Redis rate-limit.
- recovery loop دارای exception isolation است تا خطای موقت DB کل Runtime Worker را متوقف نکند.

## Phase 15 — Telegram Media Infrastructure
- Storage Provider مستقل.
- `storage_scope` و `verified_at`.
- Health check برای Chat و Bot membership.
- `TELEGRAM_BOT_API_BASE_URL` برای Cloud/Local Bot API.
- Media Worker مستقل از Bot.
- استفاده از Bot API محلی برای فایل‌های بزرگ در Production توصیه می‌شود.

Telegram مستند می‌کند که `getFile` در Bot API ابری برای دانلود تا 20MB است؛ Local Bot API Server دانلود بدون محدودیت و upload تا 2000MB را فراهم می‌کند.

## Phase 16 — Public Web + HTTPS
- FastAPI Production.
- Security headers.
- Trusted Host در صورت پیکربندی.
- Webhook با `X-Telegram-Bot-Api-Secret-Token`.
- Secret دیگر در URL webhook قرار نمی‌گیرد تا وارد access log و Referer نشود.
- Deduplication و replay کنترل‌شده Telegram updates.
- Caddy reverse proxy.

## Phase 17 — Payments & Subscription
- Telegram Stars موجود باقی می‌ماند.
- WinaPay `PaymentRequest` و `PaymentVerification` جدا شده‌اند.
- مبلغ WinaPay در تومان ذخیره و Verify می‌شود.
- Order settlement دارای row-lock نهایی و Payment reference یکتا است.
- Payment Session opaque و hash شده است.
- Callback با event ledger و idempotency مدیریت می‌شود.
- Wallet credit با external reference یکتا ثبت می‌شود.
- Subscription پس از Verify فعال/تمدید می‌شود.
- Reminder jobs هنگام فعال‌شدن/تمدید اشتراک ساخته می‌شوند.
- Telegram Stars order settlement هم با row-lock و Order idempotency محافظت می‌شود.

مستندات رسمی فعلی WinaPay endpointهای `PaymentRequest` و `PaymentVerification` را معرفی می‌کند، `Amount` را به تومان تعریف می‌کند و Verify موفق را با `Status=100` و `RefID`/`Amount` توصیف می‌کند.

## Phase 18 — Backup, Monitoring & Final Launch
- health live/readiness.
- Service heartbeat.
- Admin audit log.
- Media job events.
- Incident table.
- `pg_dump`/`pg_restore` scripts.
- Windows Scheduled Tasks.
- static audit script.
- Installer با baseline gate، compile قبل از copy، backup و rollback فایل‌های touched.

## Migration Chain نهایی
```text
0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007 → 0008 → 0009
→ 0010 → 0011 → 0012 → 0013 → 0014 → 0015
```

Head نهایی:
```text
0015_production_state
```

## Verification انجام‌شده
- Compile کل Source فعال: موفق.
- تست‌های موجود پروژه: `5 passed`.
- Local import graph: موفق.
- ORM metadata: 57 table در Source نهایی.
- Migration heads: فقط `0015_production_state`.
- `alembic upgrade head --sql`: موفق برای کل chain.
- Static Audit: موفق.
- Installer روی کپی Source Preservation با baseline 0009: در بررسی داخلی Apply/Static Audit موفق.
- بررسی secret files در Final Overlay: موفق.

## محدودیت‌های قابل‌قبول پیش از Launch واقعی
این‌ها «فاز توسعه» ناقص نیستند؛ تنظیمات زیر باید در محیط Production واقعی مقداردهی شوند:
- Bot token و username واقعی.
- PostgreSQL/Neon URL.
- Redis واقعی.
- FFmpeg و FFprobe.
- Telegram Storage Chat.
- Local Bot API Server برای Media بزرگ، در صورت نیاز.
- Domain + HTTPS/Caddy.
- WinaPay Merchant ID و حالت Sandbox/Production.
- `CONTENT_RIGHTS_ACK` و تأیید حقوق برای هر Title.

اجرای شبکه‌ای واقعی WinaPay، Telegram، Redis، Domain/HTTPS و Binaryهای FFmpeg در محیط داخلی این Audit انجام نشده است؛ نتیجه نهایی درباره آن سرویس‌ها بر اساس قرارداد کد و پیکربندی است، نه ادعای اتصال واقعی.
