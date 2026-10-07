import os
import re
import gc
import json
import glob
import time
import hashlib
import threading

from flask import Flask, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

from supabase import create_client, Client

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract


# =========================================================
# CONFIG
# =========================================================

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

DATA_DIR = os.environ.get("DATA_DIR", "/var/data")

OCR_WORKERS = int(os.environ.get("OCR_WORKERS", "1"))
OCR_DPI = int(os.environ.get("OCR_DPI", "200"))

MIN_TEXT_CHARS = 40

LLM_MODEL = os.environ.get(
    "LLM_MODEL",
    "gemini-2.5-flash"
)

EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "models/gemini-embedding-001"
)

SEARCH_K = int(os.environ.get("SEARCH_K", "6"))

EMBEDDING_BATCH_SIZE = int(
    os.environ.get("EMBEDDING_BATCH_SIZE", "16")
)


# =========================================================
# PATH
# =========================================================

KITAB_DIR = os.path.join(DATA_DIR, "kitab")

EXTRACTED_DIR = os.path.join(
    DATA_DIR,
    "extracted_text"
)

PAGE_CACHE_DIR = os.path.join(
    EXTRACTED_DIR,
    "pages"
)

MANIFEST_FILE = os.path.join(
    DATA_DIR,
    "manifest.json"
)

os.makedirs(KITAB_DIR, exist_ok=True)
os.makedirs(EXTRACTED_DIR, exist_ok=True)
os.makedirs(PAGE_CACHE_DIR, exist_ok=True)


# =========================================================
# SUPABASE
# =========================================================

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )
        print("✅ Supabase connected")
    except Exception as e:
        print("❌ Supabase connection error:", e)
else:
    print("⚠️ SUPABASE_URL / SUPABASE_KEY belum ditetapkan")


def get_supabase():

    if supabase is None:
        raise RuntimeError(
            "Supabase belum dikonfigurasi. "
            "Semak SUPABASE_URL dan SUPABASE_KEY."
        )

    return supabase


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

INDEX_READY = False
INDEX_STATUS = {
    "status": "STARTING",
    "books": 0,
    "processed": 0,
    "failed": 0,
}


@app.route("/")
def home():

    return jsonify({
        "app": "TanyaFiqhBot",
        "status": "online",
        "index_ready": INDEX_READY,
        "index_status": INDEX_STATUS
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "index_ready": INDEX_READY
    })


