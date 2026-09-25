# گزارش نهایی ممیزی Runtime و اصلاحات بحرانی — فمونا سنس (CinemaVault)

**تاریخ ممیزی:** 2026-09-25
**مخزن مقصد:** https://github.com/wpbz-ir/FemonaSence.git
**دامنه:** ممیزی فایل‌به‌فایل طبق `MASTER-PROMPT.md` (۴۵ قاعده) + شبیه‌سازی کامل runtime

---

## ۱) خلاصه اجرایی

پروژه پس از ممیزی ایستای فایل‌به‌فایل (۱۴۲ فایل، ~۹,۶۰۰ خط پایتون)، در محیط شبیه‌سازی‌شده با
**PostgreSQL واقعی (pgserver)**، **پایتون ۳.۱۴**، **aiogram 3.31** و **FastAPI** اجرا و
به‌صورت end-to-end تست شد.

**نتیجه کلیدی:** سه باگ **بحرانی (CRITICAL)** که فقط در اجرای واقعی آشکار می‌شدند پیدا،
اصلاح و با سناریوی E2E تأیید شدند. نسخه نهایی این مخزن، نسخه «تکمیل‌شده و اصلاح‌شده» است.

---

## ۲) باگ‌های بحرانی یافت‌شده و اصلاح‌شده (CRITICAL)

### 🔴 C1 — خطای `NameError: datetime` هنگام Publish در پنل ادمین
- **فایل:** `app/api/admin.py`
- **شرح:** در هندلر publish، از `datetime` استفاده شده بود ولی import نشده بود؛ **هر تلاش برای انتشار اثر** با خطای ۵۰۰ متوقف می‌شد و `published_at` هرگز ذخیره نمی‌شد.
- **فیکس:** افزودن `from datetime import datetime, timezone`.
- **تأیید:** سناریوی ایجاد Title → Publish با پاسخ 200 و ذخیره صحیح `published_at` در DB.

### 🔴 C2 — خطای `TypeError` در «علاقه‌مندی‌ها» و شکست کامل صفحه Title ربات
- **فایل:** `app/bot/handlers/catalog.py` (و `releases.py`)
- **شرح:** فراخوانی توابع favorites با آرگومان‌های پوزیشنی ناسازگار؛ هر فشردن دکمه در صفحه عنوان → استثنا و مرگ callback.
- **فیکس:** اصلاح ۳ فراخوانی favorites + افزودن ۵ گارد UUID + یکدست‌سازی ۲۶ نقطه ویرایش پیام با هلپر جدید `edit_or_send`.
- **فایل جدید:** `app/utils/telegram_ui.py` (هلپر مرکزی ویرایش/ارسال پیام با fallback).
- **تأیید:** E2E با DB واقعی: نمایش caption فارسی، toggle علاقه‌مندی و persistence آن.

### 🔴 C3 — خطای `TypeError: unexpected keyword 'ip'` در تمام عملیات ادمین
- **فایل:** `app/api/admin.py` → `_request_meta()`
- **شرح:** `record_admin_action` فیلد `ip_address` می‌خواند ولی متا با کلید `ip` ساخته می‌شد؛ **تمام عملیات تغییردهنده پنل ادمین** (ایجاد/ویرایش/حذف) با ۵۰۰ شکست می‌خورد و audit log خراب می‌ماند.
- **فیکس:** `ip` → `ip_address` (به‌همراه استفاده از `request.client.host` با گارد None).
- **تأیید:** عملیات ادمین 200 + ثبت audit log با `ip_address` صحیح.

---

## ۳) اصلاحات مهم (HIGH/MEDIUM) — ۱۵ فایل

| # | فایل | اصلاح |
|---|------|-------|
| 1 | `app/api/admin.py` | C1، C3 + سقف `LIMIT 500` روی ۳ کوئری فهرست (جلوگیری از dump کامل جدول) |
| 2 | `app/bot/handlers/catalog.py` | C2 + گاردهای UUID + edit_or_send |
| 3 | `app/bot/handlers/releases.py` | ۶ گارد UUID + edit_or_send |
| 4 | `app/utils/telegram_ui.py` | **جدید** — هلپر edit_or_send |
| 5 | `app/bot/handlers/start.py` | هندلر `/help` |
| 6 | `main.py` | هندلر سراسری `dp.errors` با پیام فارسی + نظارت worker |
| 7 | `app/services/billing.py` | `with_for_update` روی اشتراک Stars (رفع race condition در settle) |
| 8 | `app/services/ffmpeg_engine.py` | timeout 120s برای probe + `asyncio.timeout` برای transcode + kill پروسه |
| 9 | `app/core/media_config.py` | `MEDIA_FFMPEG_TIMEOUT_SECONDS=21600` (قابل تنظیم) |
| 10 | `app/services/telegram_media.py` | انتقال عملیات بلاک‌کننده به `to_thread` (×2) |
| 11 | `app/workers/media_worker.py` | پاک‌سازی workdir در `finally` (بدون نشت فایل موقت) |
| 12 | `app/services/content_admin.py` | clamp پارامتر `limit` |
| 13 | `app/api/main.py` | `/health/ready` با کش ۳۰ ثانیه |
| 14 | `app/services/media_access.py` | rate limit برای stateهای party |
| 15 | `scripts/seed_initial_data.py` | اصلاح seed: `price_toman` صفر → شکست پرداخت بانکی؛ semantics `price_irr`؛ افزودن فیچرهای `telegram_stars` |

