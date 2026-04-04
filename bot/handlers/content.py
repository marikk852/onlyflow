"""
Приём медиафайлов из разделов Telegram группы.
Скачивает файлы, вычисляет хэши, создаёт batch, проверяет дубликаты.
"""
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

from aiogram import Bot, Router
from aiogram.types import Message

import config
import database as db
from bot.keyboards import duplicate_action_kb, gallery_select_kb

router = Router()
logger = logging.getLogger("contentflow")

# Буфер для группировки медиагрупп
_media_buffer: dict[str, list] = {}
_media_timers: dict[str, asyncio.Task] = {}
BUFFER_DELAY = 3.0  # секунд ждём пока придут все файлы из группы


async def _sha256(path: Path) -> str:
    loop = asyncio.get_event_loop()
    def _hash():
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    return await loop.run_in_executor(None, _hash)


async def _flush_buffer(bot: Bot, group_key: str, model_id: int):
    """Обрабатывает накопленный буфер медиафайлов."""
    await asyncio.sleep(BUFFER_DELAY)

    messages = _media_buffer.pop(group_key, [])
    _media_timers.pop(group_key, None)

    if not messages:
        return

    model = db.get_model_by_id(model_id)
    if not model:
        return

    # Скачиваем файлы
    batch_dir = Path(config.DOWNLOADS_DIR) / f"tmp_{group_key}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    file_paths = []
    msg_ids = []
    file_hashes = []
    photos_count = 0
    videos_count = 0

    for msg in messages:
        file_id = None
        filename = None

        if msg.photo:
            file_id = msg.photo[-1].file_id
            filename = f"{msg.message_id}.jpg"
            photos_count += 1
        elif msg.video:
            file_id = msg.video.file_id
            ext = msg.video.mime_type.split("/")[-1] if msg.video.mime_type else "mp4"
            filename = f"{msg.message_id}.{ext}"
            videos_count += 1
        elif msg.document and msg.document.mime_type:
            if "image" in msg.document.mime_type or "video" in msg.document.mime_type:
                file_id = msg.document.file_id
                filename = msg.document.file_name or f"{msg.message_id}.bin"
                if "video" in msg.document.mime_type:
                    videos_count += 1
                else:
                    photos_count += 1

        if not file_id:
            continue

        dest = batch_dir / filename
        try:
            tg_file = await bot.get_file(file_id)
            await bot.download_file(tg_file.file_path, destination=str(dest))
        except Exception as e:
            logger.error(f"Ошибка скачивания файла {file_id}: {e}")
            continue

        fhash = await _sha256(dest)
        file_paths.append(str(dest))
        msg_ids.append(msg.message_id)
        file_hashes.append(fhash)

    if not file_paths:
        return

    # Создаём batch
    batch = db.create_batch(model_id, msg_ids, file_paths, file_hashes)

    # Переименовываем папку под batch_id
    final_dir = Path(config.DOWNLOADS_DIR) / str(batch.id)
    batch_dir.rename(final_dir)
    # Обновляем пути в batch
    new_paths = [str(final_dir / Path(p).name) for p in file_paths]
    import json
    with db.get_session() as s:
        from sqlalchemy import update
        from database import ContentBatch
        s.execute(update(ContentBatch).where(ContentBatch.id == batch.id).values(file_paths=json.dumps(new_paths)))
        s.commit()

    # Проверяем дубликаты
    duplicates = db.check_duplicates(file_hashes)

    # Уведомляем администратора
    if duplicates:
        dup_lines = [f"⚠️ <b>Обнаружены дубликаты</b>", f""]
        dup_lines.append(f"{len(duplicates)} из {len(file_paths)} файлов уже были загружены:")
        for d in duplicates[:10]:
            ago = _time_ago(d["uploaded_at"])
            dup_lines.append(f"• {d['account_name']} / {d['gallery_name']} • {ago}")
        if len(duplicates) > 10:
            dup_lines.append(f"  ...и ещё {len(duplicates) - 10}")
        dup_lines.append(f"\nКак поступить с дубликатами?")

        await bot.send_message(
            config.ADMIN_ID,
            "\n".join(dup_lines),
            parse_mode="HTML",
            reply_markup=duplicate_action_kb(batch.id)
        )
    else:
        # Сразу показываем превью для выбора категорий
        await _show_category_selector(bot, batch.id, model, len(file_paths), photos_count, videos_count)


async def _show_category_selector(bot: Bot, batch_id: int, model, total: int, photos: int, videos: int):
    """Показывает администратору выбор категорий для каждого аккаунта."""
    accounts = db.get_accounts_by_model(model.id)
    if not accounts:
        await bot.send_message(config.ADMIN_ID, f"⚠️ У модели {model.name} нет аккаунтов.")
        return

    accounts_galleries = []
    for acc in accounts:
        galleries = db.get_galleries(acc.id)
        if galleries:
            accounts_galleries.append({
                "account_id": acc.id,
                "account_name": acc.name,
                "galleries": [{"id": g.id, "name": g.name} for g in galleries]
            })

    if not accounts_galleries:
        await bot.send_message(config.ADMIN_ID, f"⚠️ У аккаунтов модели {model.name} нет категорий.")
        return

    text = (
        f"📁 <b>Новый контент — {model.name}</b>\n"
        f"{total} файлов ({photos} фото, {videos} видео)\n\n"
        f"Выбери категорию для каждого аккаунта:"
    )
    await bot.send_message(
        config.ADMIN_ID,
        text,
        parse_mode="HTML",
        reply_markup=gallery_select_kb(accounts_galleries, batch_id)
    )


def _time_ago(dt) -> str:
    from datetime import datetime, timezone
    now = datetime.utcnow()
    diff = now - dt
    days = diff.days
    if days == 0:
        hours = diff.seconds // 3600
        return f"{hours} ч. назад" if hours > 0 else "только что"
    return f"{days} дн. назад"


@router.message()
async def handle_media(message: Message, bot: Bot):
    """Обрабатывает входящие медиафайлы из разделов группы."""
    # Только из группы, только из разделов
    if message.chat.id != config.GROUP_ID:
        return
    if not message.message_thread_id:
        return
    # Только медиафайлы
    if not (message.photo or message.video or message.document):
        return

    topic_id = message.message_thread_id

    # Находим модель по разделу
    model = db.get_model_by_forum_topic(topic_id)
    if not model:
        return  # раздел не привязан — игнорируем

    # Группируем файлы из медиагруппы или по topic_id
    group_key = message.media_group_id or f"topic_{topic_id}_{message.message_id}"

    if group_key not in _media_buffer:
        _media_buffer[group_key] = []

    _media_buffer[group_key].append(message)

    # Перезапускаем таймер сброса
    if group_key in _media_timers:
        _media_timers[group_key].cancel()

    task = asyncio.create_task(_flush_buffer(bot, group_key, model.id))
    _media_timers[group_key] = task