@app.route("/books")
def books():

    try:

        sb = get_supabase()

        result = (
            sb.table("books")
            .select("*")
            .order("id")
            .execute()
        )

        return jsonify(
            result.data or []
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# UTILITIES
# =========================================================

def normalize_text(text):

    if not text:
        return ""

    text = text.replace("\x00", " ")

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


def file_hash(path):

    sha = hashlib.sha256()

    with open(path, "rb") as f:

        while True:

            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            sha.update(chunk)

    return sha.hexdigest()


def load_manifest():

    if not os.path.exists(MANIFEST_FILE):
        return {}

    try:

        with open(
            MANIFEST_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_manifest(manifest):

    tmp_file = MANIFEST_FILE + ".tmp"

    with open(
        tmp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            manifest,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        tmp_file,
        MANIFEST_FILE
    )


# =========================================================
# OCR CACHE
# =========================================================

def page_cache_path(book_hash, page_number):

    book_dir = os.path.join(
        PAGE_CACHE_DIR,
        book_hash
    )

    os.makedirs(
        book_dir,
        exist_ok=True
    )

    return os.path.join(
        book_dir,
        f"{page_number}.txt"
    )


def read_page_cache(
    book_hash,
    page_number
):

    path = page_cache_path(
        book_hash,
        page_number
    )

    if not os.path.exists(path):
        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return f.read()

    except Exception:

        return None


def save_page_cache(
    book_hash,
    page_number,
    text
):

    path = page_cache_path(
        book_hash,
        page_number
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text)


# =========================================================
# PDF EXTRACTION
# =========================================================

def extract_pdf_pages(path, book_hash):

    reader = PdfReader(path)

    total_pages = len(reader.pages)

    pages = []

    print(
        f"📖 PDF: {os.path.basename(path)} "
        f"({total_pages} halaman)"
    )

    for page_index in range(total_pages):

        page_number = page_index + 1

        cached = read_page_cache(
            book_hash,
            page_number
        )

        if cached is not None:

            text = normalize_text(cached)

        else:

            text = ""

            try:

                native_text = (
                    reader.pages[page_index]
                    .extract_text() or ""
                )

                native_text = normalize_text(
                    native_text
                )

                if len(native_text) >= MIN_TEXT_CHARS:

                    text = native_text

            except Exception:

                text = ""

            # OCR jika native PDF tidak mencukupi
            if len(text) < MIN_TEXT_CHARS:

                try:

                    print(
                        f"🔍 OCR halaman "
                        f"{page_number}/{total_pages}"
                    )

                    images = convert_from_path(
                        path,
                        dpi=OCR_DPI,
                        first_page=page_number,
                        last_page=page_number,
                        fmt="jpeg",
                        thread_count=1
                    )

                    if images:

                        image = images[0]

                        text = pytesseract.image_to_string(
                            image,
                            lang="ara+msa+eng"
                        )

                        del image

                    del images

                except Exception as e:

                    print(
                        f"❌ OCR page {page_number}:",
                        e
                    )

                    text = ""

            text = normalize_text(text)

            save_page_cache(
                book_hash,
                page_number,
                text
            )

        pages.append(
            (
                page_number,
                text
            )
        )

        if page_number % 10 == 0:

            print(
                f"📄 Progress OCR: "
                f"{page_number}/{total_pages}"
            )

        gc.collect()

    return pages, total_pages


# =========================================================
# TXT
# =========================================================

def extract_txt(path):

    with open(
        path,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        text = f.read()

    text = normalize_text(text)

    return [
        (1, text)
    ], 1


# =========================================================
# EXTRACT DOCUMENT
# =========================================================

def extract_document(
    path,
    book_hash
):

    extension = os.path.splitext(
        path
    )[1].lower()

    if extension == ".pdf":

        return extract_pdf_pages(
            path,
            book_hash
        )

    elif extension == ".txt":

        return extract_txt(path)

    else:

        raise ValueError(
            f"Format tidak disokong: {extension}"
        )


# =========================================================
# GEMINI
# =========================================================

def get_embeddings():

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
        output_dimensionality=3072,
    )


def get_llm():

    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.2,
    )


# =========================================================
# SUPABASE BOOK
# =========================================================

def create_book_record(
    path,
    book_hash,
    category,
    total_pages
):

    sb = get_supabase()

    file_name = os.path.basename(path)

    kitab_name = os.path.splitext(
        file_name
    )[0]

    result = (
        sb.table("books")
        .upsert(
            {
                "kitab_name": kitab_name,
                "category": category,
                "file_name": file_name,
                "file_path": path,
                "file_hash": book_hash,
                "total_pages": total_pages,
                "processed_pages": 0,
                "total_chunks": 0,
                "status": "PROCESSING",
                "current_page": 0,
                "error_message": None,
            },
            on_conflict="file_hash"
        )
        .execute()
    )

    if not result.data:

        raise RuntimeError(
            "Gagal create/update book dalam Supabase"
        )

    return result.data[0]["id"]


def update_book(
    book_id,
    **fields
):

    sb = get_supabase()

    (
        sb.table("books")
        .update(fields)
        .eq("id", book_id)
        .execute()
    )


# =========================================================
# DELETE BOOK
# =========================================================

def delete_source(book_hash):

    sb = get_supabase()

    try:

        sb.rpc(
            "delete_book_chunks",
            {
                "target_file_hash": book_hash
            }
        ).execute()

    except Exception as e:

        print(
            "⚠️ RPC delete_book_chunks:",
            e
        )

    (
        sb.table("books")
        .delete()
        .eq("file_hash", book_hash)
        .execute()
    )

    print(
        f"🗑️ Supabase data deleted: {book_hash[:12]}"
    )


# =========================================================
# PROCESS BOOK
# =========================================================

def process_book(
    path,
    category
):

    global INDEX_STATUS

    book_hash = file_hash(path)

    print()
    print("=" * 60)
    print(
        "📚 PROCESS:",
        os.path.basename(path)
    )
    print(
        "📂 CATEGORY:",
        category
    )
    print(
        "🔑 HASH:",
        book_hash[:16]
    )
    print("=" * 60)

    book_id = None

    try:

        # Bersihkan data lama jika ada
        delete_source(book_hash)

        # ---------------------------------------------
        # EXTRACT
        # ---------------------------------------------

        pages, total_pages = extract_document(
            path,
            book_hash
        )

        # ---------------------------------------------
        # CREATE BOOK
        # ---------------------------------------------

        book_id = create_book_record(
            path,
            book_hash,
            category,
            total_pages
        )

        update_book(
            book_id,
            status="EMBEDDING"
        )

        # ---------------------------------------------
        # SPLITTER
        # ---------------------------------------------

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=150,
            separators=[
                "\n\n",
                "\n",
                " ",
                ""
            ]
        )

        embeddings = get_embeddings()

        total_chunks = 0

        # ---------------------------------------------
        # PROCESS PAGE BY PAGE
        # ---------------------------------------------

        for page_number, page_text in pages:

            if not page_text:
                continue

            if len(page_text) < MIN_TEXT_CHARS:
                continue

            chunks = splitter.split_text(
                page_text
            )

            if not chunks:
                continue

            for batch_start in range(
                0,
                len(chunks),
                EMBEDDING_BATCH_SIZE
            ):

                batch = chunks[
                    batch_start:
                    batch_start + EMBEDDING_BATCH_SIZE
                ]

                print(
                    f"🧠 Embedding "
                    f"page {page_number} "
                    f"batch "
                    f"{batch_start + 1}-"
                    f"{batch_start + len(batch)}"
                )

                vectors = embeddings.embed_documents(
                    batch
                )

                rows = []

                for i, (
                    chunk_text,
                    vector
                ) in enumerate(
                    zip(batch, vectors)
                ):

                    chunk_number = (
                        batch_start + i + 1
                    )

                    rows.append(
                        {
                            "book_id": book_id,
                            "content": chunk_text,
                            "embedding": vector,
                            "kitab_name": os.path.splitext(
                                os.path.basename(path)
                            )[0],
                            "category": category,
                            "page_number": page_number,
                            "chunk_number": chunk_number,
                            "file_hash": book_hash,
                        }
                    )

                if rows:

                    (
                        get_supabase()
                        .table("kitab_chunks")
                        .upsert(
                            rows,
                            on_conflict=(
                                "file_hash,"
                                "page_number,"
                                "chunk_number"
                            )
                        )
                        .execute()
                    )

                    total_chunks += len(rows)

                del vectors
                del rows
                gc.collect()

            # Progress database
            update_book(
                book_id,
                status="EMBEDDING",
                processed_pages=page_number,
                current_page=page_number,
                total_chunks=total_chunks
            )

            print(
                f"✅ Page {page_number}/{total_pages} "
                f"| chunks: {total_chunks}"
            )

        # ---------------------------------------------
        # READY
        # ---------------------------------------------

        update_book(
            book_id,
            status="READY",
            processed_pages=total_pages,
            current_page=total_pages,
            total_chunks=total_chunks,
            error_message=None
        )

        print()
        print(
            f"🎉 SIAP: "
            f"{os.path.basename(path)}"
        )

        print(
            f"📄 Pages: {total_pages}"
        )

        print(
            f"🧩 Chunks: {total_chunks}"
        )

        return True

    except Exception as e:

        print()
        print(
            "❌ ERROR PROCESS BOOK:",
            e
        )

        if book_id:

            try:

                update_book(
                    book_id,
                    status="FAILED",
                    error_message=str(e)
                )

            except Exception:

                pass

        return False


