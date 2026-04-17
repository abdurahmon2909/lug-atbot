import logging
import random
import json
import os
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================
# SOZLAMA
# =========================
TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

if not TOKEN or not SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("Environment variables not set!")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ENGLISH, UZBEK = range(2)

WORDS_SHEET_NAME = "words"
RESULTS_SHEET_NAME = "results"

GLOBAL_WORDS_PAGE_SIZE = 20
MY_WORDS_PAGE_SIZE = 20
LEADERBOARD_PAGE_SIZE = 20
TOP_LIMIT = 5

WORDS_CACHE = []
LAST_FETCH = 0
CACHE_TTL = 60  # sekund

# =========================
# GOOGLE SHEETS
# =========================
try:
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    print("✅ Google Sheetsga ulandi!")
except Exception as e:
    print(f"❌ Google Sheets error: {e}")
    raise


def ensure_worksheet(name: str, headers: list[str]):
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=max(20, len(headers)))
        ws.append_row(headers)
        return ws

    current_headers = ws.row_values(1)
    if current_headers != headers:
        if not current_headers:
            ws.append_row(headers)
        else:
            for idx, header in enumerate(headers, start=1):
                if idx > len(current_headers) or current_headers[idx - 1] != header:
                    ws.update_cell(1, idx, header)
    return ws


words_sheet = ensure_worksheet(
    WORDS_SHEET_NAME,
    [
        "english",
        "uzbek",
        "added_by_user_id",
        "added_by_username",
        "added_by_full_name",
        "created_at",
    ],
)

results_sheet = ensure_worksheet(
    RESULTS_SHEET_NAME,
    [
        "user_id",
        "username",
        "full_name",
        "test_type",
        "total",
        "correct",
        "percent",
        "score",
        "created_at",
    ],
)

# =========================
# YORDAMCHI FUNKSIYALAR
# =========================
def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_display_name(user_id: int | None, username: str | None, full_name: str | None) -> str:
    if username:
        return f"@{username}"
    if full_name:
        return full_name
    if user_id:
        return f"User {user_id}"
    return "Unknown"


def get_user_meta(update: Update):
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user else None
    username = tg_user.username if tg_user else None
    full_name = tg_user.full_name if tg_user else None
    return user_id, username, full_name


def invalidate_cache():
    global WORDS_CACHE, LAST_FETCH
    WORDS_CACHE = []
    LAST_FETCH = 0


def get_all_words(force_refresh: bool = False):
    global WORDS_CACHE, LAST_FETCH
    try:
        now = time.time()
        if not force_refresh and WORDS_CACHE and (now - LAST_FETCH < CACHE_TTL):
            return WORDS_CACHE

        records = words_sheet.get_all_records()
        words = []

        for row in records:
            eng = str(row.get("english", "")).strip()
            uzb = str(row.get("uzbek", "")).strip()
            if not eng or not uzb:
                continue

            user_id_raw = row.get("added_by_user_id", "")
            try:
                added_by_user_id = int(str(user_id_raw).strip()) if str(user_id_raw).strip() else None
            except Exception:
                added_by_user_id = None

            words.append(
                {
                    "english": eng,
                    "uzbek": uzb,
                    "added_by_user_id": added_by_user_id,
                    "added_by_username": str(row.get("added_by_username", "")).strip(),
                    "added_by_full_name": str(row.get("added_by_full_name", "")).strip(),
                    "created_at": str(row.get("created_at", "")).strip(),
                }
            )

        WORDS_CACHE = words
        LAST_FETCH = now
        return WORDS_CACHE

    except Exception as e:
        logger.exception("get_all_words error: %s", e)
        return WORDS_CACHE if WORDS_CACHE else []


def get_user_words(user_id: int):
    return [w for w in get_all_words() if w.get("added_by_user_id") == user_id]