> نکته: هندلرهای `/paysupport` و subscription که ابتدا اضافه شده بود، با فیکس‌های `payments.py` ادغام و از دوباره‌کاری حذف شد.

---

## ۴) محیط شبیه‌سازی و نتایج اجرا

| مرحله | نتیجه |
|-------|-------|
| نصب وابستگی‌ها (Python 3.14.7 + uv) | ✅ همه dependencyها نصب شد |
| PostgreSQL واقعی (pgserver embedded) | ✅ بالا آمد |
| اجرای ۱۵ migration Alembic | ✅ ۶۴ جدول ساخته شد |
| Seed اولیه (متون فارسی UTF-8 + قیمت‌ها) | ✅ |
| بوت uvicorn (API) | ✅ |
| گیت‌های احراز هویت (401/403/200) | ✅ |
| E2E ادمین: ایجاد Title و Publish | ✅ (پس از فیکس C1/C3) |
| E2E ربات: صفحه Title با caption فارسی | ✅ (پس از فیکس C2) |
| Toggle علاقه‌مندی + persistence | ✅ |
| پرداخت Stars: order → settle → اشتراک ACTIVE ۳۰ روزه | ✅ |
| Idempotency بازپخش callback پرداخت | ✅ بدون رکورد تکراری (۱ پرداخت، ۱ اشتراک) |
| Wiring دیسپچر ربات (۶ روتر) | ✅ |
| `python -m compileall` کل پروژه | ✅ بدون خطا |

**یافته‌های تأییدشده سالم (بدون تغییر):** idempotency پرداخت، state machine پایپ‌لاین مدیا، محافظت webhook replay.

---

## ۵) وضعیت امنیتی

- ✅ هیچ توکن/Secret هاردکد در کد وجود ندارد؛ همه از `.env` خوانده می‌شوند.
- ✅ `.env` واقعی commit نشده؛ فقط `.env.example` با مقادیر placeholder (`CHANGE_ME`).
- ✅ `.gitignore` شامل `.env`، `.venv`، `__pycache__`، لاگ‌ها.
- ✅ API تنظیمات ادمین، Secret واقعی یا Merchant ID را بازنمی‌گرداند.
- ✅ روت‌های مستقل پنل ادمین با اعتبارسنجی کلید در `sessionStorage`.
- ✅ وضعیت `BLOCKED`/`DISABLED` کاربر دیگر به‌طور ضمنی به `ACTIVE` برنمی‌گردد.

---

## ۶) شروع به کار (Startup Commands)

```bash
# 1) محیط مجازی
python3.14 -m venv .venv && source .venv/bin/activate

# 2) نصب
pip install -r requirements.txt

# 3) تنظیمات
cp .env.example .env   # مقادیر BOT_TOKEN / DATABASE_URL / ADMIN_USER_ID را پر کنید

# 4) دیتابیس
alembic upgrade head
python scripts/seed_initial_data.py

# 5) اجرا
python main.py            # ربات (polling) + workers
uvicorn app.api.main:app --host 0.0.0.0 --port 8000   # API + پنل ادمین
```

`TELEGRAM_MODE=webhook` نیز پشتیبانی می‌شود (تنظیم `TELEGRAM_WEBHOOK_SECRET` و `PUBLIC_BASE_URL`).

---

## ۷) جمع‌بندی

پروژه در نسخه فعلی این مخزن **تکمیل‌شده، بدون باگ بحرانی شناخته‌شده و تست‌شده در شبیه‌سازی E2E**
است. سه باگ بحرانی C1/C2/C3 به‌همراه ۱۲ اصلاح High/Medium اعمال و همه سناریوهای حیاتی
(انتشار محتوا، نمایش ربات، علاقه‌مندی، پرداخت Stars با idempotency، audit log) تأیید شده‌اند.
