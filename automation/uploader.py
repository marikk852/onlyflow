"""
Playwright — полный флоу загрузки файлов в OnlyFans Vault.
Загрузка через чат второго аккаунта → перемещение в категорию.
"""
import asyncio
import json
import logging
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from aiogram import Bot
from playwright.async_api import BrowserContext, Page, async_playwright

import config
import database as db
from automation.file_processor import randomize_file

logger = logging.getLogger("contentflow")


async def _human_delay(ms_min: int, ms_max: int):
    await asyncio.sleep(random.randint(ms_min, ms_max) / 1000)


def _profile_dir(account_id: int) -> str:
    p = Path(config.PROFILES_DIR) / str(account_id)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


async def _log_to_telegram(bot: Bot, text: str):
    """Пишет в раздел #log группы."""
    try:
        await bot.send_message(
            config.GROUP_ID,
            text,
            message_thread_id=config.LOG_TOPIC_ID,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки лога в TG: {e}")


async def upload_to_account(
    account_id: int,
    gallery_id: int,
    file_paths: List[str],
    file_hashes: List[str],
    batch_id: int,
) -> dict:
    """
    Основной флоу для одного аккаунта:
    1. Заходим в /my/chats — находим диалог со вторым аккаунтом модели
    2. Загружаем файлы порциями по 40
    3. Vault → Messages → выделяем → перемещаем в категорию
    4. Записываем в upload_history
    """
    acc = db.get_account(account_id)
    gallery = db.get_gallery(gallery_id)
    profile = _profile_dir(account_id)
    temp_dir = Path("uploads") / f"temp_{account_id}_{batch_id}"

    # Рандомизируем копии файлов
    randomized: List[str] = []
    for fp in file_paths:
        try:
            new_path = randomize_file(fp, str(temp_dir))
            randomized.append(new_path)
        except Exception as e:
            logger.error(f"randomize_file error: {e}")
            randomized.append(fp)

    try:
        async with async_playwright() as p:
            browser: BrowserContext = await p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=config.HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport=config.VIEWPORT,
            )

            page: Page = await browser.new_page()

            # Убираем webdriver флаг
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            # ── Заходим в Vault ──────────────────────────────────────────
            await page.goto("https://onlyfans.com/my/vault/list", wait_until="domcontentloaded")
            await _human_delay(1500, 3000)

            if "sign-in" in page.url or "login" in page.url:
                await browser.close()
                return {"ok": False, "error": "Сессия истекла — необходимо обновить логин"}

            # ── Загружаем файлы порциями через чат ──────────────────────
            uploaded_count = 0
            for i in range(0, len(randomized), config.UPLOAD_BATCH_SIZE):
                chunk = randomized[i:i + config.UPLOAD_BATCH_SIZE]
                ok = await _upload_chunk_via_chat(page, chunk)
                if ok:
                    uploaded_count += len(chunk)
                    await _human_delay(*config.DELAY_AFTER_UPLOAD)

            # ── Перемещаем из Messages в категорию ──────────────────────
            if gallery and uploaded_count > 0:
                await _move_from_messages_to_gallery(page, gallery.name, uploaded_count)

            await _human_delay(1000, 2000)
            await browser.close()

        # Записываем в историю
        for fh in file_hashes:
            db.add_history(fh, account_id, gallery_id, batch_id)

        return {"ok": True, "uploaded": uploaded_count}

    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


