import logging
import random
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- SOZLAMA (Railway environment variables) ----------
TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

# Tekshirish
if not TOKEN or not SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("Iltimos, Railwayda quyidagi environment variable'larni sozlang: BOT_TOKEN, SHEET_ID, GOOGLE_CREDENTIALS")

# Google Sheets ulanish (JSON string dan)
try:
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("words")
    print("✅ Google Sheetsga ulandi!")
except Exception as e:
    print(f"❌ Google Sheets ulanish xatosi: {e}")
    raise

# Holatlar
ENGLISH, UZBEK = range(2)

logging.basicConfig(level=logging.INFO)

# ---------- YORDAMCHI FUNKSIYALAR ----------
def get_all_words():
    """Barcha so'zlarni qaytaradi: [(ing, uz), ...]"""
    try:
        records = sheet.get_all_records()
        return [(row['english'], row['uzbek']) for row in records]
    except Exception as e:
        logging.error(f"Xatolik get_all_words: {e}")
        return []

def add_word(eng, uzb):
    try:
        sheet.append_row([eng, uzb])
        return True
    except Exception as e:
        logging.error(f"Xatolik add_word: {e}")
        return False

def get_random_incorrect(correct_word, all_words, lang='eng'):
    """To'g'ri javobdan farqli 3 ta noto'g'ri variant"""
    if lang == 'eng':
        candidates = [w[0] for w in all_words if w[0] != correct_word]
    else:
        candidates = [w[1] for w in all_words if w[1] != correct_word]
    
    if len(candidates) < 3:
        # Agar kam so'z bo'lsa, o'zini takrorlashga ruxsat
        candidates = candidates * 3
    return random.sample(candidates, min(3, len(candidates)))

