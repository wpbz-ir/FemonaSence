# Runbook نهایی Phase 13 تا 18 — فمونا سنس

## وضعیت پیش از نصب
این بسته باید روی پروژه‌ای اعمال شود که Migration فعلی آن `0009_production_hardening` است. اگر پروژه از قبل به `0015_production_state` رسیده باشد، Installer با پیام `FINAL_13_18_ALREADY_APPLIED` بدون تغییر متوقف می‌شود.

## 0) ورود به محیط پروژه
```powershell
cd C:\CinemaVault
.\.venv\Scripts\Activate.ps1
```

## 1) استخراج بسته نهایی
فایل دقیق باید در Downloads باشد:

`CinemaVault_Final_Phase13_18.zip`

```powershell
cd C:\CinemaVault
Remove-Item .\_final_13_18 -Recurse -Force -ErrorAction SilentlyContinue
mkdir _final_13_18 -Force

Expand-Archive `
  -Path "$env:USERPROFILE\Downloads\CinemaVault_Final_Phase13_18.zip" `
  -DestinationPath "C:\CinemaVault\_final_13_18" `
  -Force
```

## 2) Backup پایگاه‌داده قبل از نصب
فایل `deploy\backup_neon.ps1` بعد از اعمال Overlay در پروژه قرار می‌گیرد؛ بنابراین برای Backup **قبل از نصب** از اسکریپت داخل خود بسته استفاده کنید.
```powershell
cd C:\CinemaVault
$env:DATABASE_URL = python -c "from dotenv import dotenv_values; v=dotenv_values('.env').get('DATABASE_URL'); print(v or '')"
if (-not $env:DATABASE_URL) { throw "DATABASE_URL is not configured in .env" }
powershell -ExecutionPolicy Bypass -File .\_final_13_18\preinstall_backup_neon.ps1
```

خروجی موفق باید با `PREINSTALL_BACKUP_OK` شروع شود. اگر `DATABASE_URL` خالی است، قبل از ادامه `.env` را کامل کنید. همچنین `pg_dump` باید در PATH باشد.

## 3) نصب شش Phase
```powershell
cd C:\CinemaVault\_final_13_18
python .\apply_final_13_18.py --project C:\CinemaVault
```

خروجی موفق:
```text
FINAL_13_18_APPLY_OK
```

Installer قبل از Copy، Overlay را Compile می‌کند، Backup فایل‌های touched را می‌سازد و در صورت شکست Audit بعد از Copy، Rollback انجام می‌دهد.

## 4) نصب Migrationهای نهایی
```powershell
cd C:\CinemaVault
python -m alembic upgrade head
```

## 5) بررسی Head
```powershell
python -m alembic current
```

خروجی مورد انتظار:
```text
0015_production_state (head)
```

## 6) Audit نهایی
```powershell
python .\scripts\final_static_audit.py
```

خروجی مورد انتظار:
```text
FINAL_AUDIT_OK
Python files checked: ...
Local import graph: OK
Migrations 0001..0015: OK
Secrets excluded: OK
Critical runtime/payment/media files: OK
```

## 7) نصب/به‌روزرسانی وابستگی‌ها
```powershell
cd C:\CinemaVault
python -m pip install -r requirements.txt
```

## 8) تنظیم Production در `.env`
نمونه حداقلی:
```env
APP_ENV=production
STARTUP_VALIDATE=1
BOT_TOKEN=YOUR_BOT_TOKEN
BOT_USERNAME=YOUR_BOT_USERNAME
DATABASE_URL=YOUR_NEON_DATABASE_URL
REDIS_URL=redis://127.0.0.1:6379/0
ADMIN_API_TOKEN=LONG_RANDOM_SECRET_AT_LEAST_32_CHARS
PUBLIC_BASE_URL=https://YOUR-DOMAIN.example
ALLOWED_HOSTS=YOUR-DOMAIN.example
TELEGRAM_MODE=webhook
TELEGRAM_WEBHOOK_SECRET=LONG_RANDOM_SECRET_AT_LEAST_16_CHARS
RUN_MAINTENANCE_IN_BOT=0
RUN_NOTIFICATIONS_IN_BOT=0
TELEGRAM_STORAGE_REQUIRED=1
PRODUCTION_STORAGE_CHAT_ID=-100XXXXXXXXXX
TELEGRAM_STORAGE_CHAT_ID=-100XXXXXXXXXX
CONTENT_RIGHTS_REQUIRED=1
CONTENT_RIGHTS_ACK=I_HAVE_DISTRIBUTION_RIGHTS
RATE_LIMIT_ENABLED=1
RATE_LIMIT_FAIL_CLOSED=1
ALLOW_PREMIUM_WATCH_PARTY=0
MEDIA_UPSTREAM_ALLOWLIST=cdn.example.com
MEDIA_ALLOW_ANY_HTTPS=0
WINAPAY_SANDBOX=1
WINAPAY_MERCHANT_ID=YOUR_MERCHANT_ID
FFMPEG_BIN=C:\ffmpeg\bin\ffmpeg.exe
FFPROBE_BIN=C:\ffmpeg\bin\ffprobe.exe
TELEGRAM_BOT_API_BASE_URL=https://api.telegram.org
TELEGRAM_LOCAL_FILE_ROOT=
MEDIA_WORK_ROOT=C:\CinemaVault\media_work
MEDIA_WORKER_CONCURRENCY=1
```

در حالت واقعی WinaPay، پس از انجام بررسی Sandbox:
```env
WINAPAY_SANDBOX=0
```

## 9) Redis
برای Redis محلی:
```powershell
cd C:\CinemaVault
docker compose up -d redis
```

## 10) FFmpeg / FFprobe
```powershell
& $env:FFMPEG_BIN -version
& $env:FFPROBE_BIN -version
```

## 11) Production Preflight
```powershell
cd C:\CinemaVault
python .\scripts\check_production.py
```

نتیجه باید `READY` باشد.

## 12) Webhook
در `.env`، `TELEGRAM_MODE=webhook` و Secret را تنظیم کنید، سپس:
```powershell
cd C:\CinemaVault
python .\scripts\set_webhook.py
```

Webhook:
```text
https://YOUR-DOMAIN.example/telegram/webhook
```

برای حذف Webhook:
```powershell
python .\scripts\set_webhook.py --delete
```

## 13) HTTPS / Caddy
فایل:
```text
C:\CinemaVault\deploy\Caddyfile.template
```

دامنه واقعی را جایگزین `example.com` کنید و Caddy را جلوی Uvicorn قرار دهید.

## 14) اجرای Production
```powershell
cd C:\CinemaVault
.\deploy\production.ps1
```

این اسکریپت Web API، Runtime Worker و Media Worker را جدا اجرا می‌کند. در Webhook mode، Bot polling اجرا نمی‌شود.

## 15) Task Scheduler
```powershell
cd C:\CinemaVault
powershell -ExecutionPolicy Bypass -File .\deploy\register_tasks.ps1
```

برای حذف Taskها:
```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\unregister_tasks.ps1
```

## 16) Health
```powershell
curl https://YOUR-DOMAIN.example/health/live
curl https://YOUR-DOMAIN.example/health/ready
```

## 17) Admin
پنل مدیریتی در این مسیر است:
```text
https://YOUR-DOMAIN.example/api/admin/ui
```

APIهای اصلی:
```text
GET    /api/admin/dashboard
GET    /api/admin/titles
POST   /api/admin/titles
PATCH  /api/admin/titles/{title_id}
POST   /api/admin/titles/{title_id}/pipeline
GET    /api/admin/pipelines
GET    /api/admin/jobs
GET    /api/admin/jobs/{job_id}
POST   /api/admin/jobs/{job_id}/retry
POST   /api/admin/jobs/{job_id}/cancel
PATCH  /api/admin/jobs/{job_id}/priority
```

## 18) Backup دوره‌ای
```powershell
cd C:\CinemaVault
$env:DATABASE_URL = python -c "from dotenv import dotenv_values; v=dotenv_values('.env').get('DATABASE_URL'); print(v or '')"
powershell -ExecutionPolicy Bypass -File .\deploy\backup_neon.ps1
```

Restore فقط با تأیید صریح:
```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\restore_neon.ps1 -BackupFile C:\CinemaVault\backups\FILE.dump
```

## 19) جریان واقعی ثبت فیلم
```text
Storage
↓
Source Release
↓
Rights Verified
↓
Quality Matrix
↓
Media Queue
↓
FFmpeg
↓
Probe + SHA256
↓
Telegram Storage
↓
Release READY
↓
Admin Publish
↓
Release PUBLISHED
```

Transcoding در لحظه درخواست کاربر انجام نمی‌شود.

## 20) Telegram Media بزرگ
Bot API ابری Telegram طبق مستندات رسمی فعلی در `getFile` سقف دانلود 20MB دارد؛ Local Bot API Server دانلود بدون محدودیت و upload تا 2000MB را فراهم می‌کند. برای Media واقعی پروژه این تفاوت مهم است. در حالت Local Bot API، مقدار `TELEGRAM_LOCAL_FILE_ROOT` را برابر ریشه واقعی فایل‌های Local Bot API روی همان سرور قرار دهید تا Player بتواند با Range امن فایل را سرو کند. پیش از مهاجرت به Local Bot API باید مطابق مستندات Telegram خروج از Cloud Bot API انجام شود.

## 21) WinaPay
WinaPay در مستندات رسمی فعلی `PaymentRequest` و `PaymentVerification` را در REST معرفی می‌کند. `Amount` به تومان تعریف شده و Verify موفق با `Status=100` و `RefID` گزارش می‌شود. کد پروژه Verify مبلغ را دوباره با سفارش تطبیق می‌دهد.

## 22) حقوق محتوا
این زیرساخت فقط باید برای محتوایی استفاده شود که مالک آن هستید یا مجوز قانونی توزیع آن را دارید. برای Publish در Production، Rights Gate باید تأیید شده باشد.
