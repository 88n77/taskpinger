# TaskPinger 88n77

Особистий Telegram-бот для збору задач, нагадувань і звітів. Працює 24/7. Доступ — лише власнику.

## Можливості
- 📥 Додавання тасок: покроково (`/add`) або швидко (просто написати текст)
- 📂 Категорії: 👤 особисті / 👥 командні; пріоритети 🔴🟡🟢
- ⏰ Нагадування: за 1 день і за 2 год до дедлайну + кастомний пінг
- ☀️ Щоденний дайджест зранку (9:00) — план на день + прострочені
- 🏁 Тижневий звіт у п'ятницю ввечері (18:00) — що зроблено / що лишилось
- 📊 `/report` і `/stats` на вимогу
- Парсинг дедлайну: `завтра 18:00`, `пт 09:00`, `+3h`, `+2d`, `05.06 18:00`, `2026-06-05 18:00`
- 📱 **Telegram Mini App**: розклад дня, звички зі стріками, статистика, редагування тасок
- 🎙 **Голосові → задачі**: Gemini розшифровує голосове й виділяє задачі з дедлайнами одним запитом (JSON-відповідь, резервна модель, валідація полів перед записом у БД)

## Архітектура
Один процес на asyncio: aiogram-бот + APScheduler + aiohttp-сервер Mini App.

- **API Mini App** (`bot/webapp.py`): REST на aiohttp. Кожен запит до `/api/*` несе Telegram `initData`;
  підпис перевіряється HMAC-SHA256 від токена бота + TTL, доступ лише власнику.
  Помилки обробляє middleware: 400 на некоректні дані, 404 на відсутні записи, 500 з логуванням.
- **База** (`bot/database.py`, `bot/planner_db.py`): SQLite через aiosqlite — таски, повтори, розклад,
  разові зміни на дату, звички.
- **Планувальник** (`bot/scheduler.py`): нагадування до дедлайну, ранковий дайджест, тижневі звіти.
- **AI** (`bot/voice.py`): Gemini з `responseMimeType: application/json` і низькою температурою;
  якщо основна модель недоступна — резервна; результат проходить ту саму валідацію, що й ручне введення.

## Тести
```bash
python tests/test_webapp.py
```
59 end-to-end перевірок API на тимчасовій БД: авторизація (невалідний/прострочений підпис, чужий користувач),
CRUD тасок і повторів, розклад, звички зі стріками, статистика, некоректні дати.

## Команди
| Команда | Дія |
|---------|-----|
| `/start` | головне меню |
| `/add` | додати таску покроково |
| `/tasks` | список з фільтрами (сьогодні/тиждень/прострочені/категорії…) |
| `/report` | звіт зараз |
| `/stats` | коротка статистика |
| `/cancel` | скасувати поточну дію |

---

## 1. Підготовка
1. Створи бота у **@BotFather** → отримай `BOT_TOKEN`.
2. Дізнайся свій `OWNER_ID` у **@userinfobot**.
3. Скопіюй `.env.example` → `.env` і впиши значення:
   ```
   BOT_TOKEN=123456:AAE...
   OWNER_ID=123456789
   ```

## 2. Локальний запуск (для тесту)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## 3. Деплой на VPS — варіант A: Docker (рекомендую)
```bash
git clone <repo> /opt/taskpinger   # або залий файли вручну
cd /opt/taskpinger
cp .env.example .env && nano .env  # впиши токен + owner_id
docker compose up -d --build
docker compose logs -f             # дивитись логи
```
БД і логи зберігаються в `./data` та `./logs` (volume), переживають перезапуск.

## 4. Деплой на VPS — варіант B: systemd
```bash
sudo mkdir -p /opt/taskpinger && cd /opt/taskpinger
# залий сюди файли проєкту
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env

sudo cp taskpinger.service /etc/systemd/system/
# за потреби виправ User/шляхи в /etc/systemd/system/taskpinger.service
sudo systemctl daemon-reload
sudo systemctl enable --now taskpinger
sudo systemctl status taskpinger
journalctl -u taskpinger -f          # логи
```

---

## 5. Бекап бази
Уся БД — один файл `data/tasks.db`. Достатньо копіювати його.

Щоденний бекап через cron (Linux):
```bash
crontab -e
# щодня о 03:00 — копія з датою, тримати останні 14
0 3 * * * cp /opt/taskpinger/data/tasks.db /opt/taskpinger/data/backup_$(date +\%F).db && find /opt/taskpinger/data -name 'backup_*.db' -mtime +14 -delete
```

---

## 6. Налаштування (config.py)
- `DEADLINE_REMINDERS_MIN` — за скільки хв до дедлайну пінгувати (зараз `[1440, 120]`)
- `DAILY_DIGEST_HOUR/MINUTE` — час ранкового дайджесту (зараз 09:00)
- `WEEKLY_REPORT_DAY/HOUR/MINUTE` — день і час тижневого звіту (зараз пт 18:00)
- `CATEGORIES`, `PRIORITIES` — категорії та пріоритети

## Структура
```
main.py            точка входу
config.py          налаштування
.env               BOT_TOKEN, OWNER_ID (не в git)
bot/
  database.py      SQLite CRUD (таски)
  planner_db.py    розклад, звички, статистика
  webapp.py        API Mini App (aiohttp) + перевірка initData
  voice.py         голосові → задачі (Gemini)
  handlers.py      команди, FSM, callback
  keyboards.py     клавіатури
  scheduler.py     APScheduler джоби
  reports.py       тексти звітів
  utils.py         парсинг дат, форматування
webapp/            фронтенд Mini App (HTML/JS/CSS)
tests/             end-to-end тести API
utils/logger.py    логи
data/tasks.db      база (авто)
logs/              логи по днях
```
