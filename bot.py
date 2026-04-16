import logging
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- SOZLAMA ----------


# Google Sheets ulanish
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_KEY).worksheet("words")

# Holatlar
ENGLISH, UZBEK = range(2)

logging.basicConfig(level=logging.INFO)

# ---------- YORDAMCHI FUNKSIYALAR ----------
def get_all_words():
    """Barcha so'zlarni qaytaradi: [(ing, uz), ...]"""
    records = sheet.get_all_records()
    return [(row['english'], row['uzbek']) for row in records]

def add_word(eng, uzb):
    sheet.append_row([eng, uzb])

def get_random_incorrect(correct_word, all_words, lang='eng'):
    """To'g'ri javobdan farqli 3 ta noto'g'ri variant"""
    if lang == 'eng':
        candidates = [w[0] for w in all_words if w[0] != correct_word]
    else:
        candidates = [w[1] for w in all_words if w[1] != correct_word]
    if len(candidates) < 3:
        candidates = candidates * 3
    return random.sample(candidates, 3)

# ---------- START / MENU ----------
async def start(update: Update, context):
    keyboard = [
        [InlineKeyboardButton("📖 Lug'at test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 Barcha so'zlar", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Xush kelibsiz! Quyidagi tugmalardan birini tanlang:", reply_markup=reply_markup)

# ---------- SO'Z QO'SHISH (Conversation) ----------
async def add_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✍️ Inglizcha so'zni yozing:")
    return ENGLISH

async def add_english(update: Update, context):
    context.user_data['eng'] = update.message.text.strip()
    await update.message.reply_text("🇺🇿 O'zbekcha tarjimasini yozing:")
    return UZBEK

async def add_uzbek(update: Update, context):
    eng = context.user_data['eng']
    uzb = update.message.text.strip()
    add_word(eng, uzb)
    await update.message.reply_text(f"✅ So'z qo'shildi:\n{eng} -> {uzb}")
    return ConversationHandler.END

async def add_cancel(update: Update, context):
    await update.message.reply_text("❌ So'z qo'shish bekor qilindi.")
    return ConversationHandler.END

# ---------- BARCHA SO'ZLAR ----------
async def list_words(update: Update, context):
    query = update.callback_query
    await query.answer()
    words = get_all_words()
    if not words:
        await query.edit_message_text("📭 Hali hech qanday so'z yo'q.")
        return
    text = "📚 Barcha so'zlar:\n"
    for eng, uzb in words:
        text += f"🔹 {eng} - {uzb}\n"
        if len(text) > 3800:
            text += "..."
            break
    await query.edit_message_text(text)

# ---------- TEST REJIMI ----------
async def test_mode(update: Update, context):
    query = update.callback_query
    await query.answer()
    words = get_all_words()
    if len(words) < 4:
        await query.edit_message_text("❌ Test uchun kamida 4 ta so'z kerak. Avval so'z qo'shing.")
        return
    context.user_data['test_words'] = words
    await generate_question(update, context, query)

async def generate_question(update, context, query):
    words = context.user_data['test_words']
    q_type = random.choice(['eng2uz', 'uz2eng'])
    correct = random.choice(words)
    context.user_data['correct_answer'] = correct

    if q_type == 'eng2eng':  # Inglizcha -> o'zbekcha variantlar
        question_text = f"❓ {correct[0]} ning tarjimasi qaysi?"
        wrongs = get_random_incorrect(correct[1], words, lang='uz')
        options = wrongs + [correct[1]]
        random.shuffle(options)
        callback_prefix = 'ans_uz_'
    else:  # O'zbekcha -> inglizcha variantlar (default)
        question_text = f"❓ {correct[1]} ning inglizchasi qaysi?"
        wrongs = get_random_incorrect(correct[0], words, lang='eng')
        options = wrongs + [correct[0]]
        random.shuffle(options)
        callback_prefix = 'ans_eng_'

    keyboard = []
    for opt in options:
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"{callback_prefix}{opt}")])
    keyboard.append([InlineKeyboardButton("🔁 Keyingi savol", callback_data='next_question')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(question_text, reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(question_text, reply_markup=reply_markup)

async def check_answer(update: Update, context):
    query = update.callback_query
    data = query.data
    correct = context.user_data['correct_answer']

    if data.startswith('ans_eng_'):
        user_answer = data[8:]
        if user_answer == correct[0]:
            await query.answer("✅ To'g'ri!", show_alert=True)
        else:
            await query.answer(f"❌ Xato! To'g'ri javob: {correct[0]}", show_alert=True)
    elif data.startswith('ans_uz_'):
        user_answer = data[7:]
        if user_answer == correct[1]:
            await query.answer("✅ To'g'ri!", show_alert=True)
        else:
            await query.answer(f"❌ Xato! To'g'ri javob: {correct[1]}", show_alert=True)

    # Yangi savolni avtomatik chiqarish
    await generate_question(update, context, query)

async def next_question_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    await generate_question(update, context, query)

# ---------- ASOSIY ----------
def main():
    app = Application.builder().token(TOKEN).build()

    # Handlerlar
    app.add_handler(CommandHandler("start", start))

    # So'z qo'shish conversation
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern='^add$')],
        states={
            ENGLISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_english)],
            UZBEK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_uzbek)],
        },
        fallbacks=[CommandHandler('cancel', add_cancel)],
    )
    app.add_handler(add_conv)

    # Boshqa callbacklar
    app.add_handler(CallbackQueryHandler(list_words, pattern='^list$'))
    app.add_handler(CallbackQueryHandler(test_mode, pattern='^test$'))
    app.add_handler(CallbackQueryHandler(next_question_callback, pattern='^next_question$'))
    app.add_handler(CallbackQueryHandler(check_answer, pattern='^ans_'))

    print("Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()
