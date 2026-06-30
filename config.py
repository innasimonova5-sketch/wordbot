import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ID учителей (через запятую в переменной окружения ADMIN_IDS), например: 123456789,987654321
# Узнать свой Telegram ID можно у бота @userinfobot
ADMIN_IDS = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# Во сколько (UTC) ежедневно слать ученикам напоминание о повторении, если есть слова due
REMINDER_HOUR_UTC = int(os.environ.get("REMINDER_HOUR_UTC", "8"))
