

import asyncio
import logging
import os
import random
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ==========================================================
#                       ⚙️ CONFIG
# ==========================================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"           # 👈 এখানে আপনার টোকেন বসান
ADMIN_IDS = [123456789]                     # 👈 আপনার Telegram ID বসান
FORCE_JOIN_CHANNELS = []                    # উদাহরণ: ["@channel1", "@channel2"]
DB_PATH = "bot_database.db"

# Economy
REFERRAL_BONUS = 50
WITHDRAW_MIN = 500
WITHDRAW_CURRENCIES = ["USDT", "JazzCash", "Easypaisa", "Binance"]

# Ludo
LUDO_BET_AMOUNT = 100
HOME_POSITION = 57
TOKENS_PER_PLAYER = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("ludo_bot")


# ==========================================================
#                       🗄️ DATABASE
# ==========================================================
class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    balance INTEGER DEFAULT 0,
                    referral_count INTEGER DEFAULT 0,
                    referred_by INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    reward INTEGER DEFAULT 10,
                    url TEXT,
                    is_active INTEGER DEFAULT 1
                )""")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_tasks (
                    user_id INTEGER,
                    task_id INTEGER,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, task_id)
                )""")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    currency TEXT,
                    wallet_address TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            await db.commit()

    async def user_exists(self, uid: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)) as c:
                return await c.fetchone() is not None

    async def create_user(self, uid, username, full_name, referred_by=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, full_name, referred_by)"
                " VALUES (?,?,?,?)",
                (uid, username, full_name, referred_by),
            )
            if referred_by and referred_by != uid:
                await db.execute(
                    "UPDATE users SET referral_count=referral_count+1,"
                    " balance=balance+? WHERE user_id=?",
                    (REFERRAL_BONUS, referred_by),
                )
                await db.execute(
                    "INSERT INTO transactions (user_id, amount, type, description)"
                    " VALUES (?,?, 'referral', ?)",
                    (referred_by, REFERRAL_BONUS, f"Referral from {uid}"),
                )
            await db.commit()

    async def get_user(self, uid) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c:
                r = await c.fetchone()
                return dict(r) if r else None

    async def get_balance(self, uid) -> int:
        u = await self.get_user(uid)
        return u["balance"] if u else 0

    async def get_referral_count(self, uid) -> int:
        u = await self.get_user(uid)
        return u["referral_count"] if u else 0

    async def update_balance(self, uid, amount, ttype="adjust", desc=""):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description)"
                " VALUES (?,?,?,?)",
                (uid, amount, ttype, desc),
            )
            await db.commit()

    async def get_transactions(self, uid, limit=10):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM transactions WHERE user_id=?"
                " ORDER BY created_at DESC LIMIT ?", (uid, limit)
            ) as c:
                return [dict(r) for r in await c.fetchall()]

    async def get_all_users(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users") as c:
                return [dict(r) for r in await c.fetchall()]

    async def get_user_count(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c:
                r = await c.fetchone()
                return r[0] if r else 0

    async def get_active_tasks(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE is_active=1") as c:
                return [dict(r) for r in await c.fetchall()]

    async def get_task(self, tid):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks WHERE id=?", (tid,)) as c:
                r = await c.fetchone()
                return dict(r) if r else None

    async def has_completed_task(self, uid, tid) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM user_tasks WHERE user_id=? AND task_id=?", (uid, tid)
            ) as c:
                return await c.fetchone() is not None

    async def complete_task(self, uid, tid, reward):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO user_tasks (user_id, task_id) VALUES (?,?)",
                (uid, tid),
            )
            await db.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?", (reward, uid)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description)"
                " VALUES (?,?, 'task', ?)",
                (uid, reward, f"Task #{tid} reward"),
            )
            await db.commit()

    async def add_task(self, title, description, reward, url=""):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "INSERT INTO tasks (title, description, reward, url) VALUES (?,?,?,?)",
                (title, description, reward, url),
            )
            await db.commit()
            return c.lastrowid

    async def create_withdrawal(self, uid, amount, currency, wallet) -> int:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "INSERT INTO withdrawals (user_id, amount, currency, wallet_address)"
                " VALUES (?,?,?,?)",
                (uid, amount, currency, wallet),
            )
            await db.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, amount, type, description)"
                " VALUES (?,?, 'withdraw', ?)",
                (uid, -amount, f"Withdraw {currency} → {wallet}"),
            )
            await db.commit()
            return c.lastrowid

    async def get_pending_withdrawals(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM withdrawals WHERE status='pending' ORDER BY created_at"
            ) as c:
                return [dict(r) for r in await c.fetchall()]

    async def set_withdrawal_status(self, wid, status):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM withdrawals WHERE id=?", (wid,)
            ) as c:
                r = await c.fetchone()
                if not r:
                    return None
                wd = dict(r)
            await db.execute(
                "UPDATE withdrawals SET status=? WHERE id=?", (status, wid)
            )
            if status == "rejected" and wd["status"] == "pending":
                await db.execute(
                    "UPDATE users SET balance=balance+? WHERE user_id=?",
                    (wd["amount"], wd["user_id"]),
                )
                await db.execute(
                    "INSERT INTO transactions (user_id, amount, type, description)"
                    " VALUES (?,?, 'refund', ?)",
                    (wd["user_id"], wd["amount"], f"Withdraw #{wid} refund"),
                )
            await db.commit()
            return wd


