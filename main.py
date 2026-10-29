
import asyncio
import logging
import re
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ==================================================
# 1. SOZLAMALAR — O'ZINGIZ TO'LDIRING
# ==================================================
BOT_TOKEN = "8906936395:AAG3UL1jDR7Nz0czP-vyB1SfE4t5nsFp-dE"
ADMIN_ID = 5200398952  # O'Z TELEGRAM IDINGIZNI YOZING

DB_NAME = "bot_database.db"
DEFAULT_REFERRAL_STARS = 2
GIFT_OPTIONS = [15, 25, 50, 100]

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Vaqtinchalik jarayonlar. Asosiy ma'lumotlar SQLite'da saqlanadi.
flows = {}
broadcasts = {}


# ==================================================
# 2. BAZA
# ==================================================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT NULL,
            banned INTEGER DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            invited_id INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL,
            reward INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            gift INTEGER NOT NULL,
            status TEXT DEFAULT 'Kutilmoqda',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            username TEXT PRIMARY KEY,
            mandatory INTEGER DEFAULT 1,
            ads INTEGER DEFAULT 1
        )""")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        await db.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        VALUES ('referral_reward', '2')
        """)

        await db.commit()


async def query(sql, params=(), fetchone=False, fetchall=False):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        result = None
        if fetchone:
            row = await cursor.fetchone()
            result = dict(row) if row else None
        elif fetchall:
            rows = await cursor.fetchall()
            result = [dict(r) for r in rows]
        await db.commit()
        return result


async def get_user(user_id):
    return await query(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,), fetchone=True
    )


async def get_setting(key, default=""):
    row = await query(
        "SELECT value FROM settings WHERE key=?",
        (key,), fetchone=True
    )
    return row["value"] if row else default


async def is_admin(user_id):
    return user_id == ADMIN_ID


