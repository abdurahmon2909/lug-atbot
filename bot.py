import logging
import random
import json
import os
import time
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict, BadRequest
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
PROGRESS_SHEET_NAME = "progress"

GLOBAL_WORDS_PAGE_SIZE = 20
MY_WORDS_PAGE_SIZE = 20
LEADERBOARD_PAGE_SIZE = 20
TOP_LIMIT = 5

WORDS_CACHE = []
LAST_FETCH = 0
CACHE_TTL = 300  # 5 minut

GLOBAL_TEST_MAX_QUESTIONS = 25
MY_TEST_MAX_QUESTIONS = 25


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

progress_sheet = ensure_worksheet(
    PROGRESS_SHEET_NAME,
    [
        "user_id",
        "english",
        "uzbek",
        "seen_count",
        "correct_count",
        "wrong_count",
        "last_result",
        "updated_at",
    ],
)


# =========================
# YORDAMCHI FUNKSIYALAR
# =========================
def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(value: Optional[str]) -> str:
    return (value or "").strip()


def build_full_name(first_name: Optional[str], last_name: Optional[str]) -> str:
    parts = [normalize_text(first_name), normalize_text(last_name)]
    return " ".join([p for p in parts if p]).strip()


def get_display_name(user_id: int | None, username: str | None, full_name: str | None) -> str:
    username = normalize_text(username)
    full_name = normalize_text(full_name)

    if username:
        return f"@{username}"
    if full_name:
        return full_name
    if user_id:
        return f"User {user_id}"
    return "Unknown"


