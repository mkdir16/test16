from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, field_validator
from typing import Optional
import aiofiles
import os
import uuid
from collections import defaultdict

from database import get_db, engine, Base
from models import User, Subject, Question, Option, Result
from auth import verify_telegram_init_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("DB ready")
    yield


app = FastAPI(title="Quiz App API", lifespan=lifespan)

# CORS — разрешаем фронтенду обращаться к серверу
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене укажи конкретный домен
    allow_methods=["*"],
    allow_headers=["*"],
)

# Папка для загружаемых картинок
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


# ─── Вспомогательная функция: получить текущего юзера ──────────────────────

async def get_current_user(
    # FIX: было Header(...) -> при отсутствии заголовка падал 422.
    # Теперь отдаём понятный 401.
    x_init_data: Optional[str] = Header(default=None, alias="X-Init-Data"),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Фронтенд должен передавать заголовок X-Init-Data = window.Telegram.WebApp.initData
    """
    if not x_init_data:
        raise HTTPException(status_code=401, detail="Нет заголовка X-Init-Data. Открой через Telegram Mini App")
    tg_user = verify_telegram_init_data(x_init_data)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Неверная подпись Telegram")

    try:
        tg_id = int(tg_user["id"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Некорректные данные Telegram")

    # Ищем юзера в БД, если нет — создаём
    result = await db.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            tg_id=tg_id,
            name=str(tg_user.get("first_name", ""))[:200],
            is_admin=(str(tg_id) == os.getenv("ADMIN_TG_ID", "0"))
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


# ─── ПУБЛИЧНЫЕ РОУТЫ ────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "Quiz API работает 🎉"}


@app.get("/subjects")
async def get_subjects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Получить список всех предметов"""
    result = await db.execute(select(Subject).order_by(Subject.id))
    subjects = result.scalars().all()
    return [{"id": s.id, "title": s.title, "emoji": s.emoji} for s in subjects]


@app.get("/questions/{subject_id}")
async def get_questions(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Получить вопросы по предмету (без правильного ответа — его скрываем!)"""
    # FIX: проверяем что предмет существует -> 404 вместо пустого списка
    subj = await db.execute(select(Subject).where(Subject.id == subject_id))
    if not subj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Предмет не найден")

    result = await db.execute(
        select(Question).where(Question.subject_id == subject_id).order_by(Question.id)
    )
    questions = result.scalars().all()
    if not questions:
        return []

    # FIX: было N+1 запросов (по одному на каждый вопрос).
    # Теперь один запрос на все варианты — важно для больших баз.
    q_ids = [q.id for q in questions]
    opts_result = await db.execute(
        select(Option)
        .where(Option.question_id.in_(q_ids))
        .order_by(Option.question_id, Option.order_index)
    )
    by_question: dict[int, list] = defaultdict(list)
    for o in opts_result.scalars().all():
        by_question[o.question_id].append(o)

    output = []
    for q in questions:
        options = by_question.get(q.id, [])
        output.append({
            "id": q.id,
            "text": q.text,
            "image_url": q.image_url,
            "options": [{"id": o.id, "text": o.text} for o in options]
            # НЕ возвращаем correct_option_id — студент не должен видеть!
        })
    return output


class SubmitResultRequest(BaseModel):
    subject_id: int
    answers: dict  # {question_id: chosen_option_index}

    @field_validator("answers", mode="before")
    @classmethod
    def coerce_answers(cls, v):
        # JSON ключи всегда строки, но на всякий случай принимаем и int-ключи
        if not isinstance(v, dict):
            raise ValueError("answers должен быть объектом")
        return {str(k): val for k, val in v.items()}


@app.post("/results")
async def submit_result(
    payload: SubmitResultRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Принять ответы студента, посчитать баллы и сохранить"""
    subj = await db.execute(select(Subject).where(Subject.id == payload.subject_id))
    if not subj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Предмет не найден")

    # Загружаем вопросы
    result = await db.execute(
        select(Question).where(Question.subject_id == payload.subject_id)
    )
    questions = result.scalars().all()

    if not questions:
        raise HTTPException(status_code=400, detail="В предмете нет вопросов")

    score = 0
    for q in questions:
        # FIX: поддерживаем и строковые, и числовые ключи
        chosen = payload.answers.get(str(q.id), payload.answers.get(q.id))
        if chosen is None:
            continue
        try:
            if int(chosen) == q.correct_option_id:
                score += 1
        except (ValueError, TypeError):
            continue

    # Сохраняем результат
    res = Result(
        user_id=user.id,
        subject_id=payload.subject_id,
        score=score,
        total=len(questions)
    )
    db.add(res)
    await db.commit()

    return {
        "score": score,
        "total": len(questions),
        "percentage": round(score / len(questions) * 100) if questions else 0
    }


# ─── АДМИНСКИЕ РОУТЫ ────────────────────────────────────────────────────────

def require_admin(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return user


class CreateSubjectBody(BaseModel):
    title: Optional[str] = None
    emoji: Optional[str] = "📚"


@app.post("/admin/subjects")
async def create_subject(
    title: Optional[str] = Query(default=None),
    emoji: str = Query(default="📚"),
    body: Optional[CreateSubjectBody] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """Создать новый предмет (принимает и query-параметры, и JSON body)"""
    # Поддержка обоих способов: ?title=..&emoji=.. ИЛИ JSON {"title":.., "emoji":..}
    if body and body.title:
        title = body.title
        if body.emoji:
            emoji = body.emoji
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="Название предмета пустое")
    title = title.strip()[:200]
    emoji = (emoji or "📚").strip()[:50] or "📚"

    subject = Subject(title=title, emoji=emoji)
    db.add(subject)
    await db.commit()
    await db.refresh(subject)
    return {"id": subject.id, "title": subject.title, "emoji": subject.emoji}


class AddQuestionRequest(BaseModel):
    subject_id: int
    text: str
    options: list[str]        # ["Вариант А", "Вариант Б", "Вариант В", "Вариант Г"]
    correct_index: int         # 0-based индекс в массиве options
    image_url: Optional[str] = None

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v


@app.post("/admin/questions")
async def add_question(
    payload: AddQuestionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """Добавить новый вопрос"""
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст вопроса пустой")
    # Чистим варианты от пустых строк
    clean_options = [str(o).strip() for o in (payload.options or []) if str(o).strip()]
    if len(clean_options) < 2:
        raise HTTPException(status_code=400, detail="Нужно минимум 2 варианта ответа")
    if len(clean_options) > 10:
        raise HTTPException(status_code=400, detail="Максимум 10 вариантов ответа")
    if not (0 <= payload.correct_index < len(clean_options)):
        raise HTTPException(
            status_code=400,
            detail=f"correct_index должен быть от 0 до {len(clean_options) - 1}"
        )

    # FIX: проверяем предмет -> 404 вместо 500 от ForeignKey
    subj = await db.execute(select(Subject).where(Subject.id == payload.subject_id))
    if not subj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Предмет не найден")

    q = Question(
        subject_id=payload.subject_id,
        text=text,
        image_url=(payload.image_url or None),
        correct_option_id=payload.correct_index
    )
    db.add(q)
    await db.flush()  # получаем q.id

    for i, opt_text in enumerate(clean_options):
        opt = Option(question_id=q.id, text=opt_text[:500], order_index=i)
        db.add(opt)

    await db.commit()
    return {"id": q.id, "message": "Вопрос добавлен ✅"}


class BulkQuestionItem(BaseModel):
    text: str
    options: list[str]
    correct_index: int
    image_url: Optional[str] = None


class BulkImportRequest(BaseModel):
    subject_id: int
    questions: list[BulkQuestionItem]


async def bulk_insert_questions(db: AsyncSession, subject_id: int, items: list) -> dict:
    """Общая вставка для JSON-импорта и импорта из файлов. items: объекты с text/options/correct_index/image_url."""
    imported = 0
    skipped = 0
    errors: list[str] = []

    for idx, item in enumerate(items):
        text = (getattr(item, "text", "") or "").strip()
        raw_opts = getattr(item, "options", []) or []
        clean_options = [str(o).strip() for o in raw_opts if str(o).strip()]
        try:
            correct = int(getattr(item, "correct_index", -1))
        except (ValueError, TypeError):
            correct = -1
        if not text or len(clean_options) < 2 or not (0 <= correct < len(clean_options)):
            skipped += 1
            if len(errors) < 20:
                errors.append(f"Строка {idx + 1}: неверный формат, пропущена")
            continue
        q = Question(
            subject_id=subject_id,
            text=text,
            image_url=(getattr(item, "image_url", None) or None),
            correct_option_id=correct,
        )
        db.add(q)
        await db.flush()
        for i, opt_text in enumerate(clean_options):
            db.add(Option(question_id=q.id, text=opt_text[:500], order_index=i))
        imported += 1
        # Периодически сбрасываем в БД, чтобы не держать гигантскую транзакцию
        if imported % 500 == 0:
            await db.flush()

    await db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.post("/admin/import-questions")
async def import_questions(
    payload: BulkImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """
    Массовый импорт вопросов — для больших баз тестов.
    Одним запросом можно загрузить сотни/тысячи вопросов.
    Формат: {"subject_id": 1, "questions": [{"text": "...", "options": [...], "correct_index": 0}]}
    """
    if not payload.questions:
        raise HTTPException(status_code=400, detail="Список questions пустой")
    if len(payload.questions) > 5000:
        raise HTTPException(status_code=400, detail="Максимум 5000 вопросов за один запрос. Разбей на части.")

    subj = await db.execute(select(Subject).where(Subject.id == payload.subject_id))
    if not subj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Предмет не найден")

    return await bulk_insert_questions(db, payload.subject_id, payload.questions)


IMPORT_FILE_EXTS = {"json", "docx", "xlsx", "csv", "pdf"}
MAX_IMPORT_FILE_SIZE = 25 * 1024 * 1024  # 25 МБ


@app.post("/admin/import-file")
async def import_file(
    subject_id: int = Query(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """
    Импорт базы тестов из файла: .docx / .xlsx / .csv / .pdf / .json.
    Правильный вариант помечается + или * (в начале, в конце или после буквы).
    Картинки извлекаются автоматически.
    """
    import asyncio
    import json as jsonlib
    import tempfile
    import importers

    original = (file.filename or "").strip()
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext == "xls":
        raise HTTPException(
            status_code=400,
            detail="Старый формат .xls не поддерживается. Открой в Excel и сохрани как .xlsx"
        )
    if ext not in IMPORT_FILE_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Загрузи .docx / .xlsx / .csv / .pdf / .json"
        )

    subj = await db.execute(select(Subject).where(Subject.id == subject_id))
    if not subj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Предмет не найден")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(content) > MAX_IMPORT_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Файл больше 25 МБ")

    suffix = f".{ext}"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        if ext == "json":
            try:
                parsed = jsonlib.loads(content.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(status_code=400, detail="Битый JSON-файл")
            raw = parsed if isinstance(parsed, list) else parsed.get("questions", [])
            items = []
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                items.append(BulkQuestionItem(
                    text=str(entry.get("text", "")),
                    options=list(entry.get("options", [])),
                    correct_index=int(entry.get("correct_index", -1)),
                    image_url=entry.get("image_url"),
                ))
                if len(items) >= importers.MAX_QUESTIONS_PER_REQUEST:
                    break
            result = await bulk_insert_questions(db, subject_id, items)
            result["images"] = 0
            return result

        # Тяжёлый парсинг (docx/xlsx/pdf) — в отдельном потоке, чтобы не стопать loop
        questions, warnings, images = await asyncio.to_thread(
            importers.parse_file, tmp_path, UPLOAD_DIR
        )
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if not questions and warnings:
        # Ничего не распознали — отдаём отчёт, а не молчаливый 0
        return {"imported": 0, "skipped": 0, "errors": warnings[:20],
                "images": 0, "message": "Не нашёл ни одного вопроса. Проверь формат (см. подсказку в админке)"}

    items = [
        BulkQuestionItem(
            text=q["text"], options=q["options"],
            correct_index=q["correct_index"], image_url=q.get("image_url"),
        )
        for q in questions[:importers.MAX_QUESTIONS_PER_REQUEST]
    ]
    result = await bulk_insert_questions(db, subject_id, items)
    # Докладываем о пропущенных парсером строках
    for w in warnings[:20 - len(result["errors"])]:
        result["errors"].append(w)
    result["skipped"] += max(0, len(questions) - len(items))
    result["images"] = images
    return result


@app.post("/admin/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(require_admin)
):
    """Загрузить картинку к вопросу"""
    # FIX: content_type может быть None -> было AttributeError 500
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Только картинки!")

    # FIX: санитизация расширения (path traversal, двойные расширения)
    original = file.filename or "image"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"Разрешены только: {', '.join(sorted(ALLOWED_IMAGE_EXTS))}")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Картинка больше 5 МБ")
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл")

    filename = f"{uuid.uuid4()}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)

    async with aiofiles.open(path, "wb") as f:
        await f.write(content)

    return {"image_url": f"/uploads/{filename}"}


@app.delete("/admin/questions/{question_id}")
async def delete_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """Удалить вопрос (вместе с вариантами)"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    await db.delete(q)
    await db.commit()
    return {"message": "Вопрос удалён"}


@app.delete("/admin/subjects/{subject_id}")
async def delete_subject(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """Удалить предмет (вместе с вопросами и результатами)"""
    result = await db.execute(select(Subject).where(Subject.id == subject_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    # Удаляем связанные результаты вручную (FK без cascade на уровне БД)
    res = await db.execute(select(Result).where(Result.subject_id == subject_id))
    for r in res.scalars().all():
        await db.delete(r)
    await db.delete(s)
    await db.commit()
    return {"message": "Предмет удалён"}


@app.get("/admin/results")
async def get_all_results(
    limit: int = Query(default=500, le=5000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """Посмотреть результаты всех студентов (с лимитом, чтобы не уронить сервер)"""
    results = await db.execute(
        select(Result, User, Subject)
        .join(User, Result.user_id == User.id)
        .join(Subject, Result.subject_id == Subject.id)
        .order_by(Result.created_at.desc())
        .limit(limit)
    )
    rows = results.all()
    return [
        {
            "student": r.User.name,
            "tg_id": r.User.tg_id,
            "subject": r.Subject.title,
            "score": r.Result.score,
            "total": r.Result.total,
            "date": r.Result.created_at.isoformat() if r.Result.created_at else None
        }
        for r in rows
    ]
