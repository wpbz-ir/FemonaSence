# راهنمای استقرار Production — فمونا سنس

این راهنما برای محتوایی است که مالک آن هستید یا مجوز قانونی توزیع آن را دارید. فاز Production عمداً برای دور زدن حقوق صاحب اثر یا محدودیت‌های پلتفرم طراحی نشده است.

## شش فاز نهایی

### Phase 13 — Production Configuration
- اعتبارسنجی سختگیرانه تنظیمات Production.
- جداسازی Secretها از کد و ZIP.
- HTTPS requirement برای `PUBLIC_BASE_URL`.
- حقوق انتشار Title با `rights_verified` و `rights_reference`.

### Phase 14 — Runtime Infrastructure
- Runtime Worker مستقل.
- Notification Worker مستقل.
- Reminderهای اشتراک 7 روزه و 1 روزه.
- Recovery برای jobهای stale.
- Heartbeat سرویس‌ها.
- Redis rate-limit.

### Phase 15 — Telegram Media Infrastructure
- Storage Provider مشخص و قابل health-check.
- پشتیبانی از Bot API محلی از طریق `TELEGRAM_BOT_API_BASE_URL`.
- ثبت `storage_scope` و `verified_at`.
- Media Worker جدا از Bot.

Telegram Bot API رسمی فعلاً برای `getFile` سقف دانلود 20MB دارد. Local Bot API Server امکان دانلود بدون محدودیت و upload تا 2000MB را فراهم می‌کند؛ برای کتابخانه ویدئویی واقعی باید برای فایل‌های بزرگ از زیرساخت مناسب استفاده شود.

### Phase 16 — Public Web + HTTPS
- FastAPI production.
- Security headers.
- Telegram webhook با Secret Token.
- Caddy reverse proxy نمونه.
- Webhook dedupe با `telegram_updates`.

### Phase 17 — Payment / Subscription Production
- Telegram Stars.
- WinaPay با `PaymentRequest` و `PaymentVerification`.
- Payment Session کوتاه‌عمر و opaque.
- Callback/Verify idempotent.
- ذخیره مبلغ WinaPay در تومان؛ نام‌گذاری تاریخی `amount_irr` برای رکوردهای قدیمی حفظ شده است.
- تمدید اشتراک از تاریخ انقضای اشتراک فعال.
- شارژ کیف پول با تبدیل تومان به ریال در Ledger.

مستندات فعلی WinaPay صراحتاً `PaymentRequest` و `PaymentVerification` را در endpointهای REST مربوط معرفی می‌کند و `Amount` را به تومان تعریف می‌کند؛ همچنین Callback دارای `PaymentStatus`, `Authority` و `InvoiceID` است. بنابراین فاز پرداخت از این واحد پولی تبعیت می‌کند.

### Phase 18 — Backup / Monitoring / Final Launch
- health live/readiness.
- heartbeat.
- audit logs.
- Backup/Restore دیتابیس از طریق کنسول رسمی Neon انجام می‌شود؛ اسکریپت `pg_dump`/`pg_restore` عمداً در بسته نهایی وجود ندارد.
- Windows Scheduled Tasks برای Web / Runtime / Media.
- Static Audit نهایی.

## ترتیب اجرا روی Windows

### 1. بررسی نسخه Python
پروژه فعلی برای Python `>=3.14,<3.15` تعریف شده است. Python 3.14.x روی Windows دارای `ProactorEventLoop` پیش‌فرض است؛ Media Worker که FFmpeg subprocess اجرا می‌کند از این مسیر استفاده می‌کند. تمام مسیرهای PostgreSQL async پروژه با SQLAlchemy + `asyncpg` اجرا می‌شوند تا Media Worker بتواند از Proactor پیش‌فرض Windows برای FFmpeg/subprocess استفاده کند.

