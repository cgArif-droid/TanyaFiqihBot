from flask import Flask, jsonify
import os
import re
import hashlib
import threading
import asyncio
from pathlib import Path

import requests
from supabase import create_client, Client

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import pytesseract
from pdf2image import convert_from_path

from google import genai
from google.genai import types


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY",
    ""
)

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
)

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    ""
)

DATA_DIR = Path(
    os.getenv(
        "DATA_DIR",
        "/var/data"
    )
)

KITAB_DIR = Path(
    "/app/kitab"
)

OCR_WORKERS = int(
    os.getenv(
        "OCR_WORKERS",
        "1"
    )
)

OCR_DPI = int(
    os.getenv(
        "OCR_DPI",
        "200"
    )
)

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.8-flash"
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001"
)

EMBEDDING_DIM = 3072

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
)

TURATH_SEARCH_K = int(
    os.getenv(
        "TURATH_SEARCH_K",
        "3"
    )
)

OCR_CACHE_DIR = (
    DATA_DIR
    / "extracted_text"
    / "pages"
)


# ============================================================
# CACHE
# ============================================================

ANSWER_CACHE = {}

MAX_CACHE_SIZE = 500


# ============================================================
# CLIENT
# ============================================================

gemini_client = None

supabase: Client = None


if GOOGLE_API_KEY:

    gemini_client = genai.Client(
        api_key=GOOGLE_API_KEY
    )


if SUPABASE_URL and SUPABASE_KEY:

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


# ============================================================
# CATEGORY
# ============================================================

CATEGORIES = [
    "FIQH",
    "TAUHID",
    "HADIS",
    "TAFSIR",
    "SIRAH",
    "AKHLAK",
    "USUL FIQH"
]


def get_category_from_path(path: Path):

    try:

        relative = path.relative_to(
            KITAB_DIR
        )

        parts = relative.parts

        if len(parts) >= 2:

            category = parts[0].upper()

            if category in CATEGORIES:

                return category

    except Exception:
        pass

    return "FIQH"


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "status": "running"
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "gemini": bool(gemini_client),
        "supabase": bool(supabase),
        "turath": TURATH_SERVICE_URL,
        "llm_model": LLM_MODEL,
        "embedding_model": EMBEDDING_MODEL
    })


# ============================================================
# HASH FILE
# ============================================================

def file_hash(path: Path):

    h = hashlib.sha256()

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

            h.update(chunk)

    return h.hexdigest()[:16]


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(text):

    if not text:
        return ""

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


# ============================================================
# QUESTION CACHE
# ============================================================

def normalize_question(question):

    question = (
        question
        .lower()
        .strip()
    )

    question = re.sub(
        r"\s+",
        " ",
        question
    )

    question = question.rstrip(
        "?!.,،؟"
    )

    return question


def get_cached_answer(question):

    key = normalize_question(
        question
    )

    return ANSWER_CACHE.get(
        key
    )


def save_cached_answer(
    question,
    answer
):

    key = normalize_question(
        question
    )

    if not key:
        return

    if len(
        ANSWER_CACHE
    ) >= MAX_CACHE_SIZE:

        oldest_key = next(
            iter(ANSWER_CACHE)
        )

        del ANSWER_CACHE[
            oldest_key
        ]

    ANSWER_CACHE[key] = answer


# ============================================================
# OCR PAGE
# ============================================================

