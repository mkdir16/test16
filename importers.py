"""
Парсер тестов из Word / Excel / PDF в формат Quiz API.

Ожидаемый формат (со слов владельца):
    1. Текст вопроса?
    A) вариант
    +B) правильный вариант     <- маркер + или * (в начале, в конце или после буквы)
    C) вариант
    D) вариант

Маркер может стоять в любом из видов:
    +B) текст      B) +текст      B) текст+      B*) текст      B) (*) текст
Неразмеченные вопросы (без +/*) пропускаются и попадают в отчёт.
Картинки из .docx/.xlsx/.pdf извлекаются в UPLOAD_DIR и
подставляются в поле image_url (\"/uploads/....\").
"""

import csv
import io
import os
import re
import uuid

SUPPORTED_EXTS = {"json", "docx", "xlsx", "csv", "pdf"}

MAX_FILE_SIZE = 25 * 1024 * 1024      # 25 МБ на файл импорта
MAX_QUESTIONS_PER_REQUEST = 5000
MAX_IMAGE_SIZE = 5 * 1024 * 1024      # 5 МБ на картинку
ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}

# Порядковые номера букв в латинице и кириллице
_LATIN = "ABCDEFGHIJ"
_CYRILLIC = "АБВГДЕЖЗИК"


def _letter_index(letter: str) -> int:
    letter = letter.upper()
    if letter in _LATIN:
        return _LATIN.index(letter)
    if letter in _CYRILLIC:
        return _CYRILLIC.index(letter)
    return -1


QUESTION_RE = re.compile(r"^\s*(?:№\s*)?\d{1,4}\s*[.)\-:]\s*(.+?)\s*$")
# Маркер +/* может стоять до буквы, между буквой и скобкой, после скобки и в конце
OPTION_RE = re.compile(
    r"^\s*([+*]?)\s*([A-Za-zА-ЯЁа-яё])\s*([+*]?)\s*[).:.\-]\s*(.+?)\s*([+*]?)\s*$"
)
BRACKET_MARKER_RE = re.compile(r"\(\s*[+*]\s*\)|\[\s*[+*]\s*\]")
STOP_RE = re.compile(r"^\s*(ответы|ключи?|ответ|answers?\s*key|keys?|жауаптар)\b", re.IGNORECASE)
INLINE_OPTION_RE = re.compile(r"([A-Za-zА-ЯЁа-яё])\s*[).:]\s*")
KEY_CELL_RE = re.compile(r"^[A-Za-zА-ЯЁа-яё]$|^\d{1,2}$")


def _parse_option_line(s: str) -> tuple[str, bool] | None:
    """Строка вида '[+|*] Буква [+|*]) текст [+|*]'. Возвращает (текст, помечен) или None."""
    m = OPTION_RE.match(s)
    if not m or _letter_index(m.group(2)) == -1:
        return None
    text, inner_mark = _clean_option_text(m.group(4))
    if not text:
        return None
    marked = (m.group(1) in ("+", "*") or m.group(3) in ("+", "*")
              or inner_mark or m.group(5) in ("+", "*"))
    return text, marked


def _clean_option_text(raw: str) -> tuple[str, bool]:
    """Убирает маркеры +/*, возвращает (чистый текст, помечен ли)."""
    marked = False
    s = raw.strip()
    if BRACKET_MARKER_RE.search(s):
        marked = True
        s = BRACKET_MARKER_RE.sub("", s).strip()
    if s[:1] in ("+", "*"):
        marked = True
        s = s[1:].strip()
    if len(s) >= 2 and s[-1] in ("+", "*") and s[-2] in (" ", "\t", ")", "]"):
        marked = True
        s = s[:-1].strip()
    elif s[-1:] in ("+", "*") and len(s) == 1:
        marked = True
        s = ""
    return s, marked


def _split_inline_options(remainder: str):
    """Варианты, записанные в одну строку с вопросом: '... A) .. B) +..'."""
    matches = list(INLINE_OPTION_RE.finditer(remainder))
    if len(matches) < 2:
        return remainder, []
    qtext = remainder[:matches[0].start()].strip()
    opts = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(remainder)
        opts.append(remainder[m.end():end].strip())
    return qtext, opts


