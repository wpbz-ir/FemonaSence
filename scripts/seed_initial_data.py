from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Genre, Permission, Plan, Role, StorageProvider, User, user_roles
from app.db.session import SessionLocal


GENRES = [
    ("اکشن", "Action", "action"),
    ("ماجراجویی", "Adventure", "adventure"),
    ("انیمیشن", "Animation", "animation"),
    ("کمدی", "Comedy", "comedy"),
    ("جنایی", "Crime", "crime"),
    ("درام", "Drama", "drama"),
    ("فانتزی", "Fantasy", "fantasy"),
    ("ترسناک", "Horror", "horror"),
    ("علمی‌تخیلی", "Science Fiction", "science-fiction"),
    ("رمانتیک", "Romance", "romance"),
    ("هیجانی", "Thriller", "thriller"),
    ("مستند", "Documentary", "documentary"),
]

ROLES = [
    ("SUPER_ADMIN", "مدیر ارشد"),
    ("CONTENT_MANAGER", "مدیر محتوا"),
    ("SUPPORT", "پشتیبان"),
    ("FINANCE", "مدیر مالی"),
    ("EDITOR", "ویراستار"),
    ("ANALYST", "تحلیلگر"),
]

PERMISSIONS = [
    ("content.read", "مشاهده محتوا"),
    ("content.write", "ایجاد و ویرایش محتوا"),
    ("release.manage", "مدیریت نسخه‌ها"),
    ("users.read", "مشاهده کاربران"),
    ("users.manage", "مدیریت کاربران"),
    ("finance.read", "مشاهده امور مالی"),
    ("finance.manage", "مدیریت امور مالی"),
    ("settings.manage", "مدیریت تنظیمات"),
    ("audit.read", "مشاهده گزارش حسابرسی"),
]

PLANS = [
    ("FREE", "رایگان", "Free", 0, 0, 0),
    ("BASIC", "پایه", "Basic", 99000, 30, 10),
    ("PRO", "حرفه‌ای", "Professional", 199000, 30, 20),
    ("VIP", "ویژه", "VIP", 399000, 30, 30),
]


async def ensure(session, model, field, value, factory):
    obj = await session.scalar(select(model).where(field == value))
    if obj:
        return obj
    obj = factory()
    session.add(obj)
    await session.flush()
    return obj


async def main() -> None:
    async with SessionLocal() as session:
        for fa, en, slug in GENRES:
            await ensure(
                session,
                Genre,
                Genre.slug,
                slug,
                lambda fa=fa, en=en, slug=slug: Genre(
                    name_fa=fa, name_en=en, slug=slug, active=True
                ),
            )

        for code, description in ROLES:
            await ensure(
                session,
                Role,
                Role.name,
                code,
                lambda code=code, description=description: Role(
                    name=code, description=description
                ),
            )

        for code, description in PERMISSIONS:
            await ensure(
                session,
                Permission,
                Permission.code,
                code,
                lambda code=code, description=description: Permission(
                    code=code, description=description
                ),
            )

        for code, fa, en, price, days, rank in PLANS:
            await ensure(
                session,
                Plan,
                Plan.code,
                code,
                lambda code=code, fa=fa, en=en, price=price, days=days, rank=rank: Plan(
                    code=code,
                    name_fa=fa,
                    name_en=en,
                    price_irr=Decimal(price) * 10,
                    price_toman=Decimal(price),
                    duration_days=days,
                    rank=rank,
                    active=True,
                    sort_order=rank,
                    features={"telegram_stars": int(price) // 1000} if price else {},
                ),
            )

        await ensure(
            session,
            StorageProvider,
            StorageProvider.code,
            "TELEGRAM",
            lambda: StorageProvider(
                code="TELEGRAM",
                name="Telegram Storage",
                provider_type="TELEGRAM_CHANNEL",
                active=True,
                config={},
            ),
        )

        admin_user_id = os.getenv("ADMIN_USER_ID", "").strip()
        if admin_user_id:
            try:
                tg_id = int(admin_user_id)
            except ValueError as exc:
                raise RuntimeError("ADMIN_USER_ID باید عدد صحیح باشد.") from exc

            user = await session.scalar(
                select(User).where(User.telegram_user_id == tg_id)
            )
            if not user:
                user = User(
                    telegram_user_id=tg_id,
                    status="ACTIVE",
                )
                session.add(user)
                await session.flush()

            admin_role = await session.scalar(
                select(Role).where(Role.name == "SUPER_ADMIN")
            )
            if admin_role:
                role_assigned = await session.scalar(
                    select(user_roles.c.user_id).where(
                        user_roles.c.user_id == user.id,
                        user_roles.c.role_id == admin_role.id,
                    )
                )
                if role_assigned is None:
                    await session.execute(
                        user_roles.insert().values(
                            user_id=user.id,
                            role_id=admin_role.id,
                        )
                    )

        await session.commit()
        print("SEED_OK")


if __name__ == "__main__":
    asyncio.run(main())
