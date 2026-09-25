# ممیزی نهایی ایستای CinemaVault — 2026-09-25

این بسته نهایی بر مبنای آرشیو Source پروژه و اصلاحات قابل اتکای دو نسخه بررسی‌شده بازسازی شده است. بسته به‌صورت **overlay** طراحی شده تا فایل‌هایی که در Source archive موجود نبودند (از جمله `app/api/admin_extended.py` در پروژه فعلی) بی‌دلیل حذف نشوند. هیچ فایل `.env` یا Secret واقعی داخل بسته قرار نگرفته است.

## وضعیت ممیزی ایستای نهایی
- فایل‌های پروژه داخل بسته (بدون `.venv` و `__pycache__`): **141**
- فایل `app.py` در کل درخت نهایی: **1 → app/bot/app.py**
- موارد mojibake مشهود در فایل‌های کاربردی: **0** (به‌جز ماژول اصلاح/تشخیص عمدی)
- `scripts/start_local.ps1`: بدون compile/test/health-check/stop-command
- Migration جدید: **خیر**
- reset/drop/seed خودکار DB در لانچر: **خیر**
- تست یا اجرای runtime در این ممیزی: **انجام نشد**؛ تنها خواندن، مقایسه، ویرایش و ممیزی ایستای فایل‌ها انجام شد.

## اصلاحات قطعی انجام‌شده

### پنل مدیریت
مسیرهای مستقل و قابل bookmark:
`/admin/dashboard` · `/admin/users` · `/admin/user360` · `/admin/titles` · `/admin/series` · `/admin/taxonomy` · `/admin/upload` · `/admin/pipelines` · `/admin/jobs` · `/admin/plans` · `/admin/payments` · `/admin/bot-menu` · `/admin/audit` · `/admin/settings`

کلید دسترسی پنل در `sessionStorage` نگهداری و قبل از بارگذاری صفحه با `/api/admin/dashboard` اعتبارسنجی می‌شود. API تنظیمات Secret واقعی یا Merchant ID واقعی را بازنمی‌گرداند.

### مدیریت کاربر
- وضعیت `BLOCKED` و `DISABLED` دیگر با `ensure_user()` به‌صورت ضمنی به `ACTIVE` برنمی‌گردد.
- پنل وب `User 360°` و تغییر وضعیت کاربر را ارائه می‌کند.
- مسیرهای حساس ربات نیز برای کاربر غیرفعال/مسدود محدود شده‌اند.

### Movie / Series / Release
- Movie و Animation از مسیر Title → Release اداره می‌شوند.
- Series به ساختار `Series → Season → Episode → Release` محدود شده است.
- ایجاد `Release` با وضعیت `PUBLISHED` مستقیم ممنوع است؛ ابتدا `READY` و سپس اتصال فایل و انتشار انجام می‌شود.
- انتشار Release بدون `ReleaseFile` فعال مسدود است.
- Release مستقیم روی Title از نوع `SERIES` مسدود و برای Episode اجباری شده است.
- انتشار Title با فعال بودن گیت حقوق محتوا نیازمند `rights_verified` است.

### Upload / Telegram Storage
ورود فایل حجیم طبق معماری فعلی از کانال Storage تلگرام انجام می‌شود. پنل وب **browser multipart uploader** نیست؛ فایل ثبت‌شده در `StorageFile` را به Movie یا Episode متصل می‌کند.
فایل قابل اتصال باید `READY`، دارای `storage_scope=PRODUCTION` و دارای Telegram `file_id` واقعی باشد.

### UI و سرعت ربات
- منوی اصلی دو ستونه و فشرده است.
- ژانر، بازیگران، مجموعه‌ها و سال‌ها grid/pagination دارند.
- زیرمجموعه‌های Collection نیز pagination شده‌اند.
- Callbackهای پرتکرار قبل از DB query، `callback.answer()` می‌گیرند.
- مسیر اشتراک/پرداخت هم early-ack شده است.
- share دیگر برای ساخت لینک به `get_me()` متکی نیست و از `BOT_USERNAME` تنظیم‌شده استفاده می‌کند.

### فارسی و mojibake
- لایه `app/core/text.py` برای اصلاح نمایشی موارد رایج UTF-8/Latin-1 mojibake اضافه شده است.
- تبدیل سراسری و مخرب روی DB انجام نشده است.
- لانچرهای محلی و production متغیرهای UTF-8 را تنظیم می‌کنند.

### Access / Playback
- دسترسی به Media قبل از policy، وضعیت `User.ACTIVE` و وضعیت عمومی Release را بررسی می‌کند.
- release matrix فقط Releaseهای عمومی متعلق به Title عمومی را به ربات می‌دهد.
- مسیر share مستقیم و deep-link عنوان نیز Title منتشرنشده را نمایش نمی‌دهند.

