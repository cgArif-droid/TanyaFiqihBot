import os
import re
import json
import time
import asyncio
import hashlib
import threading
import traceback
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
# ENV
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

DATA_DIR = os.getenv("DATA_DIR", "/var/data")

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "1"))

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.8-flash"
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001"
)

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).rstrip("/")


# ============================================================
# PATH
# ============================================================

DATA_PATH = Path(DATA_DIR)

DATA_PATH.mkdir(
    parents=True,
    exist_ok=True
)

OCR_CACHE_DIR = DATA_PATH / "extracted_text" / "pages"

OCR_CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# GLOBAL CLIENTS
# ============================================================

supabase = None
gemini_client = None


# ============================================================
# CACHE
# ============================================================

ANSWER_CACHE = {}

ANSWER_CACHE_MAX = 500


# ============================================================
# STARTUP
# ============================================================

print()
print("=" * 60)
print("🤖 TanyaFiqihBot STARTING")
print("=" * 60)


# ============================================================
# CONNECT SUPABASE
# ============================================================

try:

    if SUPABASE_URL and SUPABASE_KEY:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ SUPABASE CONNECTED")

    else:

        print("⚠️ SUPABASE ENV MISSING")

except Exception as e:

    print(
        "❌ SUPABASE CONNECTION ERROR:",
        repr(e)
    )

    traceback.print_exc()


# ============================================================
# CONNECT GEMINI
# ============================================================

try:

    if GOOGLE_API_KEY:

        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print("✅ GEMINI CLIENT READY")

    else:

        print("⚠️ GOOGLE_API_KEY MISSING")

except Exception as e:

    print(
        "❌ GEMINI CONNECTION ERROR:",
        repr(e)
    )

    traceback.print_exc()


# ============================================================
# TEXT HELPERS
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


def normalize_question(question):

    question = clean_text(
        question
    )

    return question


def short_text(text, max_chars=5000):

    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "..."


# ============================================================
# DETECT COMPARISON
# ============================================================

def is_madhhab_comparison(question):

    q = question.lower()

    comparison_words = [
        "banding",
        "bandingkan",
        "perbandingan",
        "mazhab",
        "mazhab-mazhab",
        "syafie dan hanafi",
        "syafie dan maliki",
        "syafie dan hanbali",
        "hanafi dan syafie",
        "maliki dan syafie",
        "hanbali dan syafie",
        "keempat-empat mazhab",
        "empat mazhab",
        "semua mazhab",
    ]

    for word in comparison_words:

        if word in q:
            return True

    # Jika soalan menyebut sekurang-kurangnya
    # dua mazhab secara jelas

    madhhab_count = 0

    madhhab_words = [
        "syafie",
        "hanafi",
        "maliki",
        "hanbali",
    ]

    for word in madhhab_words:

        if word in q:
            madhhab_count += 1

    return madhhab_count >= 2


# ============================================================
# OCR
# ============================================================

