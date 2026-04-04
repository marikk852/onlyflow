# ContentFlow — Agency Vault Uploader
> Документ для Claude Code. Читай этот файл перед каждой задачей.

---

## Суть проекта

Локальный Telegram бот для OnlyFans агентства.
Автоматизирует загрузку контента из Telegram в Vault нескольких OnlyFans аккаунтов.
Работает на компьютере агентства — без внешних платных API.

**Масштаб:** 17 моделей, 46 аккаунтов, ~200 ГБ контента в месяц.

---

## Технический стек

- **Python 3.10+**
- **aiogram 3** — Telegram бот
- **Playwright** — автоматизация браузера (Chromium)
- **SQLAlchemy + SQLite** — локальная база данных
- **aiofiles** — асинхронная работа с файлами
- **python-dotenv** — переменные окружения
- **hashlib** — рандомизация файлов перед загрузкой

---

## Структура проекта

```
contentflow/
├── PLAN.md                 # этот файл
├── .env                    # токены (не в git)
├── .env.example            # шаблон
├── requirements.txt
├── main.py                 # точка входа
├── config.py               # настройки из .env
├── database.py             # SQLAlchemy модели и CRUD
├── bot/
│   ├── __init__.py
│   ├── handlers/
│   │   ├── admin.py        # команды администратора
│   │   ├── content.py      # приём контента из разделов
│   │   └── upload.py       # управление загрузкой
│   ├── keyboards.py        # inline клавиатуры
│   └── middlewares.py      # проверка прав
├── automation/
│   ├── __init__.py
│   ├── uploader.py         # Playwright логика загрузки в Vault
│   ├── session.py          # управление браузерными профилями
│   └── file_processor.py   # рандомизация хэша файлов
├── downloads/              # временные файлы из Telegram
├── profiles/               # браузерные профили (по account_id)
│   └── {account_id}/       # куки и сессия каждого аккаунта
└── logs/                   # логи операций
```

---

## База данных (SQLite)

### Таблица: models
```
id          INTEGER PRIMARY KEY
name        TEXT NOT NULL          -- имя модели (Alina)
alias       TEXT                   -- псевдоним
created_at  DATETIME
```

### Таблица: accounts
```
id              INTEGER PRIMARY KEY
model_id        INTEGER FK → models.id
name            TEXT NOT NULL      -- название аккаунта
url             TEXT               -- ссылка на профиль OF
session_ok      BOOLEAN DEFAULT 0  -- активна ли сессия
forum_topic_id  INTEGER            -- ID раздела Telegram группы
created_at      DATETIME
```

### Таблица: galleries
```
id          INTEGER PRIMARY KEY
account_id  INTEGER FK → accounts.id
name        TEXT NOT NULL          -- название категории Vault
```

### Таблица: content_batches
```
id              INTEGER PRIMARY KEY
model_id        INTEGER FK → models.id
telegram_msg_ids TEXT              -- JSON список ID сообщений TG
file_paths      TEXT               -- JSON список путей к файлам
file_hashes     TEXT               -- JSON хэши оригинальных файлов
status          TEXT               -- pending / approved / uploading / done / cancelled
created_at      DATETIME
approved_at     DATETIME
```

### Таблица: upload_jobs
```
id              INTEGER PRIMARY KEY
batch_id        INTEGER FK → content_batches.id
account_id      INTEGER FK → accounts.id
gallery_id      INTEGER FK → galleries.id
status          TEXT               -- pending / running / done / error
error_msg       TEXT
started_at      DATETIME
finished_at     DATETIME
files_count     INTEGER
```

### Таблица: upload_history
```
id              INTEGER PRIMARY KEY
file_hash       TEXT               -- хэш оригинального файла
account_id      INTEGER FK → accounts.id
gallery_id      INTEGER FK → galleries.id
uploaded_at     DATETIME
batch_id        INTEGER FK → content_batches.id
```
> Эта таблица используется для проверки дубликатов.

---

## Структура Telegram группы

```
Группа агентства (Forum режим включён)
├── 📌 Раздел: admin          — только для администратора
├── 👤 Раздел: alina          — контент модели Alina
├── 👤 Раздел: maria          — контент модели Maria
├── 👤 Раздел: katya          — контент модели Katya
└── 📋 Раздел: log            — отчёты бота (бот пишет сюда)
```

Каждый раздел привязывается к модели через команду администратора.
Бот слушает все разделы и определяет модель по `forum_topic_id`.

---

## Роли

**ADMIN_ID** из .env — единственный кто может управлять ботом.
Все остальные участники группы могут только отправлять файлы в разделы.

---

## Команды администратора

### Управление моделями
```
/addmodel <имя>
  → создаёт модель в БД
  → бот отвечает: "✓ Модель Alina создана. Привяжи раздел: /setforum Alina"

/setforum <имя_модели>
  → бот просит переслать любое сообщение из нужного раздела
  → после пересылки — привязывает forum_topic_id к модели

/models
  → список всех моделей со статусом аккаунтов и количеством категорий

/deletemodel <имя>
  → удаляет модель и все её аккаунты
```

