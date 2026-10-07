import os
import re
import gc
import json
import glob
import time
import hashlib
import threading
import urllib.request
import urllib.parse
import html

from flask import Flask, jsonify, request

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
    ChatGoogleGenerativeAI,
    GoogleGenerativeAIEmbeddings,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

from supabase import create_client, Client

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract


# ============================================================
# CONFIG
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

DATA_DIR = os.getenv("DATA_DIR", "/var/data")

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "1"))
OCR_DPI = int(os.getenv("OCR_DPI", "200"))

MIN_TEXT_CHARS = int(
    os.getenv("MIN_TEXT_CHARS", "40")
)

SEARCH_K = int(
    os.getenv("SEARCH_K", "6")
)

EMBEDDING_BATCH_SIZE = int(
    os.getenv("EMBEDDING_BATCH_SIZE", "16")
)

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.8-flash"
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "models/gemini-embedding-001"
)

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# DIRECTORIES
# ============================================================

os.makedirs(DATA_DIR, exist_ok=True)

EXTRACTED_DIR = os.path.join(
    DATA_DIR,
    "extracted_text"
)

PAGE_CACHE_DIR = os.path.join(
    EXTRACTED_DIR,
    "pages"
)

MANIFEST_PATH = os.path.join(
    DATA_DIR,
    "manifest.json"
)

os.makedirs(EXTRACTED_DIR, exist_ok=True)
os.makedirs(PAGE_CACHE_DIR, exist_ok=True)


# ============================================================
# KITAB DIRECTORY
# ============================================================

REPO_KITAB_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "kitab"
)

PERSISTENT_KITAB_DIR = os.path.join(
    DATA_DIR,
    "kitab"
)

if os.path.isdir(PERSISTENT_KITAB_DIR):
    KITAB_DIR = PERSISTENT_KITAB_DIR
else:
    KITAB_DIR = REPO_KITAB_DIR

print(
    f"📂 Kitab directory digunakan: {KITAB_DIR}"
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ Supabase connected")

    except Exception as e:
        print(
            "❌ Supabase connection error:",
            e
        )
else:
    print(
        "⚠️ SUPABASE_URL / SUPABASE_KEY tidak lengkap"
    )


# ============================================================
# GEMINI
# ============================================================

embeddings = None
llm = None

if GOOGLE_API_KEY:

    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_MODEL,
            google_api_key=GOOGLE_API_KEY,
            output_dimensionality=3072
        )

        llm = ChatGoogleGenerativeAI(
            model=LLM_MODEL,
            google_api_key=GOOGLE_API_KEY,
            temperature=0.2
        )

        print("✅ Gemini connected")

    except Exception as e:
        print(
            "❌ Gemini connection error:",
            e
        )

else:
    print(
        "⚠️ GOOGLE_API_KEY tidak ditetapkan"
    )


# ============================================================
# TEXT SPLITTER
# ============================================================

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1200,
    chunk_overlap=150,
    separators=[
        "\n\n",
        "\n",
        ". ",
        " ",
        ""
    ]
)


# ============================================================
# GLOBAL STATE
# ============================================================

sync_lock = threading.Lock()

SYNC_STATUS = {
    "running": False,
    "message": "Belum bermula",
    "current_book": None,
    "current_page": 0,
    "total_pages": 0,
    "processed_pages": 0,
    "total_chunks": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
}


# ============================================================
# BASIC HELPERS
# ============================================================

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

            chunk = f.read(
                1024 * 1024
            )

            if not chunk:
                break

            sha.update(chunk)

    return sha.hexdigest()