def ocr_page(
    pdf_path: Path,
    page_number: int,
    book_hash: str
):

    cache_dir = (
        OCR_CACHE_DIR
        / book_hash
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    cache_file = (
        cache_dir
        / f"{page_number}.txt"
    )

    if cache_file.exists():

        try:

            return cache_file.read_text(
                encoding="utf-8"
            )

        except Exception:
            pass

    try:

        images = convert_from_path(
            str(pdf_path),
            dpi=OCR_DPI,
            first_page=page_number,
            last_page=page_number
        )

        if not images:

            return ""

        image = images[0]

        text = pytesseract.image_to_string(
            image,
            lang="ara+msa+eng"
        )

        text = clean_text(
            text
        )

        try:

            cache_file.write_text(
                text,
                encoding="utf-8"
            )

        except Exception as error:

            print(
                "⚠️ OCR CACHE ERROR:",
                error
            )

        return text

    except Exception as error:

        print(
            f"❌ OCR PAGE ERROR {page_number}:",
            error
        )

        return ""


# ============================================================
# EXTRACT PDF
# ============================================================

def extract_pdf_text(
    pdf_path: Path,
    book_hash: str
):

    try:

        from pdf2image import (
            pdfinfo_from_path
        )

        info = pdfinfo_from_path(
            str(pdf_path)
        )

        total_pages = int(
            info["Pages"]
        )

    except Exception as error:

        print(
            "❌ PDF INFO ERROR:",
            error
        )

        return []

    print(
        f"📄 JUMLAH HALAMAN: {total_pages}"
    )

    pages = []

    for page_number in range(
        1,
        total_pages + 1
    ):

        print(
            f"🔎 OCR PAGE "
            f"{page_number}/{total_pages}"
        )

        text = ocr_page(
            pdf_path,
            page_number,
            book_hash
        )

        if text:

            pages.append({
                "page": page_number,
                "text": text
            })

    return pages


# ============================================================
# EXTRACT TEXT FILE
# ============================================================

def extract_text_file(
    path: Path
):

    try:

        text = path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

        text = clean_text(
            text
        )

        if not text:

            return []

        return [{
            "page": 1,
            "text": text
        }]

    except Exception as error:

        print(
            "❌ TEXT FILE ERROR:",
            error
        )

        return []


# ============================================================
# EXTRACT BOOK
# ============================================================

def extract_book(
    path: Path,
    book_hash: str
):

    suffix = path.suffix.lower()

    if suffix == ".pdf":

        return extract_pdf_text(
            path,
            book_hash
        )

    if suffix in [
        ".txt",
        ".md"
    ]:

        return extract_text_file(
            path
        )

    return []


# ============================================================
# CHUNK TEXT
# ============================================================

def chunk_text(
    text,
    chunk_size=1500,
    overlap=250
):

    text = clean_text(
        text
    )

    if not text:

        return []

    chunks = []

    start = 0
    length = len(text)

    while start < length:

        end = min(
            start + chunk_size,
            length
        )

        chunk = text[
            start:end
        ].strip()

        if chunk:

            chunks.append(
                chunk
            )

        if end >= length:

            break

        start = max(
            end - overlap,
            start + 1
        )

    return chunks


# ============================================================
# EMBEDDING
# ============================================================

def create_embedding(
    text
):

    if not gemini_client:

        raise RuntimeError(
            "GOOGLE_API_KEY belum ditetapkan"
        )

    result = (
        gemini_client
        .models
        .embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=EMBEDDING_DIM
            )
        )
    )

    if not result.embeddings:

        raise RuntimeError(
            "Embedding kosong"
        )

    return result.embeddings[0].values


# ============================================================
# DELETE SUPABASE BOOK
# ============================================================

def delete_book_from_supabase(
    book_hash
):

    if not supabase:

        return

    try:

        (
            supabase
            .table("kitab_chunks")
            .delete()
            .eq(
                "book_hash",
                book_hash
            )
            .execute()
        )

        print(
            "🗑️ Supabase data deleted:",
            book_hash
        )

    except Exception as error:

        print(
            "❌ DELETE SUPABASE ERROR:",
            error
        )


# ============================================================
# SAVE BOOK
# ============================================================

def save_book_record(
    book_hash,
    kitab_name,
    category
):

    if not supabase:

        return

    try:

        data = {
            "book_hash": book_hash,
            "kitab_name": kitab_name,
            "category": category
        }

        (
            supabase
            .table("books")
            .upsert(
                data,
                on_conflict="book_hash"
            )
            .execute()
        )

    except Exception as error:

        print(
            "⚠️ BOOK UPSERT ERROR:",
            error
        )


# ============================================================
# SAVE CHUNK
# ============================================================

def save_chunk(
    book_hash,
    kitab_name,
    category,
    page,
    chunk_index,
    content,
    embedding
):

    if not supabase:

        return False

    data = {
        "book_hash": book_hash,
        "kitab_name": kitab_name,
        "category": category,
        "page": page,
        "chunk_index": chunk_index,
        "content": content,
        "embedding": embedding
    }

    try:

        (
            supabase
            .table("kitab_chunks")
            .insert(data)
            .execute()
        )

        return True

    except Exception as error:

        print(
            "❌ SUPABASE INSERT ERROR:",
            error
        )

        return False


# ============================================================
# PROCESS BOOK
# ============================================================