db = Database(DB_PATH)


# ==========================================================
#                    🧩 LUDO ENGINE
# ==========================================================
@dataclass
class GameState:
    game_id: str
    player_id: int
    player_tokens: List[int] = field(default_factory=lambda: [0] * TOKENS_PER_PLAYER)
    ai_tokens: List[int] = field(default_factory=lambda: [0] * TOKENS_PER_PLAYER)
    dice: int = 0
    turn: str = "player"
    finished: bool = False
    winner: Optional[str] = None
    last_message: str = ""


class LudoEngine:
    def __init__(self):
        self.games: Dict[str, GameState] = {}

    def create_game(self, gid, uid):
        g = GameState(game_id=gid, player_id=uid)
        self.games[gid] = g
        return g

    def get_user_game(self, uid) -> Optional[GameState]:
        for g in self.games.values():
            if g.player_id == uid and not g.finished:
                return g
        return None

    def delete_game(self, gid):
        self.games.pop(gid, None)

    @staticmethod
    def _advance(token, dice):
        if token == 0:
            return 1 if dice == 6 else 0
        np = token + dice
        if np > HOME_POSITION:
            return token
        return np

    @staticmethod
    def _best_token(tokens, dice):
        cands = []
        for i, t in enumerate(tokens):
            if t == 0 and dice == 6:
                cands.append((i, 1))
            elif 0 < t < HOME_POSITION:
                np = t + dice
                if np <= HOME_POSITION:
                    cands.append((i, np))
        if not cands:
            return None
        cands.sort(key=lambda x: (x[1] != HOME_POSITION, -x[1]))
        return cands[0][0]

    @staticmethod
    def _check_win(tokens):
        return all(t == HOME_POSITION for t in tokens)

    def roll_dice(self, gid):
        g = self.games.get(gid)
        if not g or g.finished:
            return 0
        g.dice = random.randint(1, 6)
        return g.dice

    def player_move(self, gid):
        g = self.games.get(gid)
        if not g or g.finished:
            return {"error": "no_game"}
        if g.turn != "player":
            return {"error": "not_your_turn"}
        if g.dice == 0:
            return {"error": "roll_first"}

        dice = g.dice
        idx = self._best_token(g.player_tokens, dice)

        if idx is None:
            g.last_message = f"⚠️ {dice} দিয়ে কোনো মুভ নেই।"
        else:
            g.player_tokens[idx] = self._advance(g.player_tokens[idx], dice)
            g.last_message = f"🎲 আপনি {dice} পেয়ে টোকেন #{idx + 1} সরালেন।"

        if self._check_win(g.player_tokens):
            g.finished = True
            g.winner = "player"
            return {"winner": "player"}

        if dice == 6 and idx is not None:
            g.dice = 0
            g.turn = "player"
            return {"bonus": True}

        g.dice = 0
        g.turn = "ai"
        return {"turn": "ai"}

    def ai_turn(self, gid):
        g = self.games.get(gid)
        if not g or g.finished:
            return {"error": "no_game"}

        dice = random.randint(1, 6)
        idx = self._best_token(g.ai_tokens, dice)

        if idx is not None:
            g.ai_tokens[idx] = self._advance(g.ai_tokens[idx], dice)
            g.last_message = f"🤖 AI {dice} পেয়ে টোকেন #{idx + 1} সরালো।"
        else:
            g.last_message = f"🤖 AI {dice} পেয়েছে, কোনো মুভ নেই।"

        if self._check_win(g.ai_tokens):
            g.finished = True
            g.winner = "ai"
            return {"winner": "ai", "ai_dice": dice}

        if dice == 6 and idx is not None:
            g.turn = "ai"
        else:
            g.turn = "player"

        return {"ai_dice": dice, "turn": g.turn}