def get_book_hash(path):

    stat = path.stat()

    raw = (
        f"{path}"
        f"|{stat.st_size}"
        f"|{stat.st_mtime_ns}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]


def ocr_page(
    pdf_path,
    page_number,
    book_hash
):

    cache_dir = (
        OCR_CACHE_DIR /
        book_hash
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    cache_file = (
        cache_dir /
        f"{page_number}.txt"
    )

    # --------------------------------------------
    # CACHE HIT
    # --------------------------------------------

    if cache_file.exists():

        try:

            return cache_file.read_text(
                encoding="utf-8"
            )

        except Exception:
            pass

    # --------------------------------------------
    # OCR
    # --------------------------------------------

    try:

        pages = convert_from_path(
            str(pdf_path),
            dpi=200,
            first_page=page_number,
            last_page=page_number
        )

        if not pages:
            return ""

        image = pages[0]

        text = pytesseract.image_to_string(
            image,
            lang="ara+msa+eng"
        )

        text = clean_text(text)

        cache_file.write_text(
            text,
            encoding="utf-8"
        )

        return text

    except Exception as e:

        print(
            "❌ OCR ERROR:",
            pdf_path,
            page_number,
            repr(e)
        )

        return ""


# ============================================================
# LOCAL BOOK SEARCH
# ============================================================

def get_local_book_metadata():

    result = []

    kitab_dir = Path("kitab")

    if not kitab_dir.exists():

        return result

    for path in kitab_dir.rglob("*"):

        if not path.is_file():
            continue

        suffix = path.suffix.lower()

        if suffix not in [
            ".pdf",
            ".txt",
        ]:
            continue

        category = (
            path.parent.name
            if path.parent.name
            else "FIQH"
        )

        result.append(
            {
                "path": str(path),
                "name": path.stem,
                "category": category,
            }
        )

    return result


# ============================================================
# SUPABASE LOCAL SEARCH
# ============================================================

def search_local(
    question,
    limit=1
):

    """
    LOCAL:
        Maksimum 1 sumber.

    Supabase RPC:
        match_kitab_chunks

    Gemini embedding hanya digunakan untuk
    menghasilkan vector carian.

    Gemini TIDAK memilih rujukan.
    """

    print()
    print("🔎 LOCAL SEARCH")
    print("Question:", question)

    if supabase is None:

        print(
            "⚠️ LOCAL: Supabase tidak tersedia"
        )

        return []

    if gemini_client is None:

        print(
            "⚠️ LOCAL: Gemini client tidak tersedia"
        )

        return []

    try:

        # ----------------------------------------
        # EMBEDDING
        # ----------------------------------------

        embedding_result = (
            gemini_client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=question,
                config=types.EmbedContentConfig(
                    output_dimensionality=3072
                )
            )
        )

        embeddings = (
            embedding_result.embeddings
            if embedding_result
            else []
        )

        if not embeddings:

            print(
                "⚠️ LOCAL: Embedding kosong"
            )

            return []

        query_embedding = (
            embeddings[0].values
        )

        # ----------------------------------------
        # SUPABASE RPC
        # ----------------------------------------

        response = (
            supabase
            .rpc(
                "match_kitab_chunks",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": 0.10,
                    "match_count": 10,
                }
            )
            .execute()
        )

        rows = response.data or []

        print(
            "📦 LOCAL RAW RESULTS:",
            len(rows)
        )

        if not rows:

            print(
                "⚠️ LOCAL: Tiada hasil"
            )

            return []

        # ----------------------------------------
        # Ambil hanya SATU
        # ----------------------------------------

        best = rows[0]

        text = (
            best.get("content")
            or best.get("text")
            or best.get("chunk_text")
            or ""
        )

        text = clean_text(text)

        if not text:

            print(
                "⚠️ LOCAL: Text kosong"
            )

            return []

        book = (
            best.get("book_name")
            or best.get("kitab")
            or best.get("book")
            or "Kitab tempatan"
        )

        category = (
            best.get("category")
            or best.get("kategori")
            or "FIQH"
        )

        page = (
            best.get("page")
            or best.get("page_number")
            or best.get("halaman")
        )

        book_hash = (
            best.get("book_hash")
            or ""
        )

        url = (
            best.get("url")
            or ""
        )

        result = {
            "source": "local",
            "book": book,
            "category": category,
            "page": page,
            "book_hash": book_hash,
            "text": text,
            "url": url,
        }

        print(
            "✅ LOCAL FOUND:",
            book
        )

        print(
            "📄 LOCAL TEXT:",
            len(text),
            "chars"
        )

        return [result][:limit]

    except Exception as e:

        print(
            "❌ LOCAL SEARCH ERROR:",
            repr(e)
        )

        traceback.print_exc()

        return []


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(question):

    """
    TURATH:

    NORMAL:
        10 Syafie

    COMPARISON:
        10 Syafie
        2 Hanafi
        2 Maliki
        2 Hanbali

    Turath service yang menentukan kategori.
    """

    print()
    print("🔎 TURATH SEARCH")
    print("Question:", question)

    comparison = is_madhhab_comparison(
        question
    )

    print(
        "⚖️ COMPARISON:",
        comparison
    )

    try:

        response = requests.post(
            f"{TURATH_SERVICE_URL}/search",
            json={
                "query": question,
                "comparison": comparison,
            },
            timeout=180,
        )

        print(
            "🌐 TURATH HTTP:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "❌ TURATH RESPONSE:",
                response.text[:2000]
            )

            return []

        data = response.json()

        print(
            "📦 TURATH JSON TYPE:",
            type(data).__name__
        )

        if isinstance(data, dict):

            passages = (
                data.get("passages")
                or data.get("results")
                or data.get("data")
                or []
            )

        elif isinstance(data, list):

            passages = data

        else:

            passages = []

        print(
            "📚 TURATH RAW PASSAGES:",
            len(passages)
        )

        results = []

        for passage in passages:

            if not isinstance(
                passage,
                dict
            ):
                continue

            # ------------------------------------
            # Turath SDK fields
            # ------------------------------------

            text = (
                passage.get("text")
                or passage.get("content")
                or passage.get("snippet")
                or passage.get("snip")
                or ""
            )

            text = clean_text(text)

            if not text:
                continue

            book = (
                passage.get("book")
                or passage.get("book_name")
                or passage.get("bookName")
                or passage.get("meta")
                or "Kitab Turath"
            )

            page = (
                passage.get("page")
                or passage.get("page_number")
                or passage.get("pageNumber")
                or passage.get("pg")
            )

            book_id = (
                passage.get("book_id")
                or passage.get("bookId")
            )

            author_id = (
                passage.get("author_id")
                or passage.get("authorId")
            )

            category_id = (
                passage.get("cat_id")
                or passage.get("category_id")
                or passage.get("categoryId")
            )

            url = (
                passage.get("url")
                or passage.get("link")
                or ""
            )

            # ------------------------------------
            # Jika service tidak beri URL,
            # bina URL berdasarkan book_id
            # ------------------------------------

            if (
                not url
                and book_id
            ):

                url = (
                    "https://turath.io/book/"
                    + str(book_id)
                )

            results.append(
                {
                    "source": "turath",
                    "book": book,
                    "category": (
                        passage.get("category")
                        or passage.get("category_name")
                        or "Turath"
                    ),
                    "category_id": category_id,
                    "book_id": book_id,
                    "author_id": author_id,
                    "page": page,
                    "text": text,
                    "url": url,
                }
            )

        print(
            "✅ TURATH VALID RESULTS:",
            len(results)
        )

        # ----------------------------------------
        # LIMIT
        # ----------------------------------------

        if comparison:

            # Service sepatutnya sudah
            # memilih 10+2+2+2.
            #
            # Jangan potong kepada 10 di sini,
            # kerana comparison memang 16.

            results = results[:16]

        else:

            results = results[:10]

        print(
            "📚 TURATH FINAL:",
            len(results)
        )

        return results

    except Exception as e:

        print(
            "❌ TURATH SEARCH ERROR:",
            repr(e)
        )

        traceback.print_exc()

        return []


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_context(
    sources
):

    if not sources:

        return ""

    blocks = []

    for i, source in enumerate(
        sources,
        1
    ):

        source_type = (
            source.get("source")
            or ""
        )

        book = (
            source.get("book")
            or "Kitab"
        )

        category = (
            source.get("category")
            or ""
        )

        page = (
            source.get("page")
            or ""
        )

        text = (
            source.get("text")
            or ""
        )

        if not text:
            continue

        block = f"""
[SUMBER {i}]
Jenis: {source_type}
Kitab: {book}
Kategori: {category}
Halaman: {page}

Petikan:
{text}
"""

        blocks.append(
            block.strip()
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# REFERENCES
# ============================================================

def build_references(
    sources
):

    if not sources:

        return (
            "📚 **Rujukan:**\n"
            "• Tiada rujukan ditemui."
        )

    lines = [
        "📚 **Rujukan:**"
    ]

    for i, source in enumerate(
        sources,
        1
    ):

        source_type = (
            source.get("source")
            or ""
        )

        book = (
            source.get("book")
            or "Kitab"
        )

        page = (
            source.get("page")
            or ""
        )

        url = (
            source.get("url")
            or ""
        )

        if source_type == "turath":

            label = "Turath"

        else:

            label = "Kitab tempatan"

        title = book

        if page:

            title += (
                f" — halaman {page}"
            )

        if url:

            lines.append(
                f"• {i}. {label}: "
                f"[{title}]({url})"
            )

        else:

            lines.append(
                f"• {i}. {label}: "
                f"{title}"
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

    """
    Gemini hanya menghuraikan.

    Gemini TIDAK:
        - mencari kitab
        - memilih kitab
        - mencari URL
        - mencipta rujukan
    """

    if not context:

        print(
            "⚠️ GEMINI: Context kosong"
        )

        return ""

    if gemini_client is None:

        print(
            "⚠️ GEMINI CLIENT TIDAK ADA"
        )

        return ""

    prompt = f"""
Anda ialah pembantu ilmu Islam untuk TanyaFiqihBot.

TUGAS ANDA:

Huraikan jawapan kepada soalan berdasarkan
PETIKAN SUMBER yang diberikan sahaja.

JANGAN mencari sumber lain.

JANGAN mencipta:
- kitab
- pengarang
- halaman
- URL
- rujukan
- dalil yang tidak terdapat dalam sumber

Jika sumber tidak mencukupi,
nyatakan dengan jujur bahawa sumber yang diberikan
tidak mencukupi untuk membuat kesimpulan yang pasti.

Soalan pengguna:

{question}

==================================================
SUMBER YANG DITEMUI
==================================================

{context}

==================================================

ARAHAN JAWAPAN:

1. Jawab dalam Bahasa Melayu.

2. Jika terdapat istilah Arab,
   terangkan maksudnya dalam Bahasa Melayu.

3. Jika terdapat perbezaan mazhab,
   jelaskan perbezaan tersebut dengan jelas.

4. Jangan mereka-reka.

5. Jangan senaraikan rujukan.
   Sistem akan menyediakan rujukan secara automatik.

6. Jangan letakkan URL.

7. Fokus kepada HURAIAN sahaja.

8. Jika sumber menunjukkan pandangan tertentu,
   nyatakan bahawa pandangan tersebut berdasarkan
   sumber yang diberikan.

Berikan jawapan yang mudah difahami.
"""

    try:

        print()
        print(
            "🤖 GEMINI GENERATING..."
        )

        response = (
            gemini_client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=2500,
                )
            )
        )

        answer = (
            response.text
            if response
            else ""
        )

        answer = clean_text(
            answer
        )

        print(
            "✅ GEMINI ANSWER:",
            len(answer),
            "chars"
        )

        return answer

    except Exception as e:

        print(
            "❌ GEMINI ERROR:",
            repr(e)
        )

        if (
            "429" in str(e)
            or
            "RESOURCE_EXHAUSTED"
            in str(e)
        ):

            print(
                "⚠️ GEMINI QUOTA HABIS"
            )

        traceback.print_exc()

        return ""


