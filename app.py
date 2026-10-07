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
from pathlib import Path

from flask import Flask, request, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    GoogleGenerativeAIEmbeddings,
)
from langchain_core.messages import HumanMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from supabase import create_client, Client

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract


# =========================================================
# ENVIRONMENT
# =========================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

DATA_DIR = os.getenv("DATA_DIR", "/var/data")

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "1"))
OCR_DPI = int(os.getenv("OCR_DPI", "200"))
MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "30"))

SEARCH_K = int(os.getenv("SEARCH_K", "6"))
TURATH_SEARCH_K = int(os.getenv("TURATH_SEARCH_K", "5"))

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

TURATH_ENABLED = os.getenv(
    "TURATH_ENABLED",
    "true"
).lower() == "true"

TURATH_TIMEOUT = int(
    os.getenv("TURATH_TIMEOUT", "30")
)

TURATH_CACHE_TTL = int(
    os.getenv("TURATH_CACHE_TTL", "300")
)

# Had maksimum konteks supaya prompt tidak terlalu besar
LOCAL_CONTEXT_LIMIT = int(
    os.getenv("LOCAL_CONTEXT_LIMIT", "2500")
)

TURATH_CONTEXT_LIMIT = int(
    os.getenv("TURATH_CONTEXT_LIMIT", "3000")
)

TOTAL_CONTEXT_LIMIT = int(
    os.getenv("TOTAL_CONTEXT_LIMIT", "18000")
)


# =========================================================
# DIRECTORIES
# =========================================================

os.makedirs(DATA_DIR, exist_ok=True)

CACHE_DIR = os.path.join(
    DATA_DIR,
    "extracted_text",
    "pages"
)

TURATH_CACHE_DIR = os.path.join(
    DATA_DIR,
    "turath_cache"
)

MANIFEST_FILE = os.path.join(
    DATA_DIR,
    "manifest.json"
)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(TURATH_CACHE_DIR, exist_ok=True)


# =========================================================
# GLOBAL CLIENTS
# =========================================================

supabase: Client | None = None
embeddings = None
llm = None


if GOOGLE_API_KEY:
    embeddings = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
        output_dimensionality=3072,
    )

    llm = ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.2,
    )


if SUPABASE_URL and SUPABASE_KEY:
    supabase = supabase_client = Client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# CATEGORY
# =========================================================

CATEGORIES = [
    "FIQH",
    "TAUHID",
    "HADIS",
    "TAFSIR",
    "SIRAH",
    "AKHLAK",
    "USUL FIQH",
]


def normalize_category(category):
    if not category:
        return None

    category = str(category).strip().upper()

    aliases = {
        "USUL": "USUL FIQH",
        "USULFIQH": "USUL FIQH",
        "USUL FIQIH": "USUL FIQH",
        "HADITH": "HADIS",
        "TAFSIR": "TAFSIR",
        "FIQIH": "FIQH",
        "AKHLAQ": "AKHLAK",
    }

    return aliases.get(category, category)


def detect_category_from_text(text):
    if not text:
        return None

    t = text.lower()

    if "/fiqh" in t:
        return "FIQH"

    if "/tauhid" in t or "/akidah" in t:
        return "TAUHID"

    if "/hadis" in t or "/hadith" in t:
        return "HADIS"

    if "/tafsir" in t:
        return "TAFSIR"

    if "/sirah" in t:
        return "SIRAH"

    if "/akhlak" in t:
        return "AKHLAK"

    if "/usul" in t:
        return "USUL FIQH"

    return None


# =========================================================
# GENERAL HELPERS
# =========================================================

def safe_filename(name):
    name = re.sub(
        r"[^\w\-. ]+",
        "_",
        name,
        flags=re.UNICODE
    )

    return name.strip()[:150]


