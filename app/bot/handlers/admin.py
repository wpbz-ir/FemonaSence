from __future__ import annotations

from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from app.bot.keyboards.main_menu import back_home_keyboard
from app.core.brand import BRAND_NAME_FA
from app.db.models import Order, Release, Subscription, Title, User
from app.runtime.db import session_scope
from app.services.rbac import is_super_admin
from app.services.user_account import ensure_user


async def send_admin(callback: CallbackQuery):
    await callback.answer()
    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        if getattr(user, "status", "ACTIVE") != "ACTIVE" or not await is_super_admin(session, user.id):
            await callback.message.edit_text("دسترسی مدیریت ندارید.", reply_markup=back_home_keyboard())
            return

        users = await session.scalar(select(func.count()).select_from(User))
        titles = await session.scalar(select(func.count()).select_from(Title))
        releases = await session.scalar(select(func.count()).select_from(Release))
        subscriptions = await session.scalar(select(func.count()).select_from(Subscription))
        orders = await session.scalar(select(func.count()).select_from(Order))

    await callback.message.edit_text(
        f"<b>🛠 مرکز مدیریت {BRAND_NAME_FA}</b>\n\n"
        f"👤 کاربران: <b>{users or 0}</b>\n"
        f"🎬 عناوین: <b>{titles or 0}</b>\n"
        f"📦 نسخه‌ها: <b>{releases or 0}</b>\n"
        f"💎 اشتراک‌ها: <b>{subscriptions or 0}</b>\n"
        f"💳 سفارش‌ها: <b>{orders or 0}</b>\n\n"
        "مدیریت محتوا و Storage از پنل وب انجام می‌شود.",
        reply_markup=back_home_keyboard(),
    )
