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
from flask import Flask, jsonify, request

from supabase import create_client, Client

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
).rstrip("/")


# ============================================================
# PATH
# ============================================================

KITAB_DIR = Path("/app/kitab")

EXTRACTED_DIR = (
    Path(DATA_DIR)
    / "extracted_text"
)

PAGES_DIR = EXTRACTED_DIR / "pages"

EXTRACTED_DIR.mkdir(
    parents=True,
    exist_ok=True
)

PAGES_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CLIENT
# ============================================================

gemini_client = None
supabase: Client | None = None


if GOOGLE_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )
        print("✅ Gemini client ready")
    except Exception as e:
        print("❌ Gemini client error:", e)


if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )
        print("✅ Supabase client ready")
    except Exception as e:
        print("❌ Supabase client error:", e)


# ============================================================
# CACHE
# ============================================================

ANSWER_CACHE = {}
MAX_CACHE_SIZE = 500

CACHE_LOCK = threading.Lock()


def normalize_question(question: str) -> str:
    question = question.lower().strip()

    question = re.sub(
        r"\s+",
        " ",
        question
    )

    return question


def get_cached_answer(question: str):
    key = normalize_question(question)

    with CACHE_LOCK:
        return ANSWER_CACHE.get(key)


def save_cached_answer(
    question: str,
    answer: str
):
    key = normalize_question(question)

    with CACHE_LOCK:

        if len(ANSWER_CACHE) >= MAX_CACHE_SIZE:

            first_key = next(
                iter(ANSWER_CACHE)
            )

            ANSWER_CACHE.pop(
                first_key,
                None
            )

        ANSWER_CACHE[key] = answer


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "\x00",
        " "
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    return text.strip()


def normalize_for_hash(text):
    return re.sub(
        r"\s+",
        " ",
        text.strip().lower()
    )


def make_hash(text):
    return hashlib.sha256(
        normalize_for_hash(text).encode(
            "utf-8"
        )
    ).hexdigest()[:16]


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
    "USUL FIQH",
]


def detect_category(path: Path):

    parts = [
        str(part).upper()
        for part in path.parts
    ]

    for category in CATEGORIES:

        if category in parts:
            return category

    return "FIQH"


# ============================================================
# CHUNKING
# ============================================================

def chunk_text(
    text,
    chunk_size=1800,
    overlap=250
):

    text = clean_text(text)

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []

    start = 0
    text_length = len(text)

    while start < text_length:

        end = min(
            start + chunk_size,
            text_length
        )

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start = max(
            end - overlap,
            start + 1
        )

    return chunks


# ============================================================
# OCR CACHE
# ============================================================

def book_cache_dir(book_hash):

    path = PAGES_DIR / book_hash

    path.mkdir(
        parents=True,
        exist_ok=True
    )

    return path


def get_cached_page(
    book_hash,
    page_number
):

    path = (
        book_cache_dir(book_hash)
        / f"{page_number}.txt"
    )

    if not path.exists():
        return None

    try:
        return path.read_text(
            encoding="utf-8"
        )
    except Exception:
        return None


def save_cached_page(
    book_hash,
    page_number,
    text
):

    path = (
        book_cache_dir(book_hash)
        / f"{page_number}.txt"
    )

    path.write_text(
        text,
        encoding="utf-8"
    )


# ============================================================
# OCR PDF
# ============================================================

def extract_pdf_text(
    pdf_path: Path,
    book_hash: str
):

    print(
        f"📖 OCR PDF: {pdf_path.name}"
    )

    pages = []

    try:

        images = convert_from_path(
            str(pdf_path),
            dpi=OCR_DPI,
            fmt="jpeg"
        )

        total = len(images)

        for index, image in enumerate(
            images,
            start=1
        ):

            cached = get_cached_page(
                book_hash,
                index
            )

            if cached is not None:

                text = cached

                print(
                    f"⚡ OCR CACHE "
                    f"{index}/{total}"
                )

            else:

                print(
                    f"🔍 OCR "
                    f"{index}/{total}"
                )

                text = pytesseract.image_to_string(
                    image,
                    lang="ara+msa+eng"
                )

                text = clean_text(text)

                save_cached_page(
                    book_hash,
                    index,
                    text
                )

            if text:
                pages.append(
                    (
                        index,
                        text
                    )
                )

    except Exception as e:

        print(
            "❌ PDF OCR ERROR:",
            e
        )

        return []

    return pages


# ============================================================
# TEXT / PDF READER
# ============================================================

