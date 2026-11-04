import asyncio
import logging
import os
from datetime import datetime

import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ==========================================
# SOZLAMALAR
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "9218262379:AAFsjhnY240_g_rxQrF8IXwvX9KkZNdMXao")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5200398952"))
DB_NAME = "bot_database.db"
PORT = int(os.getenv("PORT", "10000"))

REFERRAL_REWARD = 2
GIFTS = [15, 25, 50, 100]

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN kiritilmagan!")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# Vaqtinchalik jarayonlar
flows = {}


# ==========================================
# DATABASE
# ==========================================

async def db(sql, params=(), one=False, all=False):
    async with aiosqlite.connect(DB_NAME) as con:
        con.row_factory = aiosqlite.Row
        cur = await con.execute(sql, params)

        result = None

        if one:
            row = await cur.fetchone()
            result = dict(row) if row else None

        if all:
            rows = await cur.fetchall()
            result = [dict(x) for x in rows]

        await con.commit()
        return result


async def init_db():
    async with aiosqlite.connect(DB_NAME) as con:

        await con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            referrer INTEGER,
            banned INTEGER DEFAULT 0,
            joined TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS referrals(
            invited INTEGER PRIMARY KEY,
            referrer INTEGER,
            reward INTEGER,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gift INTEGER,
            status TEXT DEFAULT 'Kutilmoqda',
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS channels(
            username TEXT PRIMARY KEY,
            mandatory INTEGER DEFAULT 1,
            ads INTEGER DEFAULT 1
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await con.execute("""
        INSERT OR IGNORE INTO settings(key,value)
        VALUES('referral_reward','2')
        """)

        await con.commit()


async def setting(key, default=""):
    result = await db(
        "SELECT value FROM settings WHERE key=?",
        (key,), one=True
    )
    return result["value"] if result else default


async def get_user(user_id):
    return await db(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,), one=True
    )


async def register(user):
    await db("""
    INSERT OR IGNORE INTO users(user_id,username,name)
    VALUES(?,?,?)
    """, (user.id, user.username or "", user.full_name))

    await db("""
    UPDATE users SET username=?,name=? WHERE user_id=?
    """, (user.username or "", user.full_name, user.id))


def admin(user_id):
    return user_id == ADMIN_ID


# ==========================================
# MENYULAR
# ==========================================

def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👥 Referal"),
                KeyboardButton(text="💰 Mening hisobim")
            ],
            [
                KeyboardButton(text="🎁 Gift yechish"),
                KeyboardButton(text="📦 Mening buyurtmalarim")
            ],
            [
                KeyboardButton(text="💬 Adminga murojaat"),
                KeyboardButton(text="🏆 Reyting")
            ],
            [
                KeyboardButton(text="📜 Tarix"),
                KeyboardButton(text="📖 Qo'llanma")
            ]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📊 Statistika"),
                KeyboardButton(text="👥 Foydalanuvchilar")
            ],
            [
                KeyboardButton(text="📦 Buyurtmalar"),
                KeyboardButton(text="📣 Hammaga xabar")
            ],
            [
                KeyboardButton(text="📢 Reklama kanallari"),
                KeyboardButton(text="📡 Majburiy kanallar")
            ],
            [
                KeyboardButton(text="⚙️ Referal sozlamasi"),
                KeyboardButton(text="💳 Balans boshqaruvi")
            ],
            [
                KeyboardButton(text="🔎 ID qidirish"),
                KeyboardButton(text="⬅️ Foydalanuvchi paneli")
            ]
        ],
        resize_keyboard=True
    )


def gifts_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎁 {x} Stars",
                    callback_data=f"gift:{x}"
                )
            ]
            for x in GIFTS
        ] + [[
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data="cancel"
            )
        ]]
    ])


# ==========================================
# MAJBURIY OBUNA
# ==========================================

async def channels():
    return await db(
        "SELECT * FROM channels WHERE mandatory=1",
        all=True
    )


