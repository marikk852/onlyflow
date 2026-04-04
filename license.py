"""
Проверка лицензии при запуске ContentFlow.
"""
import hashlib
import os
import platform
import uuid

import requests

LICENSE_SERVER = os.environ.get("LICENSE_SERVER_URL", "")
LICENSE_KEY = os.environ.get("LICENSE_KEY", "")


def get_hardware_id() -> str:
    """Уникальный ID железа — MAC-адрес + имя машины."""
    mac = uuid.getnode()
    machine = platform.node()
    raw = f"{mac}-{machine}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def activate() -> dict:
    """Активация лицензии (первый запуск)."""
    try:
        res = requests.post(
            f"{LICENSE_SERVER}/activate",
            json={"key": LICENSE_KEY, "hardware_id": get_hardware_id()},
            timeout=10,
        )
        if res.status_code == 200:
            return {"ok": True, "agency": res.json().get("agency", "")}
        return {"ok": False, "error": res.json().get("detail", "Unknown error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def validate() -> dict:
    """Проверка лицензии при каждом запуске."""
    if not LICENSE_SERVER or not LICENSE_KEY:
        return {"ok": False, "error": "LICENSE_SERVER_URL или LICENSE_KEY не заданы в .env"}
    try:
        res = requests.post(
            f"{LICENSE_SERVER}/validate",
            json={"key": LICENSE_KEY, "hardware_id": get_hardware_id()},
            timeout=10,
        )
        if res.status_code == 200:
            return {"ok": True, "agency": res.json().get("agency", "")}
        return {"ok": False, "error": res.json().get("detail", "License invalid")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