## فایل‌های اصلی و SHA256
- `app/api/admin.py` — `7576907B19FB34FADE4B9AC6D591EA45245EFFBA9D354E56C4E4AC7C5F44DB96` — 47568 bytes
- `app/api/main.py` — `7BE0D34730A759DD56FB109D83B154185B800F02F729A93FC8B0F84F4ECA3959` — 6015 bytes
- `app/api/templates/admin.html` — `060AD5CDB9182F8D85874918D87F15B2DBF6B350CB746A5FEA1F07748E2499E6` — 30283 bytes
- `app/bot/handlers/catalog.py` — `EE1AD77A4FBBBBC0618ABF09AB460373F3DA1E24873E078F35F80EC12F3874D9` — 28288 bytes
- `app/bot/handlers/releases.py` — `9B03DF143647E928375A03F31801859A58A362BDB30A40DC13F198F566901BAA` — 10368 bytes
- `app/bot/handlers/payments.py` — `5FA10BB0318CB3BCE48E3D03377766CC8128AECDA3CB1E9704BCD52DEFE221A5` — 11530 bytes
- `app/bot/handlers/account.py` — `86FA1FAD50FB7531000CB9479B3BFE2E46D6F36C81ADA147EBA73A8217247BB0` — 5034 bytes
- `app/bot/handlers/menu.py` — `D49609C670D6E6C42C931BFAC70BC7A4930C7010DA5021A22185EF160358D860` — 1882 bytes
- `app/bot/handlers/admin.py` — `0C6982862F065E81CFBF3AEC86CBEC6AF9440BFC99B4FB13C451D372D6740F29` — 1791 bytes
- `app/bot/handlers/start.py` — `6E1A36BC3DE73168441ED485CE60BFD80B7147577B18ED4DDE421835A2AFD51E` — 3580 bytes
- `app/bot/keyboards/main_menu.py` — `AE19ECF2F501615A794ACE0B3A5F9E44FBE75043A72946327EF1927D8D345430` — 1675 bytes
- `app/bot/keyboards/paginated.py` — `AA9452C688427C6E4D4B9EF62D0B865ADEEB2B7D15CF9E7EA6150ECFC0C8604E` — 2008 bytes
- `app/services/access.py` — `816614E670956A34B32DDE9D21602F2C25C32A45E9D1A31466D03CFED29013A7` — 1973 bytes
- `app/services/catalog.py` — `EF18D8B9D93673082AB0AAF39E9443BF8B126CA3EF7213D75D500340AEBB6DD0` — 5690 bytes
- `app/services/content_admin.py` — `4F7CA3027043DC44C8F2271FFB797C004E89DA66D3BAB8807225D9EFBCECD35E` — 12007 bytes
- `app/services/content_pipeline.py` — `A7113B80437D4AE4EFB78E8BCB17FAE0B21AEFD7853BF5A17FA3E4DAA63CFBF6` — 9478 bytes
- `app/services/user_account.py` — `62CFC0ED30F8DFF4623F0ABADB0269D93ECA129AA580F6B416C306AC8D1D1A02` — 3221 bytes
- `app/services/audit.py` — `78A17D46E2BEEC5A7FF31AB2C617C888F1D87C7CDBC6AE770333930671B95D73` — 2142 bytes
- `app/services/release_matrix.py` — `89A6E73C7AD18CB5959FEF7803F0A77773913958EBEDEA897B9AF8671DA93A11` — 6074 bytes
- `app/services/media_jobs.py` — `EDAC7631318C9B18DB69D5C2FE15524BFAF20BF16DAE2EFB1CBB53DADB1CC4DD` — 9472 bytes
- `app/core/text.py` — `D32031E12050241CED3D6BF915B4F983885FC7BC824C7E749AD304682D1874DF` — 1245 bytes
- `app/db/session.py` — `69E5DFAA9D396C9638C0DE7562330F7D95266CA21FEC4AD4F6802F7232584A26` — 1051 bytes
- `scripts/start_local.ps1` — `368286399EF27BE650EFA3D29021700087B02BE8724E2EDF39E179F3092CC069` — 629 bytes
- `deploy/production.ps1` — `E14F7D4679125952AE01C472D894AF665BE8D75D8210E61D8FDF381A09867931` — 2888 bytes

## نکته مهم درباره `admin_extended.py`
آرشیو Source در دسترس این ممیزی این فایل را شامل نمی‌کرد. بنابراین فایل حدس زده یا جعلی نشده است. ZIP نهایی را روی `C:\CinemaVault` به صورت **overlay** اعمال کنید و فایل‌های موجود محلی را حذف نکنید.

## دستور اجرای نهایی در Windows PowerShell

پس از دانلود ZIP، مسیر آن را در خط اول جایگزین کنید. این دستورات reset/drop/seed/migration/test/health-check انجام نمی‌دهند:

```powershell
$Zip = "$env:USERPROFILE\Downloads\CinemaVault_FINAL_AUDITED_20260925.zip"
Copy-Item "C:\CinemaVault" "C:\CinemaVault_BACKUP_$(Get-Date -Format yyyyMMdd_HHmmss)" -Recurse
Expand-Archive -Path $Zip -DestinationPath "C:\CinemaVault" -Force
Set-Location "C:\CinemaVault"
.\.venv\Scripts\Activate.ps1
.\scripts\start_local.ps1
```

پنل مدیریت: `http://127.0.0.1:8000/admin/dashboard`

**توجه:** این بسته overlay است؛ اگر `C:\CinemaVault\app\api\admin_extended.py` در پروژه موجود است، آن فایل را نگه دارید.

فایل `docs/FINAL_AUDIT_MANIFEST_FA.txt` فهرست SHA256 و اندازه همه فایل‌های بسته را ارائه می‌کند.