def read_book(path: Path):

    suffix = path.suffix.lower()

    if suffix == ".txt":

        try:

            text = path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError:

            text = path.read_text(
                encoding="utf-8",
                errors="ignore"
            )

        return [
            (
                None,
                clean_text(text)
            )
        ]

    if suffix == ".pdf":

        book_hash = make_hash(
            path.name
        )

        return extract_pdf_text(
            path,
            book_hash
        )

    return []


# ============================================================
# EMBEDDING
# ============================================================

def create_embedding(text):

    if not gemini_client:
        raise RuntimeError(
            "Gemini client tidak tersedia"
        )

    response = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIM
        )
    )

    if not response.embeddings:
        raise RuntimeError(
            "Embedding kosong"
        )

    values = response.embeddings[0].values

    return list(values)


# ============================================================
# DELETE OLD BOOK DATA
# ============================================================

def delete_book_from_supabase(
    book_hash
):

    if not supabase:
        return

    try:

        result = (
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
            f"🗑️ Supabase data deleted: "
            f"{book_hash}"
        )

    except Exception as e:

        print(
            "❌ DELETE SUPABASE ERROR:",
            e
        )


# ============================================================
# INSERT BOOK
# ============================================================

def insert_book_chunks(
    path: Path,
    category: str
):

    print("")
    print("=" * 60)
    print(
        f"📚 PROCESS: {path.name}"
    )
    print(
        f"📂 CATEGORY: {category}"
    )

    book_hash = make_hash(
        str(path.resolve())
        + str(path.stat().st_size)
        + path.name
    )

    print(
        f"🔑 HASH: {book_hash}"
    )

    pages = read_book(path)

    if not pages:

        print(
            "⚠️ Tiada teks ditemui"
        )

        return

    all_chunks = []

    for page_number, text in pages:

        chunks = chunk_text(text)

        for chunk_index, chunk in enumerate(
            chunks,
            start=1
        ):

            all_chunks.append({
                "content": chunk,
                "page_number": page_number,
                "chunk_index": chunk_index
            })

    print(
        f"📦 Jumlah chunks: "
        f"{len(all_chunks)}"
    )

    if not supabase:

        print(
            "❌ Supabase tidak tersedia"
        )

        return

    delete_book_from_supabase(
        book_hash
    )

    total = len(all_chunks)

    for index, item in enumerate(
        all_chunks,
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

        except Exception as e:

            print(
                "❌ EMBEDDING ERROR:",
                e
            )

            continue

        row = {
            "book_hash": book_hash,
            "kitab_name": path.stem,
            "category": category,
            "content": item["content"],
            "page_number": item["page_number"],
            "chunk_index": item["chunk_index"],
            "embedding": embedding,
        }

        try:

            (
                supabase
                .table("kitab_chunks")
                .insert(row)
                .execute()
            )

            print(
                f"💾 Supabase "
                f"{index}-{total}"
            )

        except Exception as e:

            print(
                "❌ SUPABASE INSERT ERROR:",
                e
            )

    print(
        f"✅ READY: {path.stem}"
    )


# ============================================================
# FIND KITAB
# ============================================================

def find_kitab_files():

    if not KITAB_DIR.exists():

        print(
            f"⚠️ Folder kitab tidak wujud: "
            f"{KITAB_DIR}"
        )

        return []

    files = []

    for path in KITAB_DIR.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in [
            ".pdf",
            ".txt"
        ]:
            continue

        files.append(path)

    return sorted(files)


# ============================================================
# SYNC KITAB
# ============================================================

def sync_kitab():

    print("")
    print("=" * 60)
    print("🔄 SYNC KITAB")
    print("=" * 60)

    print(
        f"📂 Searching: {KITAB_DIR}"
    )

    files = find_kitab_files()

    print(
        f"📚 Jumlah kitab: {len(files)}"
    )

    for path in files:

        try:

            category = detect_category(
                path
            )

            print(
                f"\n🆕 PROCESS: "
                f"{path.name}"
            )

            insert_book_chunks(
                path,
                category
            )

        except Exception as e:

            print(
                f"❌ PROCESS ERROR "
                f"{path.name}:",
                e
            )

    print("")
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
            "⚠️ Supabase tidak tersedia"
        )

        return []

    try:

        embedding = create_embedding(
            question
        )

    except Exception as e:

        print(
            "❌ LOCAL EMBEDDING ERROR:",
            e
        )

        return []

    try:

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding": embedding,
                "match_threshold": 0.20,
                "match_count": limit
            }
        ).execute()

        rows = result.data or []

        # LOCAL MESTI SATU SAHAJA
        rows = rows[:1]

        output = []

        for row in rows:

            output.append({
                "source_type": "local",
                "content": clean_text(
                    row.get("content", "")
                ),
                "kitab_name": (
                    row.get("kitab_name")
                    or "Kitab Tempatan"
                ),
                "category": (
                    row.get("category")
                    or ""
                ),
                "page": row.get(
                    "page_number"
                ),
                "chunk_index": row.get(
                    "chunk_index"
                ),
            })

        print(
            f"📚 LOCAL: {len(output)}"
        )

        return output

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
    question,
    limit=3
):

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/search",
            params={
                "q": question,
                "limit": limit
            },
            timeout=90
        )

        response.raise_for_status()

        data = response.json()

        passages = (
            data.get("passages")
            or []
        )

        # TURATH MAKSIMUM 3
        passages = passages[:3]

        print(
            f"📖 TURATH: "
            f"{len(passages)}"
        )

        return passages

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

    blocks = []

    # --------------------------------------------------------
    # LOCAL
    # --------------------------------------------------------

    for item in local_results[:1]:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        kitab_name = item.get(
            "kitab_name",
            "Kitab Tempatan"
        )

        category = item.get(
            "category",
            ""
        )

        page = item.get(
            "page"
        )

        header = (
            f"[KITAB TEMPATAN]\n"
            f"Kitab: {kitab_name}\n"
            f"Kategori: {category}\n"
            f"Halaman: {page}\n"
        )

        blocks.append(
            header
            + "\n"
            + content
        )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    for item in turath_results[:3]:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        kitab_name = item.get(
            "kitab_name",
            "Turath"
        )

        author = item.get(
            "author"
        )

        page = item.get(
            "page"
        )

        header = (
            f"[TURATH]\n"
            f"Kitab: {kitab_name}\n"
            f"Pengarang: {author or '-'}\n"
            f"Halaman: {page or '-'}\n"
        )

        blocks.append(
            header
            + "\n"
            + content
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# BUILD REFERENCES
# ============================================================

def build_references(
    local_results,
    turath_results
):

    lines = []

    lines.append(
        "📚 *Rujukan:*"
    )

    # --------------------------------------------------------
    # LOCAL - 1 SAHAJA
    # --------------------------------------------------------

    for item in local_results[:1]:

        kitab_name = item.get(
            "kitab_name",
            "Kitab Tempatan"
        )

        category = item.get(
            "category",
            ""
        )

        page = item.get(
            "page"
        )

        line = (
            f"• {kitab_name}"
        )

        if category:
            line += (
                f" [{category}]"
            )

        if page:
            line += (
                f", halaman {page}"
            )

        lines.append(line)

    # --------------------------------------------------------
    # TURATH - 3 SAHAJA
    # --------------------------------------------------------

    for item in turath_results[:3]:

        kitab_name = item.get(
            "kitab_name",
            "Turath"
        )

        author = item.get(
            "author"
        )

        page = item.get(
            "page"
        )

        url = item.get(
            "url"
        )

        line = f"• Turath — {author or kitab_name}"

        if page:
            line += (
                f", halaman {page}"
            )

        lines.append(line)

        if url:
            lines.append(
                f"  🔗 {url}"
            )

    # --------------------------------------------------------
    # KALAU TIADA SUMBER
    # --------------------------------------------------------

    if len(lines) == 1:

        lines.append(
            "• Tiada rujukan ditemui."
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

    cached = get_cached_answer(
        question
    )

    if cached:

        print(
            "⚡ CACHE HIT"
        )

        return cached

    if not gemini_client:

        return (
            "⚠️ *Huraian AI tidak dapat "
            "dijana buat masa ini.*\n\n"
            "Rujukan yang ditemui tetap "
            "dipaparkan di bawah."
        )

    if not context.strip():

        return (
            "⚠️ Tiada kandungan sumber "
            "yang mencukupi untuk menghasilkan "
            "huraian."
        )

    system_instruction = """
Anda ialah TanyaFiqihBot, pembantu ilmu Islam
dalam Bahasa Melayu.

TUGAS ANDA HANYA MENGHURAIKAN SUMBER YANG
TELAH DICARI OLEH SISTEM.

PENTING:

- Anda TIDAK perlu mencari kitab.
- Anda TIDAK perlu mencari rujukan.
- Anda TIDAK perlu menghasilkan URL.
- Anda TIDAK perlu membuat senarai rujukan.
- Semua rujukan telah disediakan oleh sistem.
- Jangan mengaku anda telah mencari Turath atau kitab lain.
- Jangan mencipta sumber yang tiada dalam konteks.

Gunakan sumber yang diberikan untuk memahami
dan menghuraikan jawapan kepada pengguna.

Jawapan mesti:

1. Menjawab soalan pengguna secara terus.
2. Memberikan huraian yang jelas.
3. Menggunakan Bahasa Melayu yang mudah difahami.
4. Menjelaskan hukum, sebab, syarat atau perincian
   jika perkara tersebut terdapat dalam sumber.
5. Jika terdapat dalil atau hadis dalam sumber,
   terangkan maksudnya secara ringkas.
6. Jika terdapat khilaf atau perbezaan pendapat
   dalam sumber, nyatakan secara jelas.
7. Jangan mereka-reka maklumat yang tiada dalam
   sumber.
8. Jika sumber tidak cukup untuk menentukan sesuatu
   perkara, nyatakan keterbatasan tersebut.
9. Jangan sekadar menyalin sumber.
10. Jangan hanya memberikan nama kitab.
11. Jangan menulis URL.
12. Jangan menulis bahagian "Rujukan".

FORMAT:

**Jawapan**

Jawapan terus kepada persoalan pengguna.

**Huraian**

Terangkan dengan jelas berdasarkan sumber
yang diberikan.

**Kesimpulan**

Rumusan ringkas tentang jawapan.

Pastikan jawapan membantu pengguna memahami
hukum, bukan sekadar menunjukkan sumber.
"""

    prompt = f"""
SOALAN PENGGUNA:

{question}

========================================

SUMBER YANG TELAH DICARI OLEH SISTEM:

{context}

========================================

Sekarang jawab soalan pengguna berdasarkan
SUMBER DI ATAS.

Jangan mencari sumber lain.
Jangan menghasilkan URL.
Jangan menghasilkan senarai rujukan.

Tugas anda hanya:
JAWAB → HURAIKAN → BUAT KESIMPULAN.
"""

    try:

        response = gemini_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=1500,
                candidate_count=1
            )
        )

        answer = (
            response.text or ""
        ).strip()

        if not answer:

            return (
                "⚠️ *Huraian AI tidak dapat "
                "dijana buat masa ini.*"
            )

        save_cached_answer(
            question,
            answer
        )

        return answer

    except Exception as e:

        error_text = str(e)

        print(
            "❌ LLM ERROR:",
            error_text
        )

        if (
            "429" in error_text
            or
            "RESOURCE_EXHAUSTED"
            in error_text
        ):

            return (
                "⚠️ *Huraian AI tidak dapat "
                "dijana buat masa ini.*\n\n"
                "Kuota AI telah mencapai had. "
                "Namun, rujukan yang ditemui "
                "tetap dipaparkan di bawah."
            )

        return (
            "⚠️ *Huraian AI tidak dapat "
            "dijana buat masa ini.*\n\n"
            "Terdapat masalah ketika menjana "
            "huraian. Rujukan yang ditemui "
            "tetap dipaparkan di bawah."
        )