def process_book(
    path: Path
):

    kitab_name = path.stem

    category = (
        get_category_from_path(
            path
        )
    )

    print("")
    print("=" * 60)

    print(
        f"📚 PROCESS: {path.name}"
    )

    print(
        f"📂 CATEGORY: {category}"
    )

    book_hash = file_hash(
        path
    )

    print(
        f"🔑 HASH: {book_hash}"
    )

    delete_book_from_supabase(
        book_hash
    )

    pages = extract_book(
        path,
        book_hash
    )

    chunks = []

    for page_data in pages:

        page = page_data[
            "page"
        ]

        text = page_data[
            "text"
        ]

        page_chunks = chunk_text(
            text
        )

        for chunk in page_chunks:

            chunks.append({
                "page": page,
                "content": chunk
            })

    print(
        f"📦 Jumlah chunks: "
        f"{len(chunks)}"
    )

    save_book_record(
        book_hash,
        kitab_name,
        category
    )

    total = len(chunks)

    for index, item in enumerate(
        chunks,
        start=1
    ):

        print(
            f"🧠 EMBEDDING "
            f"{index}-{total}/{total}"
        )

        try:

            embedding = create_embedding(
                item["content"]
            )

        except Exception as error:

            print(
                "❌ EMBEDDING ERROR:",
                error
            )

            continue

        print(
            f"💾 Supabase "
            f"{index}-{total}"
        )

        success = save_chunk(
            book_hash=book_hash,
            kitab_name=kitab_name,
            category=category,
            page=item["page"],
            chunk_index=index,
            content=item["content"],
            embedding=embedding
        )

        if not success:

            print(
                f"⚠️ Chunk {index} "
                f"gagal disimpan"
            )

    print(
        f"✅ READY: {kitab_name}"
    )


# ============================================================
# SYNC ALL BOOKS
# ============================================================

def sync_books():

    print("")
    print(
        "🔄 SYNC KITAB"
    )

    print(
        "=" * 60
    )

    print(
        f"📂 Searching: "
        f"{KITAB_DIR}"
    )

    if not KITAB_DIR.exists():

        print(
            "⚠️ Folder kitab tidak wujud"
        )

        return

    files = []

    for path in KITAB_DIR.rglob("*"):

        if not path.is_file():

            continue

        if path.suffix.lower() in [
            ".pdf",
            ".txt",
            ".md"
        ]:

            files.append(
                path
            )

    print(
        f"📚 Jumlah kitab: "
        f"{len(files)}"
    )

    for path in files:

        try:

            print(
                f"🆕 PROCESS: "
                f"{path.name}"
            )

            process_book(
                path
            )

        except Exception as error:

            print(
                f"❌ PROCESS ERROR "
                f"{path.name}:",
                error
            )

    print(
        "🏁 SYNC SELESAI"
    )


# ============================================================
# LOCAL VECTOR SEARCH
# ============================================================

def search_local(
    query,
    limit=3
):

    if not supabase:

        return []

    try:

        embedding = create_embedding(
            query
        )

        result = (
            supabase
            .rpc(
                "match_kitab_chunks",
                {
                    "query_embedding": embedding,
                    "match_threshold": 0.25,
                    "match_count": limit
                }
            )
            .execute()
        )

        return (
            result.data
            or []
        )

    except Exception as error:

        print(
            "❌ LOCAL SEARCH ERROR:",
            error
        )

        return []


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(
    query,
    limit=3
):

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/search",
            params={
                "q": query,
                "limit": limit
            },
            timeout=60
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "ok"
        ):

            return []

        return (
            data.get(
                "passages",
                []
            )
        )

    except Exception as error:

        print(
            "❌ TURATH SEARCH ERROR:",
            error
        )

        return []


# ============================================================
# FORMAT LOCAL SOURCES
# ============================================================

