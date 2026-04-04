"""
Обработка callback-кнопок: дубликаты, выбор категорий, запуск загрузки.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

import config
import database as db
from bot.keyboards import gallery_select_kb
from automation.uploader import run_upload_queue

router = Router()
logger = logging.getLogger("contentflow")

# Хранит выбранные категории: {batch_id: {account_id: gallery_id}}
_selections: dict[int, dict[int, int]] = {}


# ── Дубликаты ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("dup:"))
async def cb_duplicate_action(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Нет доступа.")
        return

    _, action, batch_id_str = callback.data.split(":")
    batch_id = int(batch_id_str)
    batch = db.get_batch(batch_id)

    if not batch:
        await callback.message.edit_text("Batch не найден.")
        await callback.answer()
        return

    model = db.get_model_by_id(batch.model_id)

    if action == "cancel":
        db.update_batch_status(batch_id, "cancelled")
        await callback.message.edit_text("⊘ Загрузка отменена.")

    elif action == "skip":
        # Убираем дубликаты из batch
        file_hashes = json.loads(batch.file_hashes)
        file_paths = json.loads(batch.file_paths)
        duplicates = db.check_duplicates(file_hashes)
        dup_hashes = {d["hash"] for d in duplicates}

        new_paths = []
        new_hashes = []
        for p, h in zip(file_paths, file_hashes):
            if h not in dup_hashes:
                new_paths.append(p)
                new_hashes.append(h)

        if not new_paths:
            db.update_batch_status(batch_id, "cancelled")
            await callback.message.edit_text("Все файлы — дубликаты. Загрузка отменена.")
            await callback.answer()
            return

        # Обновляем batch
        with db.get_session() as s:
            from sqlalchemy import update
            from database import ContentBatch
            s.execute(update(ContentBatch).where(ContentBatch.id == batch_id).values(
                file_paths=json.dumps(new_paths),
                file_hashes=json.dumps(new_hashes),
            ))
            s.commit()

        await callback.message.edit_text(f"Дубликаты пропущены. Осталось файлов: {len(new_paths)}.")
        await _show_category_selector(bot, batch_id, model)

    elif action == "all":
        await callback.message.edit_text("Загружаем всё включая дубликаты.")
        await _show_category_selector(bot, batch_id, model)

    await callback.answer()


# ── Выбор категорий ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("selgal:"))
async def cb_select_gallery(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Нет доступа.")
        return

    parts = callback.data.split(":")
    batch_id = int(parts[1])
    account_id = int(parts[2])
    gallery_id = int(parts[3])

    if batch_id not in _selections:
        _selections[batch_id] = {}
    _selections[batch_id][account_id] = gallery_id

    acc = db.get_account(account_id)
    gal = db.get_gallery(gallery_id)
    await callback.answer(f"✓ {acc.name if acc else account_id} → {gal.name if gal else gallery_id}")

    # Обновляем клавиатуру чтобы показать сделанный выбор
    batch = db.get_batch(batch_id)
    model = db.get_model_by_id(batch.model_id) if batch else None
    if model:
        accounts = db.get_accounts_by_model(model.id)
        accounts_galleries = []
        for a in accounts:
            galleries = db.get_galleries(a.id)
            if galleries:
                selected_id = _selections.get(batch_id, {}).get(a.id)
                ags = []
                for g in galleries:
                    name = f"{'✓ ' if g.id == selected_id else ''}{g.name}"
                    ags.append({"id": g.id, "name": name})
                accounts_galleries.append({
                    "account_id": a.id,
                    "account_name": a.name,
                    "galleries": ags,
                })
        try:
            await callback.message.edit_reply_markup(
                reply_markup=gallery_select_kb(accounts_galleries, batch_id)
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("launch:"))
async def cb_launch(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Нет доступа.")
        return

    batch_id = int(callback.data.split(":")[1])
    batch = db.get_batch(batch_id)
    if not batch:
        await callback.message.edit_text("Batch не найден.")
        await callback.answer()
        return

    selections = _selections.get(batch_id, {})
    if not selections:
        await callback.answer("Сначала выбери категории для аккаунтов.", show_alert=True)
        return

    file_paths = json.loads(batch.file_paths)
    files_count = len(file_paths)

    # Создаём upload_jobs
    for account_id, gallery_id in selections.items():
        db.create_upload_job(batch_id, account_id, gallery_id, files_count)

    db.update_batch_status(batch_id, "uploading", approved_at=datetime.utcnow())

    await callback.message.edit_text(
        f"⚡ Загрузка запущена.\n"
        f"Аккаунтов: {len(selections)}, файлов: {files_count} на каждый.\n"
        f"Слежу за прогрессом в разделе #log."
    )
    await callback.answer()

    # Запускаем очередь в фоне
    asyncio.create_task(run_upload_queue(bot, batch_id))


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Нет доступа.")
        return
    batch_id = int(callback.data.split(":")[1])
    db.update_batch_status(batch_id, "cancelled")
    _selections.pop(batch_id, None)
    await callback.message.edit_text("⊘ Отменено.")
    await callback.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _show_category_selector(bot: Bot, batch_id: int, model):
    accounts = db.get_accounts_by_model(model.id)
    accounts_galleries = []
    for acc in accounts:
        galleries = db.get_galleries(acc.id)
        if galleries:
            accounts_galleries.append({
                "account_id": acc.id,
                "account_name": acc.name,
                "galleries": [{"id": g.id, "name": g.name} for g in galleries],
            })
    if not accounts_galleries:
        await bot.send_message(config.ADMIN_ID, f"⚠️ У аккаунтов модели {model.name} нет категорий.")
        return

    batch = db.get_batch(batch_id)
    file_paths = json.loads(batch.file_paths) if batch else []

    text = (
        f"📁 <b>Контент — {model.name}</b>\n"
        f"{len(file_paths)} файл(ов)\n\n"
        f"Выбери категорию для каждого аккаунта:"
    )
    await bot.send_message(
        config.ADMIN_ID,
        text,
        parse_mode="HTML",
        reply_markup=gallery_select_kb(accounts_galleries, batch_id),
    )
