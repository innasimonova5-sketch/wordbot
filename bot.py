import asyncio
import logging
import os
import random
import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database as db

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class AddCommonWords(StatesGroup):
    waiting_text = State()


class Training(StatesGroup):
    waiting_answer = State()


# ---------- Вспомогательные ----------

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def parse_word_lines(text: str):
    """Парсит строки вида 'слово - перевод' или 'слово – перевод'."""
    pairs = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in (" - ", " – ", "-", ":"):
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    pairs.append((parts[0].strip(), parts[1].strip()))
                    break
    return pairs


# ---------- Базовые команды ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    db.add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = (
        "Привет! Я помогу тебе учить слова с помощью интервального повторения.\n\n"
        "/learn слово - перевод — добавить своё слово\n"
        "/catalog — посмотреть слова от учителя и добавить себе\n"
        "/train — начать тренировку\n"
        "/stats — посмотреть статистику\n"
    )
    if is_admin(message.from_user.id):
        text += "\nТы учитель, тебе доступна команда /addwords — загрузить список слов для всех учеников."
    await message.answer(text)


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    stats = db.get_stats(message.from_user.id)
    await message.answer(
        f"📊 Твоя статистика:\n"
        f"Всего слов в изучении: {stats['total']}\n"
        f"Хорошо выучено: {stats['learned']}\n"
        f"Готово к повторению сейчас: {stats['due']}"
    )


@dp.message(Command("learn"))
async def cmd_learn(message: Message):
    raw = message.text.partition(" ")[2]
    pairs = parse_word_lines(raw)
    if not pairs:
        await message.answer("Формат: /learn слово - перевод\nНапример: /learn apple - яблоко")
        return
    word, translation = pairs[0]
    ok = db.add_user_word(message.from_user.id, word, translation)
    if ok:
        await message.answer(f"Добавил: {word} — {translation}. Слово появится в /train.")
    else:
        await message.answer("Такое слово у тебя уже есть в списке.")


# ---------- Учитель: загрузка общего словаря ----------

@dp.message(Command("addwords"))
async def cmd_addwords(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только учителю.")
        return
    await message.answer(
        "Пришли список слов, по одному в строке, в формате:\n"
        "слово - перевод\n"
        "Например:\napple - яблоко\ncat - кошка"
    )
    await state.set_state(AddCommonWords.waiting_text)


@dp.message(AddCommonWords.waiting_text)
async def process_addwords(message: Message, state: FSMContext):
    pairs = parse_word_lines(message.text)
    if not pairs:
        await message.answer("Не нашёл ни одной пары вида 'слово - перевод'. Попробуй ещё раз.")
        return
    for word, translation in pairs:
        db.add_common_word(word, translation, message.from_user.id)
    await message.answer(f"Добавлено в общий словарь: {len(pairs)} слов(а).")
    await state.clear()


@dp.message(Command("catalog"))
async def cmd_catalog(message: Message):
    rows = db.get_common_words(limit=30)
    if not rows:
        await message.answer("Учитель ещё не загрузил общий словарь.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ {r['word']} — {r['translation']}", callback_data=f"take:{r['word_id']}")]
        for r in rows
    ])
    await message.answer("Слова от учителя. Нажми, чтобы добавить себе в список:", reply_markup=kb)


@dp.callback_query(F.data.startswith("take:"))
async def cb_take_word(call: CallbackQuery):
    word_id = int(call.data.split(":")[1])
    with db.get_conn() as conn:
        row = conn.execute("SELECT word, translation FROM common_words WHERE word_id=?", (word_id,)).fetchone()
    if not row:
        await call.answer("Слово не найдено.")
        return
    ok = db.add_user_word(call.from_user.id, row["word"], row["translation"])
    await call.answer("Добавлено!" if ok else "Уже есть в твоём списке.")


# ---------- Тренировка ----------

async def send_next_question(user_id: int, state: FSMContext, send_func):
    due = db.get_due_words(user_id, limit=1)
    if not due:
        await send_func("На сегодня слов для повторения нет 🎉 Загляни позже или добавь новые: /learn")
        await state.clear()
        return

    word_row = due[0]
    mode = random.choice(["choice", "input"])

    if mode == "choice":
        distractors = db.get_random_distractors(user_id, word_row["id"], count=3)
        options = distractors + [word_row["translation"]]
        random.shuffle(options)
        if len(options) < 2:
            mode = "input"  # недостаточно слов для вариантов, переключаемся на ввод
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=opt, callback_data=f"ans:{word_row['id']}:{opt}")]
                for opt in options
            ])
            await state.update_data(word_id=word_row["id"], mode="choice")
            await state.set_state(Training.waiting_answer)
            await send_func(f"Как переводится: «{word_row['word']}»?", reply_markup=kb)
            return

    # mode == input
    await state.update_data(word_id=word_row["id"], mode="input")
    await state.set_state(Training.waiting_answer)
    await send_func(f"Как переводится: «{word_row['word']}»?\n(напиши перевод текстом)")


@dp.message(Command("train"))
async def cmd_train(message: Message, state: FSMContext):
    await send_next_question(message.from_user.id, state, message.answer)


@dp.callback_query(F.data.startswith("ans:"), Training.waiting_answer)
async def cb_answer(call: CallbackQuery, state: FSMContext):
    _, word_id_str, chosen = call.data.split(":", 2)
    word_id = int(word_id_str)
    data = await state.get_data()
    if data.get("word_id") != word_id:
        await call.answer()
        return

    with db.get_conn() as conn:
        row = conn.execute("SELECT translation, word FROM user_words WHERE id=?", (word_id,)).fetchone()
    correct = row and chosen.strip().lower() == row["translation"].strip().lower()
    db.update_progress(word_id, bool(correct))

    if correct:
        await call.message.edit_text(f"✅ Верно! {row['word']} — {row['translation']}")
    else:
        await call.message.edit_text(f"❌ Неверно. {row['word']} — {row['translation']}")
    await call.answer()

    await send_next_question(call.from_user.id, state, call.message.answer)


@dp.message(Training.waiting_answer)
async def msg_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("mode") != "input":
        return
    word_id = data.get("word_id")
    with db.get_conn() as conn:
        row = conn.execute("SELECT translation, word FROM user_words WHERE id=?", (word_id,)).fetchone()
    if not row:
        await state.clear()
        return
    correct = message.text.strip().lower() == row["translation"].strip().lower()
    db.update_progress(word_id, correct)
    if correct:
        await message.answer(f"✅ Верно! {row['word']} — {row['translation']}")
    else:
        await message.answer(f"❌ Неверно. Правильный ответ: {row['word']} — {row['translation']}")

    await send_next_question(message.from_user.id, state, message.answer)


# ---------- Ежедневное напоминание ----------

async def daily_reminder():
    for user_id in db.get_all_user_ids():
        due_count = db.count_due_words(user_id)
        if due_count > 0:
            try:
                await bot.send_message(
                    user_id,
                    f"🔔 У тебя {due_count} слов(а) готовы к повторению. Набери /train, чтобы потренироваться."
                )
            except Exception as e:
                logging.warning(f"Не смог отправить напоминание {user_id}: {e}")


async def handle_ping(request):
    return web.Response(text="Bot is running")


async def start_fake_webserver():
    """Render Free Web Service требует, чтобы сервис слушал HTTP-порт.
    Этот сервер ничего не делает, кроме как отвечает 'ok' на пинг."""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Fake webserver started on port {port}")


async def main():
    db.init_db()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(daily_reminder, "cron", hour=config.REMINDER_HOUR_UTC, minute=0)
    scheduler.start()

    await start_fake_webserver()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
