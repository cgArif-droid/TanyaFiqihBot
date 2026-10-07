from flask import Flask, request, jsonify
import os
import re
import json
import hashlib
import threading
import asyncio
import time
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
# CONFIG
# ============================================================

app = Flask(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

DATA_DIR = Path(os.getenv("DATA_DIR", "/var/data"))
KITAB_DIR = Path("/app/kitab")

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "1"))
OCR_DPI = int(os.getenv("OCR_DPI", "200"))

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
    os.getenv("TURATH_SEARCH_K", "5")
)

OCR_CACHE_DIR = (
    DATA_DIR /
    "extracted_text" /
    "pages"
)

MANIFEST_FILE = DATA_DIR / "manifest.json"


# ============================================================
# CLIENTS
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
# BASIC
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "status": "running"
    })


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "gemini": bool(gemini_client),
        "supabase": bool(supabase),
        "turath": TURATH_SERVICE_URL
    })


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
        relative = path.relative_to(KITAB_DIR)
        parts = relative.parts

        if len(parts) >= 2:
            category = parts[0].upper()

            if category in CATEGORIES:
                return category

    except Exception:
        pass

    return "FIQH"


# ============================================================
# HASH
# ============================================================

def file_hash(path: Path):

    h = hashlib.sha256()

    with open(path, "rb") as f:

        while True:

            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()[:16]


# ============================================================
# TEXT CLEAN
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = text.replace("\x00", " ")

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# OCR
# ============================================================

def ocr_page(
    pdf_path: Path,
    page_number: int,
    book_hash: str
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

    if cache_file.exists():

        try:
            return cache_file.read_text(
                encoding="utf-8"
            )

        except Exception:
            pass

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

    text = clean_text(text)

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


# ============================================================
# READ PDF
# ============================================================

def extract_pdf_text(
    pdf_path: Path,
    book_hash: str
):

    try:

        from pdf2image import pdfinfo_from_path

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
            f"🔎 OCR PAGE {page_number}/{total_pages}"
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
# TXT / MD
# ============================================================

def extract_text_file(path: Path):

    try:

        text = path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

        text = clean_text(text)

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

    text = clean_text(text)

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

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

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

def create_embedding(text):

    if not gemini_client:
        raise RuntimeError(
            "GOOGLE_API_KEY belum ditetapkan"
        )

    result = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIM
        )
    )

    if not result.embeddings:
        raise RuntimeError(
            "Embedding kosong"
        )

    return result.embeddings[0].values


# ============================================================
# DELETE OLD SUPABASE DATA
# ============================================================

def delete_book_from_supabase(
    book_hash
):

    if not supabase:
        return

    try:

        supabase.table(
            "kitab_chunks"
        ).delete().eq(
            "book_hash",
            book_hash
        ).execute()

        print(
            f"🗑️ Supabase data deleted: {book_hash}"
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

        supabase.table(
            "books"
        ).upsert(
            {
                "book_hash": book_hash,
                "kitab_name": kitab_name,
                "category": category
            },
            on_conflict="book_hash"
        ).execute()

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

        supabase.table(
            "kitab_chunks"
        ).insert(
            data
        ).execute()

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

def process_book(path: Path):

    kitab_name = path.stem
    category = get_category_from_path(path)

    print("")
    print("=" * 60)
    print(
        f"📚 PROCESS: {path.name}"
    )
    print(
        f"📂 CATEGORY: {category}"
    )

    book_hash = file_hash(path)

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

        page = page_data["page"]
        text = page_data["text"]

        page_chunks = chunk_text(
            text
        )

        for chunk in page_chunks:

            chunks.append({
                "page": page,
                "content": chunk
            })

    print(
        f"📦 Jumlah chunks: {len(chunks)}"
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
            f"🧠 EMBEDDING {index}-{total}/{total}"
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
            f"💾 Supabase {index}-{total}"
        )

        save_chunk(
            book_hash=book_hash,
            kitab_name=kitab_name,
            category=category,
            page=item["page"],
            chunk_index=index,
            content=item["content"],
            embedding=embedding
        )

    print(
        f"✅ READY: {kitab_name}"
    )


# ============================================================
# SYNC ALL BOOKS
# ============================================================

def sync_books():

    print("")
    print("🔄 SYNC KITAB")
    print("=" * 60)

    print(
        f"📂 Searching: {KITAB_DIR}"
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

            files.append(path)

    print(
        f"📚 Jumlah kitab: {len(files)}"
    )

    for path in files:

        try:

            print(
                f"🆕 PROCESS: {path.name}"
            )

            process_book(path)

        except Exception as error:

            print(
                f"❌ PROCESS ERROR {path.name}:",
                error
            )

    print(
        "🏁 SYNC SELESAI"
    )


# ============================================================
# LOCAL SEARCH
# ============================================================

def search_local(
    query,
    limit=5
):

    if not supabase:
        return []

    try:

        embedding = create_embedding(
            query
        )

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding": embedding,
                "match_threshold": 0.25,
                "match_count": limit
            }
        ).execute()

        return result.data or []

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
    limit=5
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

        if not data.get("ok"):
            return []

        return data.get(
            "passages",
            []
        )

    except Exception as error:

        print(
            "❌ TURATH SEARCH ERROR:",
            error
        )

        return []


# ============================================================
# FORMAT LOCAL
# ============================================================