# =========================================================
# FIND BOOKS
# =========================================================

def discover_books():

    books = []

    extensions = [
        "*.pdf",
        "*.PDF",
        "*.txt",
        "*.TXT"
    ]

    for category_path in glob.glob(
        os.path.join(
            KITAB_DIR,
            "*"
        )
    ):

        if not os.path.isdir(
            category_path
        ):
            continue

        category = os.path.basename(
            category_path
        ).strip().upper()

        for extension in extensions:

            books.extend(
                glob.glob(
                    os.path.join(
                        category_path,
                        extension
                    )
                )
            )

    return books


# =========================================================
# SYNC BOOKS
# =========================================================

def sync_books():

    global INDEX_READY
    global INDEX_STATUS

    print()
    print("=" * 60)
    print("🔄 SYNC KITAB")
    print("=" * 60)

    manifest = load_manifest()

    book_paths = discover_books()

    INDEX_STATUS = {
        "status": "PROCESSING",
        "books": len(book_paths),
        "processed": 0,
        "failed": 0,
    }

    print(
        f"📚 Jumlah kitab: {len(book_paths)}"
    )

    current_hashes = set()

    for path in book_paths:

        try:

            book_hash = file_hash(path)

            current_hashes.add(
                book_hash
            )

            category = os.path.basename(
                os.path.dirname(path)
            ).strip().upper()

            filename = os.path.basename(
                path
            )

            old = manifest.get(
                path
            )

            # Jika fail sama dan sudah READY
            if (
                old
                and old.get("hash") == book_hash
                and old.get("status") == "READY"
            ):

                print(
                    f"⏭️ SKIP: {filename}"
                )

                INDEX_STATUS[
                    "processed"
                ] += 1

                continue

            print(
                f"🆕 PROCESS: {filename}"
            )

            success = process_book(
                path,
                category
            )

            if success:

                manifest[path] = {
                    "hash": book_hash,
                    "status": "READY",
                    "category": category,
                    "updated_at": time.time()
                }

                INDEX_STATUS[
                    "processed"
                ] += 1

            else:

                manifest[path] = {
                    "hash": book_hash,
                    "status": "FAILED",
                    "category": category,
                    "updated_at": time.time()
                }

                INDEX_STATUS[
                    "failed"
                ] += 1

            save_manifest(
                manifest
            )

        except Exception as e:

            print(
                f"❌ Sync error "
                f"{path}: {e}"
            )

            INDEX_STATUS[
                "failed"
            ] += 1

    INDEX_STATUS[
        "status"
    ] = "READY"

    INDEX_READY = True

    print()
    print("=" * 60)
    print("✅ SYNC SELESAI")
    print("=" * 60)

    print(
        INDEX_STATUS
    )


