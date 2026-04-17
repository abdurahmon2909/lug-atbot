import logging
import random
import json
import os
import time

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

# ---------- SOZLAMA ----------
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

# Google Sheets ulanish
try:
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("words")
    print("✅ Google Sheetsga ulandi!")
except Exception as e:
    print(f"❌ Google Sheets error: {e}")
    raise

# Holatlar
ENGLISH, UZBEK = range(2)

# ---------- CACHE ----------
WORDS_CACHE = []
LAST_FETCH = 0
CACHE_TTL = 60  # sekund


# ---------- YORDAMCHI FUNKSIYALAR ----------
def get_main_menu_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📖 Test", callback_data="test")],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data="add")],
        [InlineKeyboardButton("📚 So'zlar", callback_data="list")],
    ]
    return InlineKeyboardMarkup(keyboard)


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

        records = sheet.get_all_records()

        words = []
        for row in records:
            eng = str(row.get("english", "")).strip()
            uzb = str(row.get("uzbek", "")).strip()
            if eng and uzb:
                words.append((eng, uzb))

        WORDS_CACHE = words
        LAST_FETCH = now
        return WORDS_CACHE

    except Exception as e:
        logger.exception("get_all_words error: %s", e)
        return WORDS_CACHE if WORDS_CACHE else []


def add_word(eng: str, uzb: str):
    eng = eng.strip()
    uzb = uzb.strip()

    try:
        words = get_all_words()

        eng_lower = eng.lower()
        uzb_lower = uzb.lower()

        for existing_eng, existing_uzb in words:
            if (
                existing_eng.strip().lower() == eng_lower
                and existing_uzb.strip().lower() == uzb_lower
            ):
                return "exists"

        sheet.append_row([eng, uzb])

        invalidate_cache()
        get_all_words(force_refresh=True)

        return "ok"

    except Exception as e:
        logger.exception("add_word error: %s", e)
        return "error"


def get_random_incorrect(correct_word, all_words, lang="eng"):
    if lang == "eng":
        candidates = [w[0] for w in all_words if w[0] != correct_word]
    else:
        candidates = [w[1] for w in all_words if w[1] != correct_word]

    candidates = list(dict.fromkeys(candidates))

    if not candidates:
        return []

    if len(candidates) >= 3:
        return random.sample(candidates, 3)

    return candidates


async def safe_edit_or_send(
    query,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
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


# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xush kelibsiz! Tanlang:",
        reply_markup=get_main_menu_markup(),
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_or_send(query, "Inglizcha so'zni yozing:")
    return ENGLISH


async def add_english(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Iltimos, inglizcha so'zni yuboring.")
        return ENGLISH

    context.user_data["eng"] = text
    await update.message.reply_text("O'zbekcha tarjimasini yozing:")
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

    result = add_word(eng, uzb)

    if result == "ok":
        await update.message.reply_text(f"✅ Qo'shildi: {eng} -> {uzb}")
    elif result == "exists":
        await update.message.reply_text(f"⚠️ Bu so'z allaqachon bor: {eng} -> {uzb}")
    else:
        await update.message.reply_text("❌ Xatolik!")

    await update.message.reply_text(
        "Yana tanlang:",
        reply_markup=get_main_menu_markup(),
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bekor qilindi.",
        reply_markup=get_main_menu_markup(),
    )
    return ConversationHandler.END


async def list_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    words = get_all_words()

    if not words:
        await safe_edit_or_send(
            query,
            "So'zlar yo'q. /start bilan menyuga qayting.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
            ),
        )
        return

    text = "📚 So'zlar:\n\n"
    for i, (eng, uzb) in enumerate(words[:30], 1):
        text += f"{i}. {eng} - {uzb}\n"

    keyboard = [[InlineKeyboardButton("🔙 Menyu", callback_data="menu")]]
    await safe_edit_or_send(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def test_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    words = get_all_words()

    if len(words) < 4:
        await safe_edit_or_send(
            query,
            "Test uchun kamida 4 so'z kerak!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menyu", callback_data="menu")]]
            ),
        )
        return

    context.user_data["test_words"] = words
    context.user_data["score"] = {"total": 0, "correct": 0}

    await generate_question(update, context, query)


async def generate_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query=None,
    feedback_text: str = "",
):
    words = context.user_data.get("test_words", [])

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

    q_type = random.choice(["eng2uz", "uz2eng"])
    correct = random.choice(words)

    context.user_data["correct"] = correct
    context.user_data["q_type"] = q_type

    if q_type == "eng2uz":
        question_text = f"❓ {correct[0]} = ?"
        wrongs = get_random_incorrect(correct[1], words, "uz")
        options = wrongs + [correct[1]]
        prefix = "uz_"
    else:
        question_text = f"❓ {correct[1]} = ?"
        wrongs = get_random_incorrect(correct[0], words, "eng")
        options = wrongs + [correct[0]]
        prefix = "eng_"

    options = list(dict.fromkeys(options))
    random.shuffle(options)

    keyboard = []
    for opt in options:
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"{prefix}{opt}")])

    keyboard.append([InlineKeyboardButton("🏠 Menyu", callback_data="menu")])

    score = context.user_data["score"]
    score_text = f"📊 {score['correct']}/{score['total']}\n\n"

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
        is_correct = user_ans == correct[0]
    else:
        user_ans = data[3:]
        is_correct = user_ans == correct[1]

    if is_correct:
        context.user_data["score"]["correct"] += 1
        feedback_text = "✅ To'g'ri!"
    else:
        if q_type == "eng2uz":
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct[0]} -> {correct[1]}"
        else:
            feedback_text = f"❌ Xato!\nTo'g'ri javob: {correct[1]} -> {correct[0]}"

    await generate_question(update, context, query, feedback_text=feedback_text)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit_or_send(
        query,
        "🏠 Asosiy menyu",
        reply_markup=get_main_menu_markup(),
    )


# ---------- MAIN ----------
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
        fallbacks=[CommandHandler("cancel", add_cancel)],
        per_message=True,
    )

    app.add_error_handler(error_handler)

    app.add_handler(add_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(list_words, pattern="^list$"))
    app.add_handler(CallbackQueryHandler(test_mode, pattern="^test$"))
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
