# راه‌اندازی فمونا سنس - فاز پایگاه داده و هسته Bot

## 0) وضعیت مبنا

مسیر پروژه:

```text
C:\CinemaVault
```

Python مورد استفاده:

```text
3.14.6
```

محیط مجازی باید فعال باشد:

```text
(.venv) PS C:\CinemaVault>
```

## 1) دریافت و اعمال بسته پروژه

ZIP را در `C:\CinemaVault` استخراج و فایل‌های پروژه را Merge/Overwrite کن.
فایل `.env` فعلی و پوشه `.venv` را نگه دار.

پس از استخراج:

```powershell
cd C:\CinemaVault
.\.venv\Scripts\Activate.ps1
```

بررسی:

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

باید به‌ترتیب Python 3.14.x و مسیر `C:\CinemaVault\.venv\Scripts\python.exe` را نشان دهد.

## 2) نصب وابستگی‌های فاز جدید

```powershell
python -m pip install -r requirements.txt
```

سپس:

```powershell
python -c "import aiogram, sqlalchemy, alembic, fastapi, psycopg; print('aiogram', aiogram.__version__); print('sqlalchemy', sqlalchemy.__version__); print('alembic', alembic.__version__); print('fastapi', fastapi.__version__); print('psycopg', psycopg.__version__)"
```

## 3) نصب PostgreSQL

از Windows installer رسمی PostgreSQL استفاده کن. pgAdmin هم همراه Installer ارائه می‌شود.

بعد از نصب، یک کاربر و دیتابیس ایجاد کن.

### روش SQL در pgAdmin

Query Tool را باز کن و این‌ها را اجرا کن:

```sql
CREATE USER cinema_vault WITH LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_PASSWORD';
CREATE DATABASE cinema_vault OWNER cinema_vault;
```

اگر کاربر یا دیتابیس از قبل وجود دارد، دستور ساخت دوباره را اجرا نکن.

## 4) تنظیم `.env`

```powershell
notepad .env
```

این مقادیر را نگه دار/اضافه کن:

```env
APP_ENV=development
APP_NAME=فمونا سنس
LOG_LEVEL=INFO

BOT_TOKEN=YOUR_TEST_BOT_TOKEN
ADMIN_USER_ID=YOUR_TELEGRAM_USER_ID

DATABASE_URL=postgresql+psycopg://cinema_vault:YOUR_DB_PASSWORD@localhost:5432/cinema_vault
REDIS_URL=redis://localhost:6379/0

MAIN_CHANNEL_ID=
MAIN_CHANNEL_USERNAME=
PRODUCTION_STORAGE_CHAT_ID=
TEST_STORAGE_CHAT_ID=

WINAPAY_MERCHANT_ID=
WINAPAY_SANDBOX=1
WINAPAY_BASE_URL=https://winapay.io/webservice/rest
```

اگر رمز دیتابیس شامل کاراکترهای رزروشده URL است، آن را URL-encode کن. برای کاهش خطا در محیط توسعه می‌توانی رمز را با حروف و اعداد انتخاب کنی.

## 5) بررسی Syntax

```powershell
python scripts\compile_check.py
```

خروجی مورد انتظار:

```text
COMPILE_OK
```

## 6) بررسی Migration

```powershell
alembic current
```

در دیتابیس تازه ممکن است چیزی نمایش داده نشود. سپس:

```powershell
alembic upgrade head
```

باید revision زیر اعمال شود:

```text
0001_initial
```

سپس:

```powershell
alembic current
```

باید `0001_initial` را به‌عنوان revision جاری نشان دهد.

## 7) Seed اولیه

```powershell
python scripts\seed_initial_data.py
```

خروجی:

```text
SEED_OK
```

Seed اولیه شامل نقش‌ها، مجوزها، پلن‌های اولیه، ژانرها و Provider ذخیره‌سازی Telegram است. اگر `ADMIN_USER_ID` در `.env` وجود داشته باشد، همان User به نقش `SUPER_ADMIN` متصل می‌شود.

## 8) تست اتصال Database و مدل‌ها

```powershell
python scripts\check_project.py
```

نمونه خروجی موفق:

```text
[1/3] SQLAlchemy metadata tables: 45
[2/3] Bot factory: OK
[3/3] Database connection: OK (SELECT 1)
```

## 9) اجرای Bot تست

```powershell
python main.py
```

خروجی:

```text
فمونا سنس TEST Bot is running...
```

در Telegram:

```text
/start
/help
/id
```

و دکمه‌های منوی اصلی را بررسی کن.

برای قطع اجرا، روی صفحه‌کلید `Ctrl+C` را همزمان فشار بده؛ عبارت `Ctrl + C` را داخل PowerShell تایپ نکن.

## 10) اجرای API محلی

در یک PowerShell جدید:

```powershell
cd C:\CinemaVault
.\.venv\Scripts\Activate.ps1
uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

در پنجره دیگر:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health/live | Select-Object -ExpandProperty Content
```

انتظار:

```json
{"status":"LIVE"}
```

سپس:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health/ready | Select-Object -ExpandProperty Content
```

اگر Redis در حال اجرا نباشد، `DEGRADED` طبیعی است و جزئیات DB/Redis را نشان می‌دهد.

## 11) Redis اختیاری برای محیط محلی

اگر Docker Desktop داری:

```powershell
docker run --name cinema-vault-redis -p 6379:6379 -d redis:8
```

سپس `/health/ready` باید DB و Redis را هر دو `OK` و وضعیت را `READY` نشان دهد.

اگر Redis را هنوز راه‌اندازی نکرده‌ای، Bot و Migration این فاز همچنان قابل ادامه‌اند؛ Redis برای readiness کامل و قابلیت‌های صف/قفل در فازهای بعد لازم خواهد شد.

## 12) قواعد نگهداری فایل

- `.env` هرگز وارد Git نشود.
- Bot Token در گزارش، Screenshot یا GitHub قرار نگیرد.
- فایل‌های اصلی رسانه در این فاز داخل پروژه قرار نگیرند.
- `schema.sql` فقط برای بازرسی و مرجع است؛ تغییرات آینده باید با Alembic migration انجام شوند.

## 13) بررسی نهایی فاز

اجرای کامل:

```powershell
python scripts\compile_check.py
python -m pytest -q tests\test_schema.py
alembic current
```

سپس Bot و API را اجرا کن و `/start` و health endpointها را بررسی کن.
