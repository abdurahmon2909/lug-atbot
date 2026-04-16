import logging
import random
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- SOZLAMA ----------
TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

if not TOKEN or not SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("Environment variables not set!")

# Google Sheets ulanish
try:
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("words")
    print("✅ Google Sheets connected!")
except Exception as e:
    print(f"❌ Google Sheets error: {e}")
    raise

# Holatlar
ENGLISH, UZBEK = range(2)

logging.basicConfig(level=logging.INFO)

def get_all_words():
    try:
        records = sheet.get_all_records()
        return [(row['english'], row['uzbek']) for row in records]
    except:
        return []

def add_word(eng, uzb):
    try:
        sheet.append_row([eng, uzb])
        return True
    except:
        return False

def get_random_incorrect(correct_word, all_words, lang='eng'):
    if lang == 'eng':
        candidates = [w[0] for w in all_words if w[0] != correct_word]
    else:
        candidates = [w[1] for w in all_words if w[1] != correct_word]
    
    if len(candidates) < 3:
        candidates = candidates * 3
    return random.sample(candidates, min(3, len(candidates)))

# ---------- HANDLERS ----------
async def start(update: Update, context):
    keyboard = [
        [InlineKeyboardButton("📖 Test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 So'zlar", callback_data='list')]
    ]
    await update.message.reply_text(
        "👋 Xush kelibsiz! Tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Inglizcha so'zni yozing:")
    return ENGLISH

async def add_english(update: Update, context):
    context.user_data['eng'] = update.message.text.strip()
    await update.message.reply_text("O'zbekcha tarjimasini yozing:")
    return UZBEK

async def add_uzbek(update: Update, context):
    eng = context.user_data['eng']
    uzb = update.message.text.strip()
    
    if add_word(eng, uzb):
        await update.message.reply_text(f"✅ Qo'shildi: {eng} -> {uzb}")
    else:
        await update.message.reply_text("❌ Xatolik!")
    
    # Menyuga qaytish
    keyboard = [
        [InlineKeyboardButton("📖 Test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 So'zlar", callback_data='list')]
    ]
    await update.message.reply_text("Yana tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def add_cancel(update: Update, context):
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END

async def list_words(update: Update, context):
    query = update.callback_query
    await query.answer()
    words = get_all_words()
    
    if not words:
        await query.edit_message_text("So'zlar yo'q. /start bilan menyuga qayting.")
        return
    
    text = "📚 So'zlar:\n\n"
    for i, (eng, uzb) in enumerate(words[:30], 1):
        text += f"{i}. {eng} - {uzb}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Menyu", callback_data='menu')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def test_mode(update: Update, context):
    query = update.callback_query
    await query.answer()
    words = get_all_words()
    
    if len(words) < 4:
        await query.edit_message_text("Test uchun kamida 4 so'z kerak!")
        return
    
    context.user_data['test_words'] = words
    context.user_data['score'] = {'total': 0, 'correct': 0}
    await generate_question(update, context, query)

async def generate_question(update, context, query=None):
    words = context.user_data['test_words']
    q_type = random.choice(['eng2uz', 'uz2eng'])
    correct = random.choice(words)
    context.user_data['correct'] = correct
    context.user_data['q_type'] = q_type
    
    if q_type == 'eng2uz':
        text = f"❓ {correct[0]} = ?"
        wrongs = get_random_incorrect(correct[1], words, 'uz')
        options = wrongs + [correct[1]]
        random.shuffle(options)
        prefix = 'uz_'
    else:
        text = f"❓ {correct[1]} = ?"
        wrongs = get_random_incorrect(correct[0], words, 'eng')
        options = wrongs + [correct[0]]
        random.shuffle(options)
        prefix = 'eng_'
    
    keyboard = []
    for opt in options[:4]:
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"{prefix}{opt}")])
    
    keyboard.append([InlineKeyboardButton("➡️ Keyingi", callback_data='next')])
    keyboard.append([InlineKeyboardButton("🏠 Menyu", callback_data='menu')])
    
    score = context.user_data['score']
    score_text = f"📊 {score['correct']}/{score['total']}\n\n"
    
    if query:
        await query.edit_message_text(score_text + text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.message.reply_text(score_text + text, reply_markup=InlineKeyboardMarkup(keyboard))

async def check_answer(update: Update, context):
    query = update.callback_query
    data = query.data
    correct = context.user_data['correct']
    q_type = context.user_data['q_type']
    
    context.user_data['score']['total'] += 1
    
    if data.startswith('eng_'):
        user_ans = data[4:]
        is_correct = (user_ans == correct[0])
    else:
        user_ans = data[3:]
        is_correct = (user_ans == correct[1])
    
    if is_correct:
        context.user_data['score']['correct'] += 1
        await query.answer("✅ To'g'ri!", show_alert=True)
    else:
        if q_type == 'eng2uz':
            await query.answer(f"❌ Xato! {correct[0]} -> {correct[1]}", show_alert=True)
        else:
            await query.answer(f"❌ Xato! {correct[1]} -> {correct[0]}", show_alert=True)
    
    await generate_question(update, context, query)

async def next_question(update: Update, context):
    query = update.callback_query
    await query.answer()
    await generate_question(update, context, query)

async def menu(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📖 Test", callback_data='test')],
        [InlineKeyboardButton("➕ So'z qo'shish", callback_data='add')],
        [InlineKeyboardButton("📚 So'zlar", callback_data='list')]
    ]
    await query.edit_message_text(
        "🏠 Asosiy menyu",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ---------- MAIN ----------
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Conversation handler
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern='^add$')],
        states={
            ENGLISH: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_english)],
            UZBEK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_uzbek)],
        },
        fallbacks=[CommandHandler('cancel', add_cancel)],
    )
    
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu, pattern='^menu$'))
    app.add_handler(CallbackQueryHandler(list_words, pattern='^list$'))
    app.add_handler(CallbackQueryHandler(test_mode, pattern='^test$'))
    app.add_handler(CallbackQueryHandler(next_question, pattern='^next$'))
    app.add_handler(CallbackQueryHandler(check_answer, pattern='^(eng_|uz_)'))
    
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
