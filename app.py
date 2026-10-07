import os
import re
import json
import asyncio
import hashlib
import threading
import traceback
from pathlib import Path

import requests
import pytesseract

from flask import Flask, jsonify, request
from pdf2image import convert_from_path

from supabase import create_client, Client
from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# ENVIRONMENT
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

DATA_DIR = os.getenv("DATA_DIR", "/var/data").strip()
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "2"))

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.8-flash"
).strip()

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001"
).strip()

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).strip()

SOURCE_MAX_CHARS = int(
    os.getenv("SOURCE_MAX_CHARS", "6000")
)

CONTEXT_MAX_CHARS = int(
    os.getenv("CONTEXT_MAX_CHARS", "60000")
)

TELEGRAM_MAX_CHARS = int(
    os.getenv("TELEGRAM_MAX_CHARS", "3900")
)


# ============================================================
# PATH
# ============================================================

DATA_PATH = Path(DATA_DIR)
DATA_PATH.mkdir(parents=True, exist_ok=True)

OCR_CACHE_PATH = DATA_PATH / "ocr_cache"
OCR_CACHE_PATH.mkdir(parents=True, exist_ok=True)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# GLOBAL CLIENTS
# ============================================================

supabase = None
gemini_client = None

telegram_application = None


# ============================================================
# ANSWER CACHE
# ============================================================

ANSWER_CACHE = {}
ANSWER_CACHE_MAX = 500
ANSWER_CACHE_LOCK = threading.Lock()


# ============================================================
# STARTUP LOG
# ============================================================

print("=" * 60)
print("🚀 TANYAFIQIHBOT STARTING")
print("=" * 60)

print(f"📁 DATA_DIR       : {DATA_DIR}")
print(f"🤖 LLM_MODEL      : {LLM_MODEL}")
print(f"🧠 EMBEDDING      : {EMBEDDING_MODEL}")
print(f"📚 TURATH SERVICE : {TURATH_SERVICE_URL}")
print(f"📦 OCR WORKERS    : {OCR_WORKERS}")


# ============================================================
# INITIALIZE SUPABASE
# ============================================================

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ SUPABASE CONNECTED")

    except Exception as e:
        print("❌ SUPABASE CONNECTION ERROR:")
        print(e)

        supabase = None

else:
    print("⚠️ SUPABASE ENV belum lengkap")


# ============================================================
# INITIALIZE GEMINI
# ============================================================

if GOOGLE_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print("✅ GEMINI CONNECTED")

    except Exception as e:
        print("❌ GEMINI CONNECTION ERROR:")
        print(e)

        gemini_client = None

else:
    print("⚠️ GOOGLE_API_KEY belum ditetapkan")


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_inline_text(value):
    """
    Bersihkan teks satu baris.
    Sesuai untuk:
    - nama kitab
    - kategori
    - page
    - metadata
    """

    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(
                value,
                ensure_ascii=False
            )
        except Exception:
            value = str(value)

    value = str(value)

    value = value.replace("\x00", " ")

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def clean_multiline_text(value):
    """
    Bersihkan teks tetapi kekalkan newline.
    Sesuai untuk jawapan Gemini dan petikan kitab.
    """

    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(
                value,
                ensure_ascii=False
            )
        except Exception:
            value = str(value)

    value = str(value)

    value = value.replace("\x00", "")
    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    lines = []

    for line in value.split("\n"):
        line = re.sub(
            r"[ \t]+",
            " ",
            line
        )

        lines.append(
            line.rstrip()
        )

    text = "\n".join(lines)

    # Jangan biarkan terlalu banyak baris kosong
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def short_text(value, max_chars=1000):
    """
    Potong teks untuk paparan / metadata.
    """

    text = clean_multiline_text(value)

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rstrip() + "..."


def value_to_text(value):
    """
    Convert value kepada string dengan selamat.
    """

    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            return json.dumps(
                value,
                ensure_ascii=False
            )
        except Exception:
            return str(value)

    return str(value)


# ============================================================
# TELEGRAM TEXT CLEANER
# ============================================================