async def missing(user_id):
    result = []

    for ch in await channels():
        try:
            member = await bot.get_chat_member(
                ch["username"], user_id
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED
            ):
                result.append(ch)

        except Exception:
            result.append(ch)

    return result


async def check_subscription(message, user_id):
    missing_channels = await missing(user_id)

    if not missing_channels:
        return True

    rows = []

    for ch in missing_channels:
        username = ch["username"].lstrip("@")
        rows.append([
            InlineKeyboardButton(
                text=f"📢 {ch['username']}",
                url=f"https://t.me/{username}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="✅ Tekshirish",
            callback_data="check_subs"
        )
    ])

    await message.answer(
        "🔒 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )

    return False


# ==========================================
# REFERAL MUKOFOTI
# ==========================================

async def award_referral(user_id):
    user = await get_user(user_id)

    if not user or not user["referrer"]:
        return

    referrer = user["referrer"]

    if await db(
        "SELECT invited FROM referrals WHERE invited=?",
        (user_id,), one=True
    ):
        return

    if await missing(user_id):
        return

    reward = int(await setting("referral_reward", "2"))

    async with aiosqlite.connect(DB_NAME) as con:
        await con.execute("BEGIN IMMEDIATE")

        cur = await con.execute("""
        INSERT OR IGNORE INTO referrals(invited,referrer,reward)
        VALUES(?,?,?)
        """, (user_id, referrer, reward))

        if cur.rowcount:
            await con.execute("""
            UPDATE users SET balance=balance+?
            WHERE user_id=?
            """, (reward, referrer))

            await con.execute("""
            INSERT INTO transactions(user_id,amount,reason)
            VALUES(?,?,?)
            """, (referrer, reward, "Referal mukofoti"))

        await con.commit()

    await bot.send_message(
        referrer,
        f"🎉 Yangi referalingiz tasdiqlandi!\n⭐ +{reward} Stars"
    )


# ==========================================
# START
# ==========================================

@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user

    await register(user)

    data = await get_user(user.id)

    if data["banned"]:
        await message.answer("⛔ Siz bloklangansiz.")
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) == 2 and not data["referrer"]:
        arg = parts[1]

        if arg.startswith("ref_") and arg[4:].isdigit():
            referrer = int(arg[4:])

            if referrer != user.id and await get_user(referrer):
                await db(
                    "UPDATE users SET referrer=? WHERE user_id=?",
                    (referrer, user.id)
                )

    if not await check_subscription(message, user.id):
        return

    await award_referral(user.id)

    await message.answer(
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "Stars yig'ish botiga xush kelibsiz!",
        reply_markup=admin_menu() if admin(user.id) else user_menu()
    )


@dp.callback_query(F.data == "check_subs")
async def check_subs(callback: CallbackQuery):
    if await check_subscription(
        callback.message, callback.from_user.id
    ):
        await award_referral(callback.from_user.id)

        await callback.message.answer(
            "✅ Obuna tasdiqlandi!",
            reply_markup=admin_menu()
            if admin(callback.from_user.id)
            else user_menu()
        )
    else:
        await callback.answer(
            "Hali barcha kanallarga obuna emassiz!",
            show_alert=True
        )
        return

    await callback.answer()


# ==========================================
# REFERAL
# ==========================================

@dp.message(F.text == "👥 Referal")
async def referral(message: Message):
    if not await check_subscription(message, message.from_user.id):
        return

    me = await bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{message.from_user.id}"
    )

    count = await db(
        "SELECT COUNT(*) AS n FROM referrals WHERE referrer=?",
        (message.from_user.id,), one=True
    )

    user = await get_user(message.from_user.id)

    reward = await setting("referral_reward", "2")

    await message.answer(
        "👥 <b>REFERAL PANELI</b>\n\n"
        f"🔗 Havolangiz:\n<code>{link}</code>\n\n"
        f"👥 Referallar: {count['n']}\n"
        f"⭐ Har bir referal: {reward} Stars\n"
        f"💰 Balansingiz: {user['balance']} Stars"
    )


# ==========================================
# BALANS
# ==========================================