# ============================================================
# MAIN ANSWER ENGINE
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
    print("=" * 70)
    print("❓ SOALAN:")
    print(question)
    print("=" * 70)

    cache_key = question.lower()

    # ========================================================
    # CACHE
    # ========================================================

    if cache_key in ANSWER_CACHE:

        print(
            "⚡ ANSWER CACHE HIT"
        )

        cached = (
            ANSWER_CACHE[
                cache_key
            ]
        )

        return (
            cached["answer"]
            + "\n\n"
            + cached["references"]
        )

    # ========================================================
    # LOCAL
    # ========================================================

    local_results = search_local(
        question,
        limit=1
    )

    # ========================================================
    # TURATH
    # ========================================================

    turath_results = search_turath(
        question
    )

    # ========================================================
    # COMBINE
    # ========================================================

    local_results = (
        local_results[:1]
    )

    comparison = is_madhhab_comparison(
        question
    )

    if comparison:

        turath_results = (
            turath_results[:16]
        )

    else:

        turath_results = (
            turath_results[:10]
        )

    all_sources = (
        local_results
        +
        turath_results
    )

    print()
    print("=" * 70)
    print("📊 SOURCE SUMMARY")
    print(
        "LOCAL:",
        len(local_results)
    )
    print(
        "TURATH:",
        len(turath_results)
    )
    print(
        "TOTAL:",
        len(all_sources)
    )
    print(
        "COMPARISON:",
        comparison
    )
    print("=" * 70)

    # ========================================================
    # NO SOURCE
    # ========================================================

    if not all_sources:

        print(
            "⚠️ TIADA SUMBER"
        )

        return (
            "⚠️ **Tiada rujukan ditemui.**\n\n"
            "Saya tidak menemui kandungan kitab yang "
            "mencukupi untuk menjawab soalan ini.\n\n"
            "📚 **Rujukan:**\n"
            "• Tiada rujukan ditemui."
        )

    # ========================================================
    # REFERENCES FIRST
    # ========================================================

    references = build_references(
        all_sources
    )

    # ========================================================
    # CONTEXT
    # ========================================================

    context = build_context(
        all_sources
    )

    # ========================================================
    # GEMINI
    # ========================================================

    explanation = generate_answer(
        question,
        context
    )

    # ========================================================
    # GEMINI FAIL
    # ========================================================

    if not explanation:

        explanation = (
            "⚠️ **Huraian AI tidak dapat "
            "dihasilkan buat masa ini.**\n\n"
            "Namun, rujukan kitab yang berkaitan "
            "telah ditemui dan dipaparkan di bawah."
        )

    # ========================================================
    # FINAL
    # ========================================================

    final_answer = (
        explanation
        + "\n\n"
        + references
    )

    # ========================================================
    # CACHE
    # ========================================================

    ANSWER_CACHE[
        cache_key
    ] = {
        "answer": explanation,
        "references": references,
    }

    if (
        len(ANSWER_CACHE)
        > ANSWER_CACHE_MAX
    ):

        oldest = next(
            iter(
                ANSWER_CACHE
            )
        )

        del ANSWER_CACHE[
            oldest
        ]

    return final_answer


