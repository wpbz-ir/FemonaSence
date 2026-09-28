from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import and_, or_, select

from app.db.models import Delivery

# [P1-19/کد مرده] تابعِ سازنده‌ی Delivery (ارسال فایل با TTL برای پخش/دانلودِ
# وب) حذف شد: آن جریان فقط مصرف‌کننده‌ی وب بود (web playback برداشته شده) و
# صفر فراخواننده داشت — یعنی هیچ‌وقت ردیف Delivery جدیدی ساخته نمی‌شود.
# مدل Delivery و expire_deliveries طبق الزام حفظ داده نگه داشته شده‌اند: ممکن
# است ردیف‌های ACTIVE قدیمی (دوران وب) در جدول باقی مانده باشند و این تابع تنها
# پاک‌سازی امن آن‌هاست. توجه: expire_deliveries پس از حذف فراخوانی‌اش از
# حلقه‌ی maintenance (جریان مرده + bot.delete_message داخل تراکنش) فعلاً
# هیچ فراخواننده‌ی زنده‌ای ندارد — اگر در آینده هم جریانی Delivery نسازد،
# حذف کامل آن (و در صورت خالی شدن، خود ماژول) در یک تسک آینده بلامانع است.


# [INT-c] Retry policy for DELETE_FAILED rows (the Delivery model has NO
# attempts column, so the cap is implemented with timestamps only):
# - _DELETE_RETRY_COOLDOWN: a failed row is not retried until its updated_at
#   (bumped on every failed attempt by TimestampMixin.onupdate) is at least
#   this old — spaced-out retries instead of hammering Telegram every cycle.
# - _DELETE_RETRY_MAX_AGE: give-up cap — rows whose immutable expires_at is
#   more than this old are never retried again. Measured from expires_at (NOT
#   updated_at) ON PURPOSE: updated_at resets on every failed attempt, so an
#   updated_at-based give-up window could never expire while retries continue.
_DELETE_RETRY_COOLDOWN = timedelta(hours=1)
_DELETE_RETRY_MAX_AGE = timedelta(days=1)


async def expire_deliveries(session, bot: Bot, limit: int = 50):
    """حذف پیام‌های تلگرامی Deliveryهای منقضی (پاک‌سازی ردیف‌های قدیمی وب).

    [INT-c] ردیف‌های DELETE_FAILED هم دوباره تلاش می‌شوند: قبلاً فقط
    status='ACTIVE' انتخاب می‌شد و یک خطای موقت bot.delete_message پیام را برای
    همیشه در چت کاربر جا می‌گذاشت. سقف تلاش‌ها (بدون ستون attempts) با دو مرز
    زمانی بالا پیاده شده است: cooldown بین تلاش‌ها + انصراف نهایی برای ردیف‌هایی
    که بیش از یک روز از انقضایشان گذشته است.
    """
    now = datetime.now(timezone.utc)
    fresh_active = and_(
        Delivery.status == "ACTIVE",
        Delivery.expires_at <= now,
    )
    retry_failed = and_(
        Delivery.status == "DELETE_FAILED",
        Delivery.expires_at <= now,
        # cooldown: only retry rows whose last failed attempt is old enough
        Delivery.updated_at <= now - _DELETE_RETRY_COOLDOWN,
        # give-up cap: stop retrying rows that expired more than a day ago
        Delivery.expires_at >= now - _DELETE_RETRY_MAX_AGE,
    )
    rows = list(
        (
            await session.scalars(
                select(Delivery)
                .where(or_(fresh_active, retry_failed))
                .order_by(Delivery.expires_at.asc())
                .limit(limit)
            )
        ).all()
    )

    completed = 0
    for delivery in rows:
        try:
            await bot.delete_message(
                chat_id=delivery.telegram_chat_id,
                message_id=delivery.telegram_message_id,
            )
            delivery.status = "DELETED"
            delivery.delete_error = None
        except Exception as exc:
            delivery.status = "DELETE_FAILED"
            delivery.delete_error = str(exc)[:2000]
        completed += 1

    if completed:
        await session.commit()
    return completed