async def register_user(user):
    await query("""
        INSERT OR IGNORE INTO users(user_id, username, full_name)
        VALUES (?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.full_name or ""
    ))
    await query("""
        UPDATE users SET username=?, full_name=? WHERE user_id=?
    """, (user.username or "", user.full_name or "", user.id))


# ==================================================
# 3. TUGMALAR
# ==================================================
def user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Referal"),
             KeyboardButton(text="💬 Adminga murojaat")],
            [KeyboardButton(text="🎁 Gift yechish"),
             KeyboardButton(text="💰 Mening hisobim")],
            [KeyboardButton(text="📖 Qo'llanma"),
             KeyboardButton(text="📦 Mening buyurtmalarim")]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Buyurtmalar"),
             KeyboardButton(text="👤 Foydalanuvchilar")],
            [KeyboardButton(text="📣 Hammaga xabar"),
             KeyboardButton(text="📢 Kanallarga reklama")],
            [KeyboardButton(text="📡 Majburiy kanallar"),
             KeyboardButton(text="⚙️ Referal mukofoti")],
            [KeyboardButton(text="📊 Statistika"),
             KeyboardButton(text="🔎 ID bo'yicha foydalanuvchi")],
            [KeyboardButton(text="⬅️ Foydalanuvchi paneli")]
        ],
        resize_keyboard=True
    )


def channel_keyboard(channels):
    rows = []
    for ch in channels:
        username = ch["username"].lstrip("@")
        rows.append([InlineKeyboardButton(
            text=f"📢 @{username}",
            url=f"https://t.me/{username}"
        )])
    rows.append([InlineKeyboardButton(
        text="✅ Obunani tekshirish",
        callback_data="check_subs"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gift_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎁 {gift} gift",
            callback_data=f"gift:{gift}"
        )] for gift in GIFT_OPTIONS
    ] + [[InlineKeyboardButton(
        text="❌ Bekor qilish", callback_data="cancel"
    )]])


def confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Tasdiqlash", callback_data="gift_confirm"
            ),
            InlineKeyboardButton(
                text="❌ Bekor qilish", callback_data="cancel"
            )
        ]
    ])


# ==================================================
# 4. MAJBURIY OBUNA
# ==================================================
async def get_mandatory_channels():
    return await query(
        "SELECT * FROM channels WHERE mandatory=1",
        fetchall=True
    )


async def missing_channels(user_id):
    missing = []
    for ch in await get_mandatory_channels():
        try:
            member = await bot.get_chat_member(ch["username"], user_id)
            if member.status in (
                ChatMemberStatus.LEFT, ChatMemberStatus.KICKED
            ):
                missing.append(ch)
        except Exception:
            # Kanal sozlamasi noto'g'ri bo'lsa ham tekshiruv o'tmaydi.
            missing.append(ch)
    return missing


async def subscription_gate(message_or_callback, user_id):
    missing = await missing_channels(user_id)
    if not missing:
        return True

    text = (
        "🔒 <b>Botdan foydalanish uchun barcha majburiy "
        "kanallarga obuna bo'ling.</b>\n\n"
        "Obuna bo'lgach, «Obunani tekshirish» tugmasini bosing."
    )
    markup = channel_keyboard(missing)

    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=markup)
    else:
        await message_or_callback.message.answer(
            text, reply_markup=markup
        )
        await message_or_callback.answer()
    return False


# ==================================================
# 5. START VA REFERAL
# ==================================================
@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    await register_user(user)

    record = await get_user(user.id)
    if record["banned"]:
        await message.answer("⛔ Siz botdan foydalanishdan cheklangansiz.")
        return

    # Faqat birinchi ro'yxatdan o'tishda referal qabul qilinadi.
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and record["referrer_id"] is None:
        arg = parts[1].strip()
        if arg.startswith("ref_") and arg[4:].isdigit():
            referrer_id = int(arg[4:])
            if referrer_id != user.id and await get_user(referrer_id):
                await query(
                    "UPDATE users SET referrer_id=? WHERE user_id=?",
                    (referrer_id, user.id)
                )

    if not await subscription_gate(message, user.id):
        return

    if await is_admin(user.id):
        await message.answer(
            "🛠 <b>Admin paneliga xush kelibsiz!</b>",
            reply_markup=admin_menu()
        )
    else:
        await message.answer(
            "👋 Xush kelibsiz!\nQuyidagi menyudan foydalaning.",
            reply_markup=user_menu()
        )


@dp.callback_query(F.data == "check_subs")
async def check_subs(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Avval /start bosing.", show_alert=True)
        return
    if user["banned"]:
        await callback.answer("Siz bloklangansiz.", show_alert=True)
        return

    if await subscription_gate(callback, callback.from_user.id):
        await callback.message.answer(
            "✅ Obuna tasdiqlandi!",
            reply_markup=admin_menu() if await is_admin(
                callback.from_user.id
            ) else user_menu()
        )
        await callback.answer("Tayyor!")
    else:
        await callback.answer("Hali barcha kanallarga obuna emassiz.")


# ==================================================
# 6. REFERAL PANELI
# ==================================================
@dp.message(F.text == "👥 Referal")
async def referral_panel(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"

    count = await query(
        "SELECT COUNT(*) AS n FROM referrals "
        "WHERE referrer_id=? AND active=1",
        (message.from_user.id,), fetchone=True
    )
    reward = await get_setting("referral_reward", "5")
    user = await get_user(message.from_user.id)

    await message.answer(
        "👥 <b>Sizning referal panelingiz</b>\n\n"
        f"🔗 Taklif havolangiz:\n<code>{link}</code>\n\n"
        f"✅ Faol referallar: {count['n']}\n"
        f"⭐ Har bir referal uchun: {reward} Stars\n"
        f"💰 Joriy balans: {user['balance']} Stars\n\n"
        "Havolani do'stlaringizga yuboring. Mukofot faqat "
        "yangi foydalanuvchi botga birinchi marta kirganda beriladi."
    )


# ==================================================
# 7. HISOB VA QO'LLANMA
# ==================================================
@dp.message(F.text == "💰 Mening hisobim")
async def my_balance(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return
    user = await get_user(message.from_user.id)
    refs = await query(
        "SELECT COUNT(*) AS n FROM referrals "
        "WHERE referrer_id=? AND active=1",
        (message.from_user.id,), fetchone=True
    )
    await message.answer(
        f"💰 <b>Mening hisobim</b>\n\n"
        f"⭐ Stars: <b>{user['balance']}</b>\n"
        f"👥 Faol referallar: {refs['n']}\n"
        f"🆔 Sizning ID: <code>{message.from_user.id}</code>"
    )


@dp.message(F.text == "📖 Qo'llanma")
async def guide(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return
    await message.answer(
        "📖 <b>Qo'llanma</b>\n\n"
        "1. 👥 Referal bo'limidan havolangizni oling.\n"
        "2. Do'stlaringiz botni shu havola orqali ochsin.\n"
        "3. Har bir tasdiqlangan referal uchun Stars olasiz.\n"
        "4. 🎁 Gift yechish orqali mavjud sovg'alardan birini tanlang.\n"
        "5. Buyurtma holatini «Mening buyurtmalarim»dan ko'ring.\n"
        "6. Savollar uchun «Adminga murojaat» bo'limidan yozing.\n\n"
        "Eslatma: bot bloklanishi yoki kanaldan chiqish aniqlansa, "
        "mukofotlar bekor qilinishi mumkin. Bot chatni shunchaki "
        "o'chirib tashlanganini har doim aniqlay olmaydi."
    )


# ==================================================
# 8. ADMIN BILAN YOZISHMA
# ==================================================
@dp.message(F.text == "💬 Adminga murojaat")
async def contact_admin(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return
    flows[message.from_user.id] = {"step": "contact"}
    await message.answer(
        "✍️ Adminga yubormoqchi bo'lgan xabaringizni yozing.\n"
        "Bekor qilish uchun /cancel yuboring."
    )


@dp.message(Command("cancel"))
async def cancel_flow(message: Message):
    flows.pop(message.from_user.id, None)
    broadcasts.pop(message.from_user.id, None)
    await message.answer("❌ Jarayon bekor qilindi.")


# ==================================================
# 9. GIFT BUYURTMASI
# ==================================================
@dp.message(F.text == "🎁 Gift yechish")
async def gift_menu(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return
    user = await get_user(message.from_user.id)
    await message.answer(
        f"🎁 <b>Gift yechish</b>\n"
        f"Balansingiz: <b>{user['balance']} Stars</b>\n\n"
        "Kerakli giftni tanlang:",
        reply_markup=gift_keyboard()
    )


@dp.callback_query(F.data.startswith("gift:"))
async def choose_gift(callback: CallbackQuery):
    if not await subscription_gate(callback, callback.from_user.id):
        return
    gift = int(callback.data.split(":")[1])
    if gift not in GIFT_OPTIONS:
        await callback.answer("Noto'g'ri tanlov.", show_alert=True)
        return

    user = await get_user(callback.from_user.id)
    if user["balance"] < gift:
        await callback.answer("Balansingiz yetarli emas!", show_alert=True)
        return

    flows[callback.from_user.id] = {
        "step": "gift_confirm",
        "gift": gift
    }
    await callback.message.answer(
        f"🎁 Siz <b>{gift} gift</b> tanladingiz.\n\n"
        "❓ <b>SIZ ROSTAN HAM SHUNI TANLAMOQCHIMISIZ?</b>\n\n"
        f"Narxi: {gift} Stars\n"
        f"Qoladigan balans: {user['balance'] - gift} Stars",
        reply_markup=confirm_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "gift_confirm")
async def confirm_gift(callback: CallbackQuery):
    if not await subscription_gate(callback, callback.from_user.id):
        return
    data = flows.get(callback.from_user.id, {})
    if data.get("step") != "gift_confirm":
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return

    gift = data["gift"]
    user_id = callback.from_user.id
    user = await get_user(user_id)

    if user["balance"] < gift:
        flows.pop(user_id, None)
        await callback.answer("Balans yetarli emas.", show_alert=True)
        return

    # Balansdan mablag' buyurtma yaratilishi bilan yechiladi.
    await query(
        "UPDATE users SET balance=balance-? WHERE user_id=?",
        (gift, user_id)
    )
    await query(
        "INSERT INTO orders(user_id, gift) VALUES (?, ?)",
        (user_id, gift)
    )
    order = await query(
        "SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,), fetchone=True
    )
    flows.pop(user_id, None)

    await bot.send_message(
        ADMIN_ID,
        f"🛒 <b>Yangi gift buyurtmasi!</b>\n"
        f"Buyurtma: #{order['id']}\n"
        f"Foydalanuvchi ID: <code>{user_id}</code>\n"
        f"Gift: {gift}\n"
        f"Holati: Kutilmoqda\n\n"
        f"Buyurtmani boshqarish:\n"
        f"/approve {order['id']}\n"
        f"/reject {order['id']}"
    )
    await callback.message.answer(
        f"✅ Buyurtma #{order['id']} qabul qilindi.\n"
        "Admin ko'rib chiqqach, holatini buyurtmalar bo'limidan "
        "tekshirishingiz mumkin.",
        reply_markup=user_menu()
    )
    await callback.answer("Buyurtma yaratildi!")


@dp.callback_query(F.data == "cancel")
async def cancel_button(callback: CallbackQuery):
    flows.pop(callback.from_user.id, None)
    await callback.message.answer(
        "❌ Bekor qilindi.", reply_markup=user_menu()
    )
    await callback.answer()


@dp.message(F.text == "📦 Mening buyurtmalarim")
async def my_orders(message: Message):
    if not await subscription_gate(message, message.from_user.id):
        return
    orders = await query(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 30",
        (message.from_user.id,), fetchall=True
    )
    if not orders:
        await message.answer("Siz hali buyurtma bermagansiz.")
        return
    text = "📦 <b>Mening buyurtmalarim</b>\n\n"
    for o in orders:
        text += (
            f"🧾 #{o['id']} — {o['gift']} gift\n"
            f"Holati: {o['status']}\n"
            f"Sana: {o['created_at']}\n\n"
        )
    await message.answer(text)


# ==================================================
# 10. ADMIN: BUYURTMALAR
# ==================================================
@dp.message(Command("approve"))
async def approve_order(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /approve BUYURTMA_ID")
        return
    order = await query(
        "SELECT * FROM orders WHERE id=?", (int(parts[1]),),
        fetchone=True
    )
    if not order:
        await message.answer("Buyurtma topilmadi.")
        return
    if order["status"] != "Kutilmoqda":
        await message.answer("Bu buyurtma oldin ko'rib chiqilgan.")
        return
    await query(
        "UPDATE orders SET status='Tasdiqlandi' WHERE id=?",
        (order["id"],)
    )
    await bot.send_message(
        order["user_id"],
        f"✅ #{order['id']} buyurtmangiz admin tomonidan tasdiqlandi."
    )
    await message.answer("Buyurtma tasdiqlandi.")


@dp.message(Command("reject"))
async def reject_order(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /reject BUYURTMA_ID")
        return
    order = await query(
        "SELECT * FROM orders WHERE id=?", (int(parts[1]),),
        fetchone=True
    )
    if not order:
        await message.answer("Buyurtma topilmadi.")
        return
    if order["status"] != "Kutilmoqda":
        await message.answer("Bu buyurtma oldin ko'rib chiqilgan.")
        return

    # Rad etilsa, yechilgan Stars qaytariladi.
    await query(
        "UPDATE orders SET status='Rad etildi' WHERE id=?",
        (order["id"],)
    )
    await query(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (order["gift"], order["user_id"])
    )
    await bot.send_message(
        order["user_id"],
        f"❌ #{order['id']} buyurtmangiz rad etildi.\n"
        f"{order['gift']} Stars balansingizga qaytarildi."
    )
    await message.answer("Buyurtma rad etildi, Stars qaytarildi.")


@dp.message(F.text == "📦 Buyurtmalar")
async def admin_orders(message: Message):
    if not await is_admin(message.from_user.id):
        return
    orders = await query(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 50",
        fetchall=True
    )
    if not orders:
        await message.answer("Hozircha buyurtmalar yo'q.")
        return
    for o in orders:
        await message.answer(
            f"🧾 Buyurtma #{o['id']}\n"
            f"Foydalanuvchi: {o['user_id']}\n"
            f"Gift: {o['gift']}\n"
            f"Holati: {o['status']}\n"
            f"Sana: {o['created_at']}\n\n"
            f"Tasdiqlash: /approve {o['id']}\n"
            f"Rad etish: /reject {o['id']}"
        )


# ==================================================
# 11. ADMIN: BAN / UNBAN
# ==================================================
@dp.message(Command("ban"))
async def ban_user(message: Message):
    if not await is_admin(message.from_user.id):
        return
    target = None
    parts = (message.text or "").split()

    if message.reply_to_message:
        target = message.reply_to_message.from_user.id
    elif len(parts) == 2 and parts[1].isdigit():
        target = int(parts[1])

    if not target:
        await message.answer(
            "Foydalanish: foydalanuvchi xabariga reply qilib /ban "
            "yoki /ban USER_ID"
        )
        return

    await query(
        "UPDATE users SET banned=1 WHERE user_id=?", (target,)
    )
    await message.answer(f"⛔ {target} ban qilindi.")


@dp.message(Command("unban"))
async def unban_user(message: Message):
    if not await is_admin(message.from_user.id):
        return
    target = None
    parts = (message.text or "").split()

    if message.reply_to_message:
        target = message.reply_to_message.from_user.id
    elif len(parts) == 2 and parts[1].isdigit():
        target = int(parts[1])

    if not target:
        await message.answer(
            "Foydalanish: /unban USER_ID yoki xabarga reply qilib /unban"
        )
        return

    await query(
        "UPDATE users SET banned=0 WHERE user_id=?", (target,)
    )
    await message.answer(f"✅ {target} bandan chiqarildi.")


# ==================================================
# 12. ADMIN: FOYDALANUVCHILAR VA STATISTIKA
# ==================================================
@dp.message(F.text == "👤 Foydalanuvchilar")
async def admin_users(message: Message):
    if not await is_admin(message.from_user.id):
        return
    users = await query(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT 100",
        fetchall=True
    )
    if not users:
        await message.answer("Foydalanuvchilar yo'q.")
        return

    await message.answer(
        f"👤 Jami ro'yxatdagi foydalanuvchilar: "
        f"{len(await query('SELECT user_id FROM users', fetchall=True))}\n"
        "Ro'yxat 100 tadan ko'rsatiladi."
    )
    for u in users:
        refs = await query(
            "SELECT COUNT(*) AS n FROM referrals "
            "WHERE referrer_id=? AND active=1",
            (u["user_id"],), fetchone=True
        )
        await message.answer(
            f"👤 {u['full_name']}\n"
            f"Username: @{u['username'] or 'yoq'}\n"
            f"ID: <code>{u['user_id']}</code>\n"
            f"Balans: {u['balance']} Stars\n"
            f"Faol referal: {refs['n']}\n"
            f"Ban: {'Ha' if u['banned'] else 'Yo‘q'}\n"
            f"Bloklangan deb belgilangan: "
            f"{'Ha' if u['blocked'] else 'Yo‘q'}\n\n"
            f"/ban {u['user_id']}  |  /unban {u['user_id']}"
        )


@dp.message(F.text == "📊 Statistika")
async def stats(message: Message):
    if not await is_admin(message.from_user.id):
        return
    users = await query(
        "SELECT COUNT(*) AS n FROM users", fetchone=True
    )
    banned = await query(
        "SELECT COUNT(*) AS n FROM users WHERE banned=1", fetchone=True
    )
    orders = await query(
        "SELECT COUNT(*) AS n FROM orders", fetchone=True
    )
    pending = await query(
        "SELECT COUNT(*) AS n FROM orders WHERE status='Kutilmoqda'",
        fetchone=True
    )
    refs = await query(
        "SELECT COUNT(*) AS n FROM referrals WHERE active=1",
        fetchone=True
    )
    await message.answer(
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👤 Foydalanuvchilar: {users['n']}\n"
        f"⛔ Ban qilinganlar: {banned['n']}\n"
        f"👥 Faol referallar: {refs['n']}\n"
        f"📦 Buyurtmalar: {orders['n']}\n"
        f"⏳ Kutilayotgan buyurtmalar: {pending['n']}"
    )


@dp.message(F.text == "🔎 ID bo'yicha foydalanuvchi")
async def lookup_prompt(message: Message):
    if not await is_admin(message.from_user.id):
        return
    flows[message.from_user.id] = {"step": "lookup"}
    await message.answer("Foydalanuvchi ID raqamini yuboring.")


# ==================================================
# 13. ADMIN: REFERAL MUKOFOTI
# ==================================================
@dp.message(F.text == "⚙️ Referal mukofoti")
async def reward_prompt(message: Message):
    if not await is_admin(message.from_user.id):
        return
    flows[message.from_user.id] = {"step": "reward"}
    current = await get_setting("referral_reward", "5")
    await message.answer(
        f"Hozirgi mukofot: {current} Stars.\n"
        "Har bir yangi referal uchun Stars sonini yuboring."
    )


# ==================================================
# 14. ADMIN: MAJBURIY KANALLAR
# ==================================================
@dp.message(F.text == "📡 Majburiy kanallar")
async def channel_panel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    channels = await query("SELECT * FROM channels", fetchall=True)
    text = "📡 <b>Kanallar</b>\n\n"
    if channels:
        for ch in channels:
            text += (
                f"{ch['username']} — "
                f"majburiy: {'Ha' if ch['mandatory'] else 'Yo‘q'}, "
                f"reklama: {'Ha' if ch['ads'] else 'Yo‘q'}\n"
            )
    else:
        text += "Hozircha kanallar qo'shilmagan.\n"

    await message.answer(
        text +
        "\nBuyruqlar:\n"
        "/addchannel @kanal — qo'shish\n"
        "/delchannel @kanal — o'chirish\n"
        "/adson @kanal — reklamaga yoqish\n"
        "/adsoff @kanal — reklamadan olish\n\n"
        "Bot kanalga admin qilib qo'yilgan bo'lishi kerak."
    )


async def bot_is_channel_admin(username):
    try:
        chat = await bot.get_chat(username)
        member = await bot.get_chat_member(chat.id, bot.id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        )
    except Exception:
        return False


@dp.message(Command("addchannel"))
async def add_channel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].startswith("@"):
        await message.answer("Foydalanish: /addchannel @kanal_username")
        return

    username = parts[1]
    if not await bot_is_channel_admin(username):
        await message.answer("❌ U KANALDA BOT ADMIN EMAS.")
        return

    await query("""
        INSERT INTO channels(username, mandatory, ads)
        VALUES (?, 1, 1)
        ON CONFLICT(username) DO UPDATE SET mandatory=1
    """, (username,))
    await message.answer(
        f"✅ {username} majburiy kanal sifatida qo'shildi.\n"
        "Reklama ham shu kanalga yuborishga yoqilgan."
    )


@dp.message(Command("delchannel"))
async def del_channel(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Foydalanish: /delchannel @kanal_username")
        return
    await query("DELETE FROM channels WHERE username=?", (parts[1],))
    await message.answer("Kanal ro'yxatdan o'chirildi.")


@dp.message(Command("adson"))
async def ads_on(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Foydalanish: /adson @kanal")
        return
    if not await bot_is_channel_admin(parts[1]):
        await message.answer("❌ U KANALDA BOT ADMIN EMAS.")
        return
    await query(
        "INSERT INTO channels(username, mandatory, ads) VALUES (?,0,1) "
        "ON CONFLICT(username) DO UPDATE SET ads=1",
        (parts[1],)
    )
    await message.answer("Kanal reklamalar uchun yoqildi.")


@dp.message(Command("adsoff"))
async def ads_off(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Foydalanish: /adsoff @kanal")
        return
    await query(
        "UPDATE channels SET ads=0 WHERE username=?", (parts[1],)
    )
    await message.answer("Kanalga reklama yuborish o'chirildi.")


# ==================================================
# 15. ADMIN: XABARLAR VA REKLAMA
# ==================================================
@dp.message(F.text == "📣 Hammaga xabar")
async def broadcast_start(message: Message):
    if not await is_admin(message.from_user.id):
        return
    broadcasts[message.from_user.id] = []
    flows[message.from_user.id] = {"step": "broadcast"}
    await message.answer(
        "📣 Yuboriladigan matn, rasm, video yoki stikerni yuboring.\n"
        "Bir nechta xabar ham yuborishingiz mumkin.\n"
        "Yakunlash uchun /done, bekor qilish uchun /cancel."
    )


@dp.message(F.text == "📢 Kanallarga reklama")
async def channel_ads_start(message: Message):
    if not await is_admin(message.from_user.id):
        return
    channels = await query(
        "SELECT * FROM channels WHERE ads=1", fetchall=True
    )
    if not channels:
        await message.answer(
            "Reklama kanallari yo'q. Bot admin bo'lgan kanalni "
            "/adson @kanal orqali qo'shing."
        )
        return
    broadcasts[message.from_user.id] = []
    flows[message.from_user.id] = {"step": "channel_ads"}
    await message.answer(
        "📢 Reklama uchun matn, rasm, video yoki stikerni yuboring.\n"
        "Bir nechta xabar yuborishingiz mumkin.\n"
        "/done — yuborish, /cancel — bekor qilish."
    )


@dp.message(Command("done"))
async def finish_broadcast(message: Message):
    admin_id = message.from_user.id
    if not await is_admin(admin_id):
        return
    flow = flows.get(admin_id, {})
    items = broadcasts.get(admin_id, [])
    if flow.get("step") not in ("broadcast", "channel_ads"):
        await message.answer("Faol reklama jarayoni yo'q.")
        return
    if not items:
        await message.answer("Avval yuboriladigan xabarni kiriting.")
        return

    step = flow["step"]
    sent = 0
    failed = 0

    if step == "broadcast":
        users = await query(
            "SELECT user_id FROM users WHERE banned=0", fetchall=True
        )
        for user in users:
            for source_chat, source_msg in items:
                try:
                    await bot.copy_message(
                        chat_id=user["user_id"],
                        from_chat_id=source_chat,
                        message_id=source_msg
                    )
                    sent += 1
                except Exception:
                    failed += 1
                    # Bloklanganini taxmin qilish mumkin, lekin
                    # shunchaki o'chirilgan chatni aniqlab bo'lmaydi.
                    try:
                        await query(
                            "UPDATE users SET blocked=1 WHERE user_id=?",
                            (user["user_id"],)
                        )
                    except Exception:
                        pass

    else:
        channels = await query(
            "SELECT username FROM channels WHERE ads=1",
            fetchall=True
        )
        for ch in channels:
            for source_chat, source_msg in items:
                try:
                    await bot.copy_message(
                        chat_id=ch["username"],
                        from_chat_id=source_chat,
                        message_id=source_msg
                    )
                    sent += 1
                except Exception:
                    failed += 1

    broadcasts.pop(admin_id, None)
    flows.pop(admin_id, None)
    await message.answer(
        f"✅ Jarayon tugadi.\n"
        f"Yuborilgan xabarlar: {sent}\n"
        f"Yuborilmagan xabarlar: {failed}",
        reply_markup=admin_menu()
    )


# ==================================================
# 16. UMUMIY XABAR HANDLERI
# ==================================================
@dp.message()
async def general_handler(message: Message):
    user_id = message.from_user.id
    flow = flows.get(user_id, {})

    # Admin reply orqali foydalanuvchiga javob:
    # bildirishnomadagi "Foydalanuvchi ID" satriga reply qiling.
    if await is_admin(user_id) and message.reply_to_message:
        replied = message.reply_to_message
        source_text = replied.text or replied.caption or ""
        match = re.search(r"Foydalanuvchi ID:\s*(\d+)", source_text)
        if match and (message.text or "").startswith("/send "):
            target = int(match.group(1))
            answer_text = message.text[6:].strip()
            try:
                await bot.send_message(target, answer_text)
                await message.answer("✅ Javob yuborildi.")
            except Exception as e:
                await message.answer(f"Xabar yuborilmadi: {e}")
            return

    # Admin reply bilan /ban va /unban komandalariga alohida handler bor.
    if await is_admin(user_id) and flow.get("step") in (
        "broadcast", "channel_ads"
    ):
        if message.text and message.text.startswith("/"):
            return
        broadcasts.setdefault(user_id, []).append(
            (message.chat.id, message.message_id)
        )
        await message.answer(
            f"✅ Xabar ro'yxatga olindi. Jami: "
            f"{len(broadcasts[user_id])}\n"
            "/done — tugatish va yuborish."
        )
        return

    # Admin panelidagi ma'lumot kiritish jarayonlari
    if await is_admin(user_id) and flow:
        step = flow.get("step")

        if step == "reward":
            if not message.text or not message.text.isdigit():
                await message.answer("Faqat 0 yoki undan katta butun son yuboring.")
                return
            await query("""
                INSERT INTO settings(key,value) VALUES('referral_reward',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (message.text,))
            flows.pop(user_id, None)
            await message.answer(
                f"✅ Referal mukofoti {message.text} Stars bo'ldi.",
                reply_markup=admin_menu()
            )
            return

        if step == "lookup":
            if not message.text or not message.text.isdigit():
                await message.answer("Raqamli foydalanuvchi ID yuboring.")
                return
            target = int(message.text)
            user = await get_user(target)
            flows.pop(user_id, None)
            if not user:
                await message.answer("Foydalanuvchi bazada topilmadi.")
                return
            refs = await query(
                "SELECT COUNT(*) AS n FROM referrals "
                "WHERE referrer_id=? AND active=1",
                (target,), fetchone=True
            )
            await message.answer(
                f"👤 {user['full_name']}\n"
                f"ID: {target}\n"
                f"Username: @{user['username'] or 'yoq'}\n"
                f"Balans: {user['balance']} Stars\n"
                f"Faol referallar: {refs['n']}\n"
                f"Ban: {user['banned']}\n"
                f"/ban {target} | /unban {target}"
            )
            return

    # Foydalanuvchi adminga murojaat yuboryapti
    if flow.get("step") == "contact":
        try:
            name = message.from_user.full_name
            username = (
                "@" + message.from_user.username
                if message.from_user.username else "yo'q"
            )
            await bot.send_message(
                ADMIN_ID,
                f"📩 <b>Yangi murojaat</b>\n"
                f"Ism: {name}\n"
                f"Username: {username}\n"
                f"Foydalanuvchi ID: {user_id}\n"
                f"Javob berish: xabarga reply qilib "
                f"<code>/send matn</code> yozing."
            )
            await bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            flows.pop(user_id, None)
            await message.answer(
                "✅ Xabaringiz adminga yuborildi.",
                reply_markup=user_menu()
            )
        except Exception:
            await message.answer("Xabarni yuborib bo'lmadi. Keyinroq urinib ko'ring.")
        return

    # Boshqa barcha oddiy xabarlar uchun tekshiruv
    user = await get_user(user_id)
    if user and user["banned"]:
        await message.answer("⛔ Siz botdan foydalanishdan cheklangansiz.")
        return

    if not user:
        await register_user(message.from_user)

    if not await subscription_gate(message, user_id):
        return

    if await is_admin(user_id):
        await message.answer(
            "Admin panelidan kerakli bo'limni tanlang.",
            reply_markup=admin_menu()
        )
    else:
        await message.answer(
            "Menyudagi tugmalardan foydalaning.",
            reply_markup=user_menu()
        )


# ==================================================
# 17. REFERAL MUKOFOTINI BERISH
# ==================================================
async def award_pending_referral(user_id):
    user = await get_user(user_id)
    if not user or not user["referrer_id"]:
        return

    referrer_id = user["referrer_id"]
    existing = await query(
        "SELECT * FROM referrals WHERE invited_id=?",
        (user_id,), fetchone=True
    )
    if existing:
        return

    # Taklif qilingan foydalanuvchi kanallarga obuna bo'lgandagina
    # referalni faollashtiramiz.
    if await missing_channels(user_id):
        return

    reward = int(await get_setting("referral_reward", "5"))
    await query(
        "INSERT OR IGNORE INTO referrals(invited_id,referrer_id,reward,active) "
        "VALUES(?,?,?,1)",
        (user_id, referrer_id, reward)
    )
    await query(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (reward, referrer_id)
    )
    await bot.send_message(
        referrer_id,
        f"🎉 Yangi referal tasdiqlandi!\n+{reward} Stars"
    )


# ==================================================
# 18. POLLING
# ==================================================
async def main():
    if BOT_TOKEN == "BOT_TOKENINGIZNI_BU_YERGA_YOZING":
        raise RuntimeError("BOT_TOKEN qatoriga bot tokenini kiriting.")

    await init_db()

    # Telegram'dagi mavjud foydalanuvchilarning /start so'rovlari
    # va obuna tekshiruvi paytida mukofot beriladi.
    # Eski foydalanuvchilar uchun takroriy mukofot berilmaydi.
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