# ============================================================
# TELEGRAM
# ============================================================

telegram_application = None


async def telegram_startup():

    global telegram_application

    if not TELEGRAM_TOKEN:

        print(
            "❌ TELEGRAM_TOKEN MISSING"
        )

        return

    try:

        print()
        print(
            "📡 INITIALIZING TELEGRAM..."
        )

        telegram_application = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        # --------------------------------------------
        # HANDLERS
        # --------------------------------------------

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

        # --------------------------------------------
        # INITIALIZE
        # --------------------------------------------

        await telegram_application.initialize()

        # --------------------------------------------
        # CHECK BOT
        # --------------------------------------------

        me = (
            await telegram_application.bot.get_me()
        )

        print(
            "✅ TELEGRAM CONNECTED:",
            f"@{me.username}"
        )

        # --------------------------------------------
        # START APPLICATION
        # --------------------------------------------

        await telegram_application.start()

        # --------------------------------------------
        # START POLLING
        # --------------------------------------------

        await (
            telegram_application
            .updater
            .start_polling(
                drop_pending_updates=False
            )
        )

        print(
            "✅ Telegram polling started"
        )

        print(
            "🤖 BOT:",
            f"@{me.username}"
        )

        # --------------------------------------------
        # KEEP ALIVE
        # --------------------------------------------

        while True:

            await asyncio.sleep(
                3600
            )

    except Exception as e:

        print(
            "❌ TELEGRAM START ERROR:",
            repr(e)
        )

        traceback.print_exc()


