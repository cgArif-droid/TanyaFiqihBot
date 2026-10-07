import os
import re
import json
import time
import asyncio
import hashlib
import threading
from pathlib import Path

import requests
import pytesseract

from pdf2image import convert_from_path
from flask import Flask, request, jsonify

from supabase import create_client
from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# ENVIRONMENT
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

DATA_DIR = os.getenv(
    "DATA_DIR",
    "/var/data"
).strip()

OCR_WORKERS = int(
    os.getenv(
        "OCR_WORKERS",
        "1"
    )
)

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
).strip().rstrip("/")


# ============================================================
# PATH
# ============================================================

DATA_PATH = Path(DATA_DIR)

EXTRACTED_TEXT_DIR = (
    DATA_PATH / "extracted_text"
)

PAGE_CACHE_DIR = (
    EXTRACTED_TEXT_DIR / "pages"
)

EXTRACTED_TEXT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

PAGE_CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# SUPABASE
# ============================================================

supabase = None

if SUPABASE_URL and SUPABASE_KEY:

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print(
            "✅ SUPABASE CONNECTED"
        )

    except Exception as e:

        print(
            "❌ SUPABASE CONNECTION ERROR:",
            e
        )

        supabase = None

else:

    print(
        "⚠️ SUPABASE ENV BELUM LENGKAP"
    )


# ============================================================
# GEMINI
# ============================================================

gemini_client = None

if GOOGLE_API_KEY:

    try:

        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print(
            "✅ GEMINI CLIENT READY"
        )

    except Exception as e:

        print(
            "❌ GEMINI CLIENT ERROR:",
            e
        )

        gemini_client = None

else:

    print(
        "⚠️ GOOGLE_API_KEY BELUM ADA"
    )


# ============================================================
# CACHE
# ============================================================

ANSWER_CACHE = {}

MAX_CACHE = 500

CACHE_LOCK = threading.Lock()


def normalize_question(question):

    if not question:
        return ""

    question = str(
        question
    ).strip()

    question = re.sub(
        r"\s+",
        " ",
        question
    )

    return question


def cache_key(question):

    question = normalize_question(
        question
    )

    return hashlib.sha256(
        question.lower().encode(
            "utf-8"
        )
    ).hexdigest()


def get_cached_answer(question):

    key = cache_key(
        question
    )

    with CACHE_LOCK:

        return ANSWER_CACHE.get(
            key
        )


def set_cached_answer(
    question,
    answer
):

    key = cache_key(
        question
    )

    with CACHE_LOCK:

        if len(ANSWER_CACHE) >= MAX_CACHE:

            try:

                first_key = next(
                    iter(
                        ANSWER_CACHE
                    )
                )

                ANSWER_CACHE.pop(
                    first_key,
                    None
                )

            except Exception:
                pass

        ANSWER_CACHE[key] = answer


# ============================================================
# TEXT UTILITIES
# ============================================================