class _Builder:
    """Собирает вопросы из потока строк."""

    def __init__(self, allow_unnumbered_split: bool = False):
        self.allow_unnumbered_split = allow_unnumbered_split
        self.questions: list[dict] = []
        self.warnings: list[str] = []
        self._cur: dict | None = None

    def _finalize(self):
        q = self._cur
        self._cur = None
        if not q:
            return
        if not q["text"]:
            self.warnings.append("Пропущен вопрос без текста")
            return
        if len(q["options"]) < 2:
            self.warnings.append(f'Пропущен «{q["text"][:60]}»: меньше 2 вариантов')
            return
        marked = [i for i, m in enumerate(q["marked"]) if m]
        if not marked:
            self.warnings.append(f'Пропущен «{q["text"][:60]}»: нет помеченного +/* ответа')
            return
        if len(marked) > 1:
            self.warnings.append(
                f'«{q["text"][:60]}»: несколько +/*, взят первый'
            )
        self.questions.append({
            "text": q["text"],
            "options": q["options"],
            "correct_index": marked[0],
            "image_url": q.get("image_url"),
        })

    def _new_question(self, text: str, image_url: str | None = None):
        self._finalize()
        self._cur = {"text": text.strip(), "options": [], "marked": [],
                     "image_url": image_url}

    def add_option(self, text: str, marked: bool):
        if self._cur is None:
            self.warnings.append(f"Вариант без вопроса пропущен: «{text[:60]}»")
            return
        if len(self._cur["options"]) >= 10:
            return
        self._cur["options"].append(text)
        self._cur["marked"].append(marked)

    def attach_image(self, image_url: str):
        if self._cur is not None and not self._cur.get("image_url"):
            self._cur["image_url"] = image_url

    def feed_line(self, line: str, image_url: str | None = None):
        s = (line or "").strip()
        if not s:
            return
        if STOP_RE.match(s):
            self._finalize()
            # Секция ключей в конце — дальше не парсим
            raise StopIteration
        qm = QUESTION_RE.match(s)
        if qm:
            remainder = qm.group(1)
            qtext, inline = _split_inline_options(remainder)
            self._new_question(qtext, image_url)
            for opt_raw in inline:
                text, marked = _clean_option_text(opt_raw)
                if text:
                    self.add_option(text, marked)
            return
        om = _parse_option_line(s)
        if om is not None and self._cur is not None:
            text, marked = om
            self.add_option(text, marked)
            if image_url:
                self.attach_image(image_url)
            return
        if om is not None:
            # Вариант без вопроса
            self.warnings.append(f"Вариант без вопроса пропущен: «{om[0][:60]}»")
            return
        # Обычная строка: продолжение вопроса/варианта либо новый вопрос без номера (Word-списки)
        if self._cur is None:
            self._new_question(s, image_url)
        elif (
            self.allow_unnumbered_split
            and len(self._cur["options"]) >= 2
            and any(self._cur["marked"])
        ):
            self._new_question(s, image_url)
        else:
            if not self._cur["options"]:
                self._cur["text"] = (self._cur["text"] + " " + s).strip()
            else:
                self._cur["options"][-1] = (self._cur["options"][-1] + " " + s).strip()[:500]
            if image_url:
                self.attach_image(image_url)

    def finish(self):
        self._finalize()
        return self.questions


def parse_text_questions(text: str, allow_unnumbered_split: bool = False):
    """Парсинг plain-text в вопросы. Возвращает (questions, warnings)."""
    builder = _Builder(allow_unnumbered_split=allow_unnumbered_split)
    try:
        for line in (text or "").splitlines():
            builder.feed_line(line)
    except StopIteration:
        pass
    return builder.finish(), builder.warnings


# ─── Картинки ─────────────────────────────────────────────────────────────

def save_image_bytes(data: bytes, upload_dir: str, ext_hint: str = "jpg") -> str | None:
    """Сохраняет картинку в uploads, возвращает image_url. None если мусор."""
    try:
        if not data or len(data) > MAX_IMAGE_SIZE:
            return None
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))  # verify закрывает — открываем заново
        fmt = (img.format or ext_hint).lower()
        ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png",
                   "gif": "gif", "webp": "webp"}
        ext = ext_map.get(fmt)
        if ext is None:
            # bmp/tiff и прочее — приводим к jpg
            img = img.convert("RGB")
            ext = "jpg"
            filename = f"{uuid.uuid4()}.{ext}"
            img.save(os.path.join(upload_dir, filename), "JPEG")
        else:
            filename = f"{uuid.uuid4()}.{ext}"
            with open(os.path.join(upload_dir, filename), "wb") as f:
                f.write(data)
        return f"/uploads/{filename}"
    except Exception:
        return None