def add_word(eng: str, uzb: str, user_id: int, username: str | None, full_name: str | None):
    eng = eng.strip()
    uzb = uzb.strip()

    try:
        words = get_all_words()

        eng_lower = eng.lower()
        uzb_lower = uzb.lower()

        for row in words:
            existing_eng = row["english"].strip().lower()
            existing_uzb = row["uzbek"].strip().lower()

            if existing_eng == eng_lower or existing_uzb == uzb_lower:
                return "exists"

        words_sheet.append_row(
            [
                eng,
                uzb,
                str(user_id),
                username or "",
                full_name or "",
                now_str(),
            ]
        )

        invalidate_cache()
        get_all_words(force_refresh=True)
        return "ok"

    except Exception as e:
        logger.exception("add_word error: %s", e)
        return "error"


def save_global_result(
    user_id: int,
    username: str | None,
    full_name: str | None,
    total: int,
    correct: int,
):
    percent = round((correct / total) * 100, 1) if total > 0 else 0
    score = correct

    try:
        results_sheet.append_row(
            [
                str(user_id),
                username or "",
                full_name or "",
                "global",
                str(total),
                str(correct),
                str(percent),
                str(score),
                now_str(),
            ]
        )
    except Exception as e:
        logger.exception("save_global_result error: %s", e)


def get_leaderboard_users():
    try:
        records = results_sheet.get_all_records()
        score_map = {}

        for row in records:
            test_type = str(row.get("test_type", "")).strip().lower()
            if test_type != "global":
                continue

            user_id_raw = str(row.get("user_id", "")).strip()
            username = str(row.get("username", "")).strip()
            full_name = str(row.get("full_name", "")).strip()

            try:
                score = int(float(str(row.get("score", "0")).strip() or "0"))
            except Exception:
                score = 0

            if not user_id_raw:
                continue

            if user_id_raw not in score_map:
                score_map[user_id_raw] = {
                    "user_id": user_id_raw,
                    "username": username,
                    "full_name": full_name,
                    "score": 0,
                }

            score_map[user_id_raw]["score"] += score

            if username and not score_map[user_id_raw]["username"]:
                score_map[user_id_raw]["username"] = username
            if full_name and not score_map[user_id_raw]["full_name"]:
                score_map[user_id_raw]["full_name"] = full_name

        leaderboard = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
        return leaderboard
    except Exception as e:
        logger.exception("get_leaderboard_users error: %s", e)
        return []


def get_top_users(limit: int = TOP_LIMIT):
    return get_leaderboard_users()[:limit]


def get_user_total_global_score(user_id: int) -> int:
    try:
        records = results_sheet.get_all_records()
        total_score = 0
        for row in records:
            test_type = str(row.get("test_type", "")).strip().lower()
            row_user_id = str(row.get("user_id", "")).strip()
            if test_type != "global" or row_user_id != str(user_id):
                continue
            try:
                total_score += int(float(str(row.get("score", "0")).strip() or "0"))
            except Exception:
                pass
        return total_score
    except Exception as e:
        logger.exception("get_user_total_global_score error: %s", e)
        return 0


def get_random_incorrect(correct_word, all_words, lang="eng"):
    if lang == "eng":
        candidates = [w["english"] for w in all_words if w["english"] != correct_word]
    else:
        candidates = [w["uzbek"] for w in all_words if w["uzbek"] != correct_word]

    candidates = list(dict.fromkeys(candidates))

    if not candidates:
        return []

    if len(candidates) >= 3:
        return random.sample(candidates, 3)

    return candidates


def build_test_queue(words: list[dict]) -> list[dict]:
    queue = []
    for word in words:
        queue.append({"q_type": "eng2uz", "correct": word})
        queue.append({"q_type": "uz2eng", "correct": word})
    random.shuffle(queue)
    return queue


def build_pagination_markup(prefix: str, page: int, total_items: int, page_size: int):
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    buttons = []

    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"{prefix}_{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f"{prefix}_{page + 1}"))

    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🏠 Menyu", callback_data="menu")])
    return InlineKeyboardMarkup(buttons)


