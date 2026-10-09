# UniQuiz — сайт тестов для учеников и учителей

Обычный сайт (без Telegram): ученики заходят по ссылке, вводят имя и проходят тесты.
Админка — по паролю. Сайт и API живут на одном хостинге, второй не нужен.
Работает на телефоне, планшете, ПК и TV (пульт/клавиатура: цифры — выбор, Enter — дальше).

## Структура проекта

```
test16/
├── main.py          ← Сервер FastAPI + раздача сайта
├── models.py        ← Таблицы базы данных
├── database.py      ← Подключение к PostgreSQL (Supabase)
├── auth.py          ← Пароль админа + токены (без Telegram)
├── importers.py     ← Импорт тестов из Word/Excel/PDF
├── index.html       ← Сайт для учеников (/)
├── admin.html       ← Админка (/admin)
├── requirements.txt ← Зависимости Python
├── render.yaml      ← Деплой на Render одной кнопкой
└── .env.example     ← Шаблон настроек
```

---

## ДЕПЛОЙ — ПОШАГОВО

### ШАГ 1: База данных (Supabase — бесплатно)

1. Иди на https://supabase.com → Sign Up (через GitHub)
2. Нажми **New Project**, придумай имя и пароль (запомни пароль!)
3. Жди ~2 минуты пока создаётся
4. Слева **Connect → Connection string → Transaction Pooler → URI**, скопируй строку вида:
   ```
   postgresql://postgres.xxxxx:[ТВОЙ-ПАРОЛЬ]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```
   (Нужна именно строка пулера — порт `6543`.)

### ШАГ 2: Сайт + API (Render.com — бесплатно)

1. Иди на https://render.com → Sign Up (через GitHub)
2. Репозиторий `test16` уже на GitHub — подключи его: **New → Web Service → Build and deploy from a Git repository**
3. Render сам подхватит `render.yaml`:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. В разделе **Environment Variables** добавь:
   ```
   ADMIN_PASSWORD = придумай-сложный-пароль
   DATABASE_URL   = postgresql://postgres.xxxxx:пароль@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```
5. Нажми **Deploy** — через ~5 минут получишь URL вида `https://quiz-app-xxx.onrender.com`
6. Готово! Ученикам — корень сайта, тебе — тот же адрес + `/admin` с паролем из `ADMIN_PASSWORD`.

### Если база уже была от Telegram-версии

Таблицы от старой версии совместимы, нужны 2 команды в Supabase → **SQL Editor**:

```sql
ALTER TABLE users ALTER COLUMN tg_id DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS client_id VARCHAR(100);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_client_id ON users (client_id);
```

Старые ученики (с `tg_id`) останутся в результатах, новые будут писаться через `client_id`.

---

## Как добавить вопросы

1. Открой `https://твой-сайт.onrender.com/admin`, войди по паролю
2. Вкладка **Предметы** — создай предмет (например «Математика 📐»)
3. Вкладка **Добавить вопрос** — по одному, или **Импорт 📦** — сразу пачкой из `.docx / .xlsx / .pdf`:
   ```
   1. Столица Франции?
   A) Лондон
   +B) Париж
   C) Берлин
   ```
   Правильный вариант помечай `+` или `*`. Картинки достанутся сами.

---

## Частые вопросы

**Q: Сайт не грузится / висит.**
A: Проверь в Render, что сервер запущен (на бесплатном плане он засыпает через 15 минут — первый запрос после сна ~30 сек).

**Q: Render засыпает — лечится?**
A: На бесплатном плане — нет, только платный ($7/мес) или VPS.

**Q: Как дать админку ещё одному учителю?**
A: Просто дай ему пароль из `ADMIN_PASSWORD`. Хочешь отдельный — смени общий (токены живут 30 дней, старые отвалятся).

**Q: Безопасно ли хранить пароли в переменных окружения?**
A: Да, Render шифрует их. Никогда не добавляй `.env` файл в Git!
