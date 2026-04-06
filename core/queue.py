"""
Очередь загрузки — параллельная загрузка в несколько аккаунтов одновременно.
"""
import asyncio
import json
import logging
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from aiogram import Bot

import config
import database as db
from core.file_processor import randomize_file
from core.of_api import OFApiClient, OFApiError
from core.session_manager import session_manager

logger = logging.getLogger("contentflow")

# Максимум параллельных загрузок
MAX_PARALLEL = 5


class UploadProgress:
    """Отслеживает прогресс загрузки для реалтайм обновлений."""

    def __init__(self, batch_id: int, total_jobs: int):
        self.batch_id = batch_id
        self.total_jobs = total_jobs
        self.done_jobs = 0
        self.results: list[dict] = []
        self.started_at = datetime.utcnow()
        self._callbacks: list[Callable] = []

    def on_update(self, callback: Callable):
        self._callbacks.append(callback)

    async def _notify(self):
        for cb in self._callbacks:
            try:
                await cb(self)
            except Exception:
                pass

    async def job_done(self, account_name: str, gallery_name: str, files_count: int):
        self.done_jobs += 1
        self.results.append({
            "account": account_name,
            "gallery": gallery_name,
            "files": files_count,
            "ok": True,
        })
        await self._notify()

    async def job_error(self, account_name: str, error: str):
        self.done_jobs += 1
        self.results.append({
            "account": account_name,
            "error": error,
            "ok": False,
        })
        await self._notify()

    @property
    def elapsed(self) -> str:
        diff = datetime.utcnow() - self.started_at
        m, s = divmod(diff.seconds, 60)
        return f"{m} мин {s} сек"

    @property
    def is_complete(self) -> bool:
        return self.done_jobs >= self.total_jobs


async def _upload_single_job(
    job: db.UploadJob,
    file_paths: list[str],
    file_hashes: list[str],
    progress: UploadProgress,
    semaphore: asyncio.Semaphore,
):
    """Загружает файлы в один аккаунт."""
    async with semaphore:
        acc = db.get_account(job.account_id)
        gal = db.get_gallery(job.gallery_id)
        acc_name = acc.name if acc else str(job.account_id)
        gal_name = gal.name if gal else str(job.gallery_id)

        db.update_job_status(job.id, "running", started_at=datetime.utcnow())

        temp_dir = Path("uploads") / f"temp_{job.account_id}_{job.batch_id}"
        randomized = []

        try:
            # Рандомизируем файлы
            for fp in file_paths:
                new_path = randomize_file(fp, str(temp_dir))
                randomized.append(new_path)

            # Получаем HTTP сессию аккаунта
            session = await session_manager.get(job.account_id)
            if not session or not session.client:
                raise Exception("Сессия не найдена — выполни /setsession")

            # Проверяем валидность
            is_valid = await session.check_valid()
            if not is_valid:
                raise Exception("Сессия истекла — выполни /setsession")

            api = OFApiClient(session.client, job.account_id)

            # Находим или создаём категорию
            category_id = await api.get_or_create_category(gal_name)

            # Загружаем файлы порциями
            uploaded_ids = []
            for i in range(0, len(randomized), config.UPLOAD_BATCH_SIZE):
                chunk = randomized[i:i + config.UPLOAD_BATCH_SIZE]
                chunk_ids = []

                for fp in chunk:
                    media_id = await api.upload_file(fp)
                    if media_id:
                        chunk_ids.append(media_id)
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                if chunk_ids:
                    await api.add_to_vault_category(chunk_ids, category_id)
                    uploaded_ids.extend(chunk_ids)

                await asyncio.sleep(random.uniform(1.0, 2.5))

            # Записываем историю
            for fh in file_hashes:
                db.add_history(fh, job.account_id, job.gallery_id, job.batch_id)

            db.update_job_status(job.id, "done", finished_at=datetime.utcnow())
            await progress.job_done(acc_name, gal_name, len(uploaded_ids))
            logger.info(f"[{acc_name}] ✓ {len(uploaded_ids)} файлов → {gal_name}")

        except OFApiError as e:
            if e.status_code == 401:
                await session_manager.invalidate(job.account_id)
                db.set_session_ok(job.account_id, False)
            err = f"{e.message}"
            db.update_job_status(job.id, "error", error_msg=err, finished_at=datetime.utcnow())
            await progress.job_error(acc_name, err)
            logger.error(f"[{acc_name}] ✗ {err}")

        except Exception as e:
            err = str(e)
            db.update_job_status(job.id, "error", error_msg=err, finished_at=datetime.utcnow())
            await progress.job_error(acc_name, err)
            logger.error(f"[{acc_name}] ✗ {err}")

        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)


async def run_upload_queue(
    batch_id: int,
    bot: Optional[Bot] = None,
    ws_callback: Optional[Callable] = None,
):
    """
    Запускает параллельную очередь загрузки для batch.
    bot — для отправки отчёта в Telegram
    ws_callback — для реалтайм обновлений в веб-панели
    """
    batch = db.get_batch(batch_id)
    if not batch:
        return

    file_paths = json.loads(batch.file_paths)
    file_hashes = json.loads(batch.file_hashes)
    jobs = db.get_pending_jobs(batch_id)
    model = db.get_model_by_id(batch.model_id)

    if not jobs:
        return

    progress = UploadProgress(batch_id, len(jobs))

    if ws_callback:
        progress.on_update(ws_callback)

    if bot:
        await _log_tg(bot, f"⚡ Загрузка для <b>{model.name if model else '?'}</b> — {len(jobs)} аккаунт(ов), {len(file_paths)} файл(ов).")

    # Семафор ограничивает параллельность
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    # Запускаем все джобы параллельно
    tasks = [
        asyncio.create_task(
            _upload_single_job(job, file_paths, file_hashes, progress, semaphore)
        )
        for job in jobs
    ]

    await asyncio.gather(*tasks, return_exceptions=True)

    db.update_batch_status(batch_id, "done")

    # Отчёт
    ok_jobs = [r for r in progress.results if r["ok"]]
    err_jobs = [r for r in progress.results if not r["ok"]]

    lines = [f"✓ <b>{model.name if model else '?'} — загрузка завершена</b>"]
    for r in ok_jobs:
        lines.append(f"• {r['account']} → {r['gallery']} — {r['files']} файл(ов) ✓")
    for r in err_jobs:
        lines.append(f"• {r['account']} — ✗ {r['error']}")
    lines.append(f"Время: {progress.elapsed}")

    summary = "\n".join(lines)

    if bot:
        await _log_tg(bot, summary)
        try:
            await bot.send_message(config.ADMIN_ID, summary, parse_mode="HTML")
        except Exception:
            pass

    return progress


async def _log_tg(bot: Bot, text: str):
    try:
        await bot.send_message(
            config.GROUP_ID,
            text,
            message_thread_id=config.LOG_TOPIC_ID,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"TG log error: {e}")