def format_words_page(title: str, words: list[dict], page: int, page_size: int, show_owner: bool = False):
    total_items = len(words)
    total_pages = max(1, (total_items + page_size - 1) // page_size)

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * page_size
    end = start + page_size
    chunk = words[start:end]

    text = f"{title}\n"
    text += f"📄 Sahifa: {page + 1}/{total_pages}\n"
    text += f"📚 Jami so'zlar: {total_items}\n\n"

    if not chunk:
        text += "So'zlar topilmadi."
        return text, page

    for idx, item in enumerate(chunk, start=start + 1):
        text += f"{idx}. {item['english']} - {item['uzbek']}"
        if show_owner:
            owner = get_display_name(
                item.get("added_by_user_id"),
                item.get("added_by_username"),
                item.get("added_by_full_name"),
            )
            text += f" ({owner})"
        text += "\n"

    return text, page


def format_leaderboard_page(users: list[dict], page: int, page_size: int):
    total_items = len(users)
    total_pages = max(1, (total_items + page_size - 1) // page_size)

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * page_size
    end = start + page_size
    chunk = users[start:end]

    text = "🏆 Leaderboard\n"
    text += f"📄 Sahifa: {page + 1}/{total_pages}\n"
    text += f"👥 Jami foydalanuvchilar: {total_items}\n\n"

    if not chunk:
        text += "Hozircha reyting bo'sh."
        return text, page

    for idx, item in enumerate(chunk, start=start + 1):
        user_id_val = int(item["user_id"]) if str(item["user_id"]).isdigit() else None
        name = get_display_name(user_id_val, item.get("username"), item.get("full_name"))
        text += f"{idx}. {name} — {item['score']}\n"

    return text, page


def build_rules_text():
    return (
        "ℹ️ Bot qoidalari va ishlash tartibi\n\n"
        "Ushbu bot inglizcha-o'zbekcha so'zlarni o'rganish va test ishlash uchun yaratilgan.\n\n"
        "🌍 Global test\n"
        "Bu bo'limda barcha foydalanuvchilar qo'shgan so'zlardan test ishlanadi.\n"
        "Siz o'zingiz qo'shgan so'zlar ham shu testda chiqishi mumkin.\n"
        "Faqat Global test uchun ball beriladi va Leaderboard shu bo'lim asosida shakllanadi.\n\n"
        "👤 Mening testim\n"
        "Bu bo'limda faqat siz qo'shgan so'zlardan test ishlaysiz.\n"
        "Bu mashq rejimi hisoblanadi va ball qo'shilmaydi.\n\n"
        "➕ So'z qo'shish\n"
        "Yangi inglizcha-o'zbekcha so'z juftligini botga qo'shishingiz mumkin.\n"
        "Har bir inglizcha so'z faqat 1 marta, har bir o'zbekcha so'z ham faqat 1 marta kiritiladi.\n\n"
        "📚 Mening so'zlarim\n"
        "Bu yerda siz qo'shgan so'zlarni ko'rasiz.\n\n"
        "🌐 Global so'zlar\n"
        "Bu bo'limda barcha foydalanuvchilar qo'shgan so'zlar sahifalarga bo'lingan holda ko'rsatiladi.\n\n"
        "🏆 Leaderboard\n"
        "Bu bo'limda Global test bo'yicha barcha foydalanuvchilar ballari saralangan holda ko'rsatiladi.\n\n"
        "Test tartibi:\n"
        "Har bir so'z testda 2 xil ko'rinishda ishlatiladi:\n"
        "1. Inglizcha → O'zbekcha\n"
        "2. O'zbekcha → Inglizcha\n\n"
        "Har bir variant faqat 1 martadan beriladi.\n"
        "Barcha savollar tugagach, test yakunlanadi va natija ko'rsatiladi.\n\n"
        "Taklif va murojaatlar uchun:\n"
        "@abdurahmon_2909"
    )


def get_start_text():
    top_users = get_top_users(TOP_LIMIT)

    text = (
        "🚀 Welcome to Vocabulary Arena!\n\n"
        "Bu yerda siz:\n"
        "📚 yangi so'zlar qo'shasiz\n"
        "🧠 test ishlaysiz\n"
        "🏆 va TOP foydalanuvchilar qatoriga qo'shilasiz\n\n"
        "🏆 Our Top 5 Vocab Pros:\n\n"
    )

    if top_users:
        for i, user in enumerate(top_users, start=1):
            name = get_display_name(
                int(user["user_id"]) if str(user["user_id"]).isdigit() else None,
                user.get("username"),
                user.get("full_name"),
            )
            text += f"{i}. {name} — {user['score']}\n"
    else:
        text += "Hozircha reyting bo'sh.\n"

    text += "\n🔥 O'zingizni sinab ko'ring va reytingga kiring!\n\n👇 Boshlash uchun tanlang"
    return text


def get_main_menu_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌍 Global test", callback_data="global_test")],
        [InlineKeyboardButton("👤 Mening testim", callback_data="my_test")],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data="add")],
        [InlineKeyboardButton("📚 Mening so'zlarim", callback_data="my_words_0")],
        [InlineKeyboardButton("🌐 Global so'zlar", callback_data="global_words_0")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard_0")],
        [InlineKeyboardButton("ℹ️ Qoidalar", callback_data="rules")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_after_test_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 Yana test", callback_data="repeat_test")],
            [InlineKeyboardButton("🏠 Menyu", callback_data="menu")],
        ]
    )