def clean_text(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace(
        "\x00",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def truncate_text(
    text,
    max_chars=7000
):

    text = clean_text(
        text
    )

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        +
        "\n...[dipendekkan]"
    )


# ============================================================
# CATEGORY LOCAL
# ============================================================

def detect_category(question):

    q = normalize_question(
        question
    ).lower()

    if any(
        word in q
        for word in [
            "solat",
            "sembahyang",
            "wuduk",
            "tayammum",
            "mandi wajib",
            "hadas",
            "najis",
            "puasa",
            "zakat",
            "haji",
            "umrah",
            "haid",
            "nifas",
            "istihadah",
            "nikah",
            "talak",
            "cerai",
            "jual beli",
            "muamalat",
            "faraid",
            "waris",
        ]
    ):
        return "FIQH"

    if any(
        word in q
        for word in [
            "tauhid",
            "akidah",
            "aqidah",
            "iman",
            "syirik",
            "kufur",
            "asma",
            "sifat allah",
        ]
    ):
        return "TAUHID"

    if any(
        word in q
        for word in [
            "hadis",
            "hadith",
            "sabda nabi",
            "riwayat",
        ]
    ):
        return "HADIS"

    if any(
        word in q
        for word in [
            "tafsir",
            "ayat al-quran",
            "maksud ayat",
            "surah",
        ]
    ):
        return "TAFSIR"

    if any(
        word in q
        for word in [
            "sirah",
            "rasulullah",
            "nabi muhammad",
            "perang badar",
            "perang uhud",
        ]
    ):
        return "SIRAH"

    if any(
        word in q
        for word in [
            "akhlak",
            "adab",
            "sabar",
            "ikhlas",
            "amanah",
        ]
    ):
        return "AKHLAK"

    if any(
        word in q
        for word in [
            "usul fiqh",
            "qiyas",
            "ijmak",
            "ijtihad",
            "istihsan",
            "maslahah",
        ]
    ):
        return "USUL FIQH"

    return "FIQH"


# ============================================================
# MAZHAB COMPARISON
# ============================================================

def is_madhhab_comparison(question):

    q = normalize_question(
        question
    ).lower()

    patterns = [

        # Melayu
        "banding mazhab",
        "bandingan mazhab",
        "perbandingan mazhab",
        "bandingkan mazhab",
        "bezakan mazhab",
        "perbezaan mazhab",
        "beza mazhab",
        "keempat-empat mazhab",
        "empat mazhab",
        "4 mazhab",

        # Arabic
        "مقارنة المذاهب",
        "مقارنة بين المذاهب",
        "المذاهب الأربعة",

        # Explicit madhhab
        "syafie dan hanafi",
        "syafie dan maliki",
        "syafie dan hanbali",
        "hanafi dan maliki",
        "hanafi dan hanbali",
        "maliki dan hanbali",

        "الشافعي والحنفي",
        "الشافعي والمالكي",
        "الشافعي والحنبلي",
        "الحنفي والمالكي",
        "الحنفي والحنبلي",
        "المالكي والحنبلي",
    ]

    for pattern in patterns:

        if pattern in q:
            return True

    count = 0

    if (
        "syafie" in q
        or "shafii" in q
        or "شافعي" in q
    ):
        count += 1

    if (
        "hanafi" in q
        or "حنفي" in q
    ):
        count += 1

    if (
        "maliki" in q
        or "مالكي" in q
    ):
        count += 1

    if (
        "hanbali" in q
        or "حنبلي" in q
    ):
        count += 1

    return count >= 2


# ============================================================
# FILE HASH
# ============================================================

def get_file_hash(path):

    try:

        hasher = hashlib.sha256()

        with open(
            path,
            "rb"
        ) as f:

            while True:

                chunk = f.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                hasher.update(
                    chunk
                )

        return hasher.hexdigest()[:16]

    except Exception:

        return hashlib.sha256(
            str(path).encode(
                "utf-8"
            )
        ).hexdigest()[:16]


# ============================================================
# CHUNK TEXT
# ============================================================

def chunk_text(
    text,
    chunk_size=5000,
    overlap=500
):

    text = clean_text(
        text
    )

    if not text:
        return []

    if len(text) <= chunk_size:

        return [text]

    chunks = []

    start = 0

    total = len(text)

    while start < total:

        end = min(
            start + chunk_size,
            total
        )

        chunk = text[
            start:end
        ].strip()

        if chunk:

            chunks.append(
                chunk
            )

        if end >= total:
            break

        start = max(
            end - overlap,
            start + 1
        )

    return chunks


# ============================================================
# OCR PAGE
# ============================================================

def ocr_page(
    image,
    lang="ara+msa+eng"
):

    try:

        text = pytesseract.image_to_string(
            image,
            lang=lang
        )

        return clean_text(
            text
        )

    except Exception as e:

        print(
            "❌ OCR ERROR:",
            e
        )

        return ""


# ============================================================
# OCR PDF
# ============================================================

def extract_pdf_text(
    pdf_path,
    book_hash
):

    pdf_path = Path(
        pdf_path
    )

    book_cache_dir = (
        PAGE_CACHE_DIR /
        book_hash
    )

    book_cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        print(
            f"📖 OCR PDF: "
            f"{pdf_path.name}"
        )

        images = convert_from_path(
            str(pdf_path),
            dpi=200
        )

        pages = []

        total_pages = len(
            images
        )

        for index, image in enumerate(
            images
        ):

            page_number = index + 1

            cache_file = (
                book_cache_dir /
                f"{page_number}.txt"
            )

            if cache_file.exists():

                try:

                    text = cache_file.read_text(
                        encoding="utf-8"
                    )

                    text = clean_text(
                        text
                    )

                    if text:

                        pages.append(
                            text
                        )

                        continue

                except Exception:
                    pass

            print(
                f"🔎 OCR PAGE "
                f"{page_number}/{total_pages}"
            )

            text = ocr_page(
                image
            )

            try:

                cache_file.write_text(
                    text,
                    encoding="utf-8"
                )

            except Exception as e:

                print(
                    "⚠️ OCR CACHE ERROR:",
                    e
                )

            if text:

                pages.append(
                    text
                )

        return "\n\n".join(
            pages
        )

    except Exception as e:

        print(
            "❌ PDF OCR ERROR:",
            e
        )

        return ""


# ============================================================
# TXT
# ============================================================

def read_text_file(path):

    try:

        return clean_text(
            Path(path).read_text(
                encoding="utf-8",
                errors="ignore"
            )
        )

    except Exception as e:

        print(
            "❌ READ TXT ERROR:",
            e
        )

        return ""


# ============================================================
# BOOK TEXT
# ============================================================

def extract_book_text(
    path,
    book_hash
):

    path = Path(
        path
    )

    extension = (
        path.suffix.lower()
    )

    if extension == ".txt":

        return read_text_file(
            path
        )

    if extension == ".pdf":

        return extract_pdf_text(
            path,
            book_hash
        )

    return ""


# ============================================================
# SUPABASE DELETE
# ============================================================

def delete_book_from_supabase(
    book_hash
):

    if not supabase:
        return False

    try:

        (
            supabase
            .table(
                "kitab_chunks"
            )
            .delete()
            .eq(
                "book_hash",
                book_hash
            )
            .execute()
        )

        print(
            f"🗑️ Supabase data deleted: "
            f"{book_hash}"
        )

        return True

    except Exception as e:

        print(
            "❌ DELETE SUPABASE ERROR:",
            e
        )

        return False


# ============================================================
# SUPABASE INSERT
# ============================================================

def insert_chunk(
    book_name,
    category,
    book_hash,
    chunk,
    embedding,
    chunk_index
):

    if not supabase:
        return False

    payload = {

        "book_name": book_name,

        "category": category,

        "book_hash": book_hash,

        "content": chunk,

        "embedding": embedding,

        "chunk_index": chunk_index,
    }

    try:

        (
            supabase
            .table(
                "kitab_chunks"
            )
            .insert(
                payload
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "❌ SUPABASE INSERT ERROR:",
            e
        )

        return False


# ============================================================
# GEMINI EMBEDDING
# ============================================================

def generate_embedding(text):

    if not gemini_client:
        return None

    try:

        result = (
            gemini_client
            .models
            .embed_content(

                model=EMBEDDING_MODEL,

                contents=text,

                config=types.EmbedContentConfig(
                    output_dimensionality=3072
                )
            )
        )

        if not result.embeddings:

            return None

        return (
            result
            .embeddings[0]
            .values
        )

    except Exception as e:

        print(
            "❌ EMBEDDING ERROR:",
            e
        )

        return None


# ============================================================
# PROCESS BOOK
# ============================================================

def process_book(
    path,
    category
):

    path = Path(
        path
    )

    print()
    print(
        "=" * 60
    )

    print(
        f"📚 PROCESS: "
        f"{path.name}"
    )

    print(
        f"📂 CATEGORY: "
        f"{category}"
    )

    book_hash = get_file_hash(
        path
    )

    print(
        f"🔑 HASH: "
        f"{book_hash}"
    )

    delete_book_from_supabase(
        book_hash
    )

    text = extract_book_text(
        path,
        book_hash
    )

    if not text:

        print(
            f"⚠️ TIADA TEKS: "
            f"{path.name}"
        )

        return False

    chunks = chunk_text(
        text
    )

    print(
        f"📦 Jumlah chunks: "
        f"{len(chunks)}"
    )

    for index, chunk in enumerate(
        chunks
    ):

        print(
            f"🧠 EMBEDDING "
            f"{index + 1}-"
            f"{len(chunks)}"
        )

        embedding = generate_embedding(
            chunk
        )

        if not embedding:

            print(
                "⚠️ Embedding gagal"
            )

            continue

        print(
            f"💾 Supabase "
            f"{index + 1}-"
            f"{len(chunks)}"
        )

        success = insert_chunk(
            book_name=path.stem,
            category=category,
            book_hash=book_hash,
            chunk=chunk,
            embedding=embedding,
            chunk_index=index
        )

        if not success:

            print(
                f"⚠️ Gagal simpan chunk "
                f"{index + 1}"
            )

    print(
        f"✅ READY: "
        f"{path.stem}"
    )

    return True


# ============================================================
# SYNC KITAB
# ============================================================

def sync_books():

    print()
    print(
        "🔄 SYNC KITAB"
    )
    print(
        "=" * 60
    )

    kitab_root = Path(
        "/app/kitab"
    )

    if not kitab_root.exists():

        print(
            f"⚠️ Folder kitab tidak wujud: "
            f"{kitab_root}"
        )

        return

    extensions = {
        ".pdf",
        ".txt"
    }

    books = []

    for path in kitab_root.rglob("*"):

        if not path.is_file():
            continue

        if (
            path.suffix.lower()
            not in extensions
        ):
            continue

        try:

            relative = path.relative_to(
                kitab_root
            )

            if len(relative.parts) > 1:

                category = (
                    relative.parts[0]
                )

            else:

                category = "FIQH"

        except Exception:

            category = "FIQH"

        books.append(
            (
                path,
                category
            )
        )

    print(
        f"📚 Jumlah kitab: "
        f"{len(books)}"
    )

    for path, category in books:

        try:

            process_book(
                path,
                category
            )

        except Exception as e:

            print(
                f"❌ PROCESS ERROR "
                f"{path.name}:",
                e
            )

    print(
        "🏁 SYNC SELESAI"
    )


# ============================================================
# LOCAL SEARCH
# ============================================================

def search_local(
    question,
    limit=1
):

    if not supabase:

        print(
            "⚠️ LOCAL SEARCH: "
            "Supabase tiada"
        )

        return []

    embedding = generate_embedding(
        question
    )

    if not embedding:

        return []

    try:

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding": embedding,

                "match_threshold": 0.20,

                "match_count": 1,
            }
        ).execute()

        rows = (
            result.data
            or []
        )

        # LOCAL = SATU SAHAJA
        rows = rows[:1]

        print(
            f"📚 LOCAL SOURCES: "
            f"{len(rows)}"
        )

        return rows

    except Exception as e:

        print(
            "❌ LOCAL SEARCH ERROR:",
            e
        )

        return []


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(
    question
):

    comparison = (
        is_madhhab_comparison(
            question
        )
    )

    mode = (
        "comparison"
        if comparison
        else "normal"
    )

    print()
    print(
        "🔎 TURATH SEARCH"
    )

    print(
        f"📌 MODE: {mode}"
    )

    if comparison:

        print(
            "📚 TARGET:"
        )

        print(
            "   Syafie = 10"
        )

        print(
            "   Hanafi = 2"
        )

        print(
            "   Maliki = 2"
        )

        print(
            "   Hanbali = 2"
        )

    else:

        print(
            "📚 TARGET:"
        )

        print(
            "   Syafie = 10"
        )

    try:

        response = requests.post(

            f"{TURATH_SERVICE_URL}/search",

            json={

                "query": question,

                "comparison": comparison,

            },

            timeout=180
        )

        if response.status_code != 200:

            print(
                "❌ TURATH HTTP ERROR:",
                response.status_code
            )

            try:

                print(
                    response.text[:2000]
                )

            except Exception:
                pass

            return []

        data = response.json()

        passages = (
            data.get(
                "passages"
            )
            or []
        )

        print(
            f"📖 TURATH SOURCES: "
            f"{len(passages)}"
        )

        # Jangan potong di sini.
        #
        # turath_service.mjs bertanggungjawab
        # menentukan:
        #
        # NORMAL
        #   10 Syafie
        #
        # COMPARISON
        #   10 Syafie
        #   2 Hanafi
        #   2 Maliki
        #   2 Hanbali

        return passages

    except requests.exceptions.Timeout:

        print(
            "❌ TURATH TIMEOUT"
        )

        return []

    except Exception as e:

        print(
            "❌ TURATH SEARCH ERROR:",
            e
        )

        return []


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_context(
    local_results,
    turath_results
):

    sections = []

    # --------------------------------------------------------
    # LOCAL
    # --------------------------------------------------------

    for index, item in enumerate(
        local_results,
        start=1
    ):

        content = (
            item.get("content")
            or item.get("text")
            or item.get("chunk")
            or ""
        )

        if not content:
            continue

        book_name = (
            item.get("book_name")
            or item.get("kitab")
            or "Kitab tempatan"
        )

        category = (
            item.get("category")
            or ""
        )

        sections.append(
            f"""
[SUMBER LOCAL {index}]
Kitab: {book_name}
Kategori: {category}

PETIKAN:
{truncate_text(content, 6500)}
""".strip()
        )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    for index, item in enumerate(
        turath_results,
        start=1
    ):

        content = (
            item.get("content")
            or item.get("text")
            or item.get("snip")
            or ""
        )

        if not content:
            continue

        book_name = (
            item.get("book_name")
            or item.get("book")
            or "Kitab Turath"
        )

        author = (
            item.get("author")
            or item.get("author_name")
            or ""
        )

        category = (
            item.get("category")
            or ""
        )

        page = (
            item.get("page")
            or item.get("page_number")
            or ""
        )

        sections.append(
            f"""
[SUMBER TURATH {index}]
Kitab: {book_name}
Pengarang: {author}
Kategori: {category}
Halaman: {page}

PETIKAN:
{truncate_text(content, 6500)}
""".strip()
        )

    return "\n\n".join(
        sections
    )