def get_user_meta(update: Update):
    user = None

    if update.effective_user:
        user = update.effective_user
    elif update.callback_query and update.callback_query.from_user:
        user = update.callback_query.from_user
    elif update.message and update.message.from_user:
        user = update.message.from_user

    if not user:
        return None, "", ""

    user_id = user.id
    username = normalize_text(getattr(user, "username", ""))

    full_name = normalize_text(getattr(user, "full_name", ""))
    if not full_name:
        full_name = build_full_name(
            getattr(user, "first_name", ""),
            getattr(user, "last_name", ""),
        )

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

        values = words_sheet.get_all_values()
        if not values or len(values) < 2:
            WORDS_CACHE = []
            LAST_FETCH = now
            return WORDS_CACHE

        headers = [str(h).strip() for h in values[0]]
        rows = values[1:]

        words = []
        for raw_row in rows:
            row = {}
            for idx, header in enumerate(headers):
                row[header] = raw_row[idx] if idx < len(raw_row) else ""

            eng = normalize_text(row.get("english"))
            uzb = normalize_text(row.get("uzbek"))
            if not eng or not uzb:
                continue

            user_id_raw = normalize_text(row.get("added_by_user_id"))
            try:
                added_by_user_id = int(user_id_raw) if user_id_raw else None
            except Exception:
                added_by_user_id = None

            words.append(
                {
                    "english": eng,
                    "uzbek": uzb,
                    "added_by_user_id": added_by_user_id,
                    "added_by_username": normalize_text(row.get("added_by_username")),
                    "added_by_full_name": normalize_text(row.get("added_by_full_name")),
                    "created_at": normalize_text(row.get("created_at")),
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
    eng = normalize_text(eng)
    uzb = normalize_text(uzb)
    username = normalize_text(username)
    full_name = normalize_text(full_name)

    try:
        words = get_all_words()

        eng_lower = eng.lower()
        uzb_lower = uzb.lower()

        for row in words:
            existing_eng = normalize_text(row["english"]).lower()
            existing_uzb = normalize_text(row["uzbek"]).lower()

            if existing_eng == eng_lower or existing_uzb == uzb_lower:
                return "exists"

        words_sheet.append_row(
            [
                eng,
                uzb,
                str(user_id),
                username,
                full_name,
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

    username = normalize_text(username)
    full_name = normalize_text(full_name)

    try:
        if not full_name and not username:
            full_name = f"User {user_id}"

        results_sheet.append_row(
            [
                str(user_id),
                username,
                full_name,
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


def get_results_records():
    try:
        return results_sheet.get_all_records()
    except Exception as e:
        logger.exception("get_results_records error: %s", e)
        return []


def get_leaderboard_users():
    try:
        records = get_results_records()
        score_map = {}

        for row in records:
            test_type = normalize_text(str(row.get("test_type", ""))).lower()
            if test_type != "global":
                continue

            user_id_raw = normalize_text(str(row.get("user_id", "")))
            username = normalize_text(str(row.get("username", "")))
            full_name = normalize_text(str(row.get("full_name", "")))

            try:
                score = int(float(normalize_text(str(row.get("score", "0"))) or "0"))
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
        records = get_results_records()
        total_score = 0
        for row in records:
            test_type = normalize_text(str(row.get("test_type", ""))).lower()
            row_user_id = normalize_text(str(row.get("user_id", "")))
            if test_type != "global" or row_user_id != str(user_id):
                continue
            try:
                total_score += int(float(normalize_text(str(row.get("score", "0"))) or "0"))
            except Exception:
                pass
        return total_score
    except Exception as e:
        logger.exception("get_user_total_global_score error: %s", e)
        return 0


def get_progress_records():
    try:
        return progress_sheet.get_all_records()
    except Exception as e:
        logger.exception("get_progress_records error: %s", e)
        return []


def get_user_progress_map(user_id: int) -> dict:
    records = get_progress_records()
    progress_map = {}

    for row in records:
        row_user_id = normalize_text(str(row.get("user_id", "")))
        if row_user_id != str(user_id):
            continue

        english = normalize_text(row.get("english"))
        uzbek = normalize_text(row.get("uzbek"))
        if not english or not uzbek:
            continue

        key = f"{english.lower()}::{uzbek.lower()}"
        progress_map[key] = {
            "seen_count": int(float(str(row.get("seen_count", 0)) or 0)),
            "correct_count": int(float(str(row.get("correct_count", 0)) or 0)),
            "wrong_count": int(float(str(row.get("wrong_count", 0)) or 0)),
            "last_result": normalize_text(row.get("last_result")),
        }

    return progress_map


def update_word_progress(
    user_id: int,
    english: str,
    uzbek: str,
    is_correct: bool,
):
    try:
        records = progress_sheet.get_all_records()

        target_row_index = None
        english_l = normalize_text(english).lower()
        uzbek_l = normalize_text(uzbek).lower()

        for idx, row in enumerate(records, start=2):
            row_user_id = normalize_text(str(row.get("user_id", "")))
            row_english = normalize_text(row.get("english")).lower()
            row_uzbek = normalize_text(row.get("uzbek")).lower()

            if row_user_id == str(user_id) and row_english == english_l and row_uzbek == uzbek_l:
                target_row_index = idx
                break

        if target_row_index is None:
            seen_count = 1
            correct_count = 1 if is_correct else 0
            wrong_count = 0 if is_correct else 1
            last_result = "correct" if is_correct else "wrong"

            progress_sheet.append_row(
                [
                    str(user_id),
                    english,
                    uzbek,
                    str(seen_count),
                    str(correct_count),
                    str(wrong_count),
                    last_result,
                    now_str(),
                ]
            )
            return

        current_seen = int(float(str(progress_sheet.cell(target_row_index, 4).value or 0)))
        current_correct = int(float(str(progress_sheet.cell(target_row_index, 5).value or 0)))
        current_wrong = int(float(str(progress_sheet.cell(target_row_index, 6).value or 0)))

        new_seen = current_seen + 1
        new_correct = current_correct + (1 if is_correct else 0)
        new_wrong = current_wrong + (0 if is_correct else 1)
        new_last_result = "correct" if is_correct else "wrong"

        progress_sheet.update(
            f"D{target_row_index}:H{target_row_index}",
            [[
                str(new_seen),
                str(new_correct),
                str(new_wrong),
                new_last_result,
                now_str(),
            ]]
        )

    except Exception as e:
        logger.exception("update_word_progress error: %s", e)


def build_weighted_words(words: list[dict], user_id: int, limit: int | None = None) -> list[dict]:
    if not words:
        return []

    progress_map = get_user_progress_map(user_id)
    pool = []

    for word in words:
        english = normalize_text(word["english"])
        uzbek = normalize_text(word["uzbek"])
        key = f"{english.lower()}::{uzbek.lower()}"

        stats = progress_map.get(
            key,
            {
                "seen_count": 0,
                "correct_count": 0,
                "wrong_count": 0,
                "last_result": "",
            },
        )

        wrong_count = stats["wrong_count"]
        correct_count = stats["correct_count"]
        last_result = stats["last_result"]

        weight = 1 + wrong_count * 3 - correct_count * 0.5
        if last_result == "wrong":
            weight += 2

        weight = max(1, int(round(weight)))

        for _ in range(weight):
            pool.append(word)

    random.shuffle(pool)

    selected = []
    seen_keys = set()

    for word in pool:
        key = f"{normalize_text(word['english']).lower()}::{normalize_text(word['uzbek']).lower()}"
        if key in seen_keys:
            continue
        selected.append(word)
        seen_keys.add(key)

        if limit is not None and len(selected) >= limit:
            break

    if limit is not None and len(selected) < min(limit, len(words)):
        remaining = []
        for word in words:
            key = f"{normalize_text(word['english']).lower()}::{normalize_text(word['uzbek']).lower()}"
            if key not in seen_keys:
                remaining.append(word)

        random.shuffle(remaining)
        for word in remaining:
            selected.append(word)
            if len(selected) >= min(limit, len(words)):
                break

    return selected


def get_random_incorrect(correct_word, all_words, lang="eng"):
    if lang == "eng":
        candidates = [w["english"] for w in all_words if normalize_text(w["english"]) != normalize_text(correct_word)]
    else:
        candidates = [w["uzbek"] for w in all_words if normalize_text(w["uzbek"]) != normalize_text(correct_word)]

    candidates = list(dict.fromkeys(candidates))

    if not candidates:
        return []

    if len(candidates) >= 3:
        return random.sample(candidates, 3)

    return candidates


def build_test_queue(words: list[dict]) -> list[dict]:
    queue = []

    for word in words:
        q_type = "eng2uz" if random.random() < 0.5 else "uz2eng"
        queue.append({
            "q_type": q_type,
            "correct": word,
        })

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
        "Har bir so'zdan faqat 1 ta savol tushadi. Ya'ni bir so'z uchun inglizcha yoki o'zbekcha tomoni random tanlanadi.\n"
        f"Global test maksimal {GLOBAL_TEST_MAX_QUESTIONS} ta savol bilan cheklanadi.\n"
        "Agar testni oxirigacha ishlamasdan Menyu tugmasi bilan chiqib ketsangiz ham, ishlangan qism natijasi saqlanadi.\n"
        "Faqat Global test uchun ball beriladi va Leaderboard shu bo'lim asosida shakllanadi.\n\n"
        "👤 Mening testim\n"
        "Bu bo'limda faqat siz qo'shgan so'zlardan test ishlaysiz.\n"
        "Har bir so'zdan faqat 1 ta savol tushadi.\n"
        "Bu mashq rejimi hisoblanadi va ball qo'shilmaydi.\n"
        "Bot smart repetition mantiqidan foydalanadi: siz ko'proq xato qilgan so'zlar keyingi testlarda ko'proq tushadi.\n"
        "Test tugagach, xato ishlangan savollarni alohida qayta ishlash mumkin.\n\n"
        "❌ Xatolarni qayta ishlash\n"
        "Mening testim tugagandan keyin xato javob bergan savollar uchun alohida knopka chiqadi.\n"
        "Bu bo'limda faqat xato ishlangan savollar qayta beriladi.\n\n"
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
        "Savolda ko'rinadigan so'zlar kichik harflarda chiqariladi.\n"
        "Har bir savolda 1 ta to'g'ri javob va bir nechta random variant bo'ladi.\n"
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


def get_after_test_markup(test_mode_type: str | None, has_wrong_answers: bool):
    buttons = []

    if test_mode_type == "my" and has_wrong_answers:
        buttons.append([InlineKeyboardButton("❌ Xatolarni qayta ishlash", callback_data="retry_wrong")])

    buttons.append([InlineKeyboardButton("🔁 Yana test", callback_data="repeat_test")])
    buttons.append([InlineKeyboardButton("🏠 Menyu", callback_data="menu")])

    return InlineKeyboardMarkup(buttons)


async def safe_answer_callback(query):
    if not query:
        return False

    try:
        await query.answer()
        return True
    except BadRequest as e:
        logger.warning("Callback answer warning: %s", e)
        return False
    except Exception as e:
        logger.warning("Callback answer unexpected warning: %s", e)
        return False


async def safe_edit_or_send(query, text: str, reply_markup: InlineKeyboardMarkup | None = None):
    if not query:
        return

    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
        return
    except BadRequest as e:
        logger.warning("edit_message_text warning: %s", e)
    except Exception as e:
        logger.warning("edit_message_text unexpected warning: %s", e)

    try:
        await query.message.reply_text(text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.exception("reply_text error: %s", e)


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
        "current_options",
        "test_mode_type",
        "wrong_answers",
        "global_partial_saved",
    ]:
        context.user_data.pop(key, None)


def save_partial_global_result_if_needed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    test_mode_type = context.user_data.get("test_mode_type")
    score = context.user_data.get("score", {"total": 0, "correct": 0})
    already_saved = context.user_data.get("global_partial_saved", False)

    if test_mode_type != "global":
        return

    if already_saved:
        return

    total = score.get("total", 0)
    correct = score.get("correct", 0)

    if total <= 0:
        return

    user_id, username, full_name = get_user_meta(update)
    if user_id is None:
        return

    save_global_result(user_id, username, full_name, total, correct)
    context.user_data["global_partial_saved"] = True


async def finish_test(query, context: ContextTypes.DEFAULT_TYPE, update: Update):
    score = context.user_data.get("score", {"total": 0, "correct": 0})
    total = score["total"]
    correct = score["correct"]
    percent = round((correct / total) * 100, 1) if total > 0 else 0
    test_mode_type = context.user_data.get("test_mode_type")
    has_wrong_answers = bool(context.user_data.get("wrong_answers"))

    user_id, username, full_name = get_user_meta(update)

    if test_mode_type == "global" and user_id is not None:
        save_global_result(user_id, username, full_name, total, correct)
        context.user_data["global_partial_saved"] = True
        total_global_score = get_user_total_global_score(user_id)

        text = (
            "✅ Global test tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%\n"
            f"🏅 Ushbu test uchun ball: {correct}\n"
            f"🔥 Umumiy global ballingiz: {total_global_score}"
        )
    elif test_mode_type == "my":
        text = (
            "✅ Mening testim tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%\n\n"
            "Bu mashq rejimi, ball qo'shilmadi."
        )
    elif test_mode_type == "my_retry":
        text = (
            "✅ Xato savollar testi tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%\n\n"
            "Bu mashq rejimi, ball qo'shilmadi."
        )
    else:
        text = (
            "✅ Test tugadi!\n\n"
            f"📊 Natija: {correct}/{total}\n"
            f"📈 Foiz: {percent}%"
        )

    markup = get_after_test_markup(test_mode_type, has_wrong_answers)

    await safe_edit_or_send(
        query,
        text,
        reply_markup=markup,
    )

    if test_mode_type == "global":
        clear_test_state(context)
    else:
        for key in [
            "test_words",
            "score",
            "correct",
            "q_type",
            "test_queue",
            "current_question",
            "current_options",
            "test_mode_type",
            "global_partial_saved",
        ]:
            context.user_data.pop(key, None)


# =========================
# HANDLERS
# =========================
async def restart_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_partial_global_result_if_needed(update, context)

    clear_test_state(context)
    context.user_data.pop("eng", None)

    if update.message:
        await update.message.reply_text(
            get_start_text(),
            reply_markup=get_main_menu_markup(),
        )
    elif update.callback_query:
        query = update.callback_query
        await safe_answer_callback(query)
        await safe_edit_or_send(
            query,
            get_start_text(),
            reply_markup=get_main_menu_markup(),
        )

    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_partial_global_result_if_needed(update, context)

    clear_test_state(context)
    context.user_data.pop("eng", None)
    await update.message.reply_text(
        get_start_text(),
        reply_markup=get_main_menu_markup(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

    save_partial_global_result_if_needed(update, context)

    clear_test_state(context)
    context.user_data.pop("eng", None)
    await safe_edit_or_send(query, get_start_text(), reply_markup=get_main_menu_markup())


async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

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
    await safe_answer_callback(query)
    await safe_edit_or_send(
        query,
        build_rules_text(),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
        ),
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)
    await safe_edit_or_send(query, "Inglizcha so'zni yozing:\n(Bekor qilish uchun /cancel yozing)")
    return ENGLISH


async def add_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = normalize_text(update.message.text if update.message else "")
    if not text:
        await update.message.reply_text("Iltimos, inglizcha so'zni yuboring.")
        return ENGLISH

    context.user_data["eng"] = text
    await update.message.reply_text("O'zbekcha tarjimasini yozing:\n(Bekor qilish uchun /cancel yozing)")
    return UZBEK


async def add_uzbek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eng = normalize_text(context.user_data.get("eng", ""))
    uzb = normalize_text(update.message.text if update.message else "")

    if not eng:
        await update.message.reply_text("⚠️ Avval inglizcha so'zni kiriting.")
        return ConversationHandler.END

    if not uzb:
        await update.message.reply_text("⚠️ O'zbekcha tarjimani kiriting.")
        return UZBEK

    user_id, username, full_name = get_user_meta(update)
    if user_id is None:
        await update.message.reply_text("❌ Foydalanuvchi ma'lumoti topilmadi.")
        return ConversationHandler.END

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
    save_partial_global_result_if_needed(update, context)

    clear_test_state(context)
    context.user_data.pop("eng", None)

    await update.message.reply_text(
        "Bekor qilindi.",
        reply_markup=get_main_menu_markup(),
    )
    return ConversationHandler.END


async def my_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

    data = query.data
    page = 0
    try:
        page = int(data.split("_")[-1])
    except Exception:
        page = 0

    user_id, _, _ = get_user_meta(update)
    words = get_user_words(user_id) if user_id is not None else []

    text, page = format_words_page("📚 Mening so'zlarim", words, page, MY_WORDS_PAGE_SIZE, show_owner=False)
    markup = build_pagination_markup("my_words", page, len(words), MY_WORDS_PAGE_SIZE)
    await safe_edit_or_send(query, text, reply_markup=markup)


async def global_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

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
    await safe_answer_callback(query)

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

    selected_words = words[:]
    random.shuffle(selected_words)
    selected_words = selected_words[:min(GLOBAL_TEST_MAX_QUESTIONS, len(selected_words))]

    context.user_data["test_words"] = selected_words
    context.user_data["score"] = {"total": 0, "correct": 0}
    context.user_data["test_queue"] = build_test_queue(selected_words)
    context.user_data["test_mode_type"] = "global"
    context.user_data["wrong_answers"] = []
    context.user_data["global_partial_saved"] = False

    await generate_question(update, context, query)


async def my_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

    user_id, _, _ = get_user_meta(update)
    words = get_user_words(user_id) if user_id is not None else []

    if len(words) < 4:
        await safe_edit_or_send(
            query,
            "Mening testim uchun siz kamida 4 ta so'z qo'shgan bo'lishingiz kerak!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
            ),
        )
        return

    selected_words = build_weighted_words(
        words,
        user_id=user_id,
        limit=min(MY_TEST_MAX_QUESTIONS, len(words)),
    )

    context.user_data["test_words"] = selected_words
    context.user_data["score"] = {"total": 0, "correct": 0}
    context.user_data["test_queue"] = build_test_queue(selected_words)
    context.user_data["test_mode_type"] = "my"
    context.user_data["wrong_answers"] = []
    context.user_data["global_partial_saved"] = False

    await generate_question(update, context, query)


async def retry_wrong_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

    wrongs = context.user_data.get("wrong_answers", [])

    if not wrongs:
        await safe_edit_or_send(
            query,
            "Xato ishlangan savollar topilmadi.",
            reply_markup=get_main_menu_markup(),
        )
        return

    retry_queue = wrongs[:]
    random.shuffle(retry_queue)

    context.user_data["test_words"] = [item["correct"] for item in retry_queue]
    context.user_data["score"] = {"total": 0, "correct": 0}
    context.user_data["test_queue"] = retry_queue
    context.user_data["test_mode_type"] = "my_retry"
    context.user_data["wrong_answers"] = []
    context.user_data["global_partial_saved"] = False

    await generate_question(update, context, query)


async def repeat_test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer_callback(query)

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

    if len(words) < 4 and context.user_data.get("test_mode_type") not in {"my_retry"}:
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
        question_text = f"❓ {correct['english'].lower()} = ?"
        wrongs = get_random_incorrect(correct["uzbek"], words, "uz")
        options = wrongs + [correct["uzbek"]]
        prefix = "uz_"
    else:
        question_text = f"❓ {correct['uzbek'].lower()} = ?"
        wrongs = get_random_incorrect(correct["english"], words, "eng")
        options = wrongs + [correct["english"]]
        prefix = "eng_"

    options = list(dict.fromkeys(options))
    random.shuffle(options)
    context.user_data["current_options"] = options

    keyboard = []
    for i, opt in enumerate(options):
        keyboard.append([InlineKeyboardButton(opt.lower(), callback_data=f"{prefix}{i}")])

    keyboard.append([InlineKeyboardButton("🏠 Menyu", callback_data="menu")])

    score = context.user_data["score"]
    total_questions = score["total"] + len(test_queue) + 1
    used_count = score["total"]
    remaining = total_questions - used_count

    mode_type = context.user_data.get("test_mode_type")
    if mode_type == "global":
        mode_title = "🌍 Global test"
    elif mode_type == "my_retry":
        mode_title = "❌ Xato savollar testi"
    else:
        mode_title = "👤 Mening testim"

    score_text = (
        f"{mode_title}\n"
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
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(
                final_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    answered = await safe_answer_callback(query)
    if not answered:
        return

    if "current_question" not in context.user_data:
        await safe_edit_or_send(
            query,
            "⚠️ Test holati topilmadi. Qaytadan boshlang.",
            reply_markup=get_main_menu_markup(),
        )
        return

    data = query.data
    correct = context.user_data.get("correct")
    q_type = context.user_data.get("q_type")
    options = context.user_data.get("current_options", [])

    if not correct or not q_type or not options:
        await safe_edit_or_send(
            query,
            "⚠️ Test holati topilmadi. Qaytadan boshlang.",
            reply_markup=get_main_menu_markup(),
        )
        return

    context.user_data["score"]["total"] += 1

    try:
        if data.startswith("eng_"):
            index = int(data[4:])
            user_ans = options[index]
            is_correct = user_ans == correct["english"]
        elif data.startswith("uz_"):
            index = int(data[3:])
            user_ans = options[index]
            is_correct = user_ans == correct["uzbek"]
        else:
            raise ValueError("Unknown callback prefix")
    except Exception:
        await safe_edit_or_send(
            query,
            "⚠️ Javobni qayta ishlashda xatolik. Qaytadan boshlang.",
            reply_markup=get_main_menu_markup(),
        )
        return

    if is_correct:
        context.user_data["score"]["correct"] += 1
        feedback_text = "✅ To'g'ri!"
    else:
        if context.user_data.get("test_mode_type") in {"my", "my_retry"}:
            wrong_answers = context.user_data.get("wrong_answers", [])
            wrong_answers.append(
                {
                    "q_type": q_type,
                    "correct": correct,
                }
            )
            context.user_data["wrong_answers"] = wrong_answers

        if q_type == "eng2uz":
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct['english'].lower()} -> {correct['uzbek'].lower()}"
        else:
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct['uzbek'].lower()} -> {correct['english'].lower()}"

    test_mode_type = context.user_data.get("test_mode_type")
    if test_mode_type in {"my", "my_retry"}:
        user_id, _, _ = get_user_meta(update)
        if user_id is not None:
            update_word_progress(
                user_id=user_id,
                english=correct["english"],
                uzbek=correct["uzbek"],
                is_correct=is_correct,
            )

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
    app.add_handler(CallbackQueryHandler(retry_wrong_handler, pattern="^retry_wrong$"))
    app.add_handler(CallbackQueryHandler(repeat_test_handler, pattern="^repeat_test$"))
    app.add_handler(CallbackQueryHandler(my_words_handler, pattern=r"^my_words_\d+$"))
    app.add_handler(CallbackQueryHandler(global_words_handler, pattern=r"^global_words_\d+$"))
    app.add_handler(CallbackQueryHandler(leaderboard_handler, pattern=r"^leaderboard_\d+$"))
    app.add_handler(CallbackQueryHandler(rules_handler, pattern="^rules$"))
    app.add_handler(CallbackQueryHandler(check_answer, pattern=r"^(eng_|uz_)\d+$"))

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
