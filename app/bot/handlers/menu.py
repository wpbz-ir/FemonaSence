from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.keyboards.main_menu import main_menu_keyboard
from app.core.brand import WELCOME_TEXT
from app.runtime.db import session_scope
from app.services.rbac import is_super_admin
from app.services.user_account import ensure_user
from app.utils.telegram_ui import edit_or_send

router = Router(name="menu")


@router.callback_query(F.data == "menu:home")
async def home_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # بازگشت به خانه هر وضعیت FSM کهنه (جستجو/نظر/تبلیغ/کد تخفیف) را پاک می‌کند
    # تا پیام‌های بعدی به هندلرهای اشتباه نروند.
    await state.clear()
    if not callback.message:
        return

    async with session_scope() as session:
        user = await ensure_user(session, callback.from_user)
        admin = getattr(user, "status", "ACTIVE") == "ACTIVE" and await is_super_admin(session, user.id)

    # [P1] صفحه‌ی خانه‌ی کاتالوگ معمولاً PHOTO با کپشن است؛ edit_text مستقیم با
    # «There is no text in the message to edit» می‌میرد و دکمه‌ی خانه می‌شود دکمه‌ی
    # مرده. edit_or_send مسیر edit_caption/fallback را هم پوشش می‌دهد.
    await edit_or_send(
        callback,
        f"{WELCOME_TEXT}\n\nمنوی اصلی را انتخاب کنید:",
        reply_markup=main_menu_keyboard(is_admin=admin),
    )


# [P2] هر پنج مسیرِ زیر باید وضعیت FSM کهنه (مثلاً SearchState/CommentState)
# را پاک کنند؛ وگرنه پیام آزادِ بعدیِ کاربر به‌عنوان ورودی آن FSM‌ها بلعیده می‌شود.


@router.callback_query(F.data == "menu:account")
async def account_redirect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.bot.handlers.account import send_account
    await send_account(callback)


@router.callback_query(F.data == "menu:favorites")
async def favorites_redirect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.bot.handlers.account import send_favorites
    await send_favorites(callback)


@router.callback_query(F.data == "menu:history")
async def history_redirect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.bot.handlers.account import send_history
    await send_history(callback)


@router.callback_query(F.data == "menu:wallet")
async def wallet_redirect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.bot.handlers.account import send_wallet
    await send_wallet(callback)


@router.callback_query(F.data == "menu:admin")
async def admin_redirect(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from app.bot.handlers.admin import send_admin
    await send_admin(callback)