def sha256_file(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def clean_text(text):
    if text is None:
        return ""

    text = str(text)

    text = html.unescape(text)

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.I
    )

    text = re.sub(
        r"</p\s*>",
        "\n",
        text,
        flags=re.I
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
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


def normalize_for_dedupe(text):
    text = clean_text(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text[:500].strip().lower()


def first_value(data, keys):
    if not isinstance(data, dict):
        return None

    for key in keys:
        if key in data:
            value = data[key]

            if value is not None and value != "":
                return value

    return None


def recursive_dicts(value):
    """
    Yield semua dictionary secara recursive.
    """
    if isinstance(value, dict):
        yield value

        for v in value.values():
            yield from recursive_dicts(v)

    elif isinstance(value, list):
        for item in value:
            yield from recursive_dicts(item)


def recursive_find_value(value, keys):
    """
    Cari value pertama daripada key tertentu
    dalam struktur JSON yang sangat bersarang.
    """
    keys = {
        str(k).lower()
        for k in keys
    }

    for d in recursive_dicts(value):
        for k, v in d.items():
            if str(k).lower() in keys:
                if v is not None and v != "":
                    return v

    return None


def ensure_int(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        return int(value)
    except Exception:
        pass

    match = re.search(
        r"\d+",
        str(value)
    )

    if match:
        try:
            return int(match.group())
        except Exception:
            return None

    return None


# =========================================================
# PDF / TXT EXTRACTION
# =========================================================

def extract_pdf_text_direct(path):
    """
    Cuba extract teks PDF secara biasa dahulu.
    """
    try:
        reader = PdfReader(path)

        pages = []

        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            text = clean_text(text)

            pages.append(
                {
                    "page": i,
                    "text": text,
                }
            )

        return pages

    except Exception as e:
        print(
            f"⚠️ PDF direct extraction error: {path}: {e}"
        )

        return []


def pdf_page_cache_path(book_hash, page):
    directory = os.path.join(
        CACHE_DIR,
        book_hash
    )

    os.makedirs(directory, exist_ok=True)

    return os.path.join(
        directory,
        f"{page}.txt"
    )


def read_page_cache(book_hash, page):
    path = pdf_page_cache_path(
        book_hash,
        page
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


def write_page_cache(book_hash, page, text):
    path = pdf_page_cache_path(
        book_hash,
        page
    )

    try:
        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(text)

    except Exception as e:
        print(
            f"⚠️ Cache write error: {e}"
        )


def extract_pdf_with_ocr(path, book_hash):
    """
    OCR PDF secara page-by-page.
    Cache setiap page supaya tidak OCR semula.
    """

    results = []

    try:
        reader = PdfReader(path)

        total_pages = len(reader.pages)

    except Exception:
        total_pages = 0

    if total_pages == 0:
        return results

    print(
        f"📖 OCR pages: {total_pages}"
    )

    for page_number in range(
        1,
        total_pages + 1
    ):
        cached = read_page_cache(
            book_hash,
            page_number
        )

        if cached is not None:
            results.append(
                {
                    "page": page_number,
                    "text": cached,
                }
            )

            continue

        try:
            images = convert_from_path(
                path,
                dpi=OCR_DPI,
                first_page=page_number,
                last_page=page_number,
                fmt="jpeg",
            )

            if not images:
                text = ""

            else:
                text = pytesseract.image_to_string(
                    images[0],
                    lang="ara+msa+eng"
                )

            text = clean_text(text)

            write_page_cache(
                book_hash,
                page_number,
                text
            )

            results.append(
                {
                    "page": page_number,
                    "text": text,
                }
            )

            del images

            gc.collect()

        except Exception as e:
            print(
                f"⚠️ OCR page {page_number}: {e}"
            )

            results.append(
                {
                    "page": page_number,
                    "text": "",
                }
            )

    return results


def extract_txt(path):
    try:
        with open(
            path,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as f:
            text = f.read()

        text = clean_text(text)

        return [
            {
                "page": 1,
                "text": text,
            }
        ]

    except Exception as e:
        print(
            f"❌ TXT error: {path}: {e}"
        )

        return []


def extract_book_pages(path):
    extension = Path(path).suffix.lower()

    if extension == ".txt":
        return extract_txt(path)

    if extension == ".pdf":
        book_hash = sha256_file(path)

        direct_pages = extract_pdf_text_direct(
            path
        )

        direct_char_count = sum(
            len(p["text"])
            for p in direct_pages
        )

        if direct_char_count >= MIN_TEXT_CHARS:
            print(
                f"📄 PDF text extraction berjaya: "
                f"{direct_char_count} chars"
            )

            for page in direct_pages:
                if page["text"]:
                    write_page_cache(
                        book_hash,
                        page["page"],
                        page["text"]
                    )

            return direct_pages

        print(
            "🖨️ PDF kemungkinan scan. "
            "Gunakan OCR."
        )

        return extract_pdf_with_ocr(
            path,
            book_hash
        )

    return []


# =========================================================
# BOOK DISCOVERY
# =========================================================

def discover_books():
    root = "/app/kitab"

    print(
        f"📂 Searching: {root}"
    )

    if not os.path.exists(root):
        print(
            "⚠️ Folder /app/kitab tidak wujud."
        )

        return []

    files = []

    for path in glob.glob(
        root + "/**/*",
        recursive=True
    ):
        if not os.path.isfile(path):
            continue

        ext = Path(path).suffix.lower()

        if ext not in [".pdf", ".txt"]:
            continue

        relative = os.path.relpath(
            path,
            root
        )

        parts = Path(relative).parts

        if len(parts) >= 2:
            category = normalize_category(
                parts[0]
            )
        else:
            category = "FIQH"

        files.append(
            {
                "path": path,
                "category": category or "FIQH",
            }
        )

    print(
        f"📚 Jumlah kitab: {len(files)}"
    )

    return files


# =========================================================
# MANIFEST
# =========================================================

def load_manifest():
    if not os.path.exists(
        MANIFEST_FILE
    ):
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
    try:
        with open(
            MANIFEST_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                manifest,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(
            f"⚠️ Manifest error: {e}"
        )


# =========================================================
# EMBEDDING
# =========================================================

def create_embeddings(texts):
    if not embeddings:
        raise RuntimeError(
            "GOOGLE_API_KEY belum diset."
        )

    all_embeddings = []

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

        result = embeddings.embed_documents(
            batch
        )

        all_embeddings.extend(result)

    return all_embeddings


# =========================================================
# SUPABASE
# =========================================================

def delete_book_from_supabase(book_hash):
    if not supabase:
        return

    try:
        supabase.table(
            "kitab_chunks"
        ).delete().eq(
            "book_hash",
            book_hash
        ).execute()

        supabase.table(
            "books"
        ).delete().eq(
            "book_hash",
            book_hash
        ).execute()

        print(
            f"🗑️ Supabase data deleted: "
            f"{book_hash[:16]}"
        )

    except Exception as e:
        print(
            f"⚠️ Delete Supabase error: {e}"
        )


def process_book(book, manifest):
    path = book["path"]
    category = book["category"]

    filename = os.path.basename(path)

    print()
    print("=" * 60)
    print(
        f"📚 PROCESS: {filename}"
    )
    print(
        f"📂 CATEGORY: {category}"
    )

    try:
        book_hash = sha256_file(path)

    except Exception as e:
        print(
            f"❌ Hash error: {e}"
        )

        return

    print(
        f"🔑 HASH: {book_hash[:16]}"
    )

    previous = manifest.get(
        path
    )

    if previous:
        if previous.get(
            "hash"
        ) == book_hash:
            print(
                f"⏭️ SKIP: {filename} "
                f"(tiada perubahan)"
            )

            return

    print(
        f"🆕 PROCESS: {filename}"
    )

    pages = extract_book_pages(
        path
    )

    if not pages:
        print(
            "⚠️ Tiada halaman ditemui."
        )

        return

    documents = []

    for page_data in pages:
        page_number = page_data["page"]
        text = clean_text(
            page_data["text"]
        )

        if len(text) < MIN_TEXT_CHARS:
            continue

        documents.append(
            {
                "page": page_number,
                "text": text,
            }
        )

    if not documents:
        print(
            "⚠️ Tiada teks mencukupi."
        )

        return

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
        ],
    )

    chunks = []

    for doc in documents:
        split_texts = splitter.split_text(
            doc["text"]
        )

        for index, chunk in enumerate(
            split_texts
        ):
            chunk = clean_text(chunk)

            if not chunk:
                continue

            chunks.append(
                {
                    "text": chunk,
                    "page": doc["page"],
                    "chunk_index": index,
                }
            )

    print(
        f"📦 Jumlah chunks: {len(chunks)}"
    )

    if not chunks:
        return

    if not supabase:
        print(
            "⚠️ Supabase tidak dikonfigurasi."
        )

        return

    delete_book_from_supabase(
        book_hash
    )

    texts = [
        chunk["text"]
        for chunk in chunks
    ]

    try:
        vectors = create_embeddings(
            texts
        )

    except Exception as e:
        print(
            f"❌ Embedding error: {e}"
        )

        return

    # -----------------------------------------------------
    # BOOK
    # -----------------------------------------------------

    book_name = Path(
        filename
    ).stem

    try:
        supabase.table(
            "books"
        ).insert(
            {
                "book_hash": book_hash,
                "book_name": book_name,
                "category": category,
                "file_name": filename,
            }
        ).execute()

    except Exception as e:
        print(
            f"⚠️ Book insert error: {e}"
        )

    # -----------------------------------------------------
    # CHUNKS
    # -----------------------------------------------------

    for index, (chunk, vector) in enumerate(
        zip(chunks, vectors),
        start=1
    ):
        row = {
            "book_hash": book_hash,
            "book_name": book_name,
            "category": category,
            "page": chunk["page"],
            "chunk_index": chunk["chunk_index"],
            "content": chunk["text"],
            "embedding": vector,
        }

        try:
            supabase.table(
                "kitab_chunks"
            ).insert(
                row
            ).execute()

            print(
                f"💾 Supabase "
                f"{index}-{len(chunks)}"
            )

        except Exception as e:
            print(
                f"❌ Chunk insert error "
                f"{index}: {e}"
            )

    manifest[path] = {
        "hash": book_hash,
        "category": category,
        "book_name": book_name,
        "updated": time.time(),
    }

    save_manifest(
        manifest
    )

    print(
        f"✅ READY: {book_name}"
    )


def sync_books():
    print()
    print("=" * 60)
    print("🔄 SYNC KITAB")
    print("=" * 60)

    books = discover_books()

    manifest = load_manifest()

    for book in books:
        try:
            process_book(
                book,
                manifest
            )

        except Exception as e:
            print(
                f"❌ PROCESS ERROR: "
                f"{book.get('path')}: {e}"
            )

    print()
    print(
        "🏁 SYNC SELESAI"
    )


# =========================================================
# LOCAL SUPABASE VECTOR SEARCH
# =========================================================

def search_supabase(
    question,
    category=None,
    match_count=None
):
    if not supabase:
        return []

    if not embeddings:
        return []

    match_count = (
        match_count
        or SEARCH_K
    )

    try:
        query_vector = embeddings.embed_query(
            question
        )

    except Exception as e:
        print(
            f"❌ Query embedding error: {e}"
        )

        return []

    try:
        params = {
            "query_embedding": query_vector,
            "match_count": match_count,
        }

        if category:
            params[
                "filter_category"
            ] = category

        response = supabase.rpc(
            "match_kitab_chunks",
            params
        ).execute()

        rows = response.data or []

        results = []

        for row in rows:
            results.append(
                {
                    "source_type": "local",
                    "content": row.get(
                        "content",
                        ""
                    ),
                    "kitab_name": row.get(
                        "book_name",
                        "Kitab Tempatan"
                    ),
                    "category": row.get(
                        "category"
                    ),
                    "page": row.get(
                        "page"
                    ),
                    "score": row.get(
                        "similarity"
                    ),
                }
            )

        print(
            f"📚 LOCAL SEARCH: "
            f"{len(results)} result"
        )

        return results

    except Exception as e:
        print(
            f"❌ Supabase search error: {e}"
        )

        return []


# =========================================================
# TURATH HTTP SERVICE
# =========================================================

def turath_request(
    endpoint,
    params=None,
    timeout=None
):
    timeout = (
        timeout
        or TURATH_TIMEOUT
    )

    params = params or {}

    query = urllib.parse.urlencode(
        {
            k: str(v)
            for k, v in params.items()
            if v is not None
        }
    )

    url = (
        TURATH_SERVICE_URL.rstrip("/")
        + endpoint
    )

    if query:
        url += "?" + query

    print(
        f"🌐 TURATH REQUEST: {url}"
    )

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "TanyaFiqhBot/1.0"
            ),
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read()

            return json.loads(
                raw.decode(
                    "utf-8"
                )
            )

    except Exception as e:
        print(
            f"❌ TURATH REQUEST ERROR: "
            f"{e}"
        )

        return None


def turath_search(query):
    if not TURATH_ENABLED:
        return None

    return turath_request(
        "/search",
        {
            "q": query[:500]
        },
        timeout=TURATH_TIMEOUT
    )


def turath_get_page(
    book_id,
    page
):
    if not book_id or page is None:
        return None

    return turath_request(
        f"/page/{book_id}/{page}",
        timeout=TURATH_TIMEOUT
    )


def turath_get_book(book_id):
    if not book_id:
        return None

    return turath_request(
        f"/book/{book_id}",
        timeout=TURATH_TIMEOUT
    )


# =========================================================
# TURATH CACHE
# =========================================================

def turath_cache_file(query):
    key = hashlib.sha256(
        query.encode(
            "utf-8"
        )
    ).hexdigest()

    return os.path.join(
        TURATH_CACHE_DIR,
        key + ".json"
    )


def read_turath_cache(query):
    path = turath_cache_file(
        query
    )

    if not os.path.exists(path):
        return None

    try:
        age = (
            time.time()
            - os.path.getmtime(path)
        )

        if age > TURATH_CACHE_TTL:
            return None

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return None


def write_turath_cache(
    query,
    data
):
    path = turath_cache_file(
        query
    )

    try:
        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False
            )

    except Exception as e:
        print(
            f"⚠️ Turath cache error: {e}"
        )


# =========================================================
# TURATH EXTRACTION
# =========================================================

TEXT_KEYS = [
    "content",
    "text",
    "snippet",
    "excerpt",
    "passage",
    "body",
    "matchedText",
    "matchText",
    "highlight",
    "html",
]


BOOK_ID_KEYS = [
    "bookId",
    "book_id",
    "bookID",
    "book",
]


PAGE_KEYS = [
    "page",
    "pageNumber",
    "page_number",
    "pageNo",
    "page_num",
    "printedPage",
    "printed_page",
    "pageId",
    "page_id",
    "internalPage",
]


TITLE_KEYS = [
    "bookTitle",
    "book_title",
    "title",
    "name",
]


AUTHOR_KEYS = [
    "authorName",
    "author_name",
    "author",
]


def extract_turath_passages(
    result,
    limit=5
):
    """
    Ambil passage sebenar daripada hasil Turath.

    Kita tidak menganggap struktur JSON tertentu.
    Fungsi ini berjalan secara recursive kerana
    envelope API boleh berubah.
    """

    if not result:
        return []

    passages = []

    seen = set()

    root = result

    # Kalau wrapper kita ialah:
    # {"ok": true, "query": "...", "result": ...}
    if isinstance(root, dict):
        if "result" in root:
            root = root["result"]

    for d in recursive_dicts(root):

        text_value = None

        for key in TEXT_KEYS:
            if key in d:
                value = d.get(key)

                if isinstance(
                    value,
                    str
                ):
                    cleaned = clean_text(
                        value
                    )

                    if len(cleaned) >= 20:
                        text_value = cleaned
                        break

        if not text_value:
            continue

        # ---------------------------------------------
        # BOOK ID
        # ---------------------------------------------

        book_id = first_value(
            d,
            BOOK_ID_KEYS
        )

        if isinstance(
            book_id,
            dict
        ):
            book_id = first_value(
                book_id,
                [
                    "id",
                    "bookId",
                    "book_id",
                ]
            )

        book_id = ensure_int(
            book_id
        )

        # ---------------------------------------------
        # PAGE
        # ---------------------------------------------

        page = first_value(
            d,
            PAGE_KEYS
        )

        if isinstance(
            page,
            dict
        ):
            page = first_value(
                page,
                [
                    "number",
                    "page",
                    "pageNumber",
                    "id",
                ]
            )

        page = ensure_int(
            page
        )

        # ---------------------------------------------
        # TITLE
        # ---------------------------------------------

        title = first_value(
            d,
            TITLE_KEYS
        )

        if isinstance(
            title,
            dict
        ):
            title = first_value(
                title,
                [
                    "title",
                    "name",
                ]
            )

        title = (
            str(title).strip()
            if title
            else "Turath"
        )

        # ---------------------------------------------
        # AUTHOR
        # ---------------------------------------------

        author = first_value(
            d,
            AUTHOR_KEYS
        )

        if isinstance(
            author,
            dict
        ):
            author = first_value(
                author,
                [
                    "name",
                    "fullName",
                    "full_name",
                ]
            )

        if author:
            author = str(
                author
            ).strip()

        # ---------------------------------------------
        # DEDUPE
        # ---------------------------------------------

        dedupe_key = (
            normalize_for_dedupe(
                text_value
            )
        )

        if dedupe_key in seen:
            continue

        seen.add(
            dedupe_key
        )

        passages.append(
            {
                "source_type": "turath",
                "content": text_value,
                "kitab_name": title,
                "author": author,
                "book_id": book_id,
                "page": page,
                "url": (
                    f"https://app.turath.io/book/"
                    f"{book_id}"
                    if book_id
                    else None
                ),
            }
        )

        if len(passages) >= limit:
            break

    return passages


def extract_turath_candidates(
    result,
    limit=5
):
    """
    Ambil book_id/page daripada search result
    walaupun search result tidak memberikan
    passage secara terus.
    """

    if not result:
        return []

    root = result

    if isinstance(root, dict):
        if "result" in root:
            root = root["result"]

    candidates = []

    seen = set()

    for d in recursive_dicts(root):

        book_id = first_value(
            d,
            BOOK_ID_KEYS
        )

        if isinstance(
            book_id,
            dict
        ):
            book_id = first_value(
                book_id,
                [
                    "id",
                    "bookId",
                    "book_id",
                ]
            )

        book_id = ensure_int(
            book_id
        )

        if not book_id:
            continue

        page = first_value(
            d,
            PAGE_KEYS
        )

        if isinstance(
            page,
            dict
        ):
            page = first_value(
                page,
                [
                    "number",
                    "page",
                    "pageNumber",
                    "id",
                ]
            )

        page = ensure_int(
            page
        )

        title = first_value(
            d,
            TITLE_KEYS
        )

        if isinstance(
            title,
            dict
        ):
            title = first_value(
                title,
                [
                    "title",
                    "name",
                ]
            )

        title = (
            str(title).strip()
            if title
            else "Turath"
        )

        author = first_value(
            d,
            AUTHOR_KEYS
        )

        if isinstance(
            author,
            dict
        ):
            author = first_value(
                author,
                [
                    "name",
                    "fullName",
                    "full_name",
                ]
            )

        key = (
            book_id,
            page
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        candidates.append(
            {
                "book_id": book_id,
                "page": page,
                "kitab_name": title,
                "author": author,
            }
        )

        if len(candidates) >= limit:
            break

    return candidates


def extract_page_text(
    page_result
):
    """
    Ambil teks daripada response getPage.
    """

    if not page_result:
        return ""

    root = page_result

    if isinstance(root, dict):
        if "result" in root:
            root = root["result"]

    # Cuba key yang biasa dahulu
    for key in [
        "text",
        "content",
        "body",
        "html",
    ]:
        value = recursive_find_value(
            root,
            [key]
        )

        if isinstance(
            value,
            str
        ):
            cleaned = clean_text(
                value
            )

            if len(cleaned) >= 20:
                return cleaned

    # Fallback: cari string panjang
    # dalam struktur response
    candidates = []

    def walk(value):
        if isinstance(
            value,
            str
        ):
            cleaned = clean_text(
                value
            )

            if len(cleaned) >= 50:
                candidates.append(
                    cleaned
                )

        elif isinstance(
            value,
            dict
        ):
            for v in value.values():
                walk(v)

        elif isinstance(
            value,
            list
        ):
            for v in value:
                walk(v)

    walk(root)

    if not candidates:
        return ""

    candidates.sort(
        key=len,
        reverse=True
    )

    return candidates[0]


def extract_page_metadata(
    page_result
):
    """
    Cuba ambil title, author dan page
    daripada getPage.
    """

    if not page_result:
        return {}

    root = page_result

    if isinstance(root, dict):
        if "result" in root:
            root = root["result"]

    title = recursive_find_value(
        root,
        TITLE_KEYS
    )

    author = recursive_find_value(
        root,
        AUTHOR_KEYS
    )

    page = recursive_find_value(
        root,
        PAGE_KEYS
    )

    return {
        "kitab_name": (
            str(title).strip()
            if title
            else None
        ),
        "author": (
            str(author).strip()
            if author
            else None
        ),
        "page": ensure_int(
            page
        ),
    }


# =========================================================
# TURATH SEARCH → PAGE
# =========================================================

def search_turath_passages(
    question,
    limit=None
):
    """
    ALIRAN UTAMA TURATH:

    1. search(query)
    2. Cuba ambil passage terus
    3. Jika search hanya beri book/page,
       panggil getPage(book_id, page)
    4. Gabungkan teks sebenar
    """

    if not TURATH_ENABLED:
        print(
            "ℹ️ TURATH disabled."
        )

        return []

    limit = (
        limit
        or TURATH_SEARCH_K
    )

    question = clean_text(
        question
    )

    if not question:
        return []

    print()
    print(
        "🔎 TURATH SEARCH:"
        f" {question}"
    )

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    cached = read_turath_cache(
        question
    )

    if cached is not None:
        print(
            "💾 TURATH CACHE HIT"
        )

        return cached[:limit]

    # -----------------------------------------------------
    # SEARCH
    # -----------------------------------------------------

    search_result = turath_search(
        question
    )

    if not search_result:
        print(
            "⚠️ Turath search tiada response."
        )

        return []

    # -----------------------------------------------------
    # STEP 1
    # SEARCH RESULT TERUS ADA TEXT
    # -----------------------------------------------------

    passages = extract_turath_passages(
        search_result,
        limit=limit
    )

    print(
        f"📖 TURATH DIRECT PASSAGES: "
        f"{len(passages)}"
    )

    # -----------------------------------------------------
    # STEP 2
    # JIKA TIADA TEXT, CARI BOOK/PAGE
    # -----------------------------------------------------

    candidates = extract_turath_candidates(
        search_result,
        limit=limit * 2
    )

    print(
        f"📚 TURATH CANDIDATES: "
        f"{len(candidates)}"
    )

    # -----------------------------------------------------
    # Kalau direct passages ada tetapi
    # metadata kurang, cuba lengkapkan metadata
    # -----------------------------------------------------

    for passage in passages:

        if (
            passage.get("book_id")
            and
            passage.get("page")
        ):
            continue

        # Cari candidate yang paling dekat
        # berdasarkan kitab jika ada.
        for candidate in candidates:

            if passage.get(
                "kitab_name"
            ) == candidate.get(
                "kitab_name"
            ):
                passage[
                    "book_id"
                ] = candidate.get(
                    "book_id"
                )

                passage[
                    "page"
                ] = candidate.get(
                    "page"
                )

                break

    # -----------------------------------------------------
    # Ambil halaman sebenar untuk candidate
    # -----------------------------------------------------

    existing_keys = set()

    for p in passages:
        key = (
            p.get("book_id"),
            p.get("page")
        )

        existing_keys.add(
            key
        )

    for candidate in candidates:

        if len(passages) >= limit:
            break

        book_id = candidate.get(
            "book_id"
        )

        page = candidate.get(
            "page"
        )

        if not book_id:
            continue

        if page is None:
            continue

        key = (
            book_id,
            page
        )

        if key in existing_keys:
            continue

        print(
            "📖 TURATH GET PAGE: "
            f"book={book_id}, "
            f"page={page}"
        )

        page_result = turath_get_page(
            book_id,
            page
        )

        if not page_result:
            continue

        text = extract_page_text(
            page_result
        )

        if len(text) < 20:
            print(
                "⚠️ Turath page tiada "
                "teks yang mencukupi."
            )

            continue

        metadata = extract_page_metadata(
            page_result
        )

        title = (
            metadata.get(
                "kitab_name"
            )
            or candidate.get(
                "kitab_name"
            )
            or "Turath"
        )

        author = (
            metadata.get(
                "author"
            )
            or candidate.get(
                "author"
            )
        )

        real_page = (
            metadata.get(
                "page"
            )
            or page
        )

        passage = {
            "source_type": "turath",
            "content": text,
            "kitab_name": title,
            "author": author,
            "book_id": book_id,
            "page": real_page,
            "url": (
                f"https://app.turath.io/book/"
                f"{book_id}"
            ),
        }

        # Dedupe
        key_text = normalize_for_dedupe(
            text
        )

        if any(
            normalize_for_dedupe(
                p.get("content", "")
            ) == key_text
            for p in passages
        ):
            continue

        passages.append(
            passage
        )

        existing_keys.add(
            key
        )

    # -----------------------------------------------------
    # Potong teks Turath
    # -----------------------------------------------------

    final = []

    seen = set()

    for passage in passages:

        content = clean_text(
            passage.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        key = normalize_for_dedupe(
            content
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        passage["content"] = (
            content[
                :TURATH_CONTEXT_LIMIT
            ]
        )

        final.append(
            passage
        )

        if len(final) >= limit:
            break

    print(
        f"✅ TURATH FINAL: "
        f"{len(final)} passages"
    )

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    if final:
        write_turath_cache(
            question,
            final
        )

    return final


# =========================================================
# TURATH BOOK EXTRACTION
# Untuk endpoint /turath lama
# =========================================================

def extract_turath_books(
    result,
    limit=20
):
    books = []

    seen = set()

    if not result:
        return books

    root = result

    if isinstance(root, dict):
        if "result" in root:
            root = root["result"]

    for d in recursive_dicts(root):

        book_id = first_value(
            d,
            BOOK_ID_KEYS
        )

        if isinstance(
            book_id,
            dict
        ):
            book_id = first_value(
                book_id,
                [
                    "id",
                    "bookId",
                    "book_id",
                ]
            )

        book_id = ensure_int(
            book_id
        )

        if not book_id:
            continue

        title = first_value(
            d,
            TITLE_KEYS
        )

        if isinstance(
            title,
            dict
        ):
            title = first_value(
                title,
                [
                    "title",
                    "name",
                ]
            )

        title = (
            str(title)
            if title
            else "Turath"
        )

        author = first_value(
            d,
            AUTHOR_KEYS
        )

        if isinstance(
            author,
            dict
        ):
            author = first_value(
                author,
                [
                    "name",
                    "fullName",
                ]
            )

        key = (
            book_id,
            title
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        books.append(
            {
                "book_id": book_id,
                "title": title,
                "author": author,
                "url": (
                    f"https://app.turath.io/book/"
                    f"{book_id}"
                ),
            }
        )

        if len(books) >= limit:
            break

    return books


# =========================================================
# CONTEXT BUILDER
# =========================================================

def build_context(
    results
):
    blocks = []

    total_chars = 0

    for index, result in enumerate(
        results,
        start=1
    ):
        source_type = result.get(
            "source_type",
            "local"
        )

        content = clean_text(
            result.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        if source_type == "turath":

            content = content[
                :TURATH_CONTEXT_LIMIT
            ]

            kitab = result.get(
                "kitab_name"
            ) or "Turath"

            author = result.get(
                "author"
            )

            page = result.get(
                "page"
            )

            header = (
                f"[SUMBER TURATH #{index}]\n"
                f"Kitab: {kitab}"
            )

            if author:
                header += (
                    f"\nPengarang: {author}"
                )

            if page:
                header += (
                    f"\nHalaman: {page}"
                )

        else:

            content = content[
                :LOCAL_CONTEXT_LIMIT
            ]

            kitab = result.get(
                "kitab_name"
            ) or "Kitab Tempatan"

            page = result.get(
                "page"
            )

            category = result.get(
                "category"
            )

            header = (
                f"[SUMBER KITAB TEMPATAN #{index}]\n"
                f"Kitab: {kitab}"
            )

            if category:
                header += (
                    f"\nKategori: {category}"
                )

            if page:
                header += (
                    f"\nHalaman: {page}"
                )

        block = (
            header
            + "\n"
            + "Teks:\n"
            + content
        )

        if (
            total_chars
            + len(block)
            > TOTAL_CONTEXT_LIMIT
        ):
            break

        blocks.append(
            block
        )

        total_chars += len(block)

    return "\n\n".join(
        blocks
    )


# =========================================================
# SOURCE FORMAT
# =========================================================

def format_sources(
    results
):
    lines = []

    seen = set()

    for result in results:

        source_type = result.get(
            "source_type"
        )

        if source_type == "turath":

            book_id = result.get(
                "book_id"
            )

            page = result.get(
                "page"
            )

            kitab = result.get(
                "kitab_name"
            ) or "Turath"

            author = result.get(
                "author"
            )

            url = result.get(
                "url"
            )

            key = (
                "turath",
                book_id,
                page,
                kitab
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            line = f"• {kitab}"

            if author:
                line += (
                    f" — {author}"
                )

            if page:
                line += (
                    f", halaman {page}"
                )

            if url:
                line += (
                    f"\n  {url}"
                )

            lines.append(
                line
            )

        else:

            kitab = result.get(
                "kitab_name"
            ) or "Kitab Tempatan"

            page = result.get(
                "page"
            )

            category = result.get(
                "category"
            )

            key = (
                "local",
                kitab,
                page
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            line = f"• {kitab}"

            if category:
                line += (
                    f" [{category}]"
                )

            if page:
                line += (
                    f", halaman {page}"
                )

            lines.append(
                line
            )

    return "\n".join(
        lines
    )


# =========================================================
# GEMINI ANSWER
# =========================================================

def generate_answer(
    question,
    results
):
    if not llm:
        return (
            "❌ Gemini belum dikonfigurasi."
        )

    context = build_context(
        results
    )

    if not context:
        return (
            "❌ Tiada kandungan rujukan "
            "yang boleh digunakan."
        )

    prompt = f"""
Anda ialah TanyaFiqhBot, pembantu rujukan
ilmu Islam dalam Bahasa Melayu.

Soalan pengguna:
{question}

Berikut ialah sumber yang ditemui daripada
kitab tempatan dan/atau Turath.

{context}

ARAHAN PENTING:

1. Jawab dalam Bahasa Melayu yang jelas.
2. Jawapan mesti berdasarkan teks sumber yang
   diberikan di atas.
3. Jangan reka nama kitab, pengarang, halaman
   atau fakta yang tiada dalam sumber.
4. Jika sumber tidak mencukupi untuk menentukan
   jawapan, nyatakan bahawa maklumat sumber
   tidak mencukupi.
5. Bezakan antara pendapat ulama jika teks sumber
   menunjukkan lebih daripada satu pendapat.
6. Jangan mendakwa sesuatu sebagai ijmak jika
   sumber tidak menyatakan demikian.
7. Jangan gunakan pengetahuan luar untuk
   menggantikan sumber yang diberikan.
8. Jangan buat bahagian "Rujukan" sendiri.
   Sistem akan menambah rujukan secara automatik.
9. Jika sumber Turath digunakan, utamakan teks
   Turath yang diberikan sebagai sumber primer.
10. Jika soalan meminta hukum, terangkan hukum
    berdasarkan teks dan nyatakan ringkas dalil
    atau alasan jika memang terdapat dalam sumber.

Format jawapan:

Jawapan:
...

Penjelasan:
...

Jika sumber tidak mencukupi, beritahu dengan
jelas bahawa rujukan yang ditemui belum mencukupi.
"""

    try:
        response = llm.invoke(
            [
                HumanMessage(
                    content=prompt
                )
            ]
        )

        if hasattr(
            response,
            "content"
        ):
            answer = response.content
        else:
            answer = str(response)

        return clean_text(
            answer
        )

    except Exception as e:
        print(
            f"❌ LLM error: {e}"
        )

        return (
            "❌ Berlaku masalah ketika "
            "menjana jawapan."
        )


# =========================================================
# TELEGRAM MESSAGE SPLITTER
# =========================================================

async def send_long_message(
    update,
    text,
    max_length=3800
):
    if not text:
        return

    text = str(text)

    while len(text) > max_length:

        split_at = text.rfind(
            "\n",
            0,
            max_length
        )

        if split_at < 1000:
            split_at = max_length

        part = text[
            :split_at
        ]

        await update.message.reply_text(
            part
        )

        text = text[
            split_at:
        ].lstrip()

    if text:
        await update.message.reply_text(
            text
        )


# =========================================================
# ANSWER QUESTION
# =========================================================

async def answer_question(
    update,
    question,
    category=None
):
    question = clean_text(
        question
    )

    if not question:
        return

    category = normalize_category(
        category
    )

    print()
    print("=" * 60)
    print(
        "❓ QUESTION:"
        f" {question}"
    )

    if category:
        print(
            f"📂 CATEGORY: {category}"
        )

    try:
        await update.message.reply_text(
            "🔎 Sedang mencari rujukan "
            "kitab tempatan + Turath..."
        )
    except Exception:
        pass

    # =====================================================
    # SEARCH LOCAL
    # =====================================================

    local_results = search_supabase(
        question,
        category=category,
        match_count=SEARCH_K
    )

    # =====================================================
    # SEARCH TURATH
    # =====================================================

    turath_results = []

    try:
        turath_results = search_turath_passages(
            question,
            limit=TURATH_SEARCH_K
        )

    except Exception as e:
        print(
            f"❌ Turath search exception: {e}"
        )

    # =====================================================
    # COMBINE
    # =====================================================

    results = (
        local_results
        + turath_results
    )

    print()
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
        f"{len(results)}"
    )

    if not results:

        await send_long_message(
            update,
            "❌ Maaf, saya tidak menemui "
            "rujukan yang sesuai dalam "
            "kitab tempatan atau Turath."
        )

        return

    # =====================================================
    # GENERATE ANSWER
    # =====================================================

    answer = generate_answer(
        question,
        results
    )

    # =====================================================
    # SOURCES
    # =====================================================

    sources = format_sources(
        results
    )

    if sources:
        answer += (
            "\n\n📚 Rujukan:\n"
            + sources
        )

    # =====================================================
    # SOURCE STATUS
    # =====================================================

    source_count = []

    if local_results:
        source_count.append(
            f"Kitab tempatan: "
            f"{len(local_results)}"
        )

    if turath_results:
        source_count.append(
            f"Turath: "
            f"{len(turath_results)}"
        )

    if source_count:
        answer += (
            "\n\n🔎 Sumber ditemui: "
            + " | ".join(
                source_count
            )
        )

    await send_long_message(
        update,
        answer
    )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = """
🤖 TanyaFiqhBot

Pembantu rujukan ilmu Islam
berdasarkan kitab.

📚 Sumber:
• Kitab tempatan
• Turath

Kategori:
• FIQH
• TAUHID
• HADIS
• TAFSIR
• SIRAH
• AKHLAK
• USUL FIQH

Contoh:

/fiqh Apakah hukum qunut Subuh?

/hadis Apakah hadis tentang niat?

/tafsir Apakah maksud ayat Kursi?

Atau terus taip soalan anda.
"""

    await update.message.reply_text(
        text
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    turath_status = (
        "ON"
        if TURATH_ENABLED
        else "OFF"
    )

    supabase_status = (
        "ON"
        if supabase
        else "OFF"
    )

    gemini_status = (
        "ON"
        if llm
        else "OFF"
    )

    text = f"""
🤖 TanyaFiqhBot Status

Gemini: {gemini_status}
Supabase: {supabase_status}
Turath: {turath_status}

LLM:
{LLM_MODEL}

Embedding:
{EMBEDDING_MODEL}

Turath service:
{TURATH_SERVICE_URL}
"""

    await update.message.reply_text(
        text
    )


async def category_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category
):
    if context.args:
        question = " ".join(
            context.args
        )
    else:
        await update.message.reply_text(
            f"Taip soalan selepas /"
            f"{category.lower().replace(' ', '')}."
        )

        return

    await answer_question(
        update,
        question,
        category
    )


async def fiqh_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "FIQH"
    )


async def tauhid_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "TAUHID"
    )


async def hadis_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "HADIS"
    )


async def tafsir_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "TAFSIR"
    )


async def sirah_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "SIRAH"
    )


async def akhlak_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "AKHLAK"
    )


async def usul_command(
    update,
    context
):
    await category_command(
        update,
        context,
        "USUL FIQH"
    )


async def message_handler(
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

    category = detect_category_from_text(
        question
    )

    # Buang command kategori daripada soalan
    question = re.sub(
        r"^/(fiqh|tauhid|akidah|hadis|hadith|tafsir|sirah|akhlak|usul)\b",
        "",
        question,
        flags=re.I
    ).strip()

    await answer_question(
        update,
        question,
        category
    )


# =========================================================
# FLASK ROUTES
# =========================================================

@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "service": "TanyaFiqhBot",
            "turath": TURATH_ENABLED,
            "llm_model": LLM_MODEL,
        }
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "TanyaFiqhBot",
            "turath_enabled": TURATH_ENABLED,
        }
    )


