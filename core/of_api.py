"""
OnlyFans API клиент — все HTTP запросы к OF.
Используется вместо Playwright для загрузки контента.
"""
import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("contentflow")

OF_API_URL = "https://onlyfans.com/api2/v2"


class OFApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"OF API Error {status_code}: {message}")


class OFApiClient:
    """
    Клиент для работы с OnlyFans API.
    Принимает готовый httpx.AsyncClient с куки аккаунта.
    """

    def __init__(self, client: httpx.AsyncClient, account_id: int):
        self.client = client
        self.account_id = account_id

    async def get_me(self) -> dict:
        """Получить инфо о текущем аккаунте."""
        res = await self.client.get(f"{OF_API_URL}/users/me")
        self._check(res)
        return res.json()

    async def get_vault_categories(self) -> list[dict]:
        """Получить список категорий Vault."""
        res = await self.client.get(f"{OF_API_URL}/vault/lists")
        self._check(res)
        data = res.json()
        return data if isinstance(data, list) else data.get("list", [])

    async def create_vault_category(self, name: str) -> dict:
        """Создать новую категорию Vault."""
        res = await self.client.post(
            f"{OF_API_URL}/vault/lists",
            json={"name": name}
        )
        self._check(res)
        return res.json()

    async def upload_file(self, file_path: str) -> Optional[str]:
        """
        Загружает файл в OF и возвращает media_id.
        Использует двухшаговый процесс: получаем upload URL → загружаем файл.
        """
        path = Path(file_path)
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size

        # Шаг 1: Запрашиваем URL для загрузки
        init_res = await self.client.post(
            f"{OF_API_URL}/users/me/files/upload",
            json={
                "name": path.name,
                "type": mime_type,
                "size": file_size,
            }
        )
        self._check(init_res)
        upload_data = init_res.json()

        upload_url = upload_data.get("upload_url") or upload_data.get("signedUrl")
        media_id = upload_data.get("id") or upload_data.get("fileId")

        if not upload_url:
            raise OFApiError(0, "Не получили upload URL от OF")

        # Шаг 2: Загружаем файл по полученному URL
        with open(file_path, "rb") as f:
            upload_res = await self.client.put(
                upload_url,
                content=f.read(),
                headers={"Content-Type": mime_type},
            )

        if upload_res.status_code not in (200, 201, 204):
            raise OFApiError(upload_res.status_code, "Ошибка загрузки файла")

        return str(media_id)

    async def add_to_vault_category(self, media_ids: list[str], category_id: str) -> bool:
        """Перемещает медиафайлы в категорию Vault."""
        res = await self.client.post(
            f"{OF_API_URL}/vault/lists/{category_id}/media",
            json={"ids": media_ids}
        )
        self._check(res)
        return True

    async def get_or_create_category(self, name: str) -> str:
        """Находит категорию по имени или создаёт новую."""
        categories = await self.get_vault_categories()
        for cat in categories:
            if cat.get("name", "").lower().strip() == name.lower().strip():
                return str(cat["id"])

        # Категория не найдена — создаём
        logger.info(f"[Account {self.account_id}] Создаю категорию '{name}'")
        new_cat = await self.create_vault_category(name)
        return str(new_cat["id"])

    def _check(self, res: httpx.Response):
        if res.status_code == 401:
            raise OFApiError(401, "Сессия истекла")
        if res.status_code == 429:
            raise OFApiError(429, "Rate limit — слишком много запросов")
        if res.status_code >= 400:
            try:
                detail = res.json().get("error", {}).get("message", res.text[:100])
            except Exception:
                detail = res.text[:100]
            raise OFApiError(res.status_code, detail)
