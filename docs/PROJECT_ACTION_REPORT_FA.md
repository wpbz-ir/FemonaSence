# گزارش اقدامات انجام‌شده - فمونا سنس

تاریخ: 2026-09-24

## اقدام‌های انجام‌شده

### 1. تثبیت محیط

- Python 3.14 در معماری پروژه نگه داشته شد.
- aiogram 3.31.0 به‌عنوان Bot framework تثبیت شد.
- ساختار ماژولار Bot ایجاد شد.
- Parse Mode پیش‌فرض روی HTML قرار گرفت تا تگ‌های HTML خام مثل `<b>` در پیام کاربر نمایش داده نشوند.

### 2. بازطراحی هسته Bot

- `main.py` به نقطه ورود استاندارد تبدیل شد.
- Routerهای Bot جدا شدند.
- منوی اصلی توسعه یافت.
- دسته‌های فیلم، سریال، انیمیشن، IMDb، سال تولید، ژانر، مجموعه، بازیگران، علاقه‌مندی، تاریخچه، اشتراک و داشبورد در UI اولیه تعریف شدند.
- Navigation با Callback Query و ویرایش همان پیام طراحی شد.

### 3. مدل داده محتوا

45 جدول/رابطه در Metadata تعریف شد و شامل این دامنه‌هاست:

- User / Role / Permission
- Title / Series / Season / Episode
- Genre / Country / Person / Collection
- Release / Audio Track / Subtitle Track
- Storage Provider / Storage File / Release File
- Transcode Job
- Access Policy / Membership Channel / Membership Rule
- Plan / Subscription / Order / Payment Attempt / Payment / Refund
- Wallet / Wallet Ledger
- Referral
- Favorite / Watch History / Download History
- Notification Template / Notification Job
- Analytics Event / Audit Log

### 4. Release Engine

Release به‌عنوان موجودیت مستقل طراحی شد و کیفیت، Resolution، Codec، Container، FPS، Bitrate، Source، HDR، زبان، زیرنویس، Priority و Status را پوشش می‌دهد.

یک Release می‌تواند چند فایل Storage داشته باشد و یک Storage File نیز از Provider مستقل باشد.

### 5. Hierarchical Collections

Collection با `parent_id` طراحی شد تا ساختارهایی مانند:

```text
Marvel
└── Iron Man
```

یا:

```text
Marvel
└── Avengers
```

قابل پیاده‌سازی باشد.

### 6. بازیگران/عوامل

مدل `Person` و رابطه Many-to-Many برای اتصال افراد به عناوین در نظر گرفته شد.

### 7. سیستم مالی

مدل‌های مستقل برای Plan، Order، PaymentAttempt، Payment، Refund، Subscription و Wallet Ledger ایجاد شد.

### 8. ممیزی WinaPay/VIPSystem

منطق PHP ارسال‌شده به‌عنوان Gateway Adapter تحلیل شد و در کد جدید:

- Token/Secret در کد قرار نگرفت.
- SSL verification غیرفعال نشد.
- Amount برای Verification باید از Order اصلی خوانده شود.
- PaymentAttempt و Authority موجودیت مستقل دارند.
- Idempotency باید از فعال‌سازی چندباره Subscription جلوگیری کند.
- Payment و Refund از Order جدا نگه داشته شدند.

کد PHP VIPSystem مستقیماً وارد پروژه نشده است.

### 9. Subscription Reminder

Jobهای اختصاصی برای:

```text
7 روز قبل
1 روز قبل
```

طراحی و کدنویسی شدند.

`dedupe_key` برای هر Subscription و نوع اعلان یکتا است.

همچنین Backfill برای بازیابی Jobهای ازدست‌رفته پس از Downtime در نظر گرفته شد.

### 10. API Health

دو Endpoint ایجاد شد:

```text
GET /health/live
GET /health/ready
```

`ready` وضعیت PostgreSQL و Redis را بررسی می‌کند.

### 11. Migration

Alembic با یک migration مستقل و self-contained ایجاد شد تا Migration تاریخی به Metadata آینده وابسته نباشد.

Revision:

```text
0001_initial
```

### 12. Seed

Seed اولیه برای Role، Permission، Plan، Genre، Storage Provider و Admin User (در صورت تنظیم ADMIN_USER_ID) ایجاد شد.

### 13. تست ایستا

- Python compilation: PASS
- Schema tests: PASS
- 45 table metadata import: PASS
- Alembic offline SQL generation: PASS

## وضعیت فاز

```text
Environment              ✅
Bot Core                 ✅
HTML Parse Mode          ✅
Domain Model             ✅
Release Model            ✅
Commerce Model           ✅
Subscription Reminder    ✅
Payment Adapter Boundary ✅
Alembic                  ✅
Seed                     ✅
Health API               ✅

PostgreSQL live setup    ⏳ روی سیستم کاربر
Redis live setup         ⏳ روی سیستم کاربر
Admin Panel              ⏳ فاز بعد
Media Ingestion          ⏳ فاز بعد
FFmpeg                   ⏳ فاز بعد
Telegram Storage         ⏳ فاز بعد
Production Deployment    ⏳ فاز بعد
```

## منابع بررسی‌شده

- aiogram PyPI: https://pypi.org/project/aiogram/
- SQLAlchemy PyPI: https://pypi.org/project/SQLAlchemy/2.0.54/
- Alembic PyPI: https://pypi.org/project/alembic/
- FastAPI PyPI: https://pypi.org/project/fastapi/
- psycopg PyPI: https://pypi.org/project/psycopg/3.3.6/
- redis-py PyPI: https://pypi.org/project/redis/
- SQLAlchemy PostgreSQL/psycopg docs: https://docs.sqlalchemy.org/en/20/dialects/postgresql.html
- PostgreSQL Windows installers: https://www.postgresql.org/download/windows/
- WinaPay documentation: https://winapay.io/public/document
- WinaPay download page: https://winapay.io/public/download