engine = LudoEngine()


# ==========================================================
#                    🔒 FORCE JOIN
# ==========================================================
async def is_subscribed(bot: Bot, uid: int) -> bool:
    for ch in FORCE_JOIN_CHANNELS:
        try:
            m = await bot.get_chat_member(chat_id=ch, user_id=uid)
            if m.status in ("left", "kicked"):
                return False
        except Exception as e:
            log.warning("force_join check failed for %s: %s", ch, e)
    return True


async def require_join_msg(message: Message, bot: Bot) -> bool:
    if not FORCE_JOIN_CHANNELS:
        return True
    if await is_subscribed(bot, message.from_user.id):
        return True
    await message.answer(
        "🚫 <b>বট ব্যবহার করতে চ্যানেলে জয়েন করুন!</b>\n\n"
        "নিচের চ্যানেলগুলোতে জয়েন করে <b>✅ I've Joined</b> চাপুন।",
        reply_markup=force_join_kb(),
        parse_mode="HTML",
    )
    return False


# ==========================================================
#                    ⌨️ KEYBOARDS
# ==========================================================
def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 Play Ludo"), KeyboardButton(text="💰 Wallet")],
            [KeyboardButton(text="📋 Tasks"), KeyboardButton(text="👥 Referrals")],
        ],
        resize_keyboard=True,
    )


def force_join_kb():
    btns = []
    for ch in FORCE_JOIN_CHANNELS:
        clean = ch.lstrip("@")
        btns.append([InlineKeyboardButton(text=f"📢 Join {ch}", url=f"https://t.me/{clean}")])
    btns.append([InlineKeyboardButton(text="✅ I've Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def wallet_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw"),
            InlineKeyboardButton(text="📜 History", callback_data="wallet_history"),
        ],
    ])


def withdraw_currency_kb():
    btns = [[InlineKeyboardButton(text=c, callback_data=f"wd_cur:{c}")]
            for c in WITHDRAW_CURRENCIES]
    btns.append([InlineKeyboardButton(text="❌ Cancel", callback_data="withdraw_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def tasks_kb(tasks):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📌 {t['title']}  (+{t['reward']})",
            callback_data=f"task:{t['id']}"
        )] for t in tasks
    ])


def ludo_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Play vs AI", callback_data="ludo_start")],
    ])


def ludo_game_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Roll Dice", callback_data="ludo_roll")],
        [InlineKeyboardButton(text="🏁 Give Up", callback_data="ludo_quit")],
    ])


def referral_kb(bot_username, uid):
    link = f"https://t.me/{bot_username}?start=ref_{uid}"
    share = f"https://t.me/share/url?url={link}&text=Play%20Ludo%20and%20earn%20coins!"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Open Link", url=link)],
        [InlineKeyboardButton(text="📤 Share", url=share)],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Users", callback_data="admin_users"),
            InlineKeyboardButton(text="📊 Stats", callback_data="admin_stats"),
        ],
        [InlineKeyboardButton(text="💸 Withdrawals", callback_data="admin_wd")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
    ])