# =========================================================
# SEARCH SUPABASE VECTOR
# =========================================================

def search_books(
    question,
    category=None,
    limit=SEARCH_K
):

    embeddings = get_embeddings()

    query_vector = embeddings.embed_query(
        question
    )

    sb = get_supabase()

    result = sb.rpc(
        "match_kitab_chunks",
        {
            "query_embedding": query_vector,
            "match_count": limit,
            "filter_category": category,
        }
    ).execute()

    rows = result.data or []

    docs = []

    for row in rows:

        docs.append(
            Document(
                page_content=row.get(
                    "content",
                    ""
                ),
                metadata={
                    "kitab_name": row.get(
                        "kitab_name"
                    ),
                    "category": row.get(
                        "category"
                    ),
                    "page_number": row.get(
                        "page_number"
                    ),
                    "chunk_number": row.get(
                        "chunk_number"
                    ),
                    "similarity": row.get(
                        "similarity"
                    ),
                }
            )
        )

    return docs


# =========================================================
# GENERATE ANSWER
# =========================================================

def generate_answer(
    question,
    category=None
):

    try:

        docs = search_books(
            question,
            category=category,
            limit=SEARCH_K
        )

        if not docs:

            return (
                "Maaf, saya tidak menemui "
                "rujukan yang berkaitan dalam "
                "kitab yang telah dimasukkan."
            )

        context_parts = []

        for doc in docs:

            metadata = doc.metadata

            kitab = metadata.get(
                "kitab_name",
                "Tidak diketahui"
            )

            kategori = metadata.get(
                "category",
                ""
            )

            page = metadata.get(
                "page_number",
                "-"
            )

            similarity = metadata.get(
                "similarity",
                0
            )

            context_parts.append(
                f"""
KITAB: {kitab}
KATEGORI: {kategori}
HALAMAN: {page}
SIMILARITY: {similarity}

{doc.page_content}
"""
            )

        context = "\n\n".join(
            context_parts
        )

        prompt = f"""
Anda ialah TanyaFiqhBot, pembantu rujukan
ilmu Islam.

Jawab soalan pengguna berdasarkan kandungan
kitab yang diberikan di bawah.

PENTING:
1. Utamakan maklumat daripada kitab.
2. Jangan mereka-reka dalil atau hukum.
3. Jika maklumat tidak mencukupi, nyatakan
   bahawa rujukan tidak mencukupi.
4. Jangan mendakwa sesuatu itu pendapat ulama
   tertentu jika tidak terdapat dalam konteks.
5. Jawab dalam Bahasa Melayu yang mudah difahami.
6. Jika terdapat dalil atau teks Arab dalam
   konteks, boleh sertakan teks tersebut
   jika relevan.
7. Nyatakan nama kitab dan halaman sebagai
   rujukan.
8. Untuk isu khilaf, nyatakan bahawa terdapat
   perbezaan pandangan jika konteks menunjukkan
   demikian.
9. Untuk persoalan hukum yang serius atau
   melibatkan keadaan khusus seseorang,
   sarankan pengguna merujuk ustaz/ulama
   yang berkelayakan.

SOALAN:
{question}

RUJUKAN KITAB:
{context}
"""

        llm = get_llm()

        response = llm.invoke(
            [
                HumanMessage(
                    content=prompt
                )
            ]
        )

        answer = response.content

        if not isinstance(
            answer,
            str
        ):

            answer = str(
                answer
            )

        # Tambah rujukan
        references = []

        seen = set()

        for doc in docs:

            kitab = doc.metadata.get(
                "kitab_name"
            )

            page = doc.metadata.get(
                "page_number"
            )

            key = (
                kitab,
                page
            )

            if key in seen:
                continue

            seen.add(key)

            references.append(
                f"• {kitab} — hlm. {page}"
            )

        if references:

            answer += (
                "\n\n📚 *Rujukan:*\n"
                + "\n".join(references)
            )

        return answer

    except Exception as e:

        print(
            "❌ generate_answer error:",
            e
        )

        return (
            "Maaf, berlaku masalah ketika "
            "memproses soalan. Sila cuba lagi."
        )