### 2. نصب وابستگی‌ها
```powershell
cd C:\CinemaVault
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 3. تنظیم `.env`
مقادیر واقعی را در `.env` قرار دهید. هرگز `.env` را داخل ZIP یا Git قرار ندهید.

حداقل Production:
```env
APP_ENV=production
STARTUP_VALIDATE=1
BOT_TOKEN=...
BOT_USERNAME=...
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://127.0.0.1:6379/0
ADMIN_API_TOKEN=...
PUBLIC_BASE_URL=https://YOUR-DOMAIN.example
ALLOWED_HOSTS=YOUR-DOMAIN.example
TELEGRAM_MODE=webhook
TELEGRAM_WEBHOOK_SECRET=...
RUN_MAINTENANCE_IN_BOT=0
RUN_NOTIFICATIONS_IN_BOT=0
TELEGRAM_STORAGE_REQUIRED=1
CONTENT_RIGHTS_REQUIRED=1
CONTENT_RIGHTS_ACK=I_HAVE_DISTRIBUTION_RIGHTS
PRODUCTION_STORAGE_CHAT_ID=-100...
MEDIA_UPSTREAM_ALLOWLIST=cdn.example.com
MEDIA_ALLOW_ANY_HTTPS=0
RATE_LIMIT_ENABLED=1
RATE_LIMIT_FAIL_CLOSED=1
WINAPAY_SANDBOX=1
WINAPAY_MERCHANT_ID=...
FFMPEG_BIN=C:\ffmpeg\bin\ffmpeg.exe
FFPROBE_BIN=C:\ffmpeg\bin\ffprobe.exe
```

### 4. Migration
```powershell
cd C:\CinemaVault
python -m alembic upgrade head
python -m alembic current
```
باید head نهایی `0015_production_state` باشد.

### 5. Redis
برای Redis محلی:
```powershell
cd C:\CinemaVault
docker compose up -d redis
```
یا از Redis مدیریت‌شده استفاده کنید و `REDIS_URL` را تنظیم کنید.

### 6. FFmpeg
`ffmpeg.exe` و `ffprobe.exe` باید قابل اجرا باشند:
```powershell
& $env:FFMPEG_BIN -version
& $env:FFPROBE_BIN -version
```

### 7. Health / Preflight
```powershell
cd C:\CinemaVault
python scripts\check_production.py
python scripts\final_static_audit.py
```

### 8. Webhook
```powershell
cd C:\CinemaVault
python scripts\set_webhook.py
```

برای حذف:
```powershell
python scripts\set_webhook.py --delete
```

### 9. HTTPS
Caddyfile موجود در:
```text
deploy\Caddyfile.template
```
است. دامنه واقعی را جایگزین کنید و Caddy را جلوی Uvicorn قرار دهید.

### 10. Start سرویس‌ها
```powershell
cd C:\CinemaVault
.\deploy\production.ps1
```

یا Task Scheduler:
```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\register_tasks.ps1
```

### 11. بررسی
```powershell
curl https://YOUR-DOMAIN.example/health/live
curl https://YOUR-DOMAIN.example/health/ready
```

### 12. Backup / Restore دیتابیس
Backup و Restore دیتابیس را از داخل **console.neon.tech** انجام دهید.

پروژه عمداً هیچ `pg_dump`/`pg_restore` محلی را در مسیر عملیاتی خود نگه نمی‌دارد تا فرآیند Backup/Restore پایگاه‌داده از سورس برنامه جدا بماند.

## مسیر عملی افزودن Media

1. Media منبع در Storage ثبت می‌شود.
2. Source Release ایجاد و به StorageFile متصل می‌شود.
3. Admin Quality Matrix می‌سازد.
4. Queue برای کیفیت‌های قابل پشتیبانی ایجاد می‌شود.
5. Media Worker فایل را Probe می‌کند.
6. FFmpeg نسخه‌های هدف را می‌سازد.
7. خروجی قبل از انتشار Probe و SHA-256 می‌شود.
8. خروجی در Storage قرار می‌گیرد.
9. Release به `READY` می‌رود.
10. بعد از تأیید مدیر، Release به `PUBLISHED` می‌رود.

کاربر نباید در زمان درخواست دانلود منتظر Transcode بماند.