def prepare_telegram_text(text):
    """
    Telegram dihantar sebagai plain text.
    
    Gemini kadang-kadang menghasilkan Markdown seperti:
        **tajuk**
        [link](https://...)
        # Tajuk
        * item
    
    Kita bersihkan supaya tidak bergantung kepada
    Telegram Markdown parser.
    """

    text = clean_multiline_text(text)

    if not text:
        return ""

    # Markdown links:
    # [tajuk](https://example.com)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r"\1 (\2)",
        text
    )

    # Bold / italic markdown
    text = text.replace("**", "")
    text = text.replace("__", "")

    # Inline code
    text = text.replace("`", "")

    # Heading markdown
    text = re.sub(
        r"(?m)^\s*#{1,6}\s*",
        "",
        text
    )

    # Bullet markdown
    text = re.sub(
        r"(?m)^\s*[\*\-]\s+",
        "• ",
        text
    )

    return text.strip()


# ============================================================
# TELEGRAM CHUNK
# ============================================================

def split_telegram_text(
    text,
    max_length=TELEGRAM_MAX_CHARS
):
    """
    Pecahkan mesej supaya tidak melebihi limit Telegram.
    Cuba pecahkan pada newline atau space.
    """

    text = prepare_telegram_text(text)

    if not text:
        return []

    if len(text) <= max_length:
        return [text]

    chunks = []

    remaining = text

    while len(remaining) > max_length:

        cut = remaining.rfind(
            "\n",
            0,
            max_length
        )

        if cut < int(max_length * 0.5):
            cut = remaining.rfind(
                " ",
                0,
                max_length
            )

        if cut < int(max_length * 0.5):
            cut = max_length

        chunk = remaining[:cut].strip()

        if chunk:
            chunks.append(chunk)

        remaining = remaining[cut:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


# ============================================================
# CACHE
# ============================================================

def get_cached_answer(cache_key):
    with ANSWER_CACHE_LOCK:
        return ANSWER_CACHE.get(cache_key)


def set_cached_answer(cache_key, value):
    with ANSWER_CACHE_LOCK:

        if len(ANSWER_CACHE) >= ANSWER_CACHE_MAX:

            try:
                first_key = next(
                    iter(ANSWER_CACHE)
                )

                del ANSWER_CACHE[first_key]

            except Exception:
                ANSWER_CACHE.clear()

        ANSWER_CACHE[cache_key] = value


# ============================================================
# NORMALIZE QUESTION
# ============================================================

def normalize_question(question):
    question = clean_multiline_text(question)

    # Soalan biasanya satu baris
    question = re.sub(
        r"\s+",
        " ",
        question
    )

    return question.strip()


# ============================================================
# MADHHAB COMPARISON DETECTOR
# ============================================================

def is_madhhab_comparison(question):
    q = normalize_question(question).lower()

    comparison_words = [
        "banding",
        "bandingkan",
        "perbandingan",
        "perbezaan",
        "beza",
        "berbeza",
        "mazhab",
        "keempat-empat mazhab",
        "empat mazhab",
        "semua mazhab",
        "4 mazhab",
        "empat mazhab"
    ]

    for word in comparison_words:
        if word in q:
            return True

    madhhabs = [
        "syafie",
        "syafi'i",
        "hanafi",
        "maliki",
        "hanbali"
    ]

    count = sum(
        1 for m in madhhabs
        if m in q
    )

    if count >= 2:
        return True

    return False


# ============================================================
# BOOK HASH
# ============================================================

def get_book_hash(book_path):
    """
    Hash fail kitab untuk cache OCR.
    """

    path = Path(book_path)

    if not path.exists():
        return None

    try:
        stat = path.stat()

        raw = (
            f"{path.name}|"
            f"{stat.st_size}|"
            f"{stat.st_mtime_ns}"
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    except Exception as e:
        print(
            f"⚠️ HASH ERROR {book_path}: {e}"
        )

        return None


# ============================================================
# OCR PAGE
# ============================================================

def ocr_page(
    book_path,
    page_number,
    lang="ara+eng"
):
    """
    OCR satu muka surat PDF.
    """

    try:
        book_hash = get_book_hash(
            book_path
        )

        if not book_hash:
            return ""

        cache_file = (
            OCR_CACHE_PATH /
            f"{book_hash}_{page_number}_{lang.replace('+', '_')}.txt"
        )

        if cache_file.exists():

            try:
                return cache_file.read_text(
                    encoding="utf-8"
                )

            except Exception:
                pass

        images = convert_from_path(
            book_path,
            first_page=page_number,
            last_page=page_number,
            dpi=200
        )

        if not images:
            return ""

        image = images[0]

        text = pytesseract.image_to_string(
            image,
            lang=lang
        )

        text = clean_multiline_text(text)

        try:
            cache_file.write_text(
                text,
                encoding="utf-8"
            )

        except Exception as e:
            print(
                f"⚠️ OCR CACHE WRITE ERROR: {e}"
            )

        return text

    except Exception as e:
        print("❌ OCR ERROR:")
        print(e)

        return ""


# ============================================================
# LOCAL BOOK METADATA
# ============================================================

def get_local_book_metadata(book_hash):
    """
    Ambil metadata kitab lokal berdasarkan hash.

    Fungsi ini dikekalkan untuk compatibility dengan
    sistem lama.
    """

    if not book_hash:
        return None

    if not supabase:
        return None

    try:
        result = (
            supabase
            .table("books")
            .select("*")
            .eq("book_hash", book_hash)
            .limit(1)
            .execute()
        )

        rows = result.data or []

        if rows:
            return rows[0]

    except Exception as e:
        print(
            f"⚠️ LOCAL BOOK METADATA ERROR: {e}"
        )

    return None


# ============================================================
# LOCAL VECTOR SEARCH
# ============================================================

def search_local(question, limit=1):
    """
    Cari sumber daripada Supabase vector database.
    """

    if not supabase:
        print("⚠️ Supabase tidak tersedia")
        return []

    if not gemini_client:
        print("⚠️ Gemini tidak tersedia")
        return []

    try:

        # ----------------------------------------------------
        # EMBEDDING
        # ----------------------------------------------------

        embedding_result = (
            gemini_client
            .models
            .embed_content(
                model=EMBEDDING_MODEL,
                contents=question,
                config=types.EmbedContentConfig(
                    output_dimensionality=3072
                )
            )
        )

        embeddings = getattr(
            embedding_result,
            "embeddings",
            None
        )

        if not embeddings:
            print("⚠️ Tiada embedding diterima")
            return []

        first_embedding = embeddings[0]

        vector = getattr(
            first_embedding,
            "values",
            None
        )

        if not vector:
            print("⚠️ Embedding values kosong")
            return []

        # ----------------------------------------------------
        # SUPABASE RPC
        # ----------------------------------------------------

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding": vector,
                "match_threshold": 0.10,
                "match_count": 10
            }
        ).execute()

        rows = result.data or []

        if not rows:
            print("📭 LOCAL SEARCH: tiada result")
            return []

        sources = []

        for row in rows[:limit]:

            content = (
                row.get("content")
                or row.get("text")
                or row.get("chunk_text")
                or ""
            )

            content = clean_multiline_text(
                content
            )

            if not content:
                continue

            source = {
                "type": "LOCAL",
                "book": value_to_text(
                    row.get("book")
                    or row.get("book_name")
                    or row.get("title")
                    or ""
                ),
                "category": value_to_text(
                    row.get("category")
                    or row.get("category_name")
                    or ""
                ),
                "page": value_to_text(
                    row.get("page")
                    or row.get("page_number")
                    or ""
                ),
                "text": content,
                "book_hash": value_to_text(
                    row.get("book_hash")
                    or ""
                ),
                "url": value_to_text(
                    row.get("url")
                    or row.get("link")
                    or ""
                ),
                "similarity": row.get(
                    "similarity"
                )
            }

            sources.append(source)

        print(
            f"📚 LOCAL SEARCH: {len(sources)} sumber"
        )

        return sources

    except Exception as e:

        print("❌ LOCAL SEARCH ERROR:")
        traceback.print_exc()

        return []


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(question):
    """
    Cari sumber melalui Turath service.
    """

    comparison = is_madhhab_comparison(
        question
    )

    payload = {
        "query": question,
        "comparison": comparison
    }

    url = (
        TURATH_SERVICE_URL.rstrip("/")
        + "/search"
    )

    try:

        print(
            f"🔎 TURATH SEARCH | comparison={comparison}"
        )

        response = requests.post(
            url,
            json=payload,
            timeout=(10, 180)
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            rows = data

        elif isinstance(data, dict):

            rows = (
                data.get("passages")
                or data.get("results")
                or data.get("data")
                or []
            )

        else:
            rows = []

        if not isinstance(rows, list):
            rows = []

        max_results = (
            16
            if comparison
            else 10
        )

        sources = []

        for item in rows[:max_results]:

            if not isinstance(item, dict):
                continue

            text_content = (
                item.get("text")
                or item.get("content")
                or item.get("snippet")
                or item.get("snip")
                or ""
            )

            text_content = clean_multiline_text(
                text_content
            )

            if not text_content:
                continue

            meta = item.get("meta")

            book = (
                item.get("book")
                or item.get("book_name")
                or item.get("bookName")
                or ""
            )

            if not book and isinstance(meta, dict):
                book = (
                    meta.get("book")
                    or meta.get("book_name")
                    or meta.get("title")
                    or ""
                )

            page = (
                item.get("page")
                or item.get("page_number")
                or ""
            )

            if not page and isinstance(meta, dict):
                page = (
                    meta.get("page")
                    or meta.get("page_number")
                    or ""
                )

            book_id = (
                item.get("book_id")
                or item.get("bookId")
                or ""
            )

            author_id = (
                item.get("author_id")
                or item.get("authorId")
                or ""
            )

            category_id = (
                item.get("category_id")
                or item.get("categoryId")
                or ""
            )

            source_url = (
                item.get("url")
                or item.get("link")
                or ""
            )

            if not source_url and book_id:
                source_url = (
                    f"https://turath.io/book/{book_id}"
                )

            category = (
                item.get("category")
                or item.get("category_name")
                or ""
            )

            if not category and category_id:
                category = str(category_id)

            source = {
                "type": "TURATH",
                "book": clean_inline_text(book),
                "category": clean_inline_text(category),
                "page": clean_inline_text(page),
                "text": text_content,
                "book_id": clean_inline_text(book_id),
                "author_id": clean_inline_text(author_id),
                "category_id": clean_inline_text(category_id),
                "url": clean_inline_text(source_url)
            }

            sources.append(source)

        print(
            f"📚 TURATH SEARCH: {len(sources)} sumber"
        )

        return sources

    except requests.exceptions.Timeout:

        print(
            "❌ TURATH SEARCH TIMEOUT"
        )

        return []

    except requests.exceptions.RequestException as e:

        print(
            f"❌ TURATH REQUEST ERROR: {e}"
        )

        return []

    except Exception as e:

        print("❌ TURATH SEARCH ERROR:")
        traceback.print_exc()

        return []


# ============================================================
# DEDUPLICATE SOURCES
# ============================================================

def deduplicate_sources(sources):
    """
    Buang sumber duplicate.
    """

    unique = []
    seen = set()

    for source in sources:

        book = clean_inline_text(
            source.get("book")
        )

        page = clean_inline_text(
            source.get("page")
        )

        text = clean_multiline_text(
            source.get("text")
        )

        key = (
            book.lower(),
            page.lower(),
            text[:500].lower()
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(source)

    return unique


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_context(sources):
    """
    Bina context yang akan dihantar kepada Gemini.
    """

    if not sources:
        return ""

    blocks = []
    total_chars = 0

    for index, source in enumerate(
        sources,
        start=1
    ):

        source_type = clean_inline_text(
            source.get("type")
            or ""
        )

        book = clean_inline_text(
            source.get("book")
            or "Tidak diketahui"
        )

        category = clean_inline_text(
            source.get("category")
            or ""
        )

        page = clean_inline_text(
            source.get("page")
            or ""
        )

        text = clean_multiline_text(
            source.get("text")
            or ""
        )

        if not text:
            continue

        if len(text) > SOURCE_MAX_CHARS:
            text = (
                text[:SOURCE_MAX_CHARS]
                .rstrip()
                + "\n[Petikan dipendekkan]"
            )

        block = (
            f"[SUMBER {index}]\n"
            f"Jenis: {source_type}\n"
            f"Kitab: {book}\n"
        )

        if category:
            block += (
                f"Kategori: {category}\n"
            )

        if page:
            block += (
                f"Halaman: {page}\n"
            )

        block += (
            f"Petikan:\n{text}\n"
        )

        if (
            total_chars + len(block)
            > CONTEXT_MAX_CHARS
        ):
            break

        blocks.append(block)

        total_chars += len(block)

    return "\n".join(blocks)


# ============================================================
# BUILD REFERENCES
# ============================================================

def build_references(sources):
    """
    Rujukan Telegram dalam plain text.

    Jangan gunakan Markdown kerana Telegram boleh gagal
    parse apabila nama kitab / URL mengandungi aksara khas.
    """

    if not sources:

        return (
            "📚 RUJUKAN:\n"
            "• Tiada rujukan ditemui."
        )

    lines = [
        "📚 RUJUKAN:"
    ]

    seen = set()

    for index, source in enumerate(
        sources,
        start=1
    ):

        book = clean_inline_text(
            source.get("book")
            or "Kitab tidak diketahui"
        )

        page = clean_inline_text(
            source.get("page")
            or ""
        )

        source_type = clean_inline_text(
            source.get("type")
            or ""
        )

        url = clean_inline_text(
            source.get("url")
            or ""
        )

        key = (
            book.lower(),
            page.lower(),
            url.lower()
        )

        if key in seen:
            continue

        seen.add(key)

        title = book

        if page:
            title += (
                f" — halaman {page}"
            )

        if source_type:
            lines.append(
                f"• {len(lines)}. {title} [{source_type}]"
            )
        else:
            lines.append(
                f"• {len(lines)}. {title}"
            )

        if url:
            lines.append(
                f"  {url}"
            )

    if len(lines) == 1:
        lines.append(
            "• Tiada rujukan ditemui."
        )

    return "\n".join(lines)


# ============================================================
# GEMINI ANSWER
# ============================================================

def generate_answer(
    question,
    context
):
    """
    Generate jawapan berdasarkan sumber sahaja.
    """

    if not gemini_client:
        raise RuntimeError(
            "Gemini client tidak tersedia."
        )

    prompt = f"""
Anda ialah pembantu fiqh untuk TanyaFiqihBot.

Tugas anda ialah menjawab soalan pengguna berdasarkan
SUMBER KITAB yang diberikan sahaja.

SOALAN PENGGUNA:
{question}

SUMBER KITAB:
{context}

ARAHAN PENTING:

1. Jawab dalam Bahasa Melayu yang jelas dan mudah difahami.

2. Gunakan sumber yang diberikan sebagai asas utama.

3. Jangan mereka-reka fakta, hukum, nombor halaman,
   nama kitab atau URL yang tiada dalam sumber.

4. Jika terdapat perbezaan pandangan mazhab,
   terangkan perbezaan tersebut dengan jelas.

5. Jika soalan berkaitan mazhab tertentu,
   utamakan sumber mazhab tersebut.

6. Jika sumber tidak mencukupi untuk menjawab sesuatu
   perkara, nyatakan dengan jujur bahawa sumber yang
   tersedia tidak mencukupi.

7. Jangan cipta bahagian "Rujukan" sendiri.
   Sistem akan menambah rujukan secara automatik.

8. Jangan cipta URL.

9. Jangan mendakwa sesuatu sebagai ijmak atau pendapat
   semua ulama kecuali perkara tersebut benar-benar
   disokong oleh sumber.

10. Jika terdapat istilah Arab, boleh kekalkan istilah
    Arab bersama penerangan ringkas dalam Bahasa Melayu.

11. Jawapan hendaklah tersusun:
    - Hukum / jawapan ringkas
    - Huraian
    - Perbezaan pandangan jika ada
    - Kesimpulan jika sesuai

12. Jangan gunakan terlalu banyak simbol Markdown.
    Gunakan teks biasa dengan tajuk ringkas.

Jawab soalan pengguna sekarang.
"""

    try:

        print("🤖 GEMINI GENERATING...")

        response = (
            gemini_client
            .models
            .generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=2500
                )
            )
        )

        answer = getattr(
            response,
            "text",
            None
        )

        if not answer:
            return ""

        answer = clean_multiline_text(
            answer
        )

        print(
            f"✅ GEMINI ANSWER: {len(answer)} chars"
        )

        return answer

    except Exception as e:

        print("❌ GEMINI ERROR:")
        traceback.print_exc()

        return ""