# ============================================================
# REFERENCES
# ============================================================

def build_references(
    local_results,
    turath_results
):

    lines = []

    # --------------------------------------------------------
    # LOCAL
    # --------------------------------------------------------

    if local_results:

        lines.append(
            "📚 *Rujukan Local*"
        )

        for index, item in enumerate(
            local_results,
            start=1
        ):

            book_name = (
                item.get("book_name")
                or item.get("kitab")
                or "Kitab tempatan"
            )

            category = (
                item.get("category")
                or ""
            )

            line = (
                f"{index}. "
                f"{book_name}"
            )

            if category:

                line += (
                    f" — {category}"
                )

            lines.append(
                line
            )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    if turath_results:

        if lines:
            lines.append("")

        lines.append(
            "📖 *Rujukan Turath*"
        )

        for index, item in enumerate(
            turath_results,
            start=1
        ):

            book_name = (
                item.get("book_name")
                or item.get("book")
                or "Kitab Turath"
            )

            author = (
                item.get("author")
                or item.get("author_name")
                or ""
            )

            category = (
                item.get("category")
                or ""
            )

            page = (
                item.get("page")
                or item.get("page_number")
                or ""
            )

            url = (
                item.get("url")
                or item.get("link")
                or ""
            )

            line = (
                f"{index}. "
                f"{book_name}"
            )

            if author:

                line += (
                    f" — {author}"
                )

            if category:

                line += (
                    f" [{category}]"
                )

            if page:

                line += (
                    f" — hlm. {page}"
                )

            lines.append(
                line
            )

            if url:

                lines.append(
                    f"   🔗 {url}"
                )

    return "\n".join(
        lines
    )