def format_local_sources(
    rows
):

    blocks = []

    for row in rows:

        kitab = (
            row.get(
                "kitab_name"
            )
            or row.get(
                "book_name"
            )
            or "Kitab Tempatan"
        )

        category = (
            row.get(
                "category"
            )
            or "FIQH"
        )

        page = row.get(
            "page",
            "-"
        )

        content = (
            row.get(
                "content"
            )
            or ""
        )

        blocks.append(
            f"""SUMBER TEMPATAN
Kitab: {kitab}
Kategori: {category}
Halaman: {page}
Isi:
{content}"""
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# FORMAT TURATH SOURCES
# ============================================================

def format_turath_sources(
    passages
):

    blocks = []

    for item in passages:

        kitab = (
            item.get(
                "kitab_name"
            )
            or "Turath"
        )

        author = (
            item.get(
                "author"
            )
            or "Tidak diketahui"
        )

        page = item.get(
            "page",
            "-"
        )

        content = (
            item.get(
                "content"
            )
            or ""
        )

        blocks.append(
            f"""SUMBER TURATH
Kitab: {kitab}
Pengarang: {author}
Halaman: {page}
Isi:
{content}"""
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# GENERATE GEMINI ANSWER
# ============================================================

def generate_answer(
    question,
    local_rows,
    turath_rows
):

    # ========================================================
    # CACHE
    # ========================================================

    cached_answer = get_cached_answer(
        question
    )

    if cached_answer:

        print(
            "⚡ CACHE HIT — "
            "Gemini tidak dipanggil"
        )

        return cached_answer

    print(
        "🆕 CACHE MISS — "
        "perlu 1x Gemini"
    )


    # ========================================================
    # GEMINI CHECK
    # ========================================================

    if not gemini_client:

        return (
            "Maaf, sistem Gemini belum "
            "dikonfigurasikan."
        )


    # ========================================================
    # FORMAT SOURCES
    # ========================================================

    local_text = (
        format_local_sources(
            local_rows
        )
    )

    turath_text = (
        format_turath_sources(
            turath_rows
        )
    )


    # ========================================================
    # SOURCES LIST
    # ========================================================

    sources = []

    for row in local_rows:

        sources.append(
            f"• "
            f"{row.get('kitab_name', 'Kitab Tempatan')} "
            f"[{row.get('category', 'FIQH')}]"
        )

    for item in turath_rows:

        author = item.get(
            "author",
            ""
        )

        page = item.get(
            "page",
            "-"
        )

        sources.append(
            f"• Turath — "
            f"{author}, halaman {page}"
        )


    # ========================================================
    # CONTEXT
    # ========================================================

    context_parts = []

    if local_text:

        context_parts.append(
            local_text
        )

    if turath_text:

        context_parts.append(
            turath_text
        )

    context = "\n\n".join(
        context_parts
    )


    # ========================================================
    # LIMIT CONTEXT
    # ========================================================

    MAX_CONTEXT_CHARS = 18000

    if len(context) > MAX_CONTEXT_CHARS:

        print(
            f"✂️ Context dipotong: "
            f"{len(context)} → "
            f"{MAX_CONTEXT_CHARS}"
        )

        context = context[
            :MAX_CONTEXT_CHARS
        ]


    # ========================================================
    # PROMPT
    # ========================================================

    prompt = f"""
Anda ialah TanyaFiqihBot,
pembantu ilmu Islam berbahasa Melayu.

Jawab soalan pengguna berdasarkan sumber
yang diberikan.

SOALAN PENGGUNA:
{question}

SUMBER RUJUKAN:
{context}

ARAHAN:
1. Jawab dalam Bahasa Melayu.
2. Utamakan sumber yang diberikan.
3. Jangan mereka-reka fakta atau dalil.
4. Jika sumber tidak mencukupi, nyatakan dengan jujur.
5. Jika terdapat khilaf, nyatakan secara ringkas.
6. Untuk persoalan fiqh, nyatakan hukum dan
   syarat yang berkaitan jika terdapat dalam sumber.
7. Jangan mendakwa ijmak tanpa sumber.
8. Jawab dengan jelas dan mudah difahami.
9. Jangan ulang soalan pengguna.
10. Jangan terlalu panjang.
11. Jika terdapat dalil atau teks Arab yang relevan
    dalam sumber, boleh sertakan secara ringkas.

Jawapan:
"""


    # ========================================================
    # GEMINI — SATU REQUEST SAHAJA
    # ========================================================

    try:

        print(
            "🤖 GEMINI GENERATE: "
            "1 REQUEST"
        )

        response = (
            gemini_client
            .models
            .generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=1000,
                    candidate_count=1
                )
            )
        )


        answer = (
            response.text
            if response
            and response.text
            else ""
        )


        if not answer:

            return (
                "Maaf, Gemini tidak "
                "menghasilkan jawapan."
            )


        # ====================================================
        # RUJUKAN
        # ====================================================

        if sources:

            answer += (
                "\n\n📚 Rujukan:\n"
                +
                "\n".join(
                    sources
                )
            )


        # ====================================================
        # SAVE CACHE
        # ====================================================

        save_cached_answer(
            question,
            answer
        )

        print(
            "💾 Jawapan disimpan dalam cache"
        )


        return answer


    except Exception as error:

        print(
            "❌ LLM error:",
            error
        )

        error_text = str(
            error
        )

        if (
            "429" in error_text
            or
            "RESOURCE_EXHAUSTED"
            in error_text
        ):

            return (
                "⚠️ Kuota Gemini telah habis "
                "buat sementara waktu.\n\n"
                "Sila cuba semula selepas "
                "kuota reset."
            )

        return (
            "❌ Berlaku masalah ketika "
            "menjana jawapan."
        )


# ============================================================
# ANSWER QUESTION
# ============================================================