# ---------- START / MENU ----------
async def start(update: Update, context):
    keyboard = [
        [InlineKeyboardButton("📖 Lug'at test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 Barcha so'zlar", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Xush kelibsiz! Quyidagi tugmalardan birini tanlang:\n\n"
        "📖 Lug'at test - so'zlarni o'rganish\n"
        "➕ So'z qo'shish - yangi so'z qo'shish\n"
        "📚 Barcha so'zlar - barcha so'zlarni ko'rish",
        reply_markup=reply_markup
    )

# ---------- SO'Z QO'SHISH (Conversation) ----------
async def add_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✍️ Inglizcha so'zni yozing:\n(Bekor qilish uchun /cancel yozing)")
    return ENGLISH

async def add_english(update: Update, context):
    context.user_data['eng'] = update.message.text.strip()
    await update.message.reply_text("🇺🇿 O'zbekcha tarjimasini yozing:\n(Bekor qilish uchun /cancel yozing)")
    return UZBEK

async def add_uzbek(update: Update, context):
    eng = context.user_data['eng']
    uzb = update.message.text.strip()
    
    if add_word(eng, uzb):
        await update.message.reply_text(f"✅ So'z qo'shildi!\n📝 {eng} -> {uzb}")
    else:
        await update.message.reply_text("❌ Xatolik yuz berdi. So'z qo'shib bo'lmadi.")
    
    # Menyuga qaytish
    keyboard = [
        [InlineKeyboardButton("📖 Lug'at test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 Barcha so'zlar", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Yana bir amal tanlang:", reply_markup=reply_markup)
    
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
        await query.edit_message_text("📭 Hali hech qanday so'z yo'q.\n➕ So'z qo'shish tugmasidan foydalaning!")
        return
    
    text = "📚 **Barcha so'zlar ro'yxati:**\n\n"
    for i, (eng, uzb) in enumerate(words, 1):
        text += f"{i}. 🇬🇧 {eng}  →  🇺🇿 {uzb}\n"
        if len(text) > 3800:
            text += f"\n... va yana {len(words)-i} ta so'z"
            break
    
    # Menyuga qaytish tugmasi
    keyboard = [[InlineKeyboardButton("🔙 Menyu", callback_data='menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# ---------- TEST REJIMI ----------
async def test_mode(update: Update, context):
    query = update.callback_query
    await query.answer()
    words = get_all_words()
    
    if len(words) < 4:
        await query.edit_message_text(
            "❌ Test uchun kamida 4 ta so'z kerak!\n"
            f"Hozircha {len(words)} ta so'z bor.\n"
            "➕ So'z qo'shish tugmasidan foydalaning."
        )
        return
    
    context.user_data['test_words'] = words
    context.user_data['test_score'] = {'total': 0, 'correct': 0}
    await generate_question(update, context, query)

async def generate_question(update, context, query=None):
    words = context.user_data['test_words']
    
    # Test turini random tanlash
    q_type = random.choice(['eng2uz', 'uz2eng'])
    correct = random.choice(words)
    context.user_data['correct_answer'] = correct
    context.user_data['current_question_type'] = q_type
    
    if q_type == 'eng2uz':
        # Inglizcha so'z, o'zbekcha variantlar
        question_text = f"❓ **{correct[0]}** ning ma'nosi qaysi?"
        wrongs = get_random_incorrect(correct[1], words, lang='uz')
        options = wrongs + [correct[1]]
        random.shuffle(options)
        callback_prefix = 'ans_uz_'
    else:
        # O'zbekcha so'z, inglizcha variantlar
        question_text = f"❓ **{correct[1]}** ning inglizchasi qaysi?"
        wrongs = get_random_incorrect(correct[0], words, lang='eng')
        options = wrongs + [correct[0]]
        random.shuffle(options)
        callback_prefix = 'ans_eng_'
    
    # Tugmalar yaratish (2x2 qilib)
    keyboard = []
    for i in range(0, len(options), 2):
        row = []
        for j in range(2):
            if i+j < len(options):
                row.append(InlineKeyboardButton(options[i+j], callback_data=f"{callback_prefix}{options[i+j]}"))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔁 Keyingi savol", callback_data='next_question')])
    keyboard.append([InlineKeyboardButton("🔙 Menyu", callback_data='menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    score = context.user_data['test_score']
    score_text = f"📊 Natija: {score['correct']}/{score['total']}\n\n"
    
    if query:
        await query.edit_message_text(score_text + question_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.callback_query.message.reply_text(score_text + question_text, reply_markup=reply_markup, parse_mode='Markdown')

async def check_answer(update: Update, context):
    query = update.callback_query
    data = query.data
    correct = context.user_data['correct_answer']
    q_type = context.user_data.get('current_question_type', 'eng2uz')
    
    # Javobni tekshirish
    if data.startswith('ans_eng_'):
        user_answer = data[8:]
        is_correct = (user_answer == correct[0])
    elif data.startswith('ans_uz_'):
        user_answer = data[7:]
        is_correct = (user_answer == correct[1])
    else:
        return
    
    # Ballni yangilash
    context.user_data['test_score']['total'] += 1
    if is_correct:
        context.user_data['test_score']['correct'] += 1
        await query.answer("✅ To'g'ri!", show_alert=True)
    else:
        if q_type == 'eng2uz':
            await query.answer(f"❌ Xato! {correct[0]} -> {correct[1]}", show_alert=True)
        else:
            await query.answer(f"❌ Xato! {correct[1]} -> {correct[0]}", show_alert=True)
    
    # Yangi savol
    await generate_question(update, context, query)

async def next_question_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    await generate_question(update, context, query)

async def menu_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📖 Lug'at test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 Barcha so'zlar", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "🏠 **Asosiy menyu**\n\n"
        "👋 Xush kelibsiz! Quyidagi tugmalardan birini tanlang:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ---------- ASOSIY ----------
def main():
    app = Application.builder().token(TOKEN).build()
    
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
    
    # Handlerlar
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern='^menu$'))
    app.add_handler(CallbackQueryHandler(list_words, pattern='^list$'))
    app.add_handler(CallbackQueryHandler(test_mode, pattern='^test$'))
    app.add_handler(CallbackQueryHandler(next_question_callback, pattern='^next_question$'))
    app.add_handler(CallbackQueryHandler(check_answer, pattern='^ans_'))
    
    print("🤖 Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()