def load_manifest():

    if not os.path.exists(
        MANIFEST_PATH
    ):
        return {}

    try:

        with open(
            MANIFEST_PATH,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_manifest(data):

    temp = (
        MANIFEST_PATH
        + ".tmp"
    )

    with open(
        temp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp,
        MANIFEST_PATH
    )


# ============================================================
# PAGE CACHE
# ============================================================

def get_page_cache_path(
    book_hash,
    page_number
):

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


def save_page_cache(
    book_hash,
    page_number,
    text
):

    path = get_page_cache_path(
        book_hash,
        page_number
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(text or "")


def load_page_cache(
    book_hash,
    page_number
):

    path = get_page_cache_path(
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


# ============================================================
# CATEGORY
# ============================================================

CATEGORY_MAP = {

    "FIQH": "FIQH",

    "TAUHID": "TAUHID",

    "HADIS": "HADIS",

    "TAFSIR": "TAFSIR",

    "SIRAH": "SIRAH",

    "AKHLAK": "AKHLAK",

    "USUL FIQH": "USUL FIQH",

}


def normalize_category(
    category
):

    category = (
        category or ""
    ).strip().upper()

    return CATEGORY_MAP.get(
        category,
        category
    )


def category_from_path(
    path
):

    relative = os.path.relpath(
        path,
        KITAB_DIR
    )

    parts = relative.split(
        os.sep
    )

    if len(parts) >= 2:

        category = parts[0]

        return normalize_category(
            category
        )

    return "FIQH"


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf_pages(
    pdf_path,
    book_hash
):

    print(
        f"📖 Membaca PDF: "
        f"{os.path.basename(pdf_path)}"
    )

    reader = PdfReader(
        pdf_path
    )

    total_pages = len(
        reader.pages
    )

    print(
        f"📖 PDF: "
        f"{os.path.basename(pdf_path)} "
        f"({total_pages} halaman)"
    )

    pages = []

    for index in range(
        total_pages
    ):

        page_number = index + 1

        SYNC_STATUS[
            "current_page"
        ] = page_number

        SYNC_STATUS[
            "total_pages"
        ] = total_pages

        cached = load_page_cache(
            book_hash,
            page_number
        )

        if cached is not None:

            pages.append(
                {
                    "page_number":
                        page_number,
                    "text":
                        cached
                }
            )

            SYNC_STATUS[
                "processed_pages"
            ] = page_number

            continue

        print(
            f"🔍 OCR halaman "
            f"{page_number}/{total_pages}"
        )

        text = ""

        try:

            native_text = (
                reader.pages[index]
                .extract_text()
                or ""
            )

            native_text = normalize_text(
                native_text
            )

            if len(native_text) >= MIN_TEXT_CHARS:

                text = native_text

            else:

                images = convert_from_path(
                    pdf_path,
                    dpi=OCR_DPI,
                    first_page=page_number,
                    last_page=page_number
                )

                if images:

                    image = images[0]

                    try:

                        text = pytesseract.image_to_string(
                            image,
                            lang="ara+msa+eng"
                        )

                    finally:

                        try:
                            image.close()
                        except Exception:
                            pass

                        del images

                    text = normalize_text(
                        text
                    )

        except Exception as e:

            print(
                f"❌ OCR error "
                f"halaman {page_number}:",
                e
            )

            text = ""

        save_page_cache(
            book_hash,
            page_number,
            text
        )

        pages.append(
            {
                "page_number":
                    page_number,
                "text":
                    text
            }
        )

        SYNC_STATUS[
            "processed_pages"
        ] = page_number

        gc.collect()

    return pages


# ============================================================
# TXT EXTRACTION
# ============================================================

def extract_txt(
    txt_path
):

    with open(
        txt_path,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        text = f.read()

    text = normalize_text(
        text
    )

    return [
        {
            "page_number": 1,
            "text": text
        }
    ]


# ============================================================
# DOCUMENT EXTRACTION
# ============================================================

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

    if extension == ".txt":

        return extract_txt(
            path
        )

    return []


# ============================================================
# GEMINI EMBEDDING
# ============================================================

def embed_documents(
    texts
):

    if not embeddings:
        raise RuntimeError(
            "Gemini embeddings belum tersedia"
        )

    all_vectors = []

    for start in range(
        0,
        len(texts),
        EMBEDDING_BATCH_SIZE
    ):

        batch = texts[
            start:
            start + EMBEDDING_BATCH_SIZE
        ]

        print(
            f"🧠 EMBEDDING "
            f"{start + 1}-"
            f"{min(start + len(batch), len(texts))}/"
            f"{len(texts)}"
        )

        vectors = (
            embeddings.embed_documents(
                batch
            )
        )

        all_vectors.extend(
            vectors
        )

        time.sleep(0.1)

    return all_vectors


def embed_query(
    query
):

    if not embeddings:
        raise RuntimeError(
            "Gemini embeddings belum tersedia"
        )

    return embeddings.embed_query(
        query
    )


# ============================================================
# SUPABASE BOOK FUNCTIONS
# ============================================================

def get_book_by_hash(
    book_hash
):

    if not supabase:
        return None

    try:

        result = (
            supabase
            .table("books")
            .select("*")
            .eq(
                "file_hash",
                book_hash
            )
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:

        print(
            "❌ get_book_by_hash:",
            e
        )

    return None


def create_or_update_book(
    kitab_name,
    category,
    file_name,
    file_path,
    book_hash,
    total_pages
):

    if not supabase:
        return None

    existing = get_book_by_hash(
        book_hash
    )

    payload = {

        "kitab_name":
            kitab_name,

        "category":
            category,

        "file_name":
            file_name,

        "file_path":
            file_path,

        "file_hash":
            book_hash,

        "total_pages":
            total_pages,

        "processed_pages":
            0,

        "total_chunks":
            0,

        "status":
            "PROCESSING",

        "current_page":
            0,

        "error_message":
            None,
    }

    try:

        if existing:

            result = (
                supabase
                .table("books")
                .update(payload)
                .eq(
                    "id",
                    existing["id"]
                )
                .execute()
            )

        else:

            result = (
                supabase
                .table("books")
                .insert(payload)
                .execute()
            )

        if result.data:
            return result.data[0]

    except Exception as e:

        print(
            "❌ create/update book:",
            e
        )

    return None


def update_book(
    book_id,
    values
):

    if not supabase or not book_id:
        return

    try:

        (
            supabase
            .table("books")
            .update(values)
            .eq(
                "id",
                book_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "❌ update_book:",
            e
        )


def delete_source(
    book_hash
):

    if not supabase:
        return

    try:

        supabase.rpc(
            "delete_book_chunks",
            {
                "target_file_hash":
                    book_hash
            }
        ).execute()

        print(
            "🗑️ Supabase data deleted:",
            book_hash[:16]
        )

    except Exception as e:

        print(
            "⚠️ Delete chunks:",
            e
        )


# ============================================================
# PROCESS BOOK
# ============================================================

def process_book(
    path,
    category
):

    file_name = os.path.basename(
        path
    )

    kitab_name = os.path.splitext(
        file_name
    )[0]

    print()
    print("=" * 60)
    print(
        f"📚 PROCESS: {file_name}"
    )
    print(
        f"📂 CATEGORY: {category}"
    )

    book_hash = file_hash(
        path
    )

    print(
        f"🔑 HASH: {book_hash[:16]}"
    )

    delete_source(
        book_hash
    )

    SYNC_STATUS[
        "current_book"
    ] = kitab_name

    SYNC_STATUS[
        "current_page"
    ] = 0

    SYNC_STATUS[
        "processed_pages"
    ] = 0

    SYNC_STATUS[
        "total_chunks"
    ] = 0

    try:

        pages = extract_document(
            path,
            book_hash
        )

        total_pages = len(
            pages
        )

        book = create_or_update_book(
            kitab_name,
            category,
            file_name,
            path,
            book_hash,
            total_pages
        )

        book_id = (
            book["id"]
            if book
            else None
        )

        documents = []

        for page in pages:

            text = normalize_text(
                page.get("text", "")
            )

            if not text:
                continue

            page_number = (
                page["page_number"]
            )

            chunks = text_splitter.split_text(
                text
            )

            for chunk_number, chunk in enumerate(
                chunks,
                start=1
            ):

                chunk = normalize_text(
                    chunk
                )

                if not chunk:
                    continue

                documents.append(
                    {
                        "content": chunk,
                        "page_number":
                            page_number,
                        "chunk_number":
                            chunk_number
                    }
                )

        print(
            f"📦 Jumlah chunks: "
            f"{len(documents)}"
        )

        if not documents:

            if book_id:

                update_book(
                    book_id,
                    {
                        "status":
                            "FAILED",

                        "error_message":
                            "Tiada teks berjaya diekstrak",

                        "total_chunks":
                            0
                    }
                )

            return False

        SYNC_STATUS[
            "message"
        ] = "Embedding"

        if book_id:

            update_book(
                book_id,
                {
                    "status":
                        "EMBEDDING",

                    "total_chunks":
                        len(documents),

                    "processed_pages":
                        total_pages
                }
            )

        texts = [
            item["content"]
            for item in documents
        ]

        vectors = embed_documents(
            texts
        )

        rows = []

        for i, item in enumerate(
            documents
        ):

            rows.append(
                {
                    "book_id":
                        book_id,

                    "content":
                        item["content"],

                    "embedding":
                        vectors[i],

                    "kitab_name":
                        kitab_name,

                    "category":
                        category,

                    "page_number":
                        item["page_number"],

                    "chunk_number":
                        item["chunk_number"],

                    "file_hash":
                        book_hash
                }
            )

        SYNC_STATUS[
            "message"
        ] = "Simpan ke Supabase"

        if supabase:

            for start in range(
                0,
                len(rows),
                100
            ):

                batch = rows[
                    start:
                    start + 100
                ]

                (
                    supabase
                    .table("kitab_chunks")
                    .upsert(
                        batch,
                        on_conflict=
                        "file_hash,page_number,chunk_number"
                    )
                    .execute()
                )

                print(
                    f"💾 Supabase "
                    f"{start + 1}-"
                    f"{min(start + len(batch), len(rows))}"
                )

        SYNC_STATUS[
            "total_chunks"
        ] = len(rows)

        if book_id:

            update_book(
                book_id,
                {
                    "status":
                        "READY",

                    "processed_pages":
                        total_pages,

                    "total_chunks":
                        len(rows),

                    "current_page":
                        total_pages,

                    "error_message":
                        None
                }
            )

        print(
            f"✅ READY: {kitab_name}"
        )

        return True

    except Exception as e:

        print(
            "❌ PROCESS ERROR:",
            e
        )

        existing = get_book_by_hash(
            book_hash
        )

        if existing:

            update_book(
                existing["id"],
                {
                    "status":
                        "FAILED",

                    "error_message":
                        str(e)
                }
            )

        SYNC_STATUS[
            "error"
        ] = str(e)

        return False

    finally:

        gc.collect()


# ============================================================
# DISCOVER BOOKS
# ============================================================

def discover_books():

    books = []

    if not os.path.isdir(
        KITAB_DIR
    ):

        return books

    patterns = [
        "**/*.pdf",
        "**/*.txt"
    ]

    for pattern in patterns:

        full_pattern = os.path.join(
            KITAB_DIR,
            pattern
        )

        books.extend(
            glob.glob(
                full_pattern,
                recursive=True
            )
        )

    return sorted(
        set(books)
    )


# ============================================================
# SYNC BOOKS
# ============================================================

def sync_books():

    if sync_lock.locked():

        print(
            "⚠️ Sync sedang berjalan"
        )

        return

    with sync_lock:

        SYNC_STATUS[
            "running"
        ] = True

        SYNC_STATUS[
            "message"
        ] = "Mencari kitab"

        SYNC_STATUS[
            "error"
        ] = None

        SYNC_STATUS[
            "started_at"
        ] = time.time()

        try:

            print()
            print("=" * 60)
            print("🔄 SYNC KITAB")
            print("=" * 60)

            books = discover_books()

            print(
                f"📂 Searching: {KITAB_DIR}"
            )

            print(
                f"📚 Jumlah kitab: "
                f"{len(books)}"
            )

            manifest = load_manifest()

            for path in books:

                file_name = os.path.basename(
                    path
                )

                category = category_from_path(
                    path
                )

                current_hash = file_hash(
                    path
                )

                previous = manifest.get(
                    path
                )

                if (
                    previous
                    and previous.get("hash")
                    == current_hash
                ):

                    print(
                        f"⏭️ SKIP: {file_name}"
                    )

                    continue

                print(
                    f"🆕 PROCESS: {file_name}"
                )

                success = process_book(
                    path,
                    category
                )

                if success:

                    manifest[path] = {
                        "hash":
                            current_hash,

                        "category":
                            category,

                        "updated_at":
                            time.time()
                    }

                    save_manifest(
                        manifest
                    )

        except Exception as e:

            print(
                "❌ SYNC ERROR:",
                e
            )

            SYNC_STATUS[
                "error"
            ] = str(e)

        finally:

            SYNC_STATUS[
                "running"
            ] = False

            SYNC_STATUS[
                "message"
            ] = "Selesai"

            SYNC_STATUS[
                "finished_at"
            ] = time.time()

            print(
                "🏁 SYNC SELESAI"
            )


# ============================================================
# SUPABASE VECTOR SEARCH
# ============================================================

def search_supabase(
    query,
    category=None,
    match_count=None
):

    if not supabase:
        return []

    if match_count is None:
        match_count = SEARCH_K

    query_vector = embed_query(
        query
    )

    try:

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding":
                    query_vector,

                "match_count":
                    match_count,

                "filter_category":
                    category
            }
        ).execute()

        return result.data or []

    except Exception as e:

        print(
            "❌ Vector search:",
            e
        )

        return []


# ============================================================
# TURATH SERVICE
# ============================================================

def turath_request(
    endpoint,
    params=None,
    timeout=30
):

    try:

        params = params or {}

        query_string = (
            urllib.parse.urlencode(
                params
            )
        )

        url = (
            f"{TURATH_SERVICE_URL}"
            f"{endpoint}"
        )

        if query_string:
            url += "?" + query_string

        request_obj = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "TanyaFiqhBot/1.0"
            }
        )

        with urllib.request.urlopen(
            request_obj,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

        return json.loads(
            raw
        )

    except Exception as e:

        print(
            "❌ Turath request:",
            e
        )

        return {
            "ok": False,
            "error": str(e)
        }


def turath_search(
    query
):

    return turath_request(
        "/search",
        {
            "q": query
        },
        timeout=30
    )


def turath_book(
    book_id
):

    return turath_request(
        f"/book/{book_id}",
        timeout=30
    )


def turath_page(
    book_id,
    page_number
):

    return turath_request(
        f"/page/{book_id}/{page_number}",
        timeout=30
    )


# ============================================================
# TURATH RESULT EXTRACTION
# ============================================================

def find_dicts(
    obj
):

    results = []

    if isinstance(
        obj,
        dict
    ):

        results.append(obj)

        for value in obj.values():

            results.extend(
                find_dicts(value)
            )

    elif isinstance(
        obj,
        list
    ):

        for item in obj:

            results.extend(
                find_dicts(item)
            )

    return results


def first_value(
    data,
    keys
):

    if not isinstance(
        data,
        dict
    ):
        return None

    for key in keys:

        if key in data:

            value = data[key]

            if value is not None:

                return value

    return None


def extract_turath_books(
    result
):

    if not result:
        return []

    raw = result.get(
        "result",
        result
    )

    candidates = find_dicts(
        raw
    )

    books = []
    seen = set()

    for item in candidates:

        book_id = first_value(
            item,
            [
                "id",
                "bookId",
                "book_id",
                "bookID"
            ]
        )

        title = first_value(
            item,
            [
                "title",
                "bookTitle",
                "book_title",
                "name"
            ]
        )

        author = first_value(
            item,
            [
                "author",
                "authorName",
                "author_name"
            ]
        )

        if not book_id and not title:
            continue

        identifier = str(
            book_id or title
        )

        if identifier in seen:
            continue

        seen.add(
            identifier
        )

        books.append(
            {
                "id":
                    book_id,

                "title":
                    title or "Tanpa tajuk",

                "author":
                    author or "Tidak dinyatakan",

                "raw":
                    item
            }
        )

    return books


# ============================================================
# FLASK: HOME
# ============================================================

@app.route("/")
def home():

    return """
    <!DOCTYPE html>
    <html lang="ms">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1">

        <title>TanyaFiqhBot</title>

        <style>

            body {
                font-family: Arial, sans-serif;
                background: #f5f5f5;
                margin: 0;
                padding: 30px;
            }

            .container {
                max-width: 900px;
                margin: auto;
            }

            .card {
                background: white;
                padding: 25px;
                border-radius: 14px;
                margin-bottom: 20px;
                box-shadow:
                    0 3px 12px
                    rgba(0,0,0,.08);
            }

            a {
                text-decoration: none;
            }

            .button {
                display: inline-block;
                padding: 12px 18px;
                background: #222;
                color: white;
                border-radius: 8px;
                margin: 5px;
            }

        </style>

    </head>

    <body>

        <div class="container">

            <div class="card">

                <h1>📚 TanyaFiqhBot</h1>

                <p>
                    Sistem RAG kitab Islam
                    + Turath
                </p>

                <a class="button"
                   href="/turath">
                    🔎 Cari Turath
                </a>

                <a class="button"
                   href="/books">
                    📚 Kitab Tempatan
                </a>

                <a class="button"
                   href="/status">
                    📊 Status
                </a>

                <a class="button"
                   href="/health">
                    ❤️ Health
                </a>

            </div>

        </div>

    </body>
    </html>
    """


# ============================================================
# FLASK: HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "ok": True,

        "service":
            "TanyaFiqhBot",

        "supabase":
            supabase is not None,

        "gemini":
            embeddings is not None,

        "kitab_dir":
            KITAB_DIR,

        "turath_service":
            TURATH_SERVICE_URL

    })


# ============================================================
# FLASK: STATUS
# ============================================================

@app.route("/status")
def status():

    try:

        supabase_status = (
            "CONNECTED"
            if supabase
            else "DISCONNECTED"
        )

        return jsonify({

            "ok": True,

            "sync":
                SYNC_STATUS,

            "supabase":
                supabase_status,

            "gemini":
                embeddings is not None,

            "kitab_directory":
                KITAB_DIR,

            "kitab_directory_exists":
                os.path.isdir(
                    KITAB_DIR
                ),

            "turath_service":
                TURATH_SERVICE_URL

        })

    except Exception as e:

        return jsonify({

            "ok": False,

            "error":
                str(e)

        }), 500


# ============================================================
# FLASK: BOOKS
# ============================================================

@app.route("/books")
def books():

    if not supabase:

        return jsonify({
            "ok": False,
            "error":
                "Supabase tidak connected"
        }), 500

    try:

        result = (
            supabase
            .table("books")
            .select("*")
            .order(
                "id",
                desc=True
            )
            .execute()
        )

        return jsonify({
            "ok": True,
            "books":
                result.data or []
        })

    except Exception as e:

        return jsonify({
            "ok": False,
            "error":
                str(e)
        }), 500


# ============================================================
# FLASK: TURATH SEARCH
# ============================================================

@app.route("/turath")
def turath_page():

    query = (
        request.args
        .get("q", "")
        .strip()
    )

    books = []

    raw_result = None

    error_message = None

    if query:

        raw_result = turath_search(
            query
        )

        if not raw_result.get("ok"):

            error_message = (
                raw_result.get(
                    "error",
                    "Turath error"
                )
            )

        else:

            books = extract_turath_books(
                raw_result
            )

    cards = ""

    if error_message:

        cards = f"""
        <div class="error">
            <b>❌ Turath Error</b>
            <br>
            {html.escape(error_message)}
        </div>
        """

    elif query and not books:

        cards = """
        <div class="empty">
            Tiada keputusan ditemui.
        </div>
        """

    else:

        for book in books:

            book_id = book.get(
                "id"
            )

            title = html.escape(
                str(
                    book.get(
                        "title",
                        "Tanpa tajuk"
                    )
                )
            )

            author = html.escape(
                str(
                    book.get(
                        "author",
                        "Tidak dinyatakan"
                    )
                )
            )

            if book_id:

                action = f"""
                <a class="book-button"
                   href="/turath/book/{html.escape(str(book_id))}">
                    📖 Lihat Kitab
                </a>
                """

            else:

                action = ""

            cards += f"""

            <div class="book-card">

                <h3>
                    {title}
                </h3>

                <div class="author">
                    👤 {author}
                </div>

                <div class="book-id">
                    ID:
                    {html.escape(str(book_id or "-"))}
                </div>

                {action}

            </div>

            """

    page = f"""
    <!DOCTYPE html>

    <html lang="ms">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width,
                       initial-scale=1">

        <title>
            Turath Search
            - TanyaFiqhBot
        </title>

        <style>

            body {{
                font-family:
                    Arial, sans-serif;

                background:
                    #f4f6f8;

                margin: 0;

                padding: 25px;
            }}

            .container {{
                max-width: 950px;

                margin: auto;
            }}

            .header {{
                background: white;

                padding: 25px;

                border-radius: 15px;

                box-shadow:
                    0 3px 12px
                    rgba(0,0,0,.08);
            }}

            h1 {{
                margin-top: 0;
            }}

            form {{
                display: flex;

                gap: 10px;

                margin-top: 20px;
            }}

            input {{
                flex: 1;

                padding: 14px;

                border:
                    1px solid #ccc;

                border-radius: 9px;

                font-size: 16px;
            }}

            button {{
                padding:
                    14px 22px;

                border: 0;

                border-radius: 9px;

                cursor: pointer;

                font-size: 16px;
            }}

            .book-card {{
                background: white;

                margin-top: 15px;

                padding: 20px;

                border-radius: 12px;

                box-shadow:
                    0 2px 8px
                    rgba(0,0,0,.06);
            }}

            .book-card h3 {{
                margin-top: 0;
            }}

            .author {{
                margin: 8px 0;

                color: #555;
            }}

            .book-id {{
                color: #777;

                font-size: 13px;

                margin-bottom: 15px;
            }}

            .book-button {{
                display: inline-block;

                padding: 10px 15px;

                background: #222;

                color: white;

                text-decoration: none;

                border-radius: 8px;
            }}

            .error {{
                background: #ffe5e5;

                color: #900;

                padding: 15px;

                border-radius: 10px;

                margin-top: 20px;
            }}

            .empty {{
                background: white;

                padding: 20px;

                margin-top: 20px;

                border-radius: 10px;
            }}

            .back {{
                display: inline-block;

                margin-top: 15px;

                text-decoration: none;
            }}

        </style>

    </head>

    <body>

        <div class="container">

            <div class="header">

                <h1>
                    📚 Turath Search
                </h1>

                <p>
                    Cari kitab dalam
                    pangkalan Turath.
                </p>

                <form
                    method="get"
                    action="/turath"
                >

                    <input
                        type="text"
                        name="q"
                        value="{html.escape(query)}"
                        placeholder=
                        "Contoh: Fathul Muin, solat, zakat..."
                    >

                    <button type="submit">
                        🔍 Cari
                    </button>

                </form>

                <a class="back"
                   href="/">
                    ← Kembali
                </a>

            </div>

            <div>

                {cards}

            </div>

        </div>

    </body>

    </html>
    """

    return page


# ============================================================
# FLASK: TURATH BOOK
# ============================================================

@app.route(
    "/turath/book/<book_id>"
)
def turath_book_page(
    book_id
):

    result = turath_book(
        book_id
    )

    if not result.get("ok"):

        return f"""
        <h2>❌ Turath Error</h2>
        <pre>
        {html.escape(
            str(
                result.get(
                    "error"
                )
            )
        )}
        </pre>

        <a href="/turath">
            ← Kembali
        </a>
        """, 500

    raw = result.get(
        "result",
        result
    )

    formatted = json.dumps(
        raw,
        ensure_ascii=False,
        indent=2
    )

    return f"""
    <!DOCTYPE html>

    <html lang="ms">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width,
                       initial-scale=1">

        <title>
            Turath Book
        </title>

        <style>

            body {{
                font-family: Arial;
                max-width: 1000px;
                margin: auto;
                padding: 25px;
                background: #f5f5f5;
            }}

            .card {{
                background: white;
                padding: 25px;
                border-radius: 12px;
            }}

            pre {{
                white-space: pre-wrap;
                word-break: break-word;
            }}

        </style>

    </head>

    <body>

        <div class="card">

            <h1>
                📖 Turath Book
            </h1>

            <p>
                Book ID:
                {html.escape(str(book_id))}
            </p>

            <pre>
{html.escape(formatted)}
            </pre>

            <a href="/turath">
                ← Kembali
            </a>

        </div>

    </body>

    </html>
    """


# ============================================================
# TELEGRAM ANSWER GENERATION
# ============================================================

def format_sources(
    results
):

    sources = []

    for item in results:

        kitab = item.get(
            "kitab_name",
            "Kitab"
        )

        page = item.get(
            "page_number"
        )

        similarity = item.get(
            "similarity"
        )

        if page:

            source = (
                f"{kitab}, "
                f"hlm. {page}"
            )

        else:

            source = kitab

        if similarity is not None:

            try:

                source += (
                    f" "
                    f"({float(similarity):.2f})"
                )

            except Exception:
                pass

        sources.append(
            source
        )

    return sources


def generate_answer(
    question,
    results
):

    if not llm:

        return (
            "Maaf, sistem AI belum "
            "bersedia."
        )

    if not results:

        return (
            "Maaf, saya tidak menemui "
            "rujukan yang mencukupi "
            "dalam kitab yang tersedia."
        )

    context_parts = []

    for i, item in enumerate(
        results,
        start=1
    ):

        content = item.get(
            "content",
            ""
        )

        kitab = item.get(
            "kitab_name",
            "Tidak diketahui"
        )

        category = item.get(
            "category",
            ""
        )

        page = item.get(
            "page_number"
        )

        context_parts.append(
            f"""
SUMBER {i}
Kitab: {kitab}
Kategori: {category}
Halaman: {page}

Kandungan:
{content}
"""
        )

    context = "\n\n".join(
        context_parts
    )

    prompt = f"""
Anda ialah TanyaFiqhBot,
pembantu rujukan ilmu Islam.

Jawab soalan pengguna dalam
Bahasa Melayu yang jelas,
ringkas tetapi mencukupi.

PRINSIP PENTING:

1. Jawapan mesti berdasarkan
   sumber yang diberikan.

2. Jangan mereka-reka dalil,
   nombor halaman atau fakta.

3. Jika sumber tidak mencukupi,
   nyatakan bahawa rujukan tidak
   mencukupi.

4. Bezakan antara hukum,
   pendapat ulama dan penjelasan.

5. Jika terdapat khilaf,
   nyatakan secara ringkas
   dan jangan menyamakan semua
   pendapat sebagai satu hukum.

6. Jangan gunakan sumber yang
   tiada dalam konteks.

7. Di akhir jawapan, sertakan
   bahagian:

   📚 Rujukan:
   - Nama kitab, halaman

SOALAN:

{question}

KONTEKS KITAB:

{context}
"""

    try:

        response = llm.invoke(
            [
                HumanMessage(
                    content=prompt
                )
            ]
        )

        return response.content

    except Exception as e:

        print(
            "❌ LLM error:",
            e
        )

        return (
            "Maaf, berlaku masalah "
            "semasa menghasilkan jawapan."
        )


# ============================================================
# TELEGRAM CATEGORY
# ============================================================

COMMAND_CATEGORIES = {

    "fiqh":
        "FIQH",

    "tauhid":
        "TAUHID",

    "hadis":
        "HADIS",

    "tafsir":
        "TAFSIR",

    "sirah":
        "SIRAH",

    "akhlak":
        "AKHLAK",

    "usulfiqh":
        "USUL FIQH",

    "semua":
        None
}


# ============================================================
# TELEGRAM /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = """
📚 *TanyaFiqhBot*

Assalamualaikum.

Saya boleh membantu mencari
jawapan berdasarkan kitab Islam
yang tersedia dalam sistem.

📖 Kategori:

/fiqh
/tauhid
/hadis
/tafsir
/sirah
/akhlak
/usulfiqh
/semua

Contoh:

/fiqh apakah hukum solat berjemaah?

Atau terus taip soalan anda.

🔎 Sistem juga sedang
diintegrasikan dengan Turath.
"""

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# ============================================================
# TELEGRAM /STATUS
# ============================================================

async def telegram_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        status = SYNC_STATUS

        if status["running"]:

            text = (
                "🔄 *Sedang proses kitab*\n\n"
                f"📚 {status['current_book']}\n"
                f"📖 "
                f"{status['current_page']}/"
                f"{status['total_pages']}\n"
                f"📦 Chunks: "
                f"{status['total_chunks']}\n"
                f"⚙️ {status['message']}"
            )

        else:

            text = (
                "✅ *Status TanyaFiqhBot*\n\n"
                f"📂 Kitab: "
                f"{KITAB_DIR}\n"
                f"💾 Supabase: "
                f"{'CONNECTED' if supabase else 'OFF'}\n"
                f"🧠 Gemini: "
                f"{'CONNECTED' if embeddings else 'OFF'}\n"
                f"🔎 Turath: "
                f"{TURATH_SERVICE_URL}\n"
                f"📌 Status: "
                f"{status['message']}"
            )

        await update.message.reply_text(
            text,
            parse_mode="Markdown"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Gagal mendapatkan status.\n\n{e}"
        )


# ============================================================
# TELEGRAM CATEGORY HANDLERS
# ============================================================

async def category_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    command = (
        update.message.text
        .split()[0]
        .replace("/", "")
        .lower()
    )

    category = COMMAND_CATEGORIES.get(
        command
    )

    question = (
        update.message.text
        .replace(
            update.message.text.split()[0],
            "",
            1
        )
        .strip()
    )

    if not question:

        await update.message.reply_text(
            f"Taip soalan selepas "
            f"/{command}.\n\n"
            f"Contoh:\n"
            f"/{command} apakah hukum..."
        )

        return

    await answer_question(
        update,
        question,
        category
    )


# ============================================================
# TELEGRAM NORMAL MESSAGE
# ============================================================

async def normal_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    question = (
        update.message.text or ""
    ).strip()

    if not question:
        return

    await answer_question(
        update,
        question,
        None
    )


# ============================================================
# ANSWER QUESTION
# ============================================================

async def answer_question(
    update,
    question,
    category
):

    try:

        await update.message.reply_text(
            "🔎 Sedang mencari rujukan..."
        )

        results = search_supabase(
            question,
            category=category,
            match_count=SEARCH_K
        )

        if not results:

            await update.message.reply_text(
                "❌ Tiada rujukan ditemui "
                "dalam kitab tempatan."
            )

            return

        answer = generate_answer(
            question,
            results
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        print(
            "❌ Answer error:",
            e
        )

        await update.message.reply_text(
            "❌ Maaf, berlaku masalah "
            "semasa memproses soalan."
        )


# ============================================================
# TELEGRAM BOT
# ============================================================

def telegram_thread():

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan"
        )

        return

    print(
        "🤖 Starting Telegram Bot..."
    )

    application = (
        Application.builder()
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
        CommandHandler(
            "status",
            telegram_status
        )
    )

    application.add_handler(
        CommandHandler(
            "fiqh",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "tauhid",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "hadis",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "tafsir",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "sirah",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "akhlak",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "usulfiqh",
            category_command
        )
    )

    application.add_handler(
        CommandHandler(
            "semua",
            category_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            normal_message
        )
    )

    print(
        "✅ Telegram polling started"
    )

    application.run_polling(
        stop_signals=None
    )


# ============================================================
# STARTUP SYNC
# ============================================================

def startup_sync():

    print()
    print("=" * 60)
    print("TanyaFiqhBot")
    print("=" * 60)

    print(
        f"Kitab directory: {KITAB_DIR}"
    )

    print(
        f"Kitab folder exists: "
        f"{os.path.isdir(KITAB_DIR)}"
    )

    print(
        "Supabase:",
        "CONNECTED"
        if supabase
        else "DISCONNECTED"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # Nota:
    # Sync PDF masih dikekalkan.
    # Turath berjalan secara berasingan.
    # --------------------------------------------------------

    sync_books()


# ============================================================
# MAIN
# ============================================================

def start_background_services():

    # Sync kitab
    sync_thread = threading.Thread(
        target=startup_sync,
        daemon=True,
        name="KitabSync"
    )

    sync_thread.start()

    # Telegram
    telegram = threading.Thread(
        target=telegram_thread,
        daemon=True,
        name="TelegramBot"
    )

    telegram.start()


start_background_services()


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