# ─── DOCX ─────────────────────────────────────────────────────────────────

def _docx_iter_blocks(doc):
    """Параграфы документа по порядку, включая таблицы. Yield (text, [img_bytes])."""
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            from docx.table import Table
            for row in Table(child, doc).rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        yield p


def _docx_paragraph_images(paragraph, doc) -> list[tuple[bytes, str]]:
    """Инлайн-картинки параграфа: [(blob, content_type)]."""
    out = []
    try:
        for blip in paragraph._p.findall(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
        ):
            rid = blip.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
            if not rid:
                continue
            part = doc.part.related_parts.get(rid)
            if part is None:
                continue
            out.append((part.blob, part.content_type))
    except Exception:
        pass
    return out


def parse_docx(path: str, upload_dir: str):
    from docx import Document
    doc = Document(path)
    builder = _Builder(allow_unnumbered_split=True)
    images_saved = 0
    try:
        for para in _docx_iter_blocks(doc):
            text = (para.text or "").strip()
            img_url = None
            for blob, ctype in _docx_paragraph_images(para, doc):
                hint = (ctype or "").split("/")[-1] or "jpg"
                img_url = save_image_bytes(blob, upload_dir, hint)
                if img_url:
                    images_saved += 1
                    break
            if not text and not img_url:
                continue
            if not text and img_url:
                builder.attach_image(img_url)  # картинка отдельным абзацем
                continue
            builder.feed_line(text, img_url)
    except StopIteration:
        pass
    questions = builder.finish()
    return questions, builder.warnings, images_saved


# ─── XLSX / CSV ───────────────────────────────────────────────────────────

_HEADER_WORDS = ("вопрос", "question", "вариант", "ответ", "answer", "правильн")


def _is_header_row(cells: list[str]) -> bool:
    joined = " ".join(cells).lower()
    return any(w in joined for w in _HEADER_WORDS)


def _row_to_question(cells: list[str]):
    """Строка Excel -> dict или None. Поддерживает ключ-колонку и маркеры +/*."""
    cells = [(c or "").strip() for c in cells]
    while cells and not cells[-1]:
        cells.pop()
    if not cells or not cells[0]:
        return None
    qtext = re.sub(r"^\s*(?:№\s*)?\d{1,4}\s*[.)\-:]\s*", "", cells[0]).strip()
    if not qtext:
        return None

    rest = cells[1:]

    # Ключ-колонка — ТОЛЬКО последняя ячейка, и только если без неё
    # остаётся минимум 2 варианта (иначе это просто короткий ответ типа "3").
    key_raw: str | None = None
    if len(rest) >= 3 and KEY_CELL_RE.match(rest[-1]):
        key_raw = rest.pop().strip()

    options: list[str] = []
    opt_letters: list[int] = []
    marked: list[bool] = []

    for cell in rest:
        if not cell:
            continue
        parsed = _parse_option_line(cell)
        if parsed is not None:
            m = OPTION_RE.match(cell)
            options.append(parsed[0])
            opt_letters.append(_letter_index(m.group(2)))
            marked.append(parsed[1])
        else:
            text, is_marked = _clean_option_text(cell)
            if text:
                options.append(text)
                opt_letters.append(len(options) - 1)
                marked.append(is_marked)

    # Ключ оказался вариантом ("Да/1")? Возвращаем обратно.
    if key_raw is not None and len(options) < 2:
        text, is_marked = _clean_option_text(key_raw)
        if text:
            options.append(text)
            opt_letters.append(len(options) - 1)
            marked.append(is_marked)
        key_raw = None

    if len(options) < 2:
        return None

    hits = [i for i, m_ in enumerate(marked) if m_]
    correct = hits[0] if hits else None

    # Явная ключ-колонка важнее встроенных маркеров
    if key_raw is not None:
        resolved = None
        if re.fullmatch(r"\d{1,2}", key_raw):
            n = int(key_raw) - 1
            if 0 <= n < len(options):
                resolved = n
        else:
            li = _letter_index(key_raw)
            for i, oli in enumerate(opt_letters):
                if oli == li:
                    resolved = i
                    break
            if resolved is None and 0 <= li < len(options):
                resolved = li
        if resolved is not None:
            correct = resolved

    if correct is None:
        return None
    return {"text": qtext[:2000], "options": [o[:500] for o in options],
            "correct_index": correct, "image_url": None}


