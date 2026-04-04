from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_ID: int = int(os.environ["ADMIN_ID"])
GROUP_ID: int = int(os.environ["GROUP_ID"])
LOG_TOPIC_ID: int = int(os.environ["LOG_TOPIC_ID"])

DOWNLOADS_DIR = "downloads"
PROFILES_DIR = "profiles"
LOGS_DIR = "logs"

# Задержки (мс)
DELAY_BETWEEN_CLICKS = (200, 800)
DELAY_BETWEEN_FILES = (800, 2000)
DELAY_AFTER_UPLOAD = (2000, 5000)

# Задержки между аккаунтами (сек)
DELAY_BETWEEN_ACCOUNTS_MIN = 15
DELAY_BETWEEN_ACCOUNTS_MAX = 45

# Браузер
HEADLESS = False
VIEWPORT = {"width": 1280, "height": 800}

# Размер порции загрузки
UPLOAD_BATCH_SIZE = 40
