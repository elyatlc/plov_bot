import datetime
import json
import logging
import os
from datetime import time
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
)

# ──────────────────────────── НАЛАШТУВАННЯ ────────────────────────────
TOKEN = os.environ["TOKEN"]  # токен із змінної оточення Railway
DATA_FILE = Path(__file__).parent / "data.json"
POLL_WEEKDAY = 4   # 0 = неділя ... 4 = четвер
POLL_HOUR = 9      # година ранку за київським часом

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("plovik")

# ──────────────────────────── ЗБЕРІГАННЯ ДАНИХ ────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"users": {}, "rating": {}}


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
def attending_list_text(data: dict) -> str:
    coming = [u["name"] for u in data.get("users", {}).values() if u.get("coming")]
    lines = ["🍛 На пловікє йдуть:"]
    if coming:
        lines += [f"• {name}" for name in coming]
    else:
        lines.append("• поки що ніхто 😢")
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


# ──────────────────────────── ЩОтижневий опитувач ────────────────────────────
async def send_weekly_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.user_id
    data = load_data()
    user = data["users"].get(str(user_id))
    if not user:
        return

    user["coming"] = None  # новий тиждень — скидаємо відповідь

    poll_msg = await context.bot.send_message(
        chat_id=user_id,
        text="будеш на пловікє? 🍛",
        reply_markup=main_keyboard(),
    )
    list_msg = await context.bot.send_message(
        chat_id=user_id,
        text=attending_list_text(data),
    )
    user["poll_message_id"] = poll_msg.message_id
    user["list_message_id"] = list_msg.message_id
    save_data(data)


def schedule_weekly_job(app: Application, user_id: int) -> None:
    jobs = app.job_queue.get_jobs_by_name(f"poll_{user_id}")
    for job in jobs:
        job.schedule_removal()
    app.job_queue.run_daily(
        send_weekly_poll,
        time=time(hour=POLL_HOUR, minute=0),
        days=(POLL_WEEKDAY,),
        user_id=user_id,
        chat_id=user_id,
        name=f"poll_{user_id}",
    )


# ──────────────────────────── ХЕНДЛЕРИ ────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    data = load_data()
    data["users"].setdefault(str(user.id), {"name": user.first_name or "пловнік"})
    data["users"][str(user.id)]["name"] = user.first_name or "пловнік"
    save_data(data)
    schedule_weekly_job(context.application, user.id)

    await update.message.reply_text(
        f"Йо, {user.first_name}! Я — пловік 🍛\n"
        f"Щочетверга о {POLL_HOUR}:00 я питатиму «будеш на пловікє?» "
        "просто тут, в особистих.\n"
        "Кнопки завжди під рукою 👇",
        reply_markup=main_keyboard(),
    )


async def refresh_list_messages(context: ContextTypes.DEFAULT_TYPE,
                                data: dict) -> None:
    text = attending_list_text(data)
    for user_id, user in data["users"].items():
        if not user.get("list_message_id"):
            continue
        try:
            await context.bot.edit_message_text(
                chat_id=int(user_id),
                message_id=user["list_message_id"],
                text=text,
            )
        except Exception as e:
            log.debug("Не вдалося оновити список для %s: %s", user_id, e)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = load_data()
    user_id = str(user.id)
    user_data = data["users"].setdefault(user_id, {})
    user_data["name"] = user.first_name or user_data.get("name") or "пловнік"
    action = query.data

    if action == "who":
        await query.message.reply_text(attending_list_text(data))
        return

    if action == "rating":
        await query.message.reply_text(rating_text(data))
        return

    if action == "attend":
        first_vote_this_week = not user_data.get("coming")
        user_data["coming"] = True
        if first_vote_this_week:
            rating = data.setdefault("rating", {})
            rating.setdefault(user_id, {"name": user_data["name"], "count": 0})
            rating[user_id]["name"] = user_data["name"]
            rating[user_id]["count"] += 1
        save_data(data)
        await refresh_list_messages(context, data)
        await query.answer("Записав! Будеш на пловікє 🍛")

    elif action == "decline":
        user_data["coming"] = False
        save_data(data)
        await refresh_list_messages(context, data)
        await query.answer("Шкода... Ну нічого, наступного разу!")


# ──────────────────────────── ЗАПУСК ────────────────────────────
def main() -> None:
    data = load_data()
    kyiv_tz = datetime.timezone(datetime.timedelta(hours=3))
    app = (Application.builder()
           .token(TOKEN)
           .defaults(Defaults(tzinfo=kyiv_tz))
           .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    for user_id in data["users"]:
        try:
            schedule_weekly_job(app, int(user_id))
        except ValueError:
            continue

    log.info("пловік запущений 🍛")
    app.run_polling()


if __name__ == "__main__":
    main()
