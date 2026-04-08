import os
import secrets
import string
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import database as db

load_dotenv()

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]

app = FastAPI(title="OnlyFlow License Server", docs_url=None, redoc_url=None)

db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_key() -> str:
    """Генерирует ключ вида: FLOW-XXXX-XXXX-XXXX"""
    chars = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return "FLOW-" + "-".join(parts)


def check_admin(token: Optional[str]):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Client API ────────────────────────────────────────────────────────────────

class ActivateRequest(BaseModel):
    key: str
    hardware_id: str


class ValidateRequest(BaseModel):
    key: str
    hardware_id: str


@app.post("/activate")
def activate(data: ActivateRequest):
    """Активация лицензии при первом запуске."""
    lic = db.get_license(data.key)

    if not lic:
        raise HTTPException(status_code=404, detail="License key not found")
    if not lic.is_active:
        raise HTTPException(status_code=403, detail="License is revoked")
    if lic.hardware_id and lic.hardware_id != data.hardware_id:
        raise HTTPException(status_code=403, detail="License is already activated on another machine")

    lic = db.activate_license(data.key, data.hardware_id)
    return {"ok": True, "agency": lic.agency_name}


@app.post("/validate")
def validate(data: ValidateRequest):
    """Проверка лицензии при каждом запуске ContentFlow."""
    lic = db.get_license(data.key)

    if not lic:
        raise HTTPException(status_code=404, detail="License key not found")
    if not lic.is_active:
        raise HTTPException(status_code=403, detail="License is revoked")
    if lic.hardware_id != data.hardware_id:
        raise HTTPException(status_code=403, detail="Hardware ID mismatch")

    db.update_last_check(data.key)
    return {"ok": True, "agency": lic.agency_name}


# ── Admin API ─────────────────────────────────────────────────────────────────

@app.post("/admin/licenses/generate")
def admin_generate(
    agency_name: str = "",
    notes: str = "",
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)
    key = generate_key()
    while db.get_license(key):
        key = generate_key()
    lic = db.create_license(key, agency_name, notes)
    return {"key": lic.key, "agency": lic.agency_name}


@app.post("/admin/licenses/revoke/{key}")
def admin_revoke(key: str, x_admin_token: Optional[str] = Header(None)):
    check_admin(x_admin_token)
    if not db.get_license(key):
        raise HTTPException(status_code=404, detail="Not found")
    db.revoke_license(key)
    return {"ok": True}


@app.post("/admin/licenses/enable/{key}")
def admin_enable(key: str, x_admin_token: Optional[str] = Header(None)):
    check_admin(x_admin_token)
    if not db.get_license(key):
        raise HTTPException(status_code=404, detail="Not found")
    db.enable_license(key)
    return {"ok": True}


@app.post("/admin/licenses/reset-hardware/{key}")
def admin_reset_hardware(key: str, hardware_id: str = "", x_admin_token: Optional[str] = Header(None)):
    """Сброс привязки к железу. hardware_id — новый ID (опционально, иначе сброс в null)."""
    check_admin(x_admin_token)
    lic = db.get_license(key)
    if not lic:
        raise HTTPException(status_code=404, detail="Not found")
    db.reset_hardware(key, hardware_id or None)
    return {"ok": True, "hardware_id": hardware_id or None}


@app.delete("/admin/licenses/{key}")
def admin_delete(key: str, x_admin_token: Optional[str] = Header(None)):
    check_admin(x_admin_token)
    db.delete_license(key)
    return {"ok": True}


@app.get("/admin/licenses")
def admin_list(x_admin_token: Optional[str] = Header(None)):
    check_admin(x_admin_token)
    licenses = db.get_all_licenses()
    return [
        {
            "key": l.key,
            "agency": l.agency_name,
            "is_active": l.is_active,
            "hardware_id": l.hardware_id,
            "activated_at": l.activated_at,
            "last_check": l.last_check,
            "notes": l.notes,
        }
        for l in licenses
    ]


# ── Админ-панель (HTML) ───────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return HTMLResponse(content=ADMIN_HTML)


ADMIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OnlyFlow — Licenses</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 20px; }
  h1 { font-size: 22px; margin-bottom: 20px; color: #fff; }
  .token-form { display: flex; gap: 10px; margin-bottom: 24px; }
  input { background: #1e1e1e; border: 1px solid #333; color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 14px; }
  input:focus { outline: none; border-color: #555; }
  button { background: #2563eb; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; }
  button:hover { background: #1d4ed8; }
  button.danger { background: #dc2626; }
  button.danger:hover { background: #b91c1c; }
  button.success { background: #16a34a; }
  .generate-form { display: flex; gap: 10px; margin-bottom: 24px; flex-wrap: wrap; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; background: #1a1a1a; color: #888; border-bottom: 1px solid #2a2a2a; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e1e1e; }
  tr:hover td { background: #161616; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge.active { background: #14532d; color: #4ade80; }
  .badge.revoked { background: #450a0a; color: #f87171; }
  .key { font-family: monospace; font-size: 13px; color: #60a5fa; }
  .actions { display: flex; gap: 6px; }
  .actions button { padding: 4px 10px; font-size: 12px; }
  #status { padding: 10px; background: #1e3a1e; color: #4ade80; border-radius: 6px; margin-bottom: 16px; display: none; }
</style>
</head>
<body>
<h1>OnlyFlow — License Manager</h1>

<div class="token-form">
  <input type="password" id="token" placeholder="Admin token" style="width:300px">
  <button onclick="loadLicenses()">Войти</button>
</div>

<div id="status"></div>

<div class="generate-form">
  <input type="text" id="agency" placeholder="Название агентства" style="width:220px">
  <input type="text" id="notes" placeholder="Заметки (опционально)" style="width:200px">
  <button class="success" onclick="generateLicense()">+ Создать ключ</button>
</div>

<table>
  <thead>
    <tr>
      <th>Ключ</th>
      <th>Агентство</th>
      <th>Статус</th>
      <th>Активирован</th>
      <th>Последняя проверка</th>
      <th>Hardware ID</th>
      <th>Действия</th>
    </tr>
  </thead>
  <tbody id="table-body">
    <tr><td colspan="7" style="color:#555;text-align:center;padding:30px">Введи токен и нажми Войти</td></tr>
  </tbody>
</table>

<script>
const api = async (method, path, body) => {
  const token = document.getElementById('token').value;
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Admin-Token': token },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res;
};

const showStatus = (msg, ok=true) => {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.style.background = ok ? '#1e3a1e' : '#3a1e1e';
  el.style.color = ok ? '#4ade80' : '#f87171';
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 3000);
};

const fmt = (dt) => dt ? new Date(dt).toLocaleString('ru') : '—';
const hw = (id) => id ? id.substring(0, 12) + '...' : '—';

const loadLicenses = async () => {
  const res = await api('GET', '/admin/licenses');
  if (res.status === 401) { showStatus('Неверный токен', false); return; }
  const data = await res.json();
  const tbody = document.getElementById('table-body');
  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:#555;text-align:center;padding:30px">Лицензий нет</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(l => `
    <tr>
      <td class="key">${l.key}</td>
      <td>${l.agency || '—'}</td>
      <td><span class="badge ${l.is_active ? 'active' : 'revoked'}">${l.is_active ? 'Активна' : 'Заблокирована'}</span></td>
      <td>${fmt(l.activated_at)}</td>
      <td>${fmt(l.last_check)}</td>
      <td style="font-family:monospace;font-size:11px;color:#888">${hw(l.hardware_id)}</td>
      <td class="actions">
        ${l.is_active
          ? `<button class="danger" onclick="revoke('${l.key}')">Блок</button>`
          : `<button class="success" onclick="enable('${l.key}')">Разблок</button>`}
        <button class="danger" onclick="deleteLic('${l.key}')">Удалить</button>
      </td>
    </tr>
  `).join('');
};

const generateLicense = async () => {
  const agency = document.getElementById('agency').value;
  const notes = document.getElementById('notes').value;
  const res = await api('POST', `/admin/licenses/generate?agency_name=${encodeURIComponent(agency)}&notes=${encodeURIComponent(notes)}`);
  if (res.status === 401) { showStatus('Неверный токен', false); return; }
  const data = await res.json();
  showStatus(`✓ Ключ создан: ${data.key}`);
  loadLicenses();
};

const revoke = async (key) => {
  if (!confirm(`Заблокировать ${key}?`)) return;
  await api('POST', `/admin/licenses/revoke/${key}`);
  showStatus(`Ключ ${key} заблокирован`);
  loadLicenses();
};

const enable = async (key) => {
  await api('POST', `/admin/licenses/enable/${key}`);
  showStatus(`Ключ ${key} разблокирован`);
  loadLicenses();
};

const deleteLic = async (key) => {
  if (!confirm(`Удалить ${key}? Это необратимо.`)) return;
  await api('DELETE', `/admin/licenses/${key}`);
  showStatus(`Ключ ${key} удалён`);
  loadLicenses();
};
</script>
</body>
</html>"""