async def _upload_chunk_via_chat(page: Page, file_paths: List[str]) -> bool:
    """Загружает порцию файлов через чат (Messages)."""
    try:
        # Ищем поле загрузки в чате
        await page.goto("https://onlyfans.com/my/chats", wait_until="domcontentloaded")
        await _human_delay(2000, 3500)

        # Ищем первый диалог
        chat_selectors = [".b-chats__list-item", ".chat-item", "[data-type='chat']"]
        for sel in chat_selectors:
            elem = await page.query_selector(sel)
            if elem:
                await elem.click()
                await _human_delay(1000, 2000)
                break

        # Загружаем файлы
        file_input = await page.query_selector("input[type='file']")
        if not file_input:
            # Кликаем на кнопку добавления медиа
            for sel in [".b-attach__btn", "[data-type='attach']", "button[title*='attach' i]"]:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await _human_delay(500, 1000)
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        break

        if not file_input:
            logger.warning("Не найден input[type=file] в чате")
            return False

        for i, fp in enumerate(file_paths):
            if i > 0:
                await _human_delay(*config.DELAY_BETWEEN_FILES)
            await file_input.set_input_files(fp)
            await _human_delay(*[x // 2 for x in config.DELAY_BETWEEN_FILES])

        # Ждём загрузки всех файлов
        await asyncio.sleep(3 + len(file_paths) * 1.5)
        return True

    except Exception as e:
        logger.error(f"_upload_chunk_via_chat error: {e}")
        return False


async def _move_from_messages_to_gallery(page: Page, gallery_name: str, count: int):
    """
    Vault → Messages → выделяем последние N файлов → перемещаем в категорию.
    """
    try:
        await page.goto("https://onlyfans.com/my/vault/list/messages", wait_until="domcontentloaded")
        await _human_delay(2000, 3000)

        # Выбираем файлы (чекбоксы)
        checkboxes = await page.query_selector_all("input[type='checkbox'], .b-vault__item-checkbox")
        selected = 0
        for cb in checkboxes[:count]:
            await cb.click()
            await _human_delay(100, 300)
            selected += 1

        if selected == 0:
            logger.warning("Не удалось выбрать файлы для перемещения")
            return

        # Кнопка "Переместить"
        for sel in ["button:has-text('Move')", "button:has-text('Переместить')", ".b-move-button"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await _human_delay(800, 1500)
                    break
            except Exception:
                pass

        # Ищем нужную категорию в диалоге
        await _human_delay(500, 1000)
        elements = await page.query_selector_all("a, button, div[role='button'], li")
        for el in elements:
            try:
                text = await el.inner_text()
                if gallery_name.lower().strip() in text.lower().strip():
                    await el.click()
                    await _human_delay(1000, 2000)
                    break
            except Exception:
                pass

    except Exception as e:
        logger.error(f"_move_from_messages_to_gallery error: {e}")


async def run_upload_queue(bot: Bot, batch_id: int):
    """
    Запускает очередь загрузки для всех job-ов batch.
    """
    batch = db.get_batch(batch_id)
    if not batch:
        return

    file_paths = json.loads(batch.file_paths)
    file_hashes = json.loads(batch.file_hashes)
    jobs = db.get_pending_jobs(batch_id)
    model = db.get_model_by_id(batch.model_id)

    start_time = datetime.utcnow()
    results = []

    await _log_to_telegram(bot, f"⚡ Начинаю загрузку для {model.name if model else '?'} — {len(jobs)} аккаунт(ов), {len(file_paths)} файл(ов).")

    for i, job in enumerate(jobs):
        acc = db.get_account(job.account_id)
        gal = db.get_gallery(job.gallery_id)
        acc_name = acc.name if acc else str(job.account_id)
        gal_name = gal.name if gal else str(job.gallery_id)

        db.update_job_status(job.id, "running", started_at=datetime.utcnow())
        await _log_to_telegram(bot, f"⏳ [{i+1}/{len(jobs)}] {acc_name} → {gal_name}...")

        result = await upload_to_account(
            account_id=job.account_id,
            gallery_id=job.gallery_id,
            file_paths=file_paths,
            file_hashes=file_hashes,
            batch_id=batch_id,
        )

        if result["ok"]:
            db.update_job_status(job.id, "done", finished_at=datetime.utcnow())
            results.append(f"• {acc_name} → {gal_name} — {result['uploaded']} файл(ов) ✓")
            await _log_to_telegram(bot, f"✓ {acc_name} → {gal_name} — {result['uploaded']} файл(ов)")
        else:
            db.update_job_status(job.id, "error", error_msg=result["error"], finished_at=datetime.utcnow())
            results.append(f"• {acc_name} → {gal_name} — ✗ {result['error']}")
            await _log_to_telegram(bot, f"✗ {acc_name}: {result['error']}")

        # Пауза между аккаунтами
        if i < len(jobs) - 1:
            pause = random.randint(config.DELAY_BETWEEN_ACCOUNTS_MIN, config.DELAY_BETWEEN_ACCOUNTS_MAX)
            await _log_to_telegram(bot, f"⏸ Пауза {pause}с перед следующим аккаунтом...")
            await asyncio.sleep(pause)

    db.update_batch_status(batch_id, "done")

    elapsed = datetime.utcnow() - start_time
    mins = elapsed.seconds // 60
    secs = elapsed.seconds % 60

    summary = (
        f"✓ <b>{model.name if model else '?'} — загрузка завершена</b>\n"
        + "\n".join(results)
        + f"\nВремя: {mins} мин {secs} сек"
    )
    await _log_to_telegram(bot, summary)
    await bot.send_message(config.ADMIN_ID, summary, parse_mode="HTML")