# ============================================================
# ANSWER QUESTION
# ============================================================

def answer_question(
    question
):

    question = clean_text(
        question
    )

    if not question:

        return (
            "Sila masukkan soalan."
        )

    print("")
    print("=" * 60)
    print(
        "❓ SOALAN:",
        question
    )
    print("=" * 60)

    # ========================================================
    # 1. LOCAL - SATU SAHAJA
    # ========================================================

    local_results = search_local(
        question,
        limit=1
    )

    # ========================================================
    # 2. TURATH - TIGA SAHAJA
    # ========================================================

    turath_results = search_turath(
        question,
        limit=3
    )

    print(
        f"📚 LOCAL: "
        f"{len(local_results)}"
    )

    print(
        f"📖 TURATH: "
        f"{len(turath_results)}"
    )

    print(
        f"📦 TOTAL: "
        f"{len(local_results) + len(turath_results)}"
    )

    # ========================================================
    # 3. RUJUKAN DIBINA OLEH SISTEM
    # ========================================================

    references = build_references(
        local_results,
        turath_results
    )

    # ========================================================
    # 4. CONTEXT UNTUK GEMINI
    # ========================================================

    context = build_context(
        local_results,
        turath_results
    )

    # Had context supaya tidak membazir token
    if len(context) > 18000:

        context = context[:18000]

        print(
            "✂️ Context dipotong kepada "
            "18000 aksara"
        )

    # ========================================================
    # 5. GEMINI HANYA HURAIKAN
    # ========================================================

    answer = generate_answer(
        question,
        context
    )

    # ========================================================
    # 6. RUJUKAN SENTIASA DITAMBAH
    # ========================================================

    final_answer = (
        answer
        + "\n\n"
        + references
    )

    return final_answer