@app.route("/status")
def status():
    return jsonify(
        {
            "ok": True,
            "gemini": bool(llm),
            "embeddings": bool(
                embeddings
            ),
            "supabase": bool(
                supabase
            ),
            "turath": TURATH_ENABLED,
            "turath_service": TURATH_SERVICE_URL,
        }
    )


@app.route("/books")
def books_route():
    books = discover_books()

    output = []

    for book in books:
        output.append(
            {
                "file": os.path.basename(
                    book["path"]
                ),
                "category": book[
                    "category"
                ],
                "path": book[
                    "path"
                ],
            }
        )

    return jsonify(
        {
            "ok": True,
            "count": len(output),
            "books": output,
        }
    )


@app.route("/turath")
def turath_route():
    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Parameter q diperlukan"
                ),
            }
        ), 400

    result = turath_search(
        query
    )

    passages = extract_turath_passages(
        result,
        limit=TURATH_SEARCH_K
    )

    candidates = extract_turath_candidates(
        result,
        limit=TURATH_SEARCH_K
    )

    return jsonify(
        {
            "ok": True,
            "query": query,
            "passages": passages,
            "candidates": candidates,
            "raw": result,
        }
    )


@app.route(
    "/turath/book/<book_id>"
)
def turath_book_route(
    book_id
):
    book_id_int = ensure_int(
        book_id
    )

    if not book_id_int:
        return jsonify(
            {
                "ok": False,
                "error": "Book ID tidak sah",
            }
        ), 400

    result = turath_get_book(
        book_id_int
    )

    return jsonify(
        {
            "ok": True,
            "book_id": book_id_int,
            "result": result,
        }
    )