# ============================================================
# GEMINI EXPLANATION
# ============================================================

def generate_answer(
    question,
    context
):

    if not context:

        return (
            "⚠️ Tiada petikan sumber "
            "ditemui untuk soalan ini."
        )

    cached = get_cached_answer(
        question
    )

    if cached:

        print(
            "⚡ GEMINI CACHE HIT"
        )

        return cached

    if not gemini_client:

        return (
            "⚠️ *Huraian AI tidak dapat "
            "dijana buat masa ini.*\n\n"
            "Gemini belum dikonfigurasi."
        )

    system_instruction = """
Anda ialah pembantu ilmu Islam untuk TanyaFiqihBot.

Tugas anda HANYA menghuraikan jawapan berdasarkan
PETIKAN SUMBER yang diberikan.

PERATURAN:

1. Jangan mencari rujukan sendiri.
2. Jangan mereka-reka kitab.
3. Jangan mereka-reka nombor halaman.
4. Jangan menghasilkan URL.
5. Jangan menghasilkan senarai rujukan.
6. Rujukan telah dicari oleh sistem.
7. Gunakan hanya maklumat daripada sumber yang diberikan.
8. Jika sumber tidak mencukupi, nyatakan perkara itu.
9. Jangan membuat dakwaan yang tidak disokong sumber.
10. Untuk soalan fiqh, jelaskan berdasarkan petikan.
11. Jika ada perbezaan mazhab, bentangkan perbezaan
    berdasarkan sumber yang diberikan.
12. Jawab dalam Bahasa Melayu.
13. Jika sumber dalam Bahasa Arab, terangkan maksudnya
    dalam Bahasa Melayu.
14. Jangan gunakan pengetahuan luar untuk mengisi
    maklumat yang tiada dalam sumber.

Format:

Jawapan:
...

Huraian:
...

Kesimpulan:
...

Jika sumber tidak mencukupi, nyatakan dengan jelas.
"""

    prompt = f"""
SOALAN PENGGUNA:

{question}


PETIKAN SUMBER:

{context}


Huraikan jawapan berdasarkan sumber yang diberikan.
"""

    try:

        print(
            "🤖 GEMINI: "
            "GENERATE EXPLANATION"
        )

        response = (
            gemini_client
            .models
            .generate_content(

                model=LLM_MODEL,

                contents=prompt,

                config=types.GenerateContentConfig(

                    system_instruction=(
                        system_instruction
                    ),

                    temperature=0.2,

                    max_output_tokens=2500,
                )
            )
        )

        answer = ""

        if response:

            answer = (
                response.text
                or ""
            )

        answer = answer.strip()

        if not answer:

            return (
                "⚠️ *Huraian AI tidak dapat "
                "dijana buat masa ini.*"
            )

        set_cached_answer(
            question,
            answer
        )

        return answer

    except Exception as e:

        error_text = str(e)

        print(
            "❌ GEMINI ERROR:",
            error_text
        )

        if (
            "429" in error_text
            or
            "RESOURCE_EXHAUSTED"
            in error_text
            or
            "quota"
            in error_text.lower()
        ):

            return (
                "⚠️ *Huraian AI tidak dapat "
                "dijana buat masa ini.*\n\n"
                "Kuota Gemini telah habis "
                "atau sementara tidak tersedia.\n\n"
                "📚 Rujukan sumber masih "
                "dipaparkan di bawah."
            )

        return (
            "⚠️ *Huraian AI tidak dapat "
            "dijana buat masa ini.*\n\n"
            "Rujukan sumber masih "
            "dipaparkan di bawah."
        )


