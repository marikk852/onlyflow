"""
Session Manager — управляет куки и HTTP сессиями всех аккаунтов.
Один раз логинимся через Playwright → сохраняем куки → используем для HTTP запросов.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

import config
import database as db

logger = logging.getLogger("contentflow")

# Заголовки имитирующие реальный браузер
BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://onlyfans.com",
    "Referer": "https://onlyfans.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

OF_BASE_URL = "https://onlyfans.com"
OF_API_URL = "https://onlyfans.com/api2/v2"


class AccountSession:
    """HTTP сессия одного аккаунта."""

    def __init__(self, account_id: int):
        self.account_id = account_id
        self.client: Optional[httpx.AsyncClient] = None
        self.cookies: dict = {}
        self.is_valid: bool = False

    async def load_cookies(self) -> bool:
        """Загружает куки из профиля браузера."""
        cookie_file = Path(config.PROFILES_DIR) / str(self.account_id) / "cookies.json"
        if not cookie_file.exists():
            # Пробуем загрузить из Playwright профиля
            cookies = await self._extract_playwright_cookies()
            if not cookies:
                logger.warning(f"[Account {self.account_id}] Куки не найдены")
                return False
        else:
            with open(cookie_file) as f:
                cookies = json.load(f)

        self.cookies = {c["name"]: c["value"] for c in cookies if "onlyfans.com" in c.get("domain", "")}
        await self._init_client()
        return True

    async def _extract_playwright_cookies(self) -> list:
        """Извлекает куки из Playwright профиля."""
        from playwright.async_api import async_playwright

        profile = Path(config.PROFILES_DIR) / str(self.account_id)
        if not profile.exists():
            return []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=True,
                    args=["--no-sandbox"],
                )
                cookies = await browser.cookies("https://onlyfans.com")
                await browser.close()

                # Сохраняем в json для быстрого доступа в будущем
                cookie_file = profile / "cookies.json"
                with open(cookie_file, "w") as f:
                    json.dump(cookies, f)

                return cookies
        except Exception as e:
            logger.error(f"[Account {self.account_id}] Ошибка извлечения куки: {e}")
            return []

    async def _init_client(self):
        """Создаёт httpx клиент с куки."""
        if self.client:
            await self.client.aclose()

        self.client = httpx.AsyncClient(
            headers=BASE_HEADERS,
            cookies=self.cookies,
            follow_redirects=True,
            timeout=30.0,
        )

    async def check_valid(self) -> bool:
        """Проверяет валидность сессии через API."""
        if not self.client:
            return False
        try:
            res = await self.client.get(f"{OF_API_URL}/users/me")
            self.is_valid = res.status_code == 200
            db.set_session_ok(self.account_id, self.is_valid)
            return self.is_valid
        except Exception as e:
            logger.error(f"[Account {self.account_id}] Ошибка проверки сессии: {e}")
            self.is_valid = False
            return False

    async def refresh_cookies(self) -> bool:
        """Обновляет куки из Playwright профиля."""
        cookie_file = Path(config.PROFILES_DIR) / str(self.account_id) / "cookies.json"
        if cookie_file.exists():
            cookie_file.unlink()  # Удаляем кэш
        return await self.load_cookies()

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None


class SessionManager:
    """
    Менеджер сессий всех аккаунтов.
    Синглтон — один экземпляр на всё приложение.
    """

    def __init__(self):
        self._sessions: dict[int, AccountSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, account_id: int) -> Optional[AccountSession]:
        """Возвращает сессию аккаунта, загружает если нужно."""
        async with self._lock:
            if account_id not in self._sessions:
                session = AccountSession(account_id)
                ok = await session.load_cookies()
                if not ok:
                    return None
                self._sessions[account_id] = session

            return self._sessions[account_id]

    async def validate(self, account_id: int) -> bool:
        """Проверяет и при необходимости обновляет сессию."""
        session = await self.get(account_id)
        if not session:
            return False

        is_valid = await session.check_valid()
        if not is_valid:
            logger.info(f"[Account {account_id}] Сессия истекла, обновляю куки...")
            ok = await session.refresh_cookies()
            if ok:
                is_valid = await session.check_valid()
        return is_valid

    async def invalidate(self, account_id: int):
        """Удаляет сессию из памяти (после смены логина)."""
        async with self._lock:
            if account_id in self._sessions:
                await self._sessions[account_id].close()
                del self._sessions[account_id]

    async def get_all_statuses(self) -> dict[int, bool]:
        """Возвращает статус сессий всех аккаунтов."""
        accounts = db.get_all_accounts()
        result = {}
        for acc in accounts:
            session = await self.get(acc.id)
            if session:
                result[acc.id] = await session.check_valid()
            else:
                result[acc.id] = False
        return result

    async def close_all(self):
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()


# Глобальный синглтон
session_manager = SessionManager()
