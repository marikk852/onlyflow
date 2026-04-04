from pathlib import Path

import database as db
import config


def _profile_dir(account_id: int) -> str:
    p = Path(config.PROFILES_DIR) / str(account_id)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


async def open_browser_for_login(account_id: int) -> dict:
    """
    Открывает видимый браузер с профилем аккаунта.
    Пользователь вручную логинится — куки сохраняются в профиль.
    """
    from playwright.async_api import async_playwright

    profile = _profile_dir(account_id)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=False,
                args=["--no-sandbox"],
                viewport=config.VIEWPORT,
            )
            page = await browser.new_page()
            await page.goto("https://onlyfans.com/my/vault/list", wait_until="domcontentloaded")

            # Ждём пока пользователь закроет браузер (макс 10 минут)
            try:
                await browser.wait_for_event("close", timeout=600000)
            except Exception:
                pass
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

        db.set_session_ok(account_id, True)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def check_browser_session(account_id: int) -> dict:
    """Проверяет активность сессии (headless)."""
    from playwright.async_api import async_playwright
    import asyncio

    profile = _profile_dir(account_id)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
                args=["--no-sandbox"],
            )
            page = await browser.new_page()
            await page.goto("https://onlyfans.com/my/vault/list", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            url = page.url
            await browser.close()

            ok = "vault" in url or "/my/" in url
            db.set_session_ok(account_id, ok)
            return {"ok": ok, "url": url}
    except Exception as e:
        db.set_session_ok(account_id, False)
        return {"ok": False, "error": str(e)}