async def safe_edit_or_send(query, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text(text=text, reply_markup=reply_markup)


async def post_init(application: Application):
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning("delete_webhook warning: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception:", exc_info=context.error)


def clear_test_state(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "test_words",
        "score",
        "correct",
        "q_type",
        "test_queue",
        "current_question",
        "test_mode_type",
    ]:
        context.user_data.pop(key, None)


async def finish_test(query, context: ContextTypes.DEFAULT_TYPE, update: Update):
    score = context.user_data.get("score", {"total": 0, "correct": 0})
    total = score["total"]
    correct = score["correct"]
    percent = round((correct / total) * 100, 1) if total > 0 else 0
    test_mode_type = context.user_data.get("test_mode_type")

    user_id, username, full_name = get_user_meta(update)

    if test_mode_type == "global":
        save_global_result(user_id, username, full_name, total, correct)
        total_global_score = get_user_total_global_score(user_id)

        text = (
            "✅ Global test tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%\n"
            f"🏅 Ushbu test uchun ball: {correct}\n"
            f"🔥 Umumiy global ballingiz: {total_global_score}"
        )
    else:
        text = (
            "✅ Mening testim tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%\n\n"
            "Bu mashq rejimi, ball qo'shilmadi."
        )

    clear_test_state(context)

    await safe_edit_or_send(
        query,
        text,
        reply_markup=get_after_test_markup(),
    )

# =========================
# HANDLERS
# =========================
async def restart_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_test_state(context)
    context.user_data.pop("eng", None)

    if update.message:
        await update.message.reply_text(
            get_start_text(),
            reply_markup=get_main_menu_markup(),
        )
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await safe_edit_or_send(
            query,
            get_start_text(),
            reply_markup=get_main_menu_markup(),
        )

    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_test_state(context)
    context.user_data.pop("eng", None)
    await update.message.reply_text(
        get_start_text(),
        reply_markup=get_main_menu_markup(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_test_state(context)
    context.user_data.pop("eng", None)
    await safe_edit_or_send(query, get_start_text(), reply_markup=get_main_menu_markup())


async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    page = 0
    try:
        page = int(data.split("_")[-1])
    except Exception:
        page = 0

    users = get_leaderboard_users()
    text, page = format_leaderboard_page(users, page, LEADERBOARD_PAGE_SIZE)
    markup = build_pagination_markup("leaderboard", page, len(users), LEADERBOARD_PAGE_SIZE)
    await safe_edit_or_send(query, text, reply_markup=markup)


async def rules_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_or_send(
        query,
        build_rules_text(),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
        ),
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_or_send(query, "Inglizcha so'zni yozing:\n(Bekor qilish uchun /cancel yozing)")
    return ENGLISH


async def add_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Iltimos, inglizcha so'zni yuboring.")
        return ENGLISH

    context.user_data["eng"] = text
    await update.message.reply_text("O'zbekcha tarjimasini yozing:\n(Bekor qilish uchun /cancel yozing)")
    return UZBEK


async def add_uzbek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eng = context.user_data.get("eng", "").strip()
    uzb = (update.message.text or "").strip()

    if not eng:
        await update.message.reply_text("⚠️ Avval inglizcha so'zni kiriting.")
        return ConversationHandler.END

    if not uzb:
        await update.message.reply_text("⚠️ O'zbekcha tarjimani kiriting.")
        return UZBEK

    user_id, username, full_name = get_user_meta(update)
    result = add_word(eng, uzb, user_id, username, full_name)

    if result == "ok":
        await update.message.reply_text(f"✅ So'z qo'shildi!\n📝 {eng} -> {uzb}")
    elif result == "exists":
        await update.message.reply_text(
            "⚠️ Bu inglizcha yoki o'zbekcha so'z allaqachon mavjud!"
        )
    else:
        await update.message.reply_text("❌ Xatolik!")

    context.user_data.pop("eng", None)

    await update.message.reply_text(
        "Yana tanlang:",
        reply_markup=get_main_menu_markup(),
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_test_state(context)
    context.user_data.pop("eng", None)

    await update.message.reply_text(
        "Bekor qilindi.",
        reply_markup=get_main_menu_markup(),
    )
    return ConversationHandler.END


async def my_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    page = 0
    try:
        page = int(data.split("_")[-1])
    except Exception:
        page = 0

    user_id, _, _ = get_user_meta(update)
    words = get_user_words(user_id)

    text, page = format_words_page("📚 Mening so'zlarim", words, page, MY_WORDS_PAGE_SIZE, show_owner=False)
    markup = build_pagination_markup("my_words", page, len(words), MY_WORDS_PAGE_SIZE)
    await safe_edit_or_send(query, text, reply_markup=markup)


async def global_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    page = 0
    try:
        page = int(data.split("_")[-1])
    except Exception:
        page = 0

    words = get_all_words()
    text, page = format_words_page("🌐 Global so'zlar", words, page, GLOBAL_WORDS_PAGE_SIZE, show_owner=False)
    markup = build_pagination_markup("global_words", page, len(words), GLOBAL_WORDS_PAGE_SIZE)
    await safe_edit_or_send(query, text, reply_markup=markup)


async def global_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    words = get_all_words()

    if len(words) < 4:
        await safe_edit_or_send(
            query,
            "Global test uchun kamida 4 ta so'z kerak!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
            ),
        )
        return

    context.user_data["test_words"] = words
    context.user_data["score"] = {"total": 0, "correct": 0}
    context.user_data["test_queue"] = build_test_queue(words)
    context.user_data["test_mode_type"] = "global"

    await generate_question(update, context, query)


async def my_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id, _, _ = get_user_meta(update)
    words = get_user_words(user_id)

    if len(words) < 4:
        await safe_edit_or_send(
            query,
            "Mening testim uchun siz kamida 4 ta so'z qo'shgan bo'lishingiz kerak!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
            ),
        )
        return

    context.user_data["test_words"] = words
    context.user_data["score"] = {"total": 0, "correct": 0}
    context.user_data["test_queue"] = build_test_queue(words)
    context.user_data["test_mode_type"] = "my"

    await generate_question(update, context, query)


async def repeat_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit_or_send(
        query,
        "Qaysi testni qayta ishlamoqchisiz?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🌍 Global test", callback_data="global_test")],
                [InlineKeyboardButton("👤 Mening testim", callback_data="my_test")],
                [InlineKeyboardButton("🏠 Menyu", callback_data="menu")],
            ]
        ),
    )