@dp.message(F.text == "💰 Mening hisobim")
async def balance(message: Message):
    if not await check_subscription(message, message.from_user.id):
        return

    user = await get_user(message.from_user.id)

    refs = await db(
        "SELECT COUNT(*) AS n FROM referrals WHERE referrer=?",
        (message.from_user.id,), one=True
    )

    await message.answer(
        "💰 <b>MENING HISOBIM</b>\n\n"
        f"⭐ Balans: {user['balance']} Stars\n"
        f"👥 Referallar: {refs['n']}\n"
        f"🆔 ID: <code>{message.from_user.id}</code>"
    )


# ==========================================
# GIFT
# ==========================================

@dp.message(F.text == "🎁 Gift yechish")
async def gift_menu(message: Message):
    if not await check_subscription(message, message.from_user.id):
        return

    user = await get_user(message.from_user.id)

    await message.answer(
        f"🎁 Gift tanlang.\n"
        f"Balans: {user['balance']} Stars",
        reply_markup=gifts_keyboard()
    )


@dp.callback_query(F.data.startswith("gift:"))
async def choose_gift(callback: CallbackQuery):
    if not await check_subscription(
        callback.message, callback.from_user.id
    ):
        await callback.answer()
        return

    gift = int(callback.data.split(":")[1])

    if gift not in GIFTS:
        await callback.answer("Noto'g'ri gift!", show_alert=True)
        return

    user = await get_user(callback.from_user.id)

    if user["balance"] < gift:
        await callback.answer(
            "Balansingiz yetarli emas!",
            show_alert=True
        )
        return

    flows[callback.from_user.id] = {
        "step": "gift",
        "gift": gift
    }

    await callback.message.answer(
        f"🎁 Tanlangan gift: {gift} Stars\n"
        f"Qoladigan balans: {user['balance'] - gift}\n\n"
        "Tasdiqlaysizmi?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data="gift_confirm"
                ),
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="cancel"
                )
            ]]
        )
    )

    await callback.answer()


@dp.callback_query(F.data == "gift_confirm")
async def gift_confirm(callback: CallbackQuery):
    data = flows.get(callback.from_user.id)

    if not data or data.get("step") != "gift":
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return

    gift = data["gift"]
    uid = callback.from_user.id

    async with aiosqlite.connect(DB_NAME) as con:
        await con.execute("BEGIN IMMEDIATE")

        cur = await con.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (uid,)
        )

        row = await cur.fetchone()

        if not row or row[0] < gift:
            await con.rollback()
            flows.pop(uid, None)
            await callback.answer("Balans yetarli emas!", show_alert=True)
            return

        await con.execute(
            "UPDATE users SET balance=balance-? WHERE user_id=?",
            (gift, uid)
        )

        cur = await con.execute("""
        INSERT INTO orders(user_id,gift)
        VALUES(?,?)
        """, (uid, gift))

        order_id = cur.lastrowid

        await con.execute("""
        INSERT INTO transactions(user_id,amount,reason)
        VALUES(?,?,?)
        """, (uid, -gift, f"Gift buyurtmasi #{order_id}"))

        await con.commit()

    flows.pop(uid, None)

    await bot.send_message(
        ADMIN_ID,
        f"🛒 <b>YANGI BUYURTMA</b>\n\n"
        f"Buyurtma: #{order_id}\n"
        f"User: {uid}\n"
        f"Gift: {gift} Stars\n\n"
        f"/approve {order_id}\n"
        f"/reject {order_id}"
    )

    await callback.message.answer(
        f"✅ Buyurtma #{order_id} qabul qilindi.",
        reply_markup=user_menu()
    )

    await callback.answer()


@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery):
    flows.pop(callback.from_user.id, None)

    await callback.message.answer(
        "❌ Bekor qilindi.",
        reply_markup=user_menu()
    )

    await callback.answer()


# ==========================================
# BUYURTMALAR
# ==========================================

