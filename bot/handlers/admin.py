import shutil
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import config
import database as db
from bot.keyboards import confirm_delete_kb
from automation.session import open_browser_for_login, check_browser_session

router = Router()


def is_admin(message: Message) -> bool:
    return message.from_user and message.from_user.id == config.ADMIN_ID


# ── FSM States ────────────────────────────────────────────────────────────────

class SetForum(StatesGroup):
    waiting_for_model_name = State()
    waiting_for_forwarded_msg = State()


# ── Модели ────────────────────────────────────────────────────────────────────

@router.message(Command("addmodel"))
async def cmd_add_model(message: Message):
    if not is_admin(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /addmodel [имя]")
        return
    name = args[1].strip()
    if db.get_model_by_name(name):
        await message.answer(f"Модель «{name}» уже существует.")
        return
    model = db.add_model(name)
    await message.answer(
        f"✓ Модель {model.name} создана (ID: {model.id}).\n"
        f"Привяжи раздел: /setforum {model.name}"
    )


@router.message(Command("setforum"))
async def cmd_set_forum_start(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /setforum <имя_модели>")
        return
    name = args[1].strip()
    model = db.get_model_by_name(name)
    if not model:
        await message.answer(f"Модель «{name}» не найдена.")
        return
    await state.update_data(model_id=model.id, model_name=model.name)
    await state.set_state(SetForum.waiting_for_forwarded_msg)
    await message.answer(
        f"Перешли любое сообщение из раздела группы для модели {model.name}.\n"
        "Или отправь /cancel чтобы отменить."
    )


@router.message(SetForum.waiting_for_forwarded_msg)
async def cmd_set_forum_receive(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    if message.text and message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.")
        return

    topic_id = message.message_thread_id
    if not topic_id:
        await message.answer("Это сообщение не из раздела (topic). Перешли сообщение из нужного раздела группы.")
        return

    data = await state.get_data()
    model_id = data["model_id"]
    model_name = data["model_name"]

    # Привязываем forum_topic_id к аккаунтам модели
    accounts = db.get_accounts_by_model(model_id)
    if not accounts:
        await state.clear()
        await message.answer(
            f"У модели {model_name} нет аккаунтов. Сначала добавь аккаунт: /addaccount {model_name} <название>"
        )
        return

    for acc in accounts:
        db.set_account_forum_topic(acc.id, topic_id)

    await state.clear()
    await message.answer(f"✓ Раздел (topic_id={topic_id}) привязан к модели {model_name}.")


@router.message(Command("models"))
async def cmd_models(message: Message):
    if not is_admin(message):
        return
    models = db.get_all_models()
    if not models:
        await message.answer("Моделей пока нет. /addmodel <имя>")
        return
    lines = ["<b>Модели:</b>"]
    for m in models:
        accounts = db.get_accounts_by_model(m.id)
        ok = sum(1 for a in accounts if a.session_ok)
        lines.append(
            f"\n<b>{m.name}</b> (ID: {m.id})"
            f"\n  Аккаунтов: {len(accounts)} (сессий: {ok}/{len(accounts)})"
        )
        for a in accounts:
            status = "✓" if a.session_ok else "✗"
            galleries = db.get_galleries(a.id)
            lines.append(f"  {status} [{a.id}] {a.name} — {len(galleries)} категорий")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deletemodel"))
async def cmd_delete_model(message: Message):
    if not is_admin(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /deletemodel <имя>")
        return
    name = args[1].strip()
    model = db.get_model_by_name(name)
    if not model:
        await message.answer(f"Модель «{name}» не найдена.")
        return
    await message.answer(
        f"Удалить модель {model.name} и все её аккаунты?",
        reply_markup=confirm_delete_kb("model", model.id)
    )


# ── Аккаунты ──────────────────────────────────────────────────────────────────

@router.message(Command("addaccount"))
async def cmd_add_account(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Использование: /addaccount <модель> <название> [url]")
        return
    model_name = parts[1]
    acc_name = parts[2]
    url = parts[3] if len(parts) > 3 else ""

    model = db.get_model_by_name(model_name)
    if not model:
        await message.answer(f"Модель «{model_name}» не найдена.")
        return

    acc = db.add_account(model.id, acc_name, url)
    await message.answer(
        f"✓ Аккаунт [{acc.id}] {acc.name} добавлен к модели {model.name}.\n"
        f"Настрой сессию: /setsession {acc.id}"
    )


@router.message(Command("setsession"))
async def cmd_set_session(message: Message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /setsession <account_id>")
        return
    account_id = int(args[1])
    acc = db.get_account(account_id)
    if not acc:
        await message.answer(f"Аккаунт {account_id} не найден.")
        return

    await message.answer(f"Открываю браузер для [{acc.id}] {acc.name}...\nЗалогинься и закрой окно.")
    result = await open_browser_for_login(account_id)
    if result["ok"]:
        await message.answer(f"✓ Сессия для [{acc.id}] {acc.name} сохранена.")
    else:
        await message.answer(f"✗ Ошибка: {result.get('error', 'неизвестно')}")


@router.message(Command("checksession"))
async def cmd_check_session(message: Message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /checksession <account_id>")
        return
    account_id = int(args[1])
    acc = db.get_account(account_id)
    if not acc:
        await message.answer(f"Аккаунт {account_id} не найден.")
        return

    await message.answer(f"Проверяю сессию [{acc.id}] {acc.name}...")
    result = await check_browser_session(account_id)
    if result["ok"]:
        await message.answer(f"✓ Сессия [{acc.id}] {acc.name} активна.")
    else:
        await message.answer(f"✗ Сессия [{acc.id}] {acc.name} истекла. Используй /setsession {acc.id}")


@router.message(Command("deleteaccount"))
async def cmd_delete_account(message: Message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /deleteaccount <account_id>")
        return
    account_id = int(args[1])
    acc = db.get_account(account_id)
    if not acc:
        await message.answer(f"Аккаунт {account_id} не найден.")
        return
    await message.answer(
        f"Удалить аккаунт [{acc.id}] {acc.name}?",
        reply_markup=confirm_delete_kb("account", acc.id)
    )


# ── Категории ─────────────────────────────────────────────────────────────────

@router.message(Command("addcategory"))
async def cmd_add_category(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Использование: /addcategory <account_id> <название>")
        return
    account_id = int(parts[1])
    name = parts[2].strip()
    acc = db.get_account(account_id)
    if not acc:
        await message.answer(f"Аккаунт {account_id} не найден.")
        return
    g = db.add_gallery(account_id, name)
    await message.answer(f"✓ Категория [{g.id}] «{g.name}» добавлена к аккаунту {acc.name}.")


@router.message(Command("categories"))
async def cmd_categories(message: Message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /categories <account_id>")
        return
    account_id = int(args[1])
    acc = db.get_account(account_id)
    if not acc:
        await message.answer(f"Аккаунт {account_id} не найден.")
        return
    galleries = db.get_galleries(account_id)
    if not galleries:
        await message.answer(f"У аккаунта {acc.name} нет категорий.\n/addcategory {account_id} <название>")
        return
    lines = [f"<b>Категории [{acc.id}] {acc.name}:</b>"]
    for g in galleries:
        lines.append(f"  [{g.id}] {g.name}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deletecategory"))
async def cmd_delete_category(message: Message):
    if not is_admin(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /deletecategory <category_id>")
        return
    gallery_id = int(args[1])
    g = db.get_gallery(gallery_id)
    if not g:
        await message.answer(f"Категория {gallery_id} не найдена.")
        return
    await message.answer(
        f"Удалить категорию [{g.id}] «{g.name}»?",
        reply_markup=confirm_delete_kb("gallery", g.id)
    )


# ── Мониторинг ────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message):
        return
    accounts = db.get_all_accounts()
    if not accounts:
        await message.answer("Аккаунтов нет.")
        return
    lines = ["<b>Статус аккаунтов:</b>"]
    for a in accounts:
        status = "✓ активна" if a.session_ok else "✗ нет сессии"
        topic = f"topic={a.forum_topic_id}" if a.forum_topic_id else "раздел не привязан"
        lines.append(f"[{a.id}] {a.name} — {status} ({topic})")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("queue"))
async def cmd_queue(message: Message):
    if not is_admin(message):
        return
    jobs = db.get_queue()
    if not jobs:
        await message.answer("Очередь пуста.")
        return
    lines = ["<b>Очередь загрузки:</b>"]
    for j in jobs:
        acc = db.get_account(j.account_id)
        gal = db.get_gallery(j.gallery_id)
        lines.append(
            f"[Job {j.id}] {acc.name if acc else '?'} → {gal.name if gal else '?'} "
            f"— {j.files_count} файл(ов) [{j.status}]"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("log"))
async def cmd_log(message: Message):
    if not is_admin(message):
        return
    history = db.get_last_history(20)
    if not history:
        await message.answer("История загрузок пуста.")
        return
    lines = ["<b>Последние 20 загрузок:</b>"]
    for h in history:
        acc = db.get_account(h.account_id)
        gal = db.get_gallery(h.gallery_id)
        lines.append(
            f"• {h.uploaded_at.strftime('%d.%m %H:%M')} — "
            f"{acc.name if acc else '?'} / {gal.name if gal else '?'}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Callback: подтверждение удаления ─────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm_delete:"))
async def cb_confirm_delete(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Нет доступа.")
        return
    _, entity, entity_id_str = callback.data.split(":")
    entity_id = int(entity_id_str)

    if entity == "model":
        model = db.get_model_by_id(entity_id)
        name = model.name if model else str(entity_id)
        db.delete_model(entity_id)
        await callback.message.edit_text(f"✓ Модель {name} удалена.")

    elif entity == "account":
        acc = db.get_account(entity_id)
        name = acc.name if acc else str(entity_id)
        db.delete_account(entity_id)
        profile_dir = Path("profiles") / str(entity_id)
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        await callback.message.edit_text(f"✓ Аккаунт {name} удалён.")

    elif entity == "gallery":
        g = db.get_gallery(entity_id)
        name = g.name if g else str(entity_id)
        db.delete_gallery(entity_id)
        await callback.message.edit_text(f"✓ Категория «{name}» удалена.")

    await callback.answer()


@router.callback_query(F.data == "cancel_delete")
async def cb_cancel_delete(callback: CallbackQuery):
    await callback.message.edit_text("Отменено.")
    await callback.answer()