async def generate_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query=None,
    feedback_text: str = "",
):
    words = context.user_data.get("test_words", [])
    test_queue = context.user_data.get("test_queue", [])

    if len(words) < 4:
        if query:
            await safe_edit_or_send(
                query,
                "Test uchun yetarli so'z topilmadi.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
                ),
            )
        return

    if not test_queue:
        if query:
            await finish_test(query, context, update)
        return

    current_question = test_queue.pop(0)
    context.user_data["test_queue"] = test_queue
    context.user_data["current_question"] = current_question

    q_type = current_question["q_type"]
    correct = current_question["correct"]

    context.user_data["correct"] = correct
    context.user_data["q_type"] = q_type

    if q_type == "eng2uz":
        question_text = f"❓ {correct['english']} = ?"
        wrongs = get_random_incorrect(correct["uzbek"], words, "uz")
        options = wrongs + [correct["uzbek"]]
        prefix = "uz_"
    else:
        question_text = f"❓ {correct['uzbek']} = ?"
        wrongs = get_random_incorrect(correct["english"], words, "eng")
        options = wrongs + [correct["english"]]
        prefix = "eng_"

    options = list(dict.fromkeys(options))
    random.shuffle(options)

    keyboard = []
    for opt in options:
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"{prefix}{opt}")])

    keyboard.append([InlineKeyboardButton("🏠 Menyu", callback_data="menu")])

    score = context.user_data["score"]
    total_questions = len(words) * 2
    used_count = score["total"]
    remaining = total_questions - used_count

    mode_title = "🌍 Global test" if context.user_data.get("test_mode_type") == "global" else "👤 Mening testim"

    score_text = (
        f"{mode_title}\n\n"
        f"📊 {score['correct']}/{score['total']}\n"
        f"🧩 Qoldi: {remaining}\n\n"
    )

    final_text = ""
    if feedback_text:
        final_text += feedback_text + "\n\n"
    final_text += score_text + question_text

    if query:
        await safe_edit_or_send(
            query,
            final_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.callback_query.message.reply_text(
            final_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    correct = context.user_data.get("correct")
    q_type = context.user_data.get("q_type")

    if not correct or not q_type:
        await safe_edit_or_send(
            query,
            "⚠️ Test holati topilmadi. Qaytadan boshlang.",
            reply_markup=get_main_menu_markup(),
        )
        return

    context.user_data["score"]["total"] += 1

    if data.startswith("eng_"):
        user_ans = data[4:]
        is_correct = user_ans == correct["english"]
    else:
        user_ans = data[3:]
        is_correct = user_ans == correct["uzbek"]

    if is_correct:
        context.user_data["score"]["correct"] += 1
        feedback_text = "✅ To'g'ri!"
    else:
        if q_type == "eng2uz":
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct['english']} -> {correct['uzbek']}"
        else:
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct['uzbek']} -> {correct['english']}"

    await generate_question(update, context, query, feedback_text=feedback_text)