def start_telegram():

    print(
        "🚀 Telegram thread starting..."
    )

    try:

        asyncio.run(
            telegram_startup()
        )

    except Exception as e:

        print(
            "❌ TELEGRAM THREAD ERROR:",
            repr(e)
        )

        traceback.print_exc()


# ============================================================
# TELEGRAM /start
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "🤖 **TanyaFiqihBot**\n\n"
        "Assalamualaikum.\n\n"
        "Saya membantu mencari rujukan "
        "daripada kitab-kitab Islam.\n\n"
        "📚 Fiqh\n"
        "📖 Tauhid\n"
        "📜 Hadis\n"
        "📕 Tafsir\n"
        "🕌 Sirah\n"
        "🌿 Akhlak\n"
        "📚 Usul Fiqh\n\n"
        "Taip soalan anda."
    )

    await update.message.reply_text(
        message,
        parse_mode="Markdown"
    )


# ============================================================
# TELEGRAM /help
# ============================================================

async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "Taip sahaja soalan agama anda.\n\n"
        "Contoh:\n"
        "• Apakah hukum membaca qunut Subuh?\n"
        "• Bagaimana cara solat jamak?\n"
        "• Apa perbezaan pendapat Syafie dan Hanafi?"
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

    print()
    print(
        "📨 TELEGRAM QUESTION:",
        question
    )

    # --------------------------------------------
    # Processing
    # --------------------------------------------

    processing_message = (
        await update.message.reply_text(
            "🔎 Sedang mencari rujukan kitab..."
        )
    )

    try:

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        # Telegram limit
        # Pecahkan jika terlalu panjang

        max_length = 3900

        if len(answer) <= max_length:

            await processing_message.edit_text(
                answer,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

        else:

            await processing_message.delete()

            chunks = [
                answer[i:i + max_length]
                for i in range(
                    0,
                    len(answer),
                    max_length
                )
            ]

            for chunk in chunks:

                await update.message.reply_text(
                    chunk,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )

    except Exception as e:

        print(
            "❌ TELEGRAM MESSAGE ERROR:",
            repr(e)
        )

        traceback.print_exc()

        try:

            await processing_message.edit_text(
                "⚠️ Berlaku ralat ketika memproses soalan."
            )

        except Exception:
            pass


# ============================================================
# KITAB SYNC
# ============================================================

def startup_sync():

    """
    Fungsi sync kitab.

    Nota:
    Struktur ini dikekalkan supaya sistem lama
    yang menggunakan fungsi sync_books()
    boleh terus digunakan.
    """

    try:

        print()
        print(
            "🔄 SYNC KITAB"
        )
        print("=" * 60)

        # ------------------------------------------------
        # Jika fungsi sync_books wujud dalam versi
        # app.py lama, panggil.
        # ------------------------------------------------

        sync_function = globals().get(
            "sync_books"
        )

        if callable(sync_function):

            sync_function()

        else:

            print(
                "ℹ️ Tiada sync_books() aktif."
            )

            print(
                "ℹ️ Local search akan menggunakan "
                "data Supabase sedia ada."
            )

    except Exception as e:

        print(
            "❌ STARTUP SYNC ERROR:",
            repr(e)
        )

        traceback.print_exc()


# ============================================================
# BACKGROUND SERVICES
# ============================================================

def start_background_services():

    print()
    print(
        "=" * 60
    )
    print(
        "🚀 STARTING BACKGROUND SERVICES"
    )
    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # SYNC
    # --------------------------------------------------------

    sync_thread = threading.Thread(
        target=startup_sync,
        daemon=True,
        name="kitab-sync"
    )

    sync_thread.start()

    print(
        "✅ Kitab sync thread started"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True,
        name="telegram-bot"
    )

    telegram_thread.start()

    print(
        "🚀 Telegram thread started"
    )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify(
        {
            "status": "ok",
            "bot": "TanyaFiqihBot",
            "telegram": bool(
                TELEGRAM_TOKEN
            ),
            "supabase": bool(
                supabase
            ),
            "gemini": bool(
                gemini_client
            ),
            "turath": TURATH_SERVICE_URL,
        }
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify(
        {
            "status": "healthy",
            "telegram_token": bool(
                TELEGRAM_TOKEN
            ),
            "supabase": bool(
                supabase
            ),
            "gemini": bool(
                gemini_client
            ),
            "turath_service": TURATH_SERVICE_URL,
        }
    )


# ============================================================
# WEB ASK
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
                data.get("question")
                or ""
            )

        else:

            question = (
                request.args.get(
                    "question",
                    ""
                )
            )

        question = question.strip()

        if not question:

            return jsonify(
                {
                    "error":
                    "Sila masukkan soalan."
                }
            ), 400

        answer = answer_question(
            question
        )

        return jsonify(
            {
                "question": question,
                "answer": answer,
            }
        )

    except Exception as e:

        print(
            "❌ /ask ERROR:",
            repr(e)
        )

        traceback.print_exc()

        return jsonify(
            {
                "error": str(e)
            }
        ), 500


# ============================================================
# START SERVICES WHEN GUNICORN IMPORTS app.py
# ============================================================

start_background_services()


# ============================================================
# IMPORTANT
# ============================================================
#
# JANGAN letakkan:
#
# if __name__ == "__main__":
#     app.run(...)
#
# Render menggunakan Gunicorn:
#
# gunicorn ... app:app
#
# Jadi background service perlu dimulakan
# ketika module ini diimport.
#
# ============================================================