def parse_xlsx(path: str, upload_dir: str):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    questions: list[dict] = []
    warnings: list[str] = []
    images_saved = 0

    # Картинки Excel: якорь -> строка
    anchored: dict[int, bytes] = {}
    try:
        for img in getattr(ws, "_images", []):
            try:
                row0 = img.anchor._from.row  # 0-based
                anchored.setdefault(row0, img._data())
            except Exception:
                continue
    except Exception:
        pass

    first = True
    excel_row = 0
    for row in ws.iter_rows(values_only=True):
        excel_row += 1
        cells = [("" if v is None else str(v)) for v in row]
        if not any(c.strip() for c in cells):
            continue
        if first:
            first = False
            if _is_header_row(cells):
                continue
        q = _row_to_question(cells)
        if q is None:
            warnings.append(f"Строка {excel_row}: пропущена (нет 2 вариантов или ответа)")
            continue
        blob = anchored.get(excel_row - 1) or anchored.get(excel_row)
        if blob:
            url = save_image_bytes(blob, upload_dir, "png")
            if url:
                q["image_url"] = url
                images_saved += 1
        questions.append(q)
    return questions, warnings, images_saved


def _csv_rows(content: str):
    """Строки CSV с автоопределением разделителя (, ; tab — RU Excel даёт ';')."""
    sample = content[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        for cand in (";", "\t", ","):
            if cand in sample.splitlines()[0]:
                delimiter = cand
                break
    return csv.reader(io.StringIO(content), delimiter=delimiter)


def parse_csv(path: str):
    warnings: list[str] = []
    questions: list[dict] = []
    content = None
    for enc in ("utf-8-sig", "cp1251"):
        try:
            with open(path, encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if content is None:
        return [], ["Не удалось прочитать CSV (кодировка не utf-8/cp1251)"], 0
    first = True
    for i, row in enumerate(_csv_rows(content), start=1):
        if not any((c or "").strip() for c in row):
            continue
        if first:
            first = False
            if _is_header_row(row):
                continue
        q = _row_to_question(row)
        if q is None:
            warnings.append(f"Строка {i}: пропущена (нет 2 вариантов или ответа)")
            continue
        questions.append(q)
    return questions, warnings, 0


# ─── PDF ──────────────────────────────────────────────────────────────────

def parse_pdf(path: str, upload_dir: str):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return [], ["PDF запаролен — сними пароль и попробуй снова"], 0

    questions: list[dict] = []
    warnings: list[str] = []
    images_saved = 0

    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            warnings.append(f"Стр. {page_no}: нет текстового слоя (похоже, скан — нужен OCR)")
            continue
        builder = _Builder(allow_unnumbered_split=False)
        try:
            for line in text.splitlines():
                builder.feed_line(line)
        except StopIteration:
            pass
        page_qs = builder.finish()
        warnings.extend(f"Стр. {page_no}: {w}" for w in builder.warnings)

        # Картинки страницы -> вопросам страницы без картинок, по порядку
        blobs: list[bytes] = []
        try:
            for img in page.images:
                try:
                    blobs.append(img.data)
                except Exception:
                    continue
        except Exception:
            pass
        bi = 0
        for q in page_qs:
            if bi < len(blobs):
                url = save_image_bytes(blobs[bi], upload_dir, "jpg")
                bi += 1
                if url:
                    q["image_url"] = url
                    images_saved += 1
        questions.extend(page_qs)
    return questions, warnings, images_saved


def parse_file(path: str, upload_dir: str):
    """Единая точка входа. Возвращает (questions, warnings, images_saved)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == "docx":
        return parse_docx(path, upload_dir)
    if ext == "xlsx":
        return parse_xlsx(path, upload_dir)
    if ext == "csv":
        return parse_csv(path)
    if ext == "pdf":
        return parse_pdf(path, upload_dir)
    raise ValueError(f"Формат .{ext} не поддерживается. Загрузи .docx / .xlsx / .csv / .pdf")
