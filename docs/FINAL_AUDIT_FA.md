# گزارش ممیزی نهایی — Phase 13 تا 18

## Scope
ممیزی روی Snapshot ساختاریافته `CinemaVault_SOURCE_PRESERVED.zip` و Overlay نهایی انجام شد.

## Architecture
```text
Telegram
  ↓
Bot / Webhook
  ↓
Domain Services
  ↓
PostgreSQL
  ├── Catalog / Release
  ├── Access / Subscription
  ├── Payments / Wallet
  ├── Media Jobs
  ├── Notifications
  └── Audit / Operations

Media Worker
  ↓
FFprobe → FFmpeg → Storage

Runtime Worker
  ↓
Notifications / Maintenance / Recovery

Web
  ↓
HTTPS / Health / Admin / Secure Playback / Payment Callback
```

## File-level review groups

### Core / Configuration
- `app/core/config.py`: production validation، webhook، Redis، rights gate، trusted host configuration.
- `app/core/media_config.py`: bounded worker concurrency و Storage fallback.
- `main.py`: polling mode فقط در حالت مناسب؛ Runtime/Notification قابل جداسازی.

### Database / Models
- `app/db/models/content.py`: rights verification metadata.
- `app/db/models/commerce.py`: تومان fields برای WinaPay بدون شکستن legacy IRR.
- `app/db/models/engagement.py`: notification claim lease.
- `app/db/models/media.py`: storage scope و verification timestamp.
- `app/db/models/operations.py`: Payment Session، Payment Webhook Event، Notification Attempt، Telegram Update، Incident.

### Services
- `access.py`: entitlement و plan rank.
- `content_pipeline.py`: matrix و idempotent jobs.
- `media_jobs.py`: `SKIP LOCKED`، lease، retry، cancellation.
- `experience.py`: hashed media tokens.
- `media_proxy.py`: HTTPS upstream allowlist و Range proxy.
- `winapay_billing.py`: atomic payment settlement، subscription یا wallet.
- `notification_jobs.py`: exactly-once scheduling + retry.
- `telegram_storage.py`: storage health.

### API
- `app/api/main.py`: health، security headers، startup validation.
- `app/api/admin.py`: operation controls، plan management، audit.
- `app/api/payments.py`: WinaPay start/callback/Verify flow.
- `app/api/telegram_webhook.py`: secret validation + update dedupe.
- `app/api/media_access.py`: secure playback.

### Workers
- `media_worker.py`: FFmpeg path جدا از Bot.
- `notification_worker.py`: reminder delivery.
- `runtime_worker.py`: maintenance + notification orchestration.

### Deployment
- PowerShell scripts برای startup/stop/backup/restore/scheduled task.
- Caddy TLS proxy template.
- Production runbook.

## Migration chain
```text
0001_initial
 → 0002_deliveries
 → 0003_experience_secure_media
 → 0004_premium_ux_watch_progress
 → 0005_media_processing
 → 0006_content_pipeline
 → 0007_admin_operations
 → 0008_secure_playback
 → 0009_production_hardening
 → 0010_production_content_controls
 → 0011_payment_sessions
 → 0012_notification_runtime
 → 0013_telegram_webhook
 → 0014_storage_controls
 → 0015_production_state
```

## Security controls

- Secrets خارج از Source و ZIP.
- Production HTTPS requirement.
- Opaque short-lived payment/media tokens.
- Media access re-authorization.
- Upstream allowlist در Production.
- Rate limiting قابل fail-closed.
- Admin API token طولانی و Bearer-based.
- Audit trail برای Admin operations.
- Telegram webhook secret.
- Webhook update dedupe.
- Payment webhook idempotency.
- Amount verification با مقدار سفارش.
- Wallet credit با row lock.
- FFmpeg با `create_subprocess_exec` و بدون shell string.
- کیفیت‌ها whitelist شده‌اند.
- فایل خروجی قبل از انتشار Probe و Hash می‌شود.
- Worker lease/heartbeat و stale recovery.
- Content rights gate برای جلوگیری از انتشار ناخواسته محتوای بدون مجوز.

## Concurrency / Performance

Bot request path هیچ Transcode synchronously انجام نمی‌دهد.

Media jobs با PostgreSQL `FOR UPDATE SKIP LOCKED` claim می‌شوند.

Worker concurrency محدود و تنظیم‌پذیر است.

Notification jobs نیز claim lease دارند.

Runtime maintenance از Bot در Production جدا شده است.

## Payment correctness

مستندات فعلی WinaPay، `PaymentRequest` را در
`https://winapay.io/webservice/rest/PaymentRequest` و Verify را در
`https://winapay.io/webservice/rest/PaymentVerification` معرفی می‌کند و Amount را به تومان تعریف کرده است. همچنین Callback شامل PaymentStatus/Authority/InvoiceID است. کد Production بر همین قرارداد کار می‌کند.

## محدودیت‌های واقعی

- `C:\CinemaVault` فقط روی دستگاه کاربر وجود دارد؛ اجرا/استقرار واقعی سرور هنوز نیازمند Domain، HTTPS، Redis، FFmpeg، Storage و دسترسی‌های Telegram است.
- Local Bot API Server یا هر مسیر large-media دیگر یک سرویس زیرساختی جدا است و این بسته binary آن را حمل نمی‌کند.
- WinaPay Production نیازمند Merchant واقعی و Callback URL عمومی HTTPS است.
- صحت عملی شبکه و سرویس‌های بیرونی فقط روی محیط واقعی شما قابل تأیید نهایی است.

## نتیجه

از نظر Source Code و معماری داخلی، Overlay Phase 13 تا 18 یک بسته یکپارچه است و با Baseline `0009_production_hardening` طراحی شده است. پس از Migration، head نهایی باید `0015_production_state` باشد.

در Production، Bot/Web/Runtime/Media باید Processهای جدا باشند و health/readiness پیش از انتشار عمومی بررسی شود.