# =========================================================
# TELEGRAM
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    await message.reply_text(
        """
📚 *TanyaFiqhBot*

Assalamualaikum 👋

Saya ialah chatbot rujukan ilmu Islam
berdasarkan kitab yang dimasukkan ke dalam
sistem.

🔎 Taip soalan anda untuk mencari jawapan.

Contoh:

• Apakah syarat sah solat?
• Apa hukum terlupa membaca al-Fatihah?
• Apakah rukun wuduk?

Kategori:

/fiqh
/tauhid
/hadis
/tafsir
/sirah
/akhlak
/usulfiqh

/semua
/status
""",
        parse_mode="Markdown"
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    try:

        sb = get_supabase()

        result = (
            sb.table("books")
            .select(
                "kitab_name,category,status,"
                "total_pages,processed_pages,"
                "total_chunks"
            )
            .order("id")
            .execute()
        )

        rows = result.data or []

        ready = sum(
            1
            for row in rows
            if row.get("status") == "READY"
        )

        processing = sum(
            1
            for row in rows
            if row.get("status") in [
                "PROCESSING",
                "OCR",
                "EMBEDDING"
            ]
        )

        failed = sum(
            1
            for row in rows
            if row.get("status") == "FAILED"
        )

        total_chunks = sum(
            row.get("total_chunks") or 0
            for row in rows
        )

        text = f"""
📊 *Status TanyaFiqhBot*

📚 Jumlah kitab: {len(rows)}
✅ Ready: {ready}
🔄 Processing: {processing}
❌ Failed: {failed}

🧩 Jumlah chunks: {total_chunks}
"""

        await message.reply_text(
            text,
            parse_mode="Markdown"
        )

    except Exception as e:

        await message.reply_text(
            f"❌ Gagal mendapatkan status.\n\n{e}"
        )


async def category_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    command = (
        update.message.text
        .split()[0]
        .lower()
        .replace("/", "")
    )

    category_map = {
        "fiqh": "FIQH",
        "tauhid": "TAUHID",
        "hadis": "HADIS",
        "tafsir": "TAFSIR",
        "sirah": "SIRAH",
        "akhlak": "AKHLAK",
        "usulfiqh": "USUL FIQH",
        "semua": None,
    }

    category = category_map.get(
        command
    )

    context.user_data[
        "category"
    ] = category

    if category:

        text = (
            f"📚 Mod *{category}* diaktifkan.\n\n"
            "Sila taip soalan anda."
        )

    else:

        context.user_data[
            "category"
        ] = None

        text = (
            "📚 Mod *SEMUA KITAB* diaktifkan.\n\n"
            "Sila taip soalan anda."
        )

    await message.reply_text(
        text,
        parse_mode="Markdown"
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    question = (
        message.text or ""
    ).strip()

    if not question:

        return

    category = context.user_data.get(
        "category"
    )

    await message.chat.send_action(
        "typing"
    )

    answer = generate_answer(
        question,
        category=category
    )

    await message.reply_text(
        answer,
        parse_mode="Markdown"
    )


# =========================================================
# TELEGRAM RUN
# =========================================================

def run_telegram():

    print(
        "🤖 Telegram bot starting..."
    )

    telegram_app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    for command in [
        "fiqh",
        "tauhid",
        "hadis",
        "tafsir",
        "sirah",
        "akhlak",
        "usulfiqh",
        "semua",
    ]:

        telegram_app.add_handler(
            CommandHandler(
                command,
                category_command
            )
        )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    print(
        "✅ Telegram bot ready"
    )

    telegram_app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None
    )


# =========================================================
# BACKGROUND
# =========================================================

def start_background():

    global INDEX_STATUS

    try:

        sync_books()

    except Exception as e:

        print(
            "❌ Background sync error:",
            e
        )

        INDEX_STATUS[
            "status"
        ] = "ERROR"

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True
    )

    telegram_thread.start()


# =========================================================
# START
# =========================================================

print()
print("=" * 60)
print("📚 TanyaFiqhBot")
print("=" * 60)
print(
    "Kitab directory:",
    KITAB_DIR
)
print(
    "Supabase:",
    "CONNECTED"
    if supabase
    else "NOT CONNECTED"
)
print("=" * 60)


threading.Thread(
    target=start_background,
    daemon=True
).start()


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