### Управление аккаунтами
```
/addaccount <модель> <название> [url]
  → добавляет аккаунт к модели

/setsession <account_id>
  → открывает Chromium браузер с профилем аккаунта
  → менеджер вручную логинится в OnlyFans
  → закрывает браузер → сессия сохранена

/checksession <account_id>
  → проверяет жива ли сессия (headless)
  → обновляет session_ok в БД

/deleteaccount <account_id>
  → удаляет аккаунт и его профиль браузера
```

### Управление категориями
```
/addcategory <account_id> <название>
  → добавляет категорию Vault для аккаунта
  → название должно точно совпадать с названием в OnlyFans

/categories <account_id>
  → список категорий аккаунта

/deletecategory <category_id>
  → удаляет категорию
```

### Мониторинг
```
/status
  → статус всех аккаунтов (сессии, последняя активность)

/log
  → последние 20 операций загрузки

/queue
  → текущая очередь загрузки
```

---

## Флоу работы — ежедневный

### 1. Модель отправляет файлы
Модель или менеджер отправляет фото/видео в раздел группы (например #alina).

### 2. Бот скачивает файлы
Бот автоматически:
- Скачивает все медиафайлы в `downloads/{batch_id}/`
- Вычисляет хэш каждого файла (SHA256)
- Сохраняет batch в БД со статусом `pending`

### 3. ПРОВЕРКА ДУБЛИКАТОВ (важная функция)
Перед показом превью бот проверяет каждый файл через `upload_history`:

**Если файл уже загружался:**
```
┌─────────────────────────────────────────┐
│ ⚠️ Обнаружены дубликаты                 │
│                                         │
│ 3 из 12 файлов уже были загружены:      │
│ • photo_001.jpg → Alina_Official        │
│   VIP Фото • 3 дня назад               │
│ • photo_002.jpg → Alina_Premium         │
│   Видео • 3 дня назад                  │
│ • video_001.mp4 → Alina_Official        │
│   Общее • 5 дней назад                 │
│                                         │
│ Как поступить с дубликатами?            │
│                                         │
│ [✓ Загрузить всё равно] [✗ Пропустить дубликаты] [⊘ Отменить] │
└─────────────────────────────────────────┘
```

**Если файлов-дубликатов нет** — сразу переходим к шагу 4.

### 4. Администратор получает превью
```
┌─────────────────────────────────────────┐
│ 📁 Новый контент — Alina                │
│ 12 файлов (8 фото, 4 видео)             │
│                                         │
│ Выбери категорию для каждого аккаунта:  │
│                                         │
│ Alina_Official →                        │
│ [VIP Фото] [Видео] [Общее]             │
│                                         │
│ Alina_Premium →                         │
│ [VIP Фото] [Видео] [Общее]             │
│                                         │
│ Alina_Backup →                          │
│ [VIP Фото] [Видео] [Общее]             │
│                                         │
│ [⚡ Запустить загрузку] [⊘ Отмена]     │
└─────────────────────────────────────────┘
```

### 5. Администратор выбирает категории и запускает
После нажатия [⚡ Запустить загрузку]:
- Создаются `upload_jobs` для каждого аккаунта
- Запускается очередь

### 6. Очередь загрузки (Playwright)
Для каждого аккаунта по очереди:

```
1. Открыть браузер с профилем аккаунта
2. Зайти на onlyfans.com/my/chats
3. Найти диалог со вторым аккаунтом модели
4. Загрузить файлы порциями по 40 штук
5. Дождаться прогрузки каждой порции
6. Перейти в Vault → Messages
7. Выделить загруженные файлы (до 40)
8. Переместить в нужную категорию
9. Если нужной категории нет — создать
10. Закрыть браузер
11. Пауза 15-45 секунд (случайная)
12. Следующий аккаунт
```

### 7. Запись в историю
После успешной загрузки каждого файла:
- Записать в `upload_history`: file_hash, account_id, gallery_id, uploaded_at

### 8. Отчёт в Telegram
```
✓ Alina — загрузка завершена
• Alina_Official → VIP Фото — 12 файлов ✓
• Alina_Premium → VIP Фото — 12 файлов ✓  
• Alina_Backup → VIP Фото — 12 файлов ✓
Время: 4 мин 23 сек
```

---

## Логика рандомизации файлов (file_processor.py)

Перед загрузкой в каждый аккаунт — создаётся копия файла с изменённым хэшем:

```python
# Для изображений — добавить случайные байты в метаданные
# Для видео — добавить случайные байты в конец файла
# Переименовать файл с случайным суффиксом
# Исходный файл НЕ изменяется — только копия для загрузки
```

Это нужно чтобы OnlyFans не определил одинаковые файлы по хэшу.

---

## Человекоподобное поведение (automation)

```python
# Задержки между действиями
DELAY_BETWEEN_CLICKS = (200, 800)    # мс
DELAY_BETWEEN_FILES = (800, 2000)    # мс
DELAY_BETWEEN_ACCOUNTS = (15, 45)   # секунд — случайное значение
DELAY_AFTER_UPLOAD = (2000, 5000)   # мс — ждём прогрузку

# Браузер
HEADLESS = False           # видимый браузер — менее подозрительно
VIEWPORT = (1280, 800)
DISABLE_WEBDRIVER_FLAG = True  # убрать navigator.webdriver
```

---

## Переменные окружения (.env)

```
BOT_TOKEN=         # токен от @BotFather
ADMIN_ID=          # Telegram ID администратора
GROUP_ID=          # ID группы агентства
LOG_TOPIC_ID=      # ID раздела #log в группе
```

---

## Важные правила разработки

1. **Весь код асинхронный** — asyncio везде
2. **Логирование** — каждое действие пишется в лог файл и в раздел #log группы
3. **Обработка ошибок** — если Playwright упал, аккаунт помечается как error, очередь продолжается
4. **Атомарность** — если загрузка прервалась, можно продолжить с того же места
5. **Никаких паролей в коде** — только через .env
6. **Профили браузера** — никогда не удалять автоматически, только по команде администратора

---

## Настройка Telegram бота (один раз)

### 1. Создать бота через @BotFather
```
Открыть Telegram → найти @BotFather → написать /newbot
Дать имя: ContentFlow Agency
Дать username: contentflow_agency_bot
Получить токен → вставить в .env как BOT_TOKEN
```

### 2. Настроить группу
```
Создать новую Telegram группу
Добавить бота в группу
Дать боту права администратора:
  ✓ Управление сообщениями
  ✓ Удаление сообщений

Включить Topics (разделы):
  Настройки группы → Темы → Включить

Создать разделы вручную:
  #admin   — только для администратора
  #alina   — контент модели Alina
  #maria   — контент модели Maria
  ...и т.д. для каждой модели
  #log     — отчёты бота
```

### 3. Получить нужные ID
```bash
# Узнать свой ADMIN_ID:
# Написать боту @userinfobot в Telegram → он пришлёт твой ID

# Узнать GROUP_ID:
# Добавить @userinfobot в группу → он напишет ID группы
# Или через API: https://api.telegram.org/bot{TOKEN}/getUpdates

# Узнать LOG_TOPIC_ID:
# Написать любое сообщение в раздел #log
# Через getUpdates найти message_thread_id этого сообщения
```

### 4. Вставить все ID в .env
```
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=123456789
GROUP_ID=-1001234567890
LOG_TOPIC_ID=123
```

### 5. Права в группе
```
Администратор (ADMIN_ID):
  → полный доступ ко всем командам и разделам

Модели / менеджеры:
  → могут только отправлять файлы в свои разделы
  → бот игнорирует их команды

Бот:
  → читает все сообщения во всех разделах
  → пишет только в раздел #log и в личку администратору
```

### 6. Как бот определяет модель по разделу
```python
# Когда приходит файл — бот смотрит на message.message_thread_id
# Ищет в БД accounts.forum_topic_id == message_thread_id
# Находит модель → начинает обработку
# Если раздел не привязан — игнорирует сообщение
```

### 7. Запуск бота
```bash
# В терминале в папке проекта:
cd contentflow
source venv/bin/activate   # Mac/Linux
# или
venv\Scripts\activate      # Windows

python main.py
# Бот запущен и слушает сообщения

# Для автозапуска при включении компьютера:
# Mac: launchd (инструкция в README.md)
# Windows: Task Scheduler (инструкция в README.md)
```

### 8. Структура aiogram 3 (как устроен код бота)
```python
# main.py — точка входа
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
dp.include_router(admin_router)
dp.include_router(content_router)
dp.include_router(upload_router)
asyncio.run(dp.start_polling(bot))

# bot/handlers/admin.py — команды администратора
# bot/handlers/content.py — приём файлов из разделов группы
# bot/handlers/upload.py — выбор категорий, дубликаты, запуск

# bot/keyboards.py — все inline кнопки
# bot/middlewares.py — фильтр ADMIN_ID
```

---

## Порядок разработки

### Фаза 1 — База
- [ ] Структура проекта и requirements.txt
- [ ] config.py
- [ ] database.py — все модели SQLAlchemy
- [ ] main.py — точка входа

### Фаза 2 — Telegram бот
- [ ] bot/keyboards.py — все клавиатуры
- [ ] bot/handlers/admin.py — все команды
- [ ] bot/handlers/content.py — приём файлов из разделов
- [ ] bot/middlewares.py — проверка ADMIN_ID

### Фаза 3 — Дубликаты и превью
- [ ] Логика проверки хэшей через upload_history
- [ ] Popup сообщение с информацией о дубликатах
- [ ] Кнопки: загрузить всё равно / пропустить дубликаты / отменить
- [ ] bot/handlers/upload.py — выбор категорий и запуск

### Фаза 4 — Автоматизация
- [ ] automation/session.py — открыть браузер для логина
- [ ] automation/file_processor.py — рандомизация файлов
- [ ] automation/uploader.py — полный флоу загрузки через Playwright

### Фаза 5 — Тестирование
- [ ] Тест на 2-3 аккаунтах
- [ ] Проверка дубликатов
- [ ] Проверка пауз и очереди