# ============================================================
# FALLBACK ANSWER
# ============================================================

def fallback_answer(
    question,
    sources
):
    """
    Jawapan fallback jika Gemini gagal.
    """

    if not sources:

        return (
            "⚠️ Tiada kandungan sumber yang mencukupi "
            "untuk menghasilkan huraian.\n\n"
            "Sila cuba soalan yang lebih khusus."
        )

    first = sources[0]

    book = clean_inline_text(
        first.get("book")
        or "Kitab tidak diketahui"
    )

    page = clean_inline_text(
        first.get("page")
        or ""
    )

    text = clean_multiline_text(
        first.get("text")
        or ""
    )

    header = (
        "⚠️ Huraian AI tidak dapat dijana.\n\n"
        "Petikan sumber yang ditemui:\n"
    )

    reference = (
        f"\n\n📖 Sumber: {book}"
    )

    if page:
        reference += (
            f", halaman {page}"
        )

    return (
        header
        + text
        + reference
    )


# ============================================================
# ANSWER QUESTION
# ============================================================

def answer_question(question):
    """
    Aliran utama:
    
    Question
       ↓
    Normalize
       ↓
    Cache
       ↓
    Local Vector Search
       ↓
    Turath Search
       ↓
    Context
       ↓
    Gemini
       ↓
    References
    """

    question = normalize_question(
        question
    )

    if not question:
        return (
            "⚠️ Sila masukkan soalan."
        )

    # --------------------------------------------------------
    # CACHE KEY
    # --------------------------------------------------------

    cache_key = question.lower()

    cached = get_cached_answer(
        cache_key
    )

    if cached:
        print("⚡ CACHE HIT")
        return cached

    print("=" * 60)
    print("❓ QUESTION:")
    print(question)
    print("=" * 60)

    # --------------------------------------------------------
    # SEARCH LOCAL
    # --------------------------------------------------------

    local_sources = search_local(
        question,
        limit=1
    )

    # --------------------------------------------------------
    # SEARCH TURATH
    # --------------------------------------------------------

    turath_sources = search_turath(
        question
    )

    # --------------------------------------------------------
    # COMBINE
    # --------------------------------------------------------

    sources = (
        local_sources
        + turath_sources
    )

    sources = deduplicate_sources(
        sources
    )

    print(
        f"📚 TOTAL SOURCES: {len(sources)}"
    )

    # --------------------------------------------------------
    # NO SOURCE
    # --------------------------------------------------------

    if not sources:

        result = (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan huraian.\n\n"
            "📚 RUJUKAN:\n"
            "• Tiada rujukan ditemui."
        )

        set_cached_answer(
            cache_key,
            result
        )

        return result

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    context = build_context(
        sources
    )

    if not context:

        result = (
            "⚠️ Kandungan sumber tidak mencukupi "
            "untuk menghasilkan huraian."
        )

        set_cached_answer(
            cache_key,
            result
        )

        return result

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    explanation = generate_answer(
        question,
        context
    )

    if not explanation:
        explanation = fallback_answer(
            question,
            sources
        )

    # --------------------------------------------------------
    # REFERENCES
    # --------------------------------------------------------

    references = build_references(
        sources
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    final_answer = (
        prepare_telegram_text(
            explanation
        )
        + "\n\n"
        + references
    )

    final_answer = clean_multiline_text(
        final_answer
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    set_cached_answer(
        cache_key,
        final_answer
    )

    print(
        f"✅ FINAL ANSWER: "
        f"{len(final_answer)} chars"
    )

    return final_answer


# ============================================================
# TELEGRAM SEND LONG MESSAGE
# ============================================================

async def send_long_message(
    chat,
    text
):
    """
    Hantar mesej Telegram secara selamat.

    parse_mode sengaja TIDAK digunakan.
    """

    chunks = split_telegram_text(
        text
    )

    if not chunks:
        return []

    messages = []

    for chunk in chunks:

        message = await chat.send_message(
            text=chunk,
            disable_web_page_preview=True
        )

        messages.append(message)

    return messages


# ============================================================
# TELEGRAM START
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "🤖 TanyaFiqihBot\n\n"
        "Assalamualaikum.\n\n"
        "Saya boleh membantu mencari jawapan "
        "berkaitan fiqh berdasarkan sumber kitab "
        "yang tersedia.\n\n"
        "Contoh soalan:\n"
        "• Apa hukum qunut Subuh menurut mazhab Syafie?\n"
        "• Apakah hukum sentuh perempuan selepas wuduk?\n"
        "• Bandingkan hukum zakat fitrah empat mazhab.\n\n"
        "Taip soalan anda untuk bermula."
    )

    await send_long_message(
        update.effective_chat,
        text
    )


# ============================================================
# TELEGRAM HELP
# ============================================================

async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "📖 CARA GUNA TANYAFIQIHBOT\n\n"
        "1. Taip soalan fiqh.\n"
        "2. Bot akan mencari sumber kitab.\n"
        "3. AI akan menghuraikan berdasarkan sumber.\n"
        "4. Rujukan kitab akan dipaparkan di bawah jawapan.\n\n"
        "Contoh:\n"
        "Apakah hukum membaca qunut Subuh "
        "menurut mazhab Syafie?"
    )

    await send_long_message(
        update.effective_chat,
        text
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

async def telegram_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    question = (
        update.message.text
        or ""
    ).strip()

    if not question:
        return

    # --------------------------------------------------------
    # PROCESSING MESSAGE
    # --------------------------------------------------------

    processing_message = None

    try:

        processing_message = (
            await update.message.reply_text(
                "🔎 Sedang mencari sumber kitab..."
            )
        )

        # ----------------------------------------------------
        # RUN SEARCH + GEMINI OFF EVENT LOOP
        # ----------------------------------------------------

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        if not answer:
            answer = (
                "⚠️ Tiada jawapan dapat dihasilkan."
            )

        answer = prepare_telegram_text(
            answer
        )

        # ----------------------------------------------------
        # EDIT PROCESSING MESSAGE
        # ----------------------------------------------------

        chunks = split_telegram_text(
            answer
        )

        if not chunks:
            chunks = [
                "⚠️ Tiada jawapan dapat dihasilkan."
            ]

        try:

            await processing_message.edit_text(
                chunks[0],
                disable_web_page_preview=True
            )

        except Exception as edit_error:

            print(
                "⚠️ TELEGRAM EDIT ERROR:"
            )
            print(edit_error)

            try:
                await processing_message.delete()
            except Exception:
                pass

            await update.message.reply_text(
                chunks[0],
                disable_web_page_preview=True
            )

        # ----------------------------------------------------
        # SEND REMAINING CHUNKS
        # ----------------------------------------------------

        for chunk in chunks[1:]:

            await update.message.reply_text(
                chunk,
                disable_web_page_preview=True
            )

        print(
            "✅ TELEGRAM MESSAGE SENT"
        )

    except Exception as e:

        print(
            "❌ TELEGRAM MESSAGE ERROR:"
        )

        traceback.print_exc()

        error_text = (
            "❌ Maaf, berlaku ralat semasa "
            "memproses soalan.\n\n"
            f"Ralat: {str(e)[:500]}"
        )

        try:

            if processing_message:

                await processing_message.edit_text(
                    error_text
                )

            else:

                await update.message.reply_text(
                    error_text
                )

        except Exception as send_error:

            print(
                "❌ TELEGRAM ERROR MESSAGE FAILED:"
            )

            print(send_error)


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def telegram_error_handler(
    update,
    context
):

    print(
        "❌ TELEGRAM UPDATE ERROR:"
    )

    try:
        traceback.print_exception(
            type(context.error),
            context.error,
            context.error.__traceback__
        )

    except Exception:
        print(
            context.error
        )


# ============================================================
# TELEGRAM STARTUP
# ============================================================

async def telegram_startup():

    global telegram_application

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN belum ditetapkan."
        )

        return

    try:

        print(
            "🤖 STARTING TELEGRAM BOT..."
        )

        telegram_application = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        # ----------------------------------------------------
        # HANDLERS
        # ----------------------------------------------------

        telegram_application.add_handler(
            CommandHandler(
                "start",
                telegram_start
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "help",
                telegram_help
            )
        )

        telegram_application.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                telegram_message
            )
        )

        telegram_application.add_error_handler(
            telegram_error_handler
        )

        # ----------------------------------------------------
        # INITIALIZE
        # ----------------------------------------------------

        await telegram_application.initialize()

        # ----------------------------------------------------
        # BOT INFO
        # ----------------------------------------------------

        try:

            me = await (
                telegram_application.bot.get_me()
            )

            print(
                f"✅ TELEGRAM BOT: "
                f"@{me.username}"
            )

        except Exception as e:

            print(
                f"⚠️ BOT INFO ERROR: {e}"
            )

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        await telegram_application.start()

        # ----------------------------------------------------
        # POLLING
        # ----------------------------------------------------

        if telegram_application.updater:

            await (
                telegram_application
                .updater
                .start_polling(
                    drop_pending_updates=True
                )
            )

        print(
            "✅ TELEGRAM POLLING STARTED"
        )

        # ----------------------------------------------------
        # KEEP RUNNING
        # ----------------------------------------------------

        while True:
            await asyncio.sleep(3600)

    except Exception as e:

        print(
            "❌ TELEGRAM STARTUP ERROR:"
        )

        traceback.print_exc()

    finally:

        print(
            "🛑 TELEGRAM SERVICE STOPPED"
        )