# ============================================================
# MAIN ANSWER
# ============================================================

def answer_question(
    question
):

    question = normalize_question(
        question
    )

    if not question:

        return (
            "⚠️ Sila masukkan soalan."
        )

    print()
    print(
        "=" * 60
    )

    print(
        f"❓ SOALAN: "
        f"{question}"
    )

    comparison = (
        is_madhhab_comparison(
            question
        )
    )

    if comparison:

        print(
            "⚖️ MODE: "
            "PERBANDINGAN MAZHAB"
        )

    else:

        print(
            "🕌 MODE: "
            "MAZHAB SYAFIE"
        )

    # --------------------------------------------------------
    # LOCAL = 1
    # --------------------------------------------------------

    local_results = search_local(
        question,
        limit=1
    )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    turath_results = search_turath(
        question
    )

    total_sources = (
        len(local_results)
        +
        len(turath_results)
    )

    print(
        f"📦 TOTAL SOURCES: "
        f"{total_sources}"
    )

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    context = build_context(
        local_results,
        turath_results
    )

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    answer = generate_answer(
        question,
        context
    )

    # --------------------------------------------------------
    # REFERENCES
    # --------------------------------------------------------

    references = build_references(
        local_results,
        turath_results
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    parts = []

    if answer:

        parts.append(
            answer
        )

    if references:

        parts.append(
            references
        )

    if not parts:

        return (
            "⚠️ Tiada sumber ditemui "
            "untuk soalan ini."
        )

    return "\n\n".join(
        parts
    )


# ============================================================
# TELEGRAM START
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(

        "🤖 *TanyaFiqihBot*\n\n"

        "Assalamualaikum.\n\n"

        "Sila kemukakan soalan "
        "berkaitan ilmu Islam.\n\n"

        "📚 Sistem akan mencari sumber "
        "kitab terlebih dahulu sebelum "
        "menghasilkan huraian.",

        parse_mode="Markdown"
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

    try:

        await update.message.chat.send_action(
            action="typing"
        )

    except Exception:
        pass

    try:

        answer = answer_question(
            question
        )

    except Exception as e:

        print(
            "❌ ANSWER ERROR:",
            e
        )

        answer = (
            "⚠️ Berlaku ralat semasa "
            "memproses soalan.\n\n"
            "Sila cuba semula."
        )

    # Telegram limit lebih kurang 4096.
    max_length = 3900

    if len(answer) <= max_length:

        await update.message.reply_text(
            answer,
            disable_web_page_preview=True
        )

        return

    chunks = []

    start = 0

    while start < len(answer):

        end = min(
            start + max_length,
            len(answer)
        )

        if end < len(answer):

            newline = answer.rfind(
                "\n",
                start,
                end
            )

            if newline > start:

                end = newline

        chunks.append(
            answer[start:end]
        )

        start = end

    for chunk in chunks:

        await update.message.reply_text(
            chunk,
            disable_web_page_preview=True
        )


# ============================================================
# TELEGRAM ERROR
# ============================================================

async def telegram_error_handler(
    update,
    context
):

    print(
        "❌ TELEGRAM ERROR:",
        context.error
    )


# ============================================================
# TELEGRAM STARTUP
# ============================================================

telegram_application = None


def start_telegram():

    global telegram_application

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN TIADA"
        )

        return

    try:

        async def runner():

            global telegram_application

            telegram_application = (
                Application
                .builder()
                .token(
                    TELEGRAM_TOKEN
                )
                .build()
            )

            telegram_application.add_handler(
                CommandHandler(
                    "start",
                    telegram_start
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

            await (
                telegram_application
                .initialize()
            )

            await (
                telegram_application
                .start()
            )

            await (
                telegram_application
                .updater
                .start_polling()
            )

            print(
                "✅ Telegram polling started"
            )

            while True:

                await asyncio.sleep(
                    3600
                )

        asyncio.run(
            runner()
        )

    except Exception as e:

        print(
            "❌ TELEGRAM START ERROR:",
            e
        )


# ============================================================
# HOME
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({

        "status": "ok",

        "bot": "TanyaFiqihBot",

        "architecture": {

            "local_sources": 1,

            "turath_normal": {
                "shafii": 10
            },

            "turath_comparison": {

                "shafii": 10,

                "hanafi": 2,

                "maliki": 2,

                "hanbali": 2
            },

            "gemini": (
                "explanation_only"
            )
        },

        "models": {

            "llm": LLM_MODEL,

            "embedding": EMBEDDING_MODEL
        },

        "embedding_dimension": 3072,

        "turath_service": (
            TURATH_SERVICE_URL
        ),

        "ocr_workers": OCR_WORKERS,

        "data_dir": DATA_DIR,

        "cache_size": len(
            ANSWER_CACHE
        )
    })


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    turath_ok = False

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/health",
            timeout=5
        )

        turath_ok = (
            response.status_code == 200
        )

    except Exception:
        turath_ok = False

    return jsonify({

        "status": "healthy",

        "supabase": bool(
            supabase
        ),

        "gemini": bool(
            gemini_client
        ),

        "telegram": bool(
            TELEGRAM_TOKEN
        ),

        "turath_service": turath_ok,

        "turath_service_url": (
            TURATH_SERVICE_URL
        ),

        "local_references": 1,

        "turath_normal": {

            "shafii": 10
        },

        "turath_comparison": {

            "shafii": 10,

            "hanafi": 2,

            "maliki": 2,

            "hanbali": 2
        },

        "cache_size": len(
            ANSWER_CACHE
        )
    })


# ============================================================
# ASK API
# ============================================================

@app.route(
    "/ask",
    methods=["GET", "POST"]
)
def ask():

    try:

        if request.method == "POST":

            data = (
                request.get_json(
                    silent=True
                )
                or {}
            )

            question = (
                data.get(
                    "question"
                )
                or ""
            )

        else:

            question = (
                request.args.get(
                    "q",
                    ""
                )
            )

        question = normalize_question(
            question
        )

        if not question:

            return jsonify({

                "ok": False,

                "error": (
                    "Sila masukkan soalan."
                )

            }), 400

        answer = answer_question(
            question
        )

        return jsonify({

            "ok": True,

            "question": question,

            "comparison": (
                is_madhhab_comparison(
                    question
                )
            ),

            "answer": answer

        })

    except Exception as e:

        print(
            "❌ /ask ERROR:",
            e
        )

        return jsonify({

            "ok": False,

            "error": str(e)

        }), 500


# ============================================================
# STARTUP SYNC
# ============================================================

def startup_sync():

    try:

        sync_books()

    except Exception as e:

        print(
            "❌ STARTUP SYNC ERROR:",
            e
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "=" * 60
    )

    print(
        "🤖 TanyaFiqihBot STARTING"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # SYNC KITAB
    # --------------------------------------------------------

    sync_thread = threading.Thread(
        target=startup_sync,
        daemon=True
    )

    sync_thread.start()

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True
    )

    telegram_thread.start()

    # --------------------------------------------------------
    # FLASK
    # --------------------------------------------------------

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        f"🌐 Flask starting "
        f"on port {port}"
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
