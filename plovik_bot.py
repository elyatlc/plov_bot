import json
import logging
from datetime import time
from pathlib import Path
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ──────────────────────────── НАЛАШТУВАННЯ ────────────────────────────
TOKEN = os.environ["TOKEN"]
DATA_FILE = Path(__file__).parent / "data.json"
POLL_WEEKDAY = 3   # 0 = понеділок ... 3 = четвер
POLL_HOUR = 9      # година ранку, коли бот пише "будеш на пловікє?"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("plovik")

# ──────────────────────────── ЗБЕРІГАННЯ ДАНИХ ────────────────────────────
# data.json має вигляд:
# {
#   "chats": {
#     "<chat_id>": {
#       "title": "...",
#       "poll_message_id": 123,     # повідомлення з кнопками
#       "list_message_id": 456,     # повідомлення зі списком тих, хто буде
#       "attending": {"<user_id>": "<ім'я>", ...},
#       "declined":  {"<user_id>": "<ім'я>", ...}
#     }
#   },
#   "rating": {"<user_id>": {"name": "<ім'я>", "count": 5}, ...}
# }

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"chats": {}, "rating": {}}


def save_data(data: dict) -> None:
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ──────────────────────────── КЛАВІАТУРА ────────────────────────────
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("буду! ✅", callback_data="attend"),
            InlineKeyboardButton("ні... ❌", callback_data="decline"),
        ],
        [
            InlineKeyboardButton("хто буде 👥", callback_data="who"),
            InlineKeyboardButton("рейтинг пловніків 🏆", callback_data="rating"),
        ],
    ])


# ──────────────────────────── ТЕКСТИ ────────────────────────────
def attending_list_text(chat: dict) -> str:
    attending = chat.get("attending", {})
    declined = chat.get("declined", {})
    lines = ["🍛 На пловікє йдуть:"]
    if attending:
        lines += [f"• {name}" for name in attending.values()]
    else:
        lines.append("• поки що ніхто 😢")
    if declined:
        lines.append("")
        lines.append("Не йдуть: " + ", ".join(declined.values()))
    return "\n".join(lines)


def rating_text(data: dict) -> str:
    rating = sorted(
        data.get("rating", {}).values(),
        key=lambda r: r["count"],
        reverse=True,
    )
    if not rating:
        return "🏆 Рейтинг пловніків поки що порожній."
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Рейтинг пловніків:"]
    for i, r in enumerate(rating):
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} {r['name']} — {r['count']} пловіків")
    return "\n".join(lines)


# ──────────────────────────── Щотижневий опитувач ────────────────────────────
async def send_weekly_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    data = load_data()
    chat = data["chats"].get(str(chat_id))
    if not chat:
        return

    # новий тиждень — очищуємо відповіді
    chat["attending"] = {}
    chat["declined"] = {}

    poll_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="будеш на пловікє? 🍛",
        reply_markup=main_keyboard(),
    )
    list_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=attending_list_text(chat),
    )
    chat["poll_message_id"] = poll_msg.message_id
    chat["list_message_id"] = list_msg.message_id
    save_data(data)


def schedule_weekly_job(app: Application, chat_id: int) -> None:
    """Знімає старий щотижневий джоб і створює новий для чату."""
    jobs = app.job_queue.get_jobs_by_name(f"poll_{chat_id}")
    for job in jobs:
        job.schedule_removal()
    app.job_queue.run_weekly(
        send_weekly_poll,
        time=time(hour=POLL_HOUR, minute=0),
        days=(POLL_WEEKDAY,),
        chat_id=chat_id,
        name=f"poll_{chat_id}",
    )


# ──────────────────────────── ХЕНДЛЕРИ ────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text(
            "Привіт! Я — пловік 🍛\n"
            "Додай мене в груповий чат і запусти там /start — "
            "і щочетверга вранці я питатиму, хто йде на пловікє."
        )
        return

    data = load_data()
    data["chats"].setdefault(str(chat.id), {"title": chat.title or ""})
    save_data(data)
    schedule_weekly_job(context.application, chat.id)

    await update.message.reply_text(
        f"Йо! Я на звʼязку 🍛\n"
        f"Щочетверга о {POLL_HOUR}:00 я питатиму «будеш на пловікє?».\n"
        "Кнопки внизу завжди під рукою 👇",
        reply_markup=main_keyboard(),
    )


async def refresh_list_message(context: ContextTypes.DEFAULT_TYPE,
                               chat_id: int, data: dict) -> None:
    """Редагує повідомлення зі списком, щоб список був актуальним."""
    chat = data["chats"].get(str(chat_id))
    if not chat or not chat.get("list_message_id"):
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=chat["list_message_id"],
            text=attending_list_text(chat),
        )
    except Exception as e:  # повідомлення не змінилось або видалене
        log.debug("Не вдалося оновити список: %s", e)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = load_data()
    chat_id = query.message.chat_id
    chat = data["chats"].setdefault(str(chat_id), {})
    action = query.data
    user_id = str(user.id)
    display_name = user.first_name or user.username or "безіменний пловнік"

    if action == "who":
        await query.message.reply_text(attending_list_text(chat))
        return

    if action == "rating":
        await query.message.reply_text(rating_text(data))
        return

    attending = chat.setdefault("attending", {})
    declined = chat.setdefault("declined", {})
    rating = data.setdefault("rating", {})

    if action == "attend":
        attending[user_id] = display_name
        declined.pop(user_id, None)
        rating.setdefault(user_id, {"name": display_name, "count": 0})
        rating[user_id]["name"] = display_name
        if not context.user_data.get(f"counted_{chat_id}"):
            rating[user_id]["count"] += 1
            context.user_data[f"counted_{chat_id}"] = True
        save_data(data)
        await refresh_list_message(context, chat_id, data)
        await query.answer("Записав! Будеш на пловікє 🍛", show_alert=False)

    elif action == "decline":
        declined[user_id] = display_name
        attending.pop(user_id, None)
        save_data(data)
        await refresh_list_message(context, chat_id, data)
        await query.answer("Шкода... Ну нічого, наступного разу!", show_alert=False)


# ──────────────────────────── ЗАПУСК ────────────────────────────
def main() -> None:
    data = load_data()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    # відновлюємо щотижневі джоби для всіх відомих чатів після перезапуску
    for chat_id in data["chats"]:
        try:
            schedule_weekly_job(app, int(chat_id))
        except ValueError:
            continue

    log.info("пловік запущений 🍛")
    app.run_polling()


if __name__ == "__main__":
    main()
