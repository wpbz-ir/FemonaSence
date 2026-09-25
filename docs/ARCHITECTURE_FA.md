# معماری مصوب فمونا سنس - فاز 1

## هسته

```text
Python 3.14
aiogram 3.31
FastAPI 0.141
SQLAlchemy 2.0
Alembic 1.20
PostgreSQL
Redis
```

## دامنه محتوا

```text
Title
├── Movie
└── Series
    └── Season
        └── Episode
```

Taxonomy:

```text
Genre
Country
Person
Collection (hierarchical)
```

## Release Engine

```text
Movie/Episode
    ↓
Release
    ├── quality
    ├── resolution
    ├── codecs
    ├── source
    ├── HDR
    ├── audio tracks
    ├── subtitle tracks
    ├── priority
    └── status
```

هر Release می‌تواند به چند StorageFile لینک شود تا بعداً Mirror/Provider مستقل باشد.

## Storage

```text
StorageProvider
    ↓
StorageFile
    ↓
ReleaseFile
    ↓
Release
```

Provider فعلی seed شده:

```text
TELEGRAM
```

Providerهای آینده می‌توانند S3/R2/B2 یا سایر Object Storageها باشند.

## دسترسی

Release با AccessPolicy از عضویت و اشتراک جدا شده است.

```text
User
 ↓
Entitlement / Subscription / Membership
 ↓
Release
```

## مالی

```text
Plan
 ↓
Order
 ↓
PaymentAttempt
 ↓
Payment
 ↓
Subscription
```

و کیف پول:

```text
Wallet
 ↓
WalletLedgerEntry*
```

موجودی قابل حسابرسی است و هر تغییر مهم باید Ledger داشته باشد.

## پرداخت

Adapter Pattern:

```text
PaymentProvider
├── WinaPayProvider
└── TelegramStarsProvider
```

کد PHP مربوط به VIPSystem به پروژه منتقل نشده است؛ منطق پرداخت به‌صورت Adapter مستقل طراحی شده است.

## اعلان اشتراک

```text
Subscription
 ↓
NotificationJob
 ├── SUBSCRIPTION_EXPIRY_7D
 └── SUBSCRIPTION_EXPIRY_1D
```

برای هر اعلان `dedupe_key` یکتا است تا ارسال تکراری پس از Restart/Retry رخ ندهد.

## API Health

```text
/health/live
/health/ready
```

`ready` باید هم Database و هم Redis را سالم ببیند.

## تصمیم کلیدی Release

کیفیت، زبان، زیرنویس و مشخصات فنی در Release مستقل هستند؛ UI از داده موجود ساخته می‌شود. بنابراین اگر فیلم فقط 720p و 1080p داشته باشد، 480p نمایش داده نمی‌شود.

برای تبدیل Master به چند کیفیت، Media Processing/FFmpeg در فاز بعدی اضافه می‌شود.