@dp.message(F.text == "📦 Mening buyurtmalarim")
async def my_orders(message: Message):
    orders = await db(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 30",
        (message.from_user.id,), all=True
    )

    if not orders:
        await message.answer("Buyurtmalaringiz mavjud emas.")
        return

    text = "📦 <b>BUYURTMALARIM</b>\n\n"

    for o in orders:
        text += (
            f"#{o['id']} — {o['gift']} Stars\n"
            f"Holati: {o['status']}\n"
            f"Sana: {o['created']}\n\n"
        )

    await message.answer(text)


@dp.message(Command("approve"))
async def approve(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    if not command.args or not command.args.isdigit():
        await message.answer("/approve ORDER_ID")
        return

    oid = int(command.args)

    async with aiosqlite.connect(DB_NAME) as con:
        await con.execute("BEGIN IMMEDIATE")

        cur = await con.execute(
            "SELECT * FROM orders WHERE id=?", (oid,)
        )
        order = await cur.fetchone()

        if not order or order[3] != "Kutilmoqda":
            await con.rollback()
            await message.answer("Buyurtma topilmadi yoki ko'rib chiqilgan.")
            return

        await con.execute(
            "UPDATE orders SET status='Tasdiqlandi' WHERE id=?",
            (oid,)
        )

        await con.commit()

    await bot.send_message(
        order[1],
        f"✅ #{oid} buyurtmangiz tasdiqlandi!"
    )

    await message.answer("Buyurtma tasdiqlandi.")


@dp.message(Command("reject"))
async def reject(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    if not command.args or not command.args.isdigit():
        await message.answer("/reject ORDER_ID")
        return

    oid = int(command.args)

    async with aiosqlite.connect(DB_NAME) as con:
        await con.execute("BEGIN IMMEDIATE")

        cur = await con.execute(
            "SELECT * FROM orders WHERE id=?", (oid,)
        )
        order = await cur.fetchone()

        if not order or order[3] != "Kutilmoqda":
            await con.rollback()
            await message.answer("Buyurtma topilmadi.")
            return

        await con.execute(
            "UPDATE orders SET status='Rad etildi' WHERE id=?",
            (oid,)
        )

        await con.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (order[2], order[1])
        )

        await con.execute("""
        INSERT INTO transactions(user_id,amount,reason)
        VALUES(?,?,?)
        """, (order[1], order[2], f"Rad etilgan buyurtma #{oid}"))

        await con.commit()

    await bot.send_message(
        order[1],
        f"❌ Buyurtma #{oid} rad etildi.\n"
        f"{order[2]} Stars qaytarildi."
    )

    await message.answer("Stars qaytarildi.")


# ==========================================
# REYTING
# ==========================================

@dp.message(F.text == "🏆 Reyting")
async def ranking(message: Message):
    rows = await db("""
    SELECT u.name, u.user_id, COUNT(r.invited) AS total
    FROM users u
    LEFT JOIN referrals r ON r.referrer=u.user_id
    GROUP BY u.user_id
    ORDER BY total DESC
    LIMIT 10
    """, all=True)

    text = "🏆 <b>TOP REFERALLAR</b>\n\n"

    for i, u in enumerate(rows, 1):
        text += f"{i}. {u['name']} — {u['total']} ta\n"

    await message.answer(text)


# ==========================================
# TARIX
# ==========================================

@dp.message(F.text == "📜 Tarix")
async def history(message: Message):
    rows = await db("""
    SELECT * FROM transactions
    WHERE user_id=?
    ORDER BY id DESC LIMIT 20
    """, (message.from_user.id,), all=True)

    if not rows:
        await message.answer("Hozircha operatsiyalar yo'q.")
        return

    text = "📜 <b>OPERATSIYALAR TARIXI</b>\n\n"

    for r in rows:
        text += (
            f"⭐ {r['amount']:+} Stars\n"
            f"Sabab: {r['reason']}\n"
            f"Sana: {r['created']}\n\n"
        )

    await message.answer(text)


# ==========================================
# QO'LLANMA
# ==========================================

@dp.message(F.text == "📖 Qo'llanma")
async def guide(message: Message):
    await message.answer(
        "📖 <b>BOT QO'LLANMASI</b>\n\n"
        "1. Referal havolangizni oling.\n"
        "2. Do'stlaringizni taklif qiling.\n"
        "3. Har bir tasdiqlangan referal uchun Stars oling.\n"
        "4. Gift tanlab buyurtma bering.\n"
        "5. Buyurtmalaringizni kuzatib boring.\n"
        "6. Savollar bo'lsa adminga murojaat qiling."
    )


# ==========================================
# ADMIN: STATISTIKA
# ==========================================

@dp.message(F.text == "📊 Statistika")
async def statistics(message: Message):
    if not admin(message.from_user.id):
        return

    users = await db(
        "SELECT COUNT(*) AS n FROM users", one=True
    )
    orders = await db(
        "SELECT COUNT(*) AS n FROM orders", one=True
    )
    refs = await db(
        "SELECT COUNT(*) AS n FROM referrals", one=True
    )
    pending = await db(
        "SELECT COUNT(*) AS n FROM orders WHERE status='Kutilmoqda'",
        one=True
    )

    await message.answer(
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👤 Foydalanuvchilar: {users['n']}\n"
        f"👥 Referallar: {refs['n']}\n"
        f"📦 Buyurtmalar: {orders['n']}\n"
        f"⏳ Kutilayotgan: {pending['n']}"
    )


# ==========================================
# ADMIN: FOYDALANUVCHILAR
# ==========================================

@dp.message(F.text == "👥 Foydalanuvchilar")
async def users_list(message: Message):
    if not admin(message.from_user.id):
        return

    users = await db(
        "SELECT * FROM users ORDER BY joined DESC LIMIT 30",
        all=True
    )

    for u in users:
        await message.answer(
            f"👤 {u['name']}\n"
            f"ID: <code>{u['user_id']}</code>\n"
            f"Balans: {u['balance']}\n"
            f"Ban: {u['banned']}\n\n"
            f"/ban {u['user_id']}\n"
            f"/unban {u['user_id']}"
        )


@dp.message(Command("ban"))
async def ban(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    if not command.args or not command.args.isdigit():
        await message.answer("/ban USER_ID")
        return

    uid = int(command.args)

    await db(
        "UPDATE users SET banned=1 WHERE user_id=?",
        (uid,)
    )

    await message.answer("⛔ Foydalanuvchi bloklandi.")


@dp.message(Command("unban"))
async def unban(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    if not command.args or not command.args.isdigit():
        await message.answer("/unban USER_ID")
        return

    uid = int(command.args)

    await db(
        "UPDATE users SET banned=0 WHERE user_id=?",
        (uid,)
    )

    await message.answer("✅ Ban olib tashlandi.")


# ==========================================
# ADMIN: BALANS
# ==========================================

@dp.message(F.text == "💳 Balans boshqaruvi")
async def balance_admin(message: Message):
    if not admin(message.from_user.id):
        return

    flows[message.from_user.id] = {"step": "balance"}

    await message.answer(
        "Quyidagi formatda yuboring:\n\n"
        "<code>USER_ID AMOUNT</code>\n\n"
        "Masalan: <code>123456789 20</code>\n"
        "Manfiy son balansdan ayiradi."
    )


# ==========================================
# ADMIN: REFERAL SOZLAMASI
# ==========================================

@dp.message(F.text == "⚙️ Referal sozlamasi")
async def reward_admin(message: Message):
    if not admin(message.from_user.id):
        return

    flows[message.from_user.id] = {"step": "reward"}

    await message.answer(
        f"Hozirgi mukofot: {await setting('referral_reward', '2')} Stars\n"
        "Yangi mukofot miqdorini yuboring."
    )


# ==========================================
# ADMIN: ID QIDIRISH
# ==========================================

@dp.message(F.text == "🔎 ID qidirish")
async def search_admin(message: Message):
    if not admin(message.from_user.id):
        return

    flows[message.from_user.id] = {"step": "search"}

    await message.answer("Foydalanuvchi ID raqamini yuboring.")


# ==========================================
# ADMIN: KANALLAR
# ==========================================

@dp.message(F.text == "📡 Majburiy kanallar")
async def channels_admin(message: Message):
    if not admin(message.from_user.id):
        return

    rows = await db("SELECT * FROM channels", all=True)

    text = "📡 <b>KANALLAR</b>\n\n"

    for c in rows:
        text += (
            f"{c['username']} | "
            f"Majburiy: {c['mandatory']} | "
            f"Reklama: {c['ads']}\n"
        )

    text += (
        "\n/addchannel @kanal\n"
        "/delchannel @kanal"
    )

    await message.answer(text)


@dp.message(Command("addchannel"))
async def add_channel(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    username = (command.args or "").strip()

    if not username.startswith("@"):
        await message.answer("/addchannel @kanal")
        return

    try:
        chat = await bot.get_chat(username)
        member = await bot.get_chat_member(chat.id, bot.id)

        if member.status != ChatMemberStatus.ADMINISTRATOR:
            await message.answer("Bot kanal administratori bo'lishi kerak.")
            return

    except Exception:
        await message.answer("Kanalni tekshirib bo'lmadi.")
        return

    await db("""
    INSERT INTO channels(username,mandatory,ads)
    VALUES(?,1,1)
    ON CONFLICT(username) DO UPDATE SET mandatory=1
    """, (username,))

    await message.answer("✅ Kanal qo'shildi.")


@dp.message(Command("delchannel"))
async def del_channel(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    username = (command.args or "").strip()

    await db(
        "DELETE FROM channels WHERE username=?",
        (username,)
    )

    await message.answer("Kanal o'chirildi.")


# ==========================================
# ADMIN: BROADCAST
# ==========================================

@dp.message(F.text == "📣 Hammaga xabar")
async def broadcast_start(message: Message):
    if not admin(message.from_user.id):
        return

    flows[message.from_user.id] = {"step": "broadcast"}

    await message.answer(
        "Yuboriladigan xabarni yuboring.\n"
        "/cancel — bekor qilish"
    )


@dp.message(Command("cancel"))
async def cancel_flow(message: Message):
    flows.pop(message.from_user.id, None)

    await message.answer(
        "Jarayon bekor qilindi.",
        reply_markup=admin_menu()
        if admin(message.from_user.id)
        else user_menu()
    )


# ==========================================
# ADMIN VA FOYDALANUVCHI XABARLARI
# ==========================================

@dp.message()
async def other_messages(message: Message):
    uid = message.from_user.id

    if not message.from_user:
        return

    user = await get_user(uid)

    if not user:
        await register(message.from_user)
        user = await get_user(uid)

    if user and user["banned"]:
        await message.answer("⛔ Siz bloklangansiz.")
        return

    flow = flows.get(uid, {})

    if flow.get("step") == "reward" and admin(uid):
        if not message.text or not message.text.isdigit():
            await message.answer("Faqat musbat son yuboring.")
            return

        amount = int(message.text)

        if amount < 1 or amount > 100000:
            await message.answer("1 dan 100000 gacha son kiriting.")
            return

        await db("""
        INSERT INTO settings(key,value)
        VALUES('referral_reward',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (str(amount),))

        flows.pop(uid, None)
        await message.answer(f"✅ Referal mukofoti {amount} Stars bo'ldi.")
        return

    if flow.get("step") == "balance" and admin(uid):
        try:
            target, amount = map(int, message.text.split())
        except Exception:
            await message.answer("USER_ID AMOUNT formatida yuboring.")
            return

        if not await get_user(target):
            await message.answer("Foydalanuvchi topilmadi.")
            return

        async with aiosqlite.connect(DB_NAME) as con:
            await con.execute("BEGIN IMMEDIATE")

            cur = await con.execute(
                "SELECT balance FROM users WHERE user_id=?",
                (target,)
            )
            row = await cur.fetchone()

            if row[0] + amount < 0:
                await con.rollback()
                await message.answer("Balans manfiy bo'lib qoladi.")
                return

            await con.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (amount, target)
            )

            await con.execute("""
            INSERT INTO transactions(user_id,amount,reason)
            VALUES(?,?,?)
            """, (target, amount, "Admin balans o'zgarishi"))

            await con.commit()

        flows.pop(uid, None)

        await bot.send_message(
            target,
            f"💰 Balansingiz {amount:+} Stars ga o'zgartirildi."
        )

        await message.answer("Balans yangilandi.")
        return

    if flow.get("step") == "search" and admin(uid):
        if not message.text or not message.text.isdigit():
            await message.answer("ID raqamini yuboring.")
            return

        target = await get_user(int(message.text))
        flows.pop(uid, None)

        if not target:
            await message.answer("Foydalanuvchi topilmadi.")
            return

        await message.answer(
            f"👤 {target['name']}\n"
            f"ID: {target['user_id']}\n"
            f"Balans: {target['balance']}\n"
            f"Ban: {target['banned']}"
        )
        return

    if flow.get("step") == "broadcast" and admin(uid):
        users = await db(
            "SELECT user_id FROM users WHERE banned=0",
            all=True
        )

        sent = 0
        failed = 0

        for u in users:
            try:
                await bot.copy_message(
                    chat_id=u["user_id"],
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                sent += 1
            except Exception:
                failed += 1

            await asyncio.sleep(0.04)

        flows.pop(uid, None)

        await message.answer(
            f"📣 Tarqatish yakunlandi.\n"
            f"Yuborildi: {sent}\n"
            f"Xatolik: {failed}",
            reply_markup=admin_menu()
        )
        return

    if flow.get("step") == "contact":
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📩 Yangi murojaat\n"
                f"Ism: {message.from_user.full_name}\n"
                f"ID: {uid}"
            )

            await bot.copy_message(
                ADMIN_ID,
                message.chat.id,
                message.message_id
            )

            flows.pop(uid, None)

            await message.answer(
                "✅ Murojaatingiz yuborildi.",
                reply_markup=user_menu()
            )

        except Exception:
            await message.answer("Xabar yuborilmadi.")
        return

    if not await check_subscription(message, uid):
        return

    await message.answer(
        "Kerakli bo'limni menyudan tanlang.",
        reply_markup=admin_menu() if admin(uid) else user_menu()
    )


# ==========================================
# ADMIN: MUROJAATLARGA JAVOB
# ==========================================

@dp.message(Command("reply"))
async def reply_user(message: Message, command: CommandObject):
    if not admin(message.from_user.id):
        return

    try:
        parts = (command.args or "").split(maxsplit=1)
        target = int(parts[0])
        text = parts[1]
    except Exception:
        await message.answer(
            "Foydalanish: /reply USER_ID xabar"
        )
        return

    try:
        await bot.send_message(
            target,
            "📩 <b>Admin javobi:</b>\n\n" + text
        )
        await message.answer("Javob yuborildi.")
    except Exception:
        await message.answer("Xabarni yuborib bo'lmadi.")


# ==========================================
# FOYDALANUVCHI PANELIGA QAYTISH
# ==========================================

@dp.message(F.text == "⬅️ Foydalanuvchi paneli")
async def back_user(message: Message):
    if not admin(message.from_user.id):
        return

    await message.answer(
        "Foydalanuvchi paneli.",
        reply_markup=user_menu()
    )


@dp.message(F.text == "💬 Adminga murojaat")
async def contact_start(message: Message):
    if not await check_subscription(message, message.from_user.id):
        return

    flows[message.from_user.id] = {"step": "contact"}

    await message.answer(
        "✍️ Xabaringizni yuboring.\n"
        "/cancel — bekor qilish"
    )


# ==========================================
# HTTP SERVER — RENDER
# ==========================================

async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "telegram-bot"
    })


async def main():
    await init_db()

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    logging.info("HTTP server ishga tushdi: %s", PORT)

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