def admin_wd_kb(items):
    btns = []
    for w in items:
        btns.append([
            InlineKeyboardButton(text=f"✅ #{w['id']}", callback_data=f"wd_approve:{w['id']}"),
            InlineKeyboardButton(text=f"❌ #{w['id']}", callback_data=f"wd_reject:{w['id']}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=btns)


# ==========================================================
#                    🚦 ROUTER + STATES
# ==========================================================
router = Router()


class WithdrawStates(StatesGroup):
    currency = State()
    amount = State()
    wallet = State()


class Broadcast(StatesGroup):
    waiting = State()


def is_admin(uid):
    return uid in ADMIN_IDS


# ==========================================================
#                    🚀 START + REFERRALS
# ==========================================================
@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, command: CommandObject):
    user = message.from_user

    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            rid = int(command.args.replace("ref_", ""))
            if rid != user.id:
                referred_by = rid
        except ValueError:
            pass

    if not await require_join_msg(message, bot):
        return

    if not await db.user_exists(user.id):
        await db.create_user(user.id, user.username or "", user.full_name or "", referred_by)

    bal = await db.get_balance(user.id)
    refs = await db.get_referral_count(user.id)

    await message.answer(
        f"👋 <b>স্বাগতম, {user.full_name}!</b>\n\n"
        f"💰 ব্যালেন্স: <b>{bal}</b> কয়েন\n"
        f"👥 রেফারেল: <b>{refs}</b>\n\n"
        "নিচের মেনু থেকে অপশন বেছে নিন।",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "check_join")
async def check_join_cb(cb: CallbackQuery, bot: Bot):
    if await is_subscribed(bot, cb.from_user.id):
        try:
            await cb.message.edit_text("✅ ধন্যবাদ!")
        except Exception:
            pass
        await cb.message.answer("আপনি প্রস্তুত 🎉", reply_markup=main_menu_kb())
    else:
        await cb.answer("❌ আপনি সব চ্যানেলে জয়েন করেননি!", show_alert=True)


@router.message(F.text == "👥 Referrals")
async def referrals(message: Message, bot: Bot):
    me = await bot.get_me()
    refs = await db.get_referral_count(message.from_user.id)
    await message.answer(
        "👥 <b>রেফারেল প্রোগ্রাম</b>\n\n"
        f"প্রতি রেফারে <b>{REFERRAL_BONUS}</b> কয়েন!\n\n"
        f"📊 আপনার রেফারেল: <b>{refs}</b>\n"
        f"💰 আয়: <b>{refs * REFERRAL_BONUS}</b> কয়েন\n\n"
        "আপনার লিংক শেয়ার করুন 👇",
        reply_markup=referral_kb(me.username, message.from_user.id),
        parse_mode="HTML",
    )


# ==========================================================
#                    📋 TASKS
# ==========================================================
@router.message(F.text == "📋 Tasks")
async def show_tasks(message: Message):
    tasks = await db.get_active_tasks()
    if not tasks:
        await message.answer("📭 এখন কোনো টাস্ক নেই।")
        return

    text = "📋 <b>টাস্ক লিস্ট</b>\n\nকমপ্লিট করে কয়েন কামান!\n\n"
    for t in tasks:
        text += f"• <b>{t['title']}</b> — +{t['reward']} কয়েন\n"
        if t.get("description"):
            text += f"  <i>{t['description']}</i>\n"
        text += "\n"
    await message.answer(text, reply_markup=tasks_kb(tasks), parse_mode="HTML")


@router.callback_query(F.data.startswith("task:"))
async def complete_task(cb: CallbackQuery):
    tid = int(cb.data.split(":")[1])
    uid = cb.from_user.id

    if await db.has_completed_task(uid, tid):
        await cb.answer("✅ আপনি ইতিমধ্যে এটা করেছেন!", show_alert=True)
        return

    task = await db.get_task(tid)
    if not task or not task["is_active"]:
        await cb.answer("❌ টাস্ক পাওয়া যায়নি।", show_alert=True)
        return

    await db.complete_task(uid, tid, task["reward"])
    await cb.answer(f"🎉 +{task['reward']} কয়েন!", show_alert=True)

    bal = await db.get_balance(uid)
    await cb.message.answer(
        f"✅ <b>{task['title']}</b> সম্পন্ন!\n💰 নতুন ব্যালেন্স: <b>{bal}</b>",
        parse_mode="HTML",
    )


# ==========================================================
#                    🎮 LUDO
# ==========================================================
@router.message(F.text == "🎮 Play Ludo")
async def ludo_menu(message: Message):
    bal = await db.get_balance(message.from_user.id)
    await message.answer(
        "🎮 <b>লুডু গেম</b>\n\n"
        f"💵 বেট: <b>{LUDO_BET_AMOUNT}</b> কয়েন\n"
        f"🏆 জিতলে: <b>{LUDO_BET_AMOUNT * 2}</b> কয়েন\n"
        f"💰 আপনার ব্যালেন্স: <b>{bal}</b>\n\n"
        "AI-কে হারিয়ে জিতে নিন!",
        reply_markup=ludo_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ludo_start")
async def start_ludo(cb: CallbackQuery):
    uid = cb.from_user.id
    bal = await db.get_balance(uid)

    if bal < LUDO_BET_AMOUNT:
        await cb.answer("❌ পর্যাপ্ত ব্যালেন্স নেই!", show_alert=True)
        return
    if engine.get_user_game(uid):
        await cb.answer("⚠️ আপনার একটি গেম চলছে!", show_alert=True)
        return

    gid = f"ludo_{uid}_{uuid.uuid4().hex[:8]}"
    engine.create_game(gid, uid)
    await db.update_balance(uid, -LUDO_BET_AMOUNT, "game_bet", "Ludo bet")

    await cb.message.edit_text(
        "🎲 <b>গেম শুরু!</b>\n\n"
        f"আপনার টোকেন: {[0, 0, 0, 0]}\n"
        f"AI টোকেন:   {[0, 0, 0, 0]}\n\n"
        "ডাইস রোল করুন 👇",
        reply_markup=ludo_game_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ludo_roll")
async def roll_dice(cb: CallbackQuery):
    uid = cb.from_user.id
    game = engine.get_user_game(uid)

    if not game:
        await cb.answer("❌ কোনো গেম নেই।", show_alert=True)
        return
    if game.turn != "player":
        await cb.answer("⏳ অপেক্ষা করুন...", show_alert=True)
        return

    engine.roll_dice(game.game_id)
    result = engine.player_move(game.game_id)

    if result.get("winner") == "player":
        reward = LUDO_BET_AMOUNT * 2
        await db.update_balance(uid, reward, "game_win", "Ludo win")
        await cb.message.edit_text(
            f"🏆 <b>আপনি জিতেছেন!</b>\n\n💰 +{reward} কয়েন\n{game.last_message}",
            parse_mode="HTML",
        )
        engine.delete_game(game.game_id)
        return

    ai_result = engine.ai_turn(game.game_id)

    if ai_result.get("winner") == "ai":
        await cb.message.edit_text(
            f"😢 <b>আপনি হেরেছেন!</b>\n\n"
            f"{game.last_message}\n\n💰 -{LUDO_BET_AMOUNT} কয়েন",
            parse_mode="HTML",
        )
        engine.delete_game(game.game_id)
        return

    text = (
        f"🎲 <b>রাউন্ড আপডেট</b>\n\n"
        f"{game.last_message}\n\n"
        f"👤 আপনার টোকেন: {game.player_tokens}\n"
        f"🤖 AI টোকেন:   {game.ai_tokens}\n\n"
    )
    text += "আপনার পালা 🎲" if ai_result.get("turn") == "player" else "AI আবার চালবে!"

    await cb.message.edit_text(text, reply_markup=ludo_game_kb(), parse_mode="HTML")


@router.callback_query(F.data == "ludo_quit")
async def quit_ludo(cb: CallbackQuery):
    game = engine.get_user_game(cb.from_user.id)
    if not game:
        await cb.answer("❌ কোনো গেম নেই।", show_alert=True)
        return
    engine.delete_game(game.game_id)
    await cb.message.edit_text("🏁 আপনি গেম ছেড়ে দিলেন।", parse_mode="HTML")


# ==========================================================
#                    💰 WALLET
# ==========================================================
@router.message(F.text == "💰 Wallet")
async def wallet_menu(message: Message):
    uid = message.from_user.id
    bal = await db.get_balance(uid)
    refs = await db.get_referral_count(uid)
    await message.answer(
        "💰 <b>আপনার ওয়ালেট</b>\n\n"
        f"💵 ব্যালেন্স: <b>{bal}</b> কয়েন\n"
        f"👥 রেফারেল: <b>{refs}</b>\n\n"
        f"⚠️ মিনিমাম উইথড্র: <b>{WITHDRAW_MIN}</b> কয়েন",
        reply_markup=wallet_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "wallet_history")
async def wallet_history(cb: CallbackQuery):
    txs = await db.get_transactions(cb.from_user.id, 10)
    if not txs:
        await cb.message.answer("📭 কোনো লেনদেন নেই।")
        return
    text = "📜 <b>সাম্প্রতিক লেনদেন</b>\n\n"
    for t in txs:
        sign = "+" if t["amount"] >= 0 else ""
        text += f"• {sign}{t['amount']} — <i>{t['type']}</i>\n  {t['description']}\n"
    await cb.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "withdraw")
async def start_withdraw(cb: CallbackQuery, state: FSMContext):
    bal = await db.get_balance(cb.from_user.id)
    if bal < WITHDRAW_MIN:
        await cb.answer(
            f"❌ মিনিমাম {WITHDRAW_MIN} কয়েন লাগবে। আপনার আছে {bal}।",
            show_alert=True,
        )
        return
    await cb.message.answer(
        "💸 <b>উইথড্র</b>\n\nকারেন্সি সিলেক্ট করুন:",
        reply_markup=withdraw_currency_kb(),
        parse_mode="HTML",
    )
    await state.set_state(WithdrawStates.currency)


@router.callback_query(WithdrawStates.currency, F.data.startswith("wd_cur:"))
async def choose_currency(cb: CallbackQuery, state: FSMContext):
    cur = cb.data.split(":")[1]
    await state.update_data(currency=cur)
    await cb.message.answer(
        f"💱 কারেন্সি: <b>{cur}</b>\n\nপরিমাণ লিখুন (মিন {WITHDRAW_MIN}):",
        parse_mode="HTML",
    )
    await state.set_state(WithdrawStates.amount)


@router.message(WithdrawStates.amount)
async def enter_amount(message: Message, state: FSMContext):
    try:
        amt = int(message.text.strip())
    except ValueError:
        await message.answer("❌ সংখ্যা দিন।")
        return
    bal = await db.get_balance(message.from_user.id)
    if amt < WITHDRAW_MIN:
        await message.answer(f"❌ মিনিমাম {WITHDRAW_MIN}।")
        return
    if amt > bal:
        await message.answer(f"❌ ব্যালেন্স কম। আছে {bal}।")
        return
    await state.update_data(amount=amt)
    await message.answer("🔗 ওয়ালেট এড্রেস / ফোন নম্বর দিন:")
    await state.set_state(WithdrawStates.wallet)


@router.message(WithdrawStates.wallet)
async def enter_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    if len(wallet) < 4:
        await message.answer("❌ এড্রেস ছোট।")
        return
    data = await state.get_data()
    wid = await db.create_withdrawal(
        message.from_user.id, data["amount"], data["currency"], wallet
    )
    await message.answer(
        "✅ <b>উইথড্র রিকোয়েস্ট সাবমিট!</b>\n\n"
        f"🆔 ID: <code>{wid}</code>\n"
        f"💵 পরিমাণ: <b>{data['amount']}</b>\n"
        f"💱 কারেন্সি: <b>{data['currency']}</b>\n"
        f"🔗 ওয়ালেট: <code>{wallet}</code>\n\n"
        "⏳ অ্যাডমিন অনুমোদনের অপেক্ষায়।",
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "withdraw_cancel")
async def cancel_withdraw(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("❌ বাতিল।")
    except Exception:
        await cb.message.answer("❌ বাতিল।")


# ==========================================================
#                    🛠 ADMIN
# ==========================================================
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 অ্যাক্সেস নেই।")
        return
    total = await db.get_user_count()
    await message.answer(
        f"🛠 <b>অ্যাডমিন প্যানেল</b>\n\n👥 ইউজার: <b>{total}</b>",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_stats")
async def cb_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    users = await db.get_all_users()
    await cb.message.edit_text(
        f"📊 <b>স্ট্যাটস</b>\n\n👥 ইউজার: <b>{len(users)}</b>\n"
        f"💰 মোট ব্যালেন্স: <b>{sum(u['balance'] for u in users)}</b>",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_users")
async def cb_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    users = await db.get_all_users()
    text = "👥 <b>ইউজার (সর্বশেষ ২০)</b>\n\n"
    for u in users[-20:]:
        un = f"@{u['username']}" if u["username"] else "—"
        text += f"• <code>{u['user_id']}</code> {un} — {u['balance']}\n"
    await cb.message.edit_text(text, reply_markup=admin_kb(), parse_mode="HTML")


@router.callback_query(F.data == "admin_wd")
async def cb_wd(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    items = await db.get_pending_withdrawals()
    if not items:
        await cb.message.edit_text("💸 কোনো পেন্ডিং উইথড্র নেই।", reply_markup=admin_kb())
        return
    text = "💸 <b>পেন্ডিং উইথড্র</b>\n\n"
    for w in items:
        text += (
            f"🆔 <b>#{w['id']}</b> | user <code>{w['user_id']}</code>\n"
            f"💵 {w['amount']} → {w['currency']}\n"
            f"🔗 <code>{w['wallet_address']}</code>\n\n"
        )
    await cb.message.edit_text(text, reply_markup=admin_wd_kb(items), parse_mode="HTML")


@router.callback_query(F.data.startswith("wd_approve:"))
async def cb_approve(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    wid = int(cb.data.split(":")[1])
    wd = await db.set_withdrawal_status(wid, "approved")
    if not wd:
        await cb.answer("পাওয়া যায়নি।", show_alert=True)
        return
    await cb.answer("✅ অনুমোদিত", show_alert=True)
    try:
        await bot.send_message(
            wd["user_id"],
            f"✅ আপনার উইথড্র <b>#{wid}</b> ({wd['amount']} {wd['currency']}) "
            f"অনুমোদিত হয়েছে!",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("wd_reject:"))
async def cb_reject(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    wid = int(cb.data.split(":")[1])
    wd = await db.set_withdrawal_status(wid, "rejected")
    if not wd:
        await cb.answer("পাওয়া যায়নি।", show_alert=True)
        return
    await cb.answer("❌ রিজেক্ট + রিফান্ড", show_alert=True)
    try:
        await bot.send_message(
            wd["user_id"],
            f"❌ আপনার উইথড্র <b>#{wid}</b> রিজেক্ট হয়েছে। "
            f"{wd['amount']} কয়েন ফেরত দেওয়া হয়েছে।",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("🚫", show_alert=True)
        return
    await cb.message.answer("📢 ব্রডকাস্ট মেসেজ পাঠান:")
    await state.set_state(Broadcast.waiting)


@router.message(Broadcast.waiting)
async def do_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    users = await db.get_all_users()
    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], message.text)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📢 <b>ব্রডকাস্ট সম্পন্ন</b>\n\n✅ পাঠানো: {sent}\n❌ ব্যর্থ: {failed}",
        parse_mode="HTML",
    )
    await state.clear()


# ==========================================================
#                    🏁 MAIN
# ==========================================================
async def main():
    await db.init()
    log.info("Database ready")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    log.info("Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")