@app.route(
    "/turath/page/<book_id>/<page>"
)
def turath_page_route(
    book_id,
    page
):
    book_id_int = ensure_int(
        book_id
    )

    page_int = ensure_int(
        page
    )

    if not book_id_int or page_int is None:
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Book ID atau page "
                    "tidak sah"
                ),
            }
        ), 400

    result = turath_get_page(
        book_id_int,
        page_int
    )

    text = extract_page_text(
        result
    )

    metadata = extract_page_metadata(
        result
    )

    return jsonify(
        {
            "ok": True,
            "book_id": book_id_int,
            "page": page_int,
            "text": text,
            "metadata": metadata,
            "raw": result,
        }
    )


# =========================================================
# TELEGRAM STARTUP
# =========================================================

telegram_application = None


def telegram_worker():
    global telegram_application

    if not TELEGRAM_TOKEN:
        print(
            "⚠️ TELEGRAM_TOKEN belum diset."
        )

        return

    try:
        telegram_application = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        telegram_application.add_handler(
            CommandHandler(
                "start",
                start_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "status",
                status_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "fiqh",
                fiqh_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "tauhid",
                tauhid_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "hadis",
                hadis_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "tafsir",
                tafsir_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "sirah",
                sirah_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "akhlak",
                akhlak_command
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "usul",
                usul_command
            )
        )

        telegram_application.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                message_handler
            )
        )

        print(
            "✅ Telegram polling started"
        )

        # Penting:
        # stop_signals=None kerana polling
        # berjalan dalam background thread.
        telegram_application.run_polling(
            stop_signals=None
        )

    except Exception as e:
        print(
            f"❌ Telegram worker error: {e}"
        )


# =========================================================
# STARTUP
# =========================================================

def startup():
    print()
    print("=" * 60)
    print(
        "🚀 START TanyaFiqhBot"
    )
    print("=" * 60)

    print(
        f"🤖 LLM: {LLM_MODEL}"
    )

    print(
        f"🧠 Embedding: "
        f"{EMBEDDING_MODEL}"
    )

    print(
        f"🌐 Turath: "
        f"{TURATH_ENABLED}"
    )

    print(
        f"🔗 Turath service: "
        f"{TURATH_SERVICE_URL}"
    )

    print(
        f"💾 DATA_DIR: "
        f"{DATA_DIR}"
    )

    # Sync kitab tempatan
    try:
        sync_books()

    except Exception as e:
        print(
            f"❌ Sync error: {e}"
        )

    # Telegram
    thread = threading.Thread(
        target=telegram_worker,
        daemon=True
    )

    thread.start()


# =========================================================
# RUN
# =========================================================

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