def answer_question(
    question
):

    print("")

    print(
        "============================================"
    )

    print(
        "❓ SOALAN:",
        question
    )


    # ========================================================
    # CHECK CACHE FIRST
    # ========================================================

    cached_answer = get_cached_answer(
        question
    )

    if cached_answer:

        print(
            "⚡ CACHE HIT — "
            "tiada Gemini / Supabase / Turath"
        )

        return cached_answer


    # ========================================================
    # LOCAL
    # ========================================================

    local_rows = search_local(
        question,
        limit=3
    )

    print(
        f"📚 LOCAL: "
        f"{len(local_rows)}"
    )


    # ========================================================
    # TURATH
    # ========================================================

    turath_rows = search_turath(
        question,
        limit=3
    )

    print(
        f"📖 TURATH: "
        f"{len(turath_rows)}"
    )


    # ========================================================
    # TOTAL
    # ========================================================

    print(
        f"📦 TOTAL: "
        f"{len(local_rows) + len(turath_rows)}"
    )


    # ========================================================
    # NO SOURCES
    # ========================================================

    if (
        not local_rows
        and
        not turath_rows
    ):

        return (
            "Maaf, saya tidak menemui "
            "sumber yang berkaitan "
            "dengan soalan tersebut."
        )


    # ========================================================
    # GEMINI
    # ========================================================

    return generate_answer(
        question,
        local_rows,
        turath_rows
    )


# ============================================================
# TELEGRAM /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    await update.message.reply_text(
        """🤖 TanyaFiqihBot

Assalamualaikum.

Tanya soalan berkaitan:

• Fiqh
• Tauhid
• Hadis
• Tafsir
• Sirah
• Akhlak
• Usul Fiqh

Contoh:

Apakah rukun mandi wajib?
"""
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

async def handle_message(
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
            "typing"
        )

    except Exception:

        pass


    try:

        loop = asyncio.get_running_loop()

        answer = (
            await loop.run_in_executor(
                None,
                answer_question,
                question
            )
        )

        await update.message.reply_text(
            answer
        )

    except Exception as error:

        print(
            "❌ TELEGRAM HANDLE ERROR:",
            error
        )

        try:

            await update.message.reply_text(
                "❌ Maaf, berlaku masalah "
                "ketika memproses soalan."
            )

        except Exception:

            pass


# ============================================================
# TELEGRAM MAIN
# ============================================================

async def telegram_main():

    if not TELEGRAM_TOKEN:

        print(
            "❌ TELEGRAM_TOKEN belum ditetapkan"
        )

        return


    application = (
        Application
        .builder()
        .token(
            TELEGRAM_TOKEN
        )
        .build()
    )


    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )


    application.add_handler(
        MessageHandler(
            filters.TEXT
            &
            ~filters.COMMAND,
            handle_message
        )
    )


    await application.initialize()

    await application.start()


    # ========================================================
    # PENTING:
    # TIADA stop_signals
    # ========================================================

    await (
        application
        .updater
        .start_polling()
    )


    print(
        "✅ Telegram polling started"
    )


    try:

        while True:

            await asyncio.sleep(
                3600
            )

    except asyncio.CancelledError:

        pass

    finally:

        try:

            await (
                application
                .updater
                .stop()
            )

        except Exception:
            pass

        try:

            await application.stop()

        except Exception:
            pass

        try:

            await application.shutdown()

        except Exception:
            pass


# ============================================================
# TELEGRAM THREAD
# ============================================================

def run_telegram():

    try:

        asyncio.run(
            telegram_main()
        )

    except Exception as error:

        print(
            "❌ TELEGRAM ERROR:",
            repr(error)
        )


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("")
    print(
        "🚀 STARTING TanyaFiqihBot"
    )

    print(
        f"🧠 LLM: {LLM_MODEL}"
    )

    print(
        f"🔢 EMBEDDING: "
        f"{EMBEDDING_MODEL}"
    )

    print(
        f"📚 KITAB DIR: "
        f"{KITAB_DIR}"
    )


    # ========================================================
    # DATA DIRECTORY
    # ========================================================

    try:

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        OCR_CACHE_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

    except Exception as error:

        print(
            "⚠️ DATA DIR ERROR:",
            error
        )


    # ========================================================
    # SYNC KITAB
    # ========================================================

    try:

        sync_books()

    except Exception as error:

        print(
            "❌ SYNC ERROR:",
            error
        )


    # ========================================================
    # TELEGRAM
    # ========================================================

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True
    )

    telegram_thread.start()


# ============================================================
# START
# ============================================================

startup()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )
    )