# ============================================================
# START TELEGRAM THREAD
# ============================================================

def start_telegram():

    try:

        asyncio.run(
            telegram_startup()
        )

    except Exception as e:

        print(
            "❌ TELEGRAM THREAD ERROR:"
        )

        traceback.print_exc()


# ============================================================
# SYNC BOOKS
# ============================================================

def startup_sync():

    """
    Jalankan sync_books jika fungsi tersebut wujud
    daripada sistem lama / module tambahan.
    """

    try:

        sync_function = globals().get(
            "sync_books"
        )

        if callable(sync_function):

            print(
                "🔄 RUNNING BOOK SYNC..."
            )

            sync_function()

            print(
                "✅ BOOK SYNC COMPLETE"
            )

        else:

            print(
                "ℹ️ sync_books() tidak didefinisikan. "
                "Skip sync."
            )

    except Exception as e:

        print(
            "❌ BOOK SYNC ERROR:"
        )

        traceback.print_exc()


# ============================================================
# BACKGROUND SERVICES
# ============================================================

_background_started = False
_background_lock = threading.Lock()


def start_background_services():

    global _background_started

    with _background_lock:

        if _background_started:
            print(
                "ℹ️ Background services already started."
            )

            return

        _background_started = True

    # --------------------------------------------------------
    # SYNC THREAD
    # --------------------------------------------------------

    sync_thread = threading.Thread(
        target=startup_sync,
        name="book-sync",
        daemon=True
    )

    sync_thread.start()

    print(
        "✅ BOOK SYNC THREAD STARTED"
    )

    # --------------------------------------------------------
    # TELEGRAM THREAD
    # --------------------------------------------------------

    if TELEGRAM_TOKEN:

        telegram_thread = threading.Thread(
            target=start_telegram,
            name="telegram-bot",
            daemon=True
        )

        telegram_thread.start()

        print(
            "✅ TELEGRAM THREAD STARTED"
        )

    else:

        print(
            "⚠️ TELEGRAM DISABLED "
            "(TELEGRAM_TOKEN kosong)"
        )


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "status": "ok",
        "service": "TanyaFiqihBot",
        "message": "TanyaFiqihBot is running."
    })


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "status": "ok",
        "supabase": supabase is not None,
        "gemini": gemini_client is not None,
        "telegram": bool(TELEGRAM_TOKEN),
        "turath_service": TURATH_SERVICE_URL,
        "llm_model": LLM_MODEL,
        "embedding_model": EMBEDDING_MODEL
    })


# ============================================================
# ASK API
# ============================================================

@app.route(
    "/ask",
    methods=["POST"]
)
def ask():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        question = (
            data.get("question")
            or data.get("query")
            or ""
        )

        question = normalize_question(
            question
        )

        if not question:

            return jsonify({
                "success": False,
                "error": "Sila masukkan soalan."
            }), 400

        answer = answer_question(
            question
        )

        return jsonify({
            "success": True,
            "question": question,
            "answer": answer
        })

    except Exception as e:

        print(
            "❌ /ask ERROR:"
        )

        traceback.print_exc()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# START SERVICES
# ============================================================

start_background_services()


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    host = os.getenv(
        "HOST",
        "0.0.0.0"
    )

    print("=" * 60)
    print(
        f"🌐 FLASK SERVER: "
        f"http://{host}:{port}"
    )
    print("=" * 60)

    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True
    )