def format_local_sources(
    rows
):

    blocks = []

    for row in rows:

        kitab = (
            row.get("kitab_name")
            or row.get("book_name")
            or "Kitab Tempatan"
        )

        category = (
            row.get("category")
            or "FIQH"
        )

        page = row.get(
            "page",
            "-"
        )

        content = (
            row.get("content")
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
# FORMAT TURATH
# ============================================================

def format_turath_sources(
    passages
):

    blocks = []

    for item in passages:

        kitab = (
            item.get("kitab_name")
            or "Turath"
        )

        author = (
            item.get("author")
            or "Tidak diketahui"
        )

        page = item.get(
            "page",
            "-"
        )

        content = (
            item.get("content")
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
# GEMINI ANSWER
# ============================================================

def generate_answer(
    question,
    local_rows,
    turath_rows
):

    if not gemini_client:

        return (
            "Maaf, sistem Gemini belum dikonfigurasikan."
        )

    local_text = format_local_sources(
        local_rows
    )

    turath_text = format_turath_sources(
        turath_rows
    )

    sources = []

    for row in local_rows:

        sources.append(
            f"• {row.get('kitab_name', 'Kitab Tempatan')} "
            f"[{row.get('category', 'FIQH')}]"
        )

    for item in turath_rows:

        kitab = item.get(
            "kitab_name",
            "Turath"
        )

        author = item.get(
            "author",
            ""
        )

        page = item.get(
            "page",
            "-"
        )

        url = item.get(
            "url",
            ""
        )

        sources.append(
            f"• Turath — {author}, halaman {page}"
        )

    context = "\n\n".join(
        [
            x
            for x in [
                local_text,
                turath_text
            ]
            if x
        ]
    )

    prompt = f"""
Anda ialah TanyaFiqihBot, pembantu ilmu Islam
berbahasa Melayu.

Jawab soalan pengguna berdasarkan sumber yang
diberikan sahaja.

SOALAN:
{question}

SUMBER:
{context}

ARAHAN:
1. Jawab dalam Bahasa Melayu.
2. Gunakan sumber yang diberikan.
3. Jangan mereka-reka dalil atau fakta.
4. Jika sumber tidak mencukupi, nyatakan dengan jujur.
5. Jika terdapat perbezaan pendapat, nyatakan secara ringkas.
6. Untuk isu fiqh, jelaskan hukum dan syarat jika ada.
7. Jangan mendakwa sesuatu itu ijmak jika sumber tidak menyatakannya.
8. Jangan gunakan pengetahuan luar sebagai sumber utama.
9. Jawapan hendaklah jelas dan mudah difahami.
10. Jika terdapat teks Arab dalam sumber, boleh sertakan teks Arab
    yang relevan bersama maksud ringkas.

Jawapan:
"""

    try:

        response = gemini_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1500,
                candidate_count=1
            )
        )

        answer = (
            response.text
            if response and response.text
            else ""
        )

        if not answer:

            return (
                "Maaf, sistem tidak menghasilkan jawapan."
            )

        source_text = ""

        if sources:

            source_text = (
                "\n\n📚 Rujukan:\n"
                +
                "\n".join(
                    sources
                )
            )

        return answer + source_text

    except Exception as error:

        print(
            "❌ LLM error:",
            error
        )

        return (
            "❌ Berlaku masalah ketika menjana jawapan.\n\n"
            "📚 Rujukan:\n"
            +
            "\n".join(sources)
            if sources
            else
            "❌ Berlaku masalah ketika menjana jawapan."
        )


# ============================================================
# HANDLE QUESTION
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

    local_rows = search_local(
        question,
        limit=5
    )

    print(
        f"📚 LOCAL: {len(local_rows)}"
    )

    turath_rows = search_turath(
        question,
        limit=TURATH_SEARCH_K
    )

    print(
        f"📖 TURATH: {len(turath_rows)}"
    )

    print(
        f"📦 TOTAL: "
        f"{len(local_rows) + len(turath_rows)}"
    )

    if not local_rows and not turath_rows:

        return (
            "Maaf, saya tidak menemui sumber "
            "yang berkaitan dengan soalan tersebut."
        )

    return generate_answer(
        question,
        local_rows,
        turath_rows
    )


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        """🤖 *TanyaFiqihBot*

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
“Apakah rukun mandi wajib?”
""",
        parse_mode="Markdown"
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    question = (
        update.message.text or ""
    ).strip()

    if not question:
        return

    await update.message.chat.send_action(
        "typing"
    )

    try:

        loop = asyncio.get_running_loop()

        answer = await loop.run_in_executor(
            None,
            answer_question,
            question
        )

        await update.message.reply_text(
            answer
        )

    except Exception as error:

        print(
            "❌ TELEGRAM HANDLE ERROR:",
            error
        )

        await update.message.reply_text(
            "❌ Maaf, berlaku masalah ketika memproses soalan."
        )


async def telegram_main():

    if not TELEGRAM_TOKEN:

        print(
            "❌ TELEGRAM_TOKEN belum ditetapkan"
        )

        return

    application = (
        Application
        .builder()
        .token(TELEGRAM_TOKEN)
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
            filters.TEXT &
            ~filters.COMMAND,
            handle_message
        )
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling()

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

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


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
        f"🔢 EMBEDDING: {EMBEDDING_MODEL}"
    )

    print(
        f"📚 KITAB DIR: {KITAB_DIR}"
    )

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

    # Sync kitab
    try:

        sync_books()

    except Exception as error:

        print(
            "❌ SYNC ERROR:",
            error
        )

    # Telegram
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
