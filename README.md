# 🎓 UniQuiz — Telegram Mini App для университетских тестов

## 📁 Структура проекта
```
quiz-app/
├── backend/
│   ├── main.py          ← Главный сервер FastAPI
│   ├── models.py        ← Таблицы базы данных
│   ├── database.py      ← Подключение к PostgreSQL
│   ├── auth.py          ← Проверка Telegram подписи
│   ├── requirements.txt ← Зависимости Python
│   └── .env.example     ← Шаблон секретных ключей
└── frontend/
    ├── index.html       ← Приложение для студентов
    └── admin.html       ← Панель администратора
```

---

## 🚀 ДЕПЛОЙ — ПОШАГОВО

### ШАГ 1: База данных (Supabase — бесплатно)

1. Иди на https://supabase.com → Sign Up (через GitHub)
2. Нажми **New Project**
3. Придумай имя и пароль (запомни пароль!)
4. Жди ~2 минуты пока создаётся
5. Иди в **Settings → Database → Connection String**
6. Выбери **URI** и скопируй строку вида:
   ```
   postgresql://postgres:[ТВОЙ-ПАРОЛЬ]@db.abcxyz.supabase.co:5432/postgres
   ```
7. Замени `postgresql://` на `postgresql+asyncpg://`

---

### ШАГ 2: Бэкенд (Render.com — бесплатно)

1. Иди на https://render.com → Sign Up (через GitHub)
2. Создай репозиторий на GitHub и залей туда папку `backend/`
3. В Render нажми **New → Web Service**
4. Подключи свой репо
5. Настройки:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. В разделе **Environment Variables** добавь:
   ```
   BOT_TOKEN = твой_токен_от_BotFather
   DATABASE_URL = postgresql+asyncpg://postgres:пароль@db.xxx.supabase.co:5432/postgres
   ADMIN_TG_ID = твой_telegram_id
   ```
7. Нажми **Deploy** — через ~5 минут получишь URL вида `https://quiz-app-xxx.onrender.com`

---

### ШАГ 3: Фронтенд (Vercel — бесплатно)

1. Иди на https://vercel.com → Sign Up
2. Залей папку `frontend/` на GitHub (в тот же или отдельный репо)
3. В Vercel: **New Project → Import** твой репо
4. Нажми **Deploy**
5. Получишь URL вида `https://quiz-app.vercel.app`

---

### ШАГ 4: Подключи бот к Mini App

1. Напиши @BotFather команду `/newbot` — создай бота (или используй старый)
2. Отправь `/mybots` → выбери бота → **Bot Settings → Menu Button → Configure menu button**
3. Вставь URL фронтенда: `https://quiz-app.vercel.app/index.html`
4. Готово! Студенты открывают мини-апп прямо в Telegram

---

### ШАГ 5: Обнови URL в коде фронтенда

В обоих файлах `index.html` и `admin.html` найди строку:
```javascript
const API_BASE = "https://YOUR-BACKEND.onrender.com";
```
Замени на свой URL с Render, например:
```javascript
const API_BASE = "https://quiz-app-abc123.onrender.com";
```

---

### ШАГ 6: Узнай свой Telegram ID

1. Напиши @userinfobot в Telegram
2. Он пришлёт твой ID — это и есть `ADMIN_TG_ID`

---

## 🔧 Как добавить вопросы

1. Открой `https://quiz-app.vercel.app/admin.html` в Telegram Mini App
2. Сначала создай предмет во вкладке **Предметы** (например "Математика 📐")
3. Перейди во вкладку **Добавить вопрос**
4. Выбери предмет, введи вопрос, варианты ответов, отметь правильный
5. При желании загрузи картинку
6. Нажми **Сохранить**

---

## ❓ Частые вопросы

**Q: Почему в Mini App не загружается?**
A: Убедись что в `API_BASE` правильный URL бэкенда. Проверь в Render что сервер запущен (не sleeping).

**Q: Render засыпает через 15 минут**
A: На бесплатном плане — да. Первый запрос после сна занимает ~30 сек. Для постоянной работы нужен платный план ($7/мес) или VPS.

**Q: Как дать доступ ещё одному учителю?**
A: В базе данных Supabase (Table Editor → users) найди его запись и поставь `is_admin = true`.

**Q: Безопасно ли хранить BOT_TOKEN в переменных окружения?**
A: Да, Render шифрует их. Никогда не добавляй `.env` файл в Git!