# =========================
# MAIN
# =========================
def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(False)
        .post_init(post_init)
        .build()
    )

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern="^add$")],
        states={
            ENGLISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_english)],
            UZBEK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_uzbek)],
        },
        fallbacks=[
            CommandHandler("cancel", add_cancel),
            CommandHandler("start", restart_to_menu),
        ],
        
        allow_reentry=True,
    )

    app.add_error_handler(error_handler)

    app.add_handler(add_conv)
    app.add_handler(CommandHandler("start", start))

    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(global_test_handler, pattern="^global_test$"))
    app.add_handler(CallbackQueryHandler(my_test_handler, pattern="^my_test$"))
    app.add_handler(CallbackQueryHandler(repeat_test_handler, pattern="^repeat_test$"))
    app.add_handler(CallbackQueryHandler(my_words_handler, pattern=r"^my_words_\d+$"))
    app.add_handler(CallbackQueryHandler(global_words_handler, pattern=r"^global_words_\d+$"))
    app.add_handler(CallbackQueryHandler(leaderboard_handler, pattern=r"^leaderboard_\d+$"))
    app.add_handler(CallbackQueryHandler(rules_handler, pattern="^rules$"))
    app.add_handler(CallbackQueryHandler(check_answer, pattern=r"^(eng_|uz_)"))

    print("🤖 Bot ishga tushdi...")

    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    except Conflict:
        logger.error(
            "409 Conflict: shu token bilan boshqa bot instance ham ishlayapti. "
            "Railway/local botlardan bittasini to'xtating."
        )


if __name__ == "__main__":
    main()