# ============================================================
# TELEGRAM
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 *TanyaFiqihBot*\n\n"
        "Assalamualaikum.\n\n"
        "Sila ajukan soalan berkaitan "
        "Fiqh, Tauhid, Hadis, Tafsir, Sirah, "
        "Akhlak atau Usul Fiqh.",
        parse_mode="Markdown"
    )


async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📚 TanyaFiqihBot\n\n"
        "Contoh soalan:\n\n"
        "• Apa hukum mandi wajib?\n"
        "• Apakah rukun wuduk?\n"
        "• Apa hukum terlupa membaca qunut?\n"
        "• Bagaimana cara solat jamak?\n"
        "• Apakah syarat sah puasa?"
    )


async def telegram_message(
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

    # Elak Telegram freeze sementara
    # proses Gemini / search berjalan
    await update.message.chat.send_action(
        action="typing"
    )

    try:

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        # Telegram limit sekitar 4096 chars
        if len(answer) <= 4000:

            await update.message.reply_text(
                answer,
                parse_mode="Markdown"
            )

        else:

            chunks = [
                answer[i:i + 4000]
                for i in range(
                    0,
                    len(answer),
                    4000
                )
            ]

            for chunk in chunks:

                await update.message.reply_text(
                    chunk,
                    parse_mode="Markdown"
                )

    except Exception as e:

        print(
            "❌ TELEGRAM ERROR:",
            e
        )

        try:

            await update.message.reply_text(
                "⚠️ Berlaku masalah ketika "
                "memproses soalan."
            )

        except Exception:
            pass


# ============================================================
# TELEGRAM RUNNER
# ============================================================

def run_telegram():

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan"
        )

        return

    async def runner():

        application = (
            Application
            .builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        application.add_handler(
            CommandHandler(
                "start",
                telegram_start
            )
        )

        application.add_handler(
            CommandHandler(
                "help",
                telegram_help
            )
        )

        application.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                telegram_message
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

                await asyncio.sleep(3600)

        finally:

            await application.updater.stop()

            await application.stop()

            await application.shutdown()

    try:

        asyncio.run(
            runner()
        )

    except Exception as e:

        print(
            "❌ TELEGRAM RUNNER ERROR:",
            e
        )


# ============================================================
# BACKGROUND SYNC
# ============================================================

def background_sync():

    try:

        # Tunggu service siap
        time.sleep(5)

        sync_kitab()

    except Exception as e:

        print(
            "❌ BACKGROUND SYNC ERROR:",
            e
        )


# ============================================================
# START BACKGROUND SERVICES
# ============================================================

telegram_thread = threading.Thread(
    target=run_telegram,
    daemon=True
)

telegram_thread.start()


sync_thread = threading.Thread(
    target=background_sync,
    daemon=True
)

sync_thread.start()


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "status": "running",
        "llm_model": LLM_MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "local_limit": 1,
        "turath_limit": 3,
    })


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "telegram": bool(
            TELEGRAM_TOKEN
        ),
        "gemini": bool(
            gemini_client
        ),
        "supabase": bool(
            supabase
        ),
        "local_limit": 1,
        "turath_limit": 3,
    })


@app.route("/ask")
def ask():

    question = (
        request.args.get(
            "q",
            ""
        )
        .strip()
    )

    if not question:

        return jsonify({
            "ok": False,
            "error": "Parameter q diperlukan"
        }), 400

    answer = answer_question(
        question
    )

    return jsonify({
        "ok": True,
        "question": question,
        "answer": answer
    })


@app.route("/sync")
def manual_sync():

    thread = threading.Thread(
        target=sync_kitab,
        daemon=True
    )

    thread.start()

    return jsonify({
        "ok": True,
        "message": "Sync kitab dimulakan"
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print("")
    print("=" * 60)
    print("🤖 TanyaFiqihBot")
    print("=" * 60)
    print(
        f"LLM: {LLM_MODEL}"
    )
    print(
        f"Embedding: {EMBEDDING_MODEL}"
    )
    print(
        "Local references: 1"
    )
    print(
        "Turath references: 3"
    )
    print(
        "Gemini: explanation only"
    )
    print(
        "References: independent"
    )
    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=port
    )
