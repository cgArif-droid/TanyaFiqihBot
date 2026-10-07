import os
import re
import gc
import json
import glob
import time
import hashlib
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from langchain_core.messages import HumanMessage

from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)

from langchain_text_splitters import RecursiveCharacterTextSplitter

from pypdf import PdfReader

from pdf2image import convert_from_path
import pytesseract

from supabase import create_client


# ============================================================
# CONFIG
# ============================================================

GOOGLE_API_KEY = os.environ.get(
    "GOOGLE_API_KEY",
    ""
)

TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN",
    ""
)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
)

SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    ""
)

DATA_DIR = os.environ.get(
    "DATA_DIR",
    "/var/data"
)

OCR_WORKERS = int(
    os.environ.get(
        "OCR_WORKERS",
        "1"
    )
)

OCR_DPI = int(
    os.environ.get(
        "OCR_DPI",
        "200"
    )
)

MIN_TEXT_CHARS = 40

LLM_MODEL = "gemini-2.5-flash"

EMBEDDING_MODEL = "models/gemini-embedding-001"

EMBEDDING_DIMENSION = 3072

SEARCH_K = 6

EMBEDDING_BATCH_SIZE = 16

MAX_CONTEXT_CHARS = 24000


# ============================================================
# DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

KITAB_DIR = os.path.join(
    BASE_DIR,
    "kitab"
)

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


os.makedirs(
    DATA_DIR,
    exist_ok=True
)

os.makedirs(
    EXTRACTED_DIR,
    exist_ok=True
)

os.makedirs(
    PAGE_CACHE_DIR,
    exist_ok=True
)


# ============================================================
# SUPABASE
# ============================================================

supabase = None


def create_supabase():

    global supabase

    if supabase is not None:

        return supabase

    if not SUPABASE_URL:

        raise RuntimeError(
            "SUPABASE_URL belum ditetapkan."
        )

    if not SUPABASE_KEY:

        raise RuntimeError(
            "SUPABASE_KEY belum ditetapkan."
        )

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )

    return supabase


# ============================================================
# GLOBAL STATUS
# ============================================================

INDEX_READY = False

INDEX_STATUS = "starting"

INDEX_PROGRESS = {
    "book": "",
    "page": 0,
    "total_pages": 0,
    "message": "",
}

FAILED_BOOKS = []

INDEX_LOCK = threading.Lock()

OCR_LANGUAGE = None

OCR_LANGUAGE_LOCK = threading.Lock()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    with INDEX_LOCK:

        status = INDEX_STATUS
        ready = INDEX_READY

    return jsonify(
        {
            "name": "TanyaFiqhBot",
            "status": status,
            "index_ready": ready,
        }
    )


@app.route("/health")
def health():

    with INDEX_LOCK:

        progress = dict(
            INDEX_PROGRESS
        )

        status = INDEX_STATUS

        ready = INDEX_READY

        failed = list(
            FAILED_BOOKS
        )

    return jsonify(
        {
            "status": status,
            "index_ready": ready,
            "progress": progress,
            "failed_books": failed,
        }
    )


# ============================================================
# STATUS
# ============================================================

def update_status(
    status=None,
    book=None,
    page=None,
    total_pages=None,
    message=None,
):

    global INDEX_STATUS

    with INDEX_LOCK:

        if status is not None:

            INDEX_STATUS = status

        if book is not None:

            INDEX_PROGRESS[
                "book"
            ] = book

        if page is not None:

            INDEX_PROGRESS[
                "page"
            ] = page

        if total_pages is not None:

            INDEX_PROGRESS[
                "total_pages"
            ] = total_pages

        if message is not None:

            INDEX_PROGRESS[
                "message"
            ] = message


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text):

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
# FILE HASH
# ============================================================

def calculate_file_hash(path):

    sha = hashlib.sha256()

    with open(
        path,
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:

                break

            sha.update(
                chunk
            )

    return sha.hexdigest()


# ============================================================
# MANIFEST
# ============================================================

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
        ) as file:

            return json.load(
                file
            )

    except Exception as e:

        print(
            "Manifest error:",
            e
        )

        return {}


def save_manifest(manifest):

    temp = (
        MANIFEST_FILE
        + ".tmp"
    )

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                manifest,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp,
            MANIFEST_FILE
        )

    except Exception as e:

        print(
            "Manifest save error:",
            e
        )


# ============================================================
# FIND BOOKS
# ============================================================

def find_books():

    if not os.path.exists(
        KITAB_DIR
    ):

        print(
            "Folder kitab tidak wujud:",
            KITAB_DIR
        )

        return []

    patterns = [

        os.path.join(
            KITAB_DIR,
            "**",
            "*.pdf"
        ),

        os.path.join(
            KITAB_DIR,
            "**",
            "*.PDF"
        ),

        os.path.join(
            KITAB_DIR,
            "**",
            "*.txt"
        ),

        os.path.join(
            KITAB_DIR,
            "**",
            "*.TXT"
        ),

    ]

    files = []

    for pattern in patterns:

        files.extend(
            glob.glob(
                pattern,
                recursive=True
            )
        )

    return sorted(
        set(files)
    )


# ============================================================
# CATEGORY
# ============================================================

def get_category(path):

    try:

        relative = os.path.relpath(
            path,
            KITAB_DIR
        )

        parts = relative.split(
            os.sep
        )

        if len(parts) >= 2:

            return parts[0].upper()

    except Exception:

        pass

    return "LAIN-LAIN"


# ============================================================
# OCR LANGUAGE
# ============================================================

def get_ocr_language():

    global OCR_LANGUAGE

    if OCR_LANGUAGE:

        return OCR_LANGUAGE

    with OCR_LANGUAGE_LOCK:

        if OCR_LANGUAGE:

            return OCR_LANGUAGE

        try:

            languages = (
                pytesseract
                .get_languages(
                    config=""
                )
            )

            selected = []

            if "msa" in languages:

                selected.append(
                    "msa"
                )

            if "ara" in languages:

                selected.append(
                    "ara"
                )

            if "eng" in languages:

                selected.append(
                    "eng"
                )

            if selected:

                OCR_LANGUAGE = "+".join(
                    selected
                )

            else:

                OCR_LANGUAGE = "eng"

        except Exception as e:

            print(
                "OCR language error:",
                e
            )

            OCR_LANGUAGE = "eng"

    print(
        "OCR language:",
        OCR_LANGUAGE
    )

    return OCR_LANGUAGE


# ============================================================
# PAGE CACHE
# ============================================================

def get_page_cache_path(
    book_hash,
    page_number
):

    folder = os.path.join(
        PAGE_CACHE_DIR,
        book_hash
    )

    os.makedirs(
        folder,
        exist_ok=True
    )

    return os.path.join(
        folder,
        f"{page_number}.txt"
    )


def load_page_cache(
    book_hash,
    page_number
):

    path = get_page_cache_path(
        book_hash,
        page_number
    )

    if not os.path.exists(
        path
    ):

        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            text = file.read()

        text = normalize_text(
            text
        )

        if text:

            return text

    except Exception as e:

        print(
            "Cache read error:",
            e
        )

    return None


def save_page_cache(
    book_hash,
    page_number,
    text
):

    text = normalize_text(
        text
    )

    if not text:

        return False

    path = get_page_cache_path(
        book_hash,
        page_number
    )

    temp = path + ".tmp"

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                text
            )

        os.replace(
            temp,
            path
        )

        return True

    except Exception as e:

        print(
            "Cache save error:",
            e
        )

        return False


# ============================================================
# WHOLE TEXT CACHE
# ============================================================

def get_whole_text_path(book_hash):

    return os.path.join(
        EXTRACTED_DIR,
        f"{book_hash}.txt"
    )


def load_whole_text(book_hash):

    path = get_whole_text_path(
        book_hash
    )

    if not os.path.exists(
        path
    ):

        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            text = file.read()

        text = normalize_text(
            text
        )

        if text:

            return text

    except Exception as e:

        print(
            "Whole cache error:",
            e
        )

    return None


def save_whole_text(
    book_hash,
    text
):

    text = normalize_text(
        text
    )

    if not text:

        return False

    path = get_whole_text_path(
        book_hash
    )

    temp = path + ".tmp"

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                text
            )

        os.replace(
            temp,
            path
        )

        return True

    except Exception as e:

        print(
            "Whole text save error:",
            e
        )

        return False


# ============================================================
# OCR ONE PAGE
# ============================================================

def ocr_single_page(
    pdf_path,
    page_number,
    book_hash
):

    cached = load_page_cache(
        book_hash,
        page_number
    )

    if cached:

        return (
            page_number,
            cached,
            True
        )

    try:

        print(
            f"OCR page {page_number}"
        )

        images = convert_from_path(
            pdf_path,
            dpi=OCR_DPI,
            first_page=page_number,
            last_page=page_number,
            fmt="jpeg",
            grayscale=True,
            thread_count=1,
        )

        if not images:

            return (
                page_number,
                "",
                False
            )

        image = images[0]

        language = get_ocr_language()

        text = pytesseract.image_to_string(
            image,
            lang=language,
            config="--psm 6"
        )

        text = normalize_text(
            text
        )

        if text:

            save_page_cache(
                book_hash,
                page_number,
                text
            )

        del image
        del images

        gc.collect()

        return (
            page_number,
            text,
            False
        )

    except Exception as e:

        print(
            f"OCR page {page_number} gagal:",
            e
        )

        gc.collect()

        return (
            page_number,
            "",
            False
        )


# ============================================================
# EXTRACT PDF BY PAGE
# ============================================================

def extract_pdf_pages(
    pdf_path,
    book_hash
):

    print(
        "Buka PDF:",
        pdf_path
    )

    reader = PdfReader(
        pdf_path
    )

    total_pages = len(
        reader.pages
    )

    filename = os.path.basename(
        pdf_path
    )

    print(
        f"Jumlah halaman: {total_pages}"
    )

    update_status(
        book=filename,
        page=0,
        total_pages=total_pages,
        message="Membaca PDF"
    )

    page_texts = [
        ""
        for _ in range(
            total_pages
        )
    ]

    ocr_pages = []

    # --------------------------------------------------------
    # TEXT LAYER
    # --------------------------------------------------------

    for index in range(
        total_pages
    ):

        page_number = index + 1

        try:

            text = (
                reader.pages[
                    index
                ].extract_text()
            )

            text = normalize_text(
                text
            )

        except Exception as e:

            print(
                f"Text page {page_number} error:",
                e
            )

            text = ""

        if len(text) >= MIN_TEXT_CHARS:

            page_texts[
                index
            ] = text

        else:

            cached_page = load_page_cache(
                book_hash,
                page_number
            )

            if cached_page:

                page_texts[
                    index
                ] = cached_page

            else:

                ocr_pages.append(
                    page_number
                )

        if (
            page_number % 25 == 0
            or page_number == total_pages
        ):

            update_status(
                page=page_number,
                total_pages=total_pages,
                message=(
                    f"Semak text "
                    f"{page_number}/"
                    f"{total_pages}"
                )
            )

    # --------------------------------------------------------
    # OCR
    # --------------------------------------------------------

    print(
        f"Page perlu OCR: {len(ocr_pages)}"
    )

    if ocr_pages:

        workers = max(
            1,
            OCR_WORKERS
        )

        update_status(
            page=0,
            total_pages=len(
                ocr_pages
            ),
            message="OCR sedang berjalan"
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = {}

            for page_number in ocr_pages:

                future = executor.submit(
                    ocr_single_page,
                    pdf_path,
                    page_number,
                    book_hash
                )

                futures[
                    future
                ] = page_number

            completed = 0

            for future in as_completed(
                futures
            ):

                page_number = futures[
                    future
                ]

                try:

                    (
                        result_page,
                        text,
                        from_cache,
                    ) = future.result()

                    page_texts[
                        result_page - 1
                    ] = text

                except Exception as e:

                    print(
                        f"OCR future "
                        f"{page_number} error:",
                        e
                    )

                completed += 1

                update_status(
                    page=completed,
                    total_pages=len(
                        ocr_pages
                    ),
                    message=(
                        f"OCR "
                        f"{completed}/"
                        f"{len(ocr_pages)}"
                    )
                )

                if completed % 10 == 0:

                    gc.collect()

    try:

        reader.stream.close()

    except Exception:

        pass

    del reader

    gc.collect()

    return page_texts


# ============================================================
# TXT
# ============================================================

def extract_txt(path):

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            return normalize_text(
                file.read()
            )

    except Exception:

        try:

            with open(
                path,
                "r",
                encoding="utf-8-sig"
            ) as file:

                return normalize_text(
                    file.read()
                )

        except Exception as e:

            print(
                "TXT error:",
                e
            )

            return ""


# ============================================================
# EMBEDDING
# ============================================================

def create_embeddings():

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
        output_dimensionality=EMBEDDING_DIMENSION,
    )


# ============================================================
# DELETE SOURCE FROM SUPABASE
# ============================================================

def delete_source(book_hash):

    if not book_hash:

        return

    try:

        client = create_supabase()

        result = client.rpc(
            "delete_book_chunks",
            {
                "target_file_hash": book_hash
            }
        ).execute()

        print(
            "Data lama dipadam:",
            book_hash
        )

        return result

    except Exception as e:

        print(
            "Delete source error:",
            e
        )


# ============================================================
# GET BOOK
# ============================================================

def get_book_by_hash(book_hash):

    client = create_supabase()

    result = (
        client
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

    return None


# ============================================================
# CREATE / UPDATE BOOK
# ============================================================

def create_book_record(
    path,
    book_hash,
    category,
    total_pages
):

    client = create_supabase()

    filename = os.path.basename(
        path
    )

    existing = get_book_by_hash(
        book_hash
    )

    data = {
        "kitab_name": filename,
        "category": category,
        "file_name": filename,
        "file_path": path,
        "file_hash": book_hash,
        "total_pages": total_pages,
        "processed_pages": 0,
        "total_chunks": 0,
        "status": "PROCESSING",
        "current_page": 0,
    }

    if existing:

        result = (
            client
            .table("books")
            .update(data)
            .eq(
                "id",
                existing["id"]
            )
            .execute()
        )

        if result.data:

            return result.data[0]

        return existing

    result = (
        client
        .table("books")
        .insert(data)
        .execute()
    )

    if not result.data:

        raise RuntimeError(
            "Gagal create rekod books."
        )

    return result.data[0]


# ============================================================
# UPDATE BOOK
# ============================================================

def update_book(
    book_id,
    **data
):

    client = create_supabase()

    (
        client
        .table("books")
        .update(data)
        .eq(
            "id",
            book_id
        )
        .execute()
    )


# ============================================================
# PROCESS PDF
# ============================================================

def process_pdf_book(
    path,
    book_hash,
    category
):

    filename = os.path.basename(
        path
    )

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    print(
        "Membaca halaman PDF..."
    )

    page_texts = extract_pdf_pages(
        path,
        book_hash
    )

    total_pages = len(
        page_texts
    )

    # --------------------------------------------------------
    # Book record
    # --------------------------------------------------------

    book = create_book_record(
        path,
        book_hash,
        category,
        total_pages
    )

    book_id = book["id"]

    # --------------------------------------------------------
    # Splitter
    # --------------------------------------------------------

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            "",
        ],
    )

    embeddings = create_embeddings()

    total_chunks = 0

    # --------------------------------------------------------
    # Process setiap halaman
    # --------------------------------------------------------

    for page_index, page_text in enumerate(
        page_texts
    ):

        page_number = page_index + 1

        page_text = normalize_text(
            page_text
        )

        if not page_text:

            continue

        chunks = splitter.split_text(
            page_text
        )

        if not chunks:

            continue

        print(
            f"Halaman {page_number}: "
            f"{len(chunks)} chunks"
        )

        # ----------------------------------------------------
        # Embedding secara batch
        # ----------------------------------------------------

        for start in range(
            0,
            len(chunks),
            EMBEDDING_BATCH_SIZE
        ):

            batch = chunks[
                start:
                start + EMBEDDING_BATCH_SIZE
            ]

            batch = [
                normalize_text(x)
                for x in batch
                if normalize_text(x)
            ]

            if not batch:

                continue

            print(
                f"Embedding halaman "
                f"{page_number}, "
                f"{start + 1}-"
                f"{start + len(batch)}"
            )

            vectors = embeddings.embed_documents(
                batch
            )

            rows = []

            for local_index, (
                chunk_text,
                vector
            ) in enumerate(
                zip(
                    batch,
                    vectors
                )
            ):

                chunk_number = (
                    start
                    + local_index
                    + 1
                )

                rows.append(
                    {
                        "book_id": book_id,
                        "content": chunk_text,
                        "embedding": vector,
                        "kitab_name": filename,
                        "category": category,
                        "page_number": page_number,
                        "chunk_number": chunk_number,
                        "file_hash": book_hash,
                    }
                )

            if rows:

                client = create_supabase()

                client.table(
                    "kitab_chunks"
                ).upsert(
                    rows,
                    on_conflict=(
                        "file_hash,"
                        "page_number,"
                        "chunk_number"
                    )
                ).execute()

                total_chunks += len(
                    rows
                )

            update_book(
                book_id,
                processed_pages=page_number,
                total_chunks=total_chunks,
                status="EMBEDDING",
                current_page=page_number,
            )

            update_status(
                book=filename,
                page=page_number,
                total_pages=total_pages,
                message=(
                    f"Embedding halaman "
                    f"{page_number}/"
                    f"{total_pages}"
                )
            )

            del vectors
            del rows

            gc.collect()

        del chunks

        gc.collect()

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    update_book(
        book_id,
        processed_pages=total_pages,
        total_chunks=total_chunks,
        status="READY",
        current_page=total_pages,
    )

    print()
    print(
        "KITAB SIAP:",
        filename
    )

    print(
        "Jumlah chunks:",
        total_chunks
    )


# ============================================================
# PROCESS TXT
# ============================================================

def process_txt_book(
    path,
    book_hash,
    category
):

    filename = os.path.basename(
        path
    )

    full_text = extract_txt(
        path
    )

    if not full_text:

        raise RuntimeError(
            "Tiada teks berjaya diperoleh."
        )

    book = create_book_record(
        path,
        book_hash,
        category,
        1
    )

    book_id = book["id"]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            "",
        ],
    )

    chunks = splitter.split_text(
        full_text
    )

    embeddings = create_embeddings()

    total_chunks = 0

    for start in range(
        0,
        len(chunks),
        EMBEDDING_BATCH_SIZE
    ):

        batch = chunks[
            start:
            start + EMBEDDING_BATCH_SIZE
        ]

        vectors = embeddings.embed_documents(
            batch
        )

        rows = []

        for local_index, (
            chunk_text,
            vector
        ) in enumerate(
            zip(
                batch,
                vectors
            )
        ):

            chunk_number = (
                start
                + local_index
                + 1
            )

            rows.append(
                {
                    "book_id": book_id,
                    "content": chunk_text,
                    "embedding": vector,
                    "kitab_name": filename,
                    "category": category,
                    "page_number": 1,
                    "chunk_number": chunk_number,
                    "file_hash": book_hash,
                }
            )

        if rows:

            client = create_supabase()

            client.table(
                "kitab_chunks"
            ).upsert(
                rows,
                on_conflict=(
                    "file_hash,"
                    "page_number,"
                    "chunk_number"
                )
            ).execute()

            total_chunks += len(
                rows
            )

        update_book(
            book_id,
            processed_pages=1,
            total_chunks=total_chunks,
            status="EMBEDDING",
            current_page=1,
        )

        del vectors
        del rows

        gc.collect()

    update_book(
        book_id,
        processed_pages=1,
        total_chunks=total_chunks,
        status="READY",
        current_page=1,
    )

    print(
        "TXT SIAP:",
        filename
    )


# ============================================================
# PROCESS BOOK
# ============================================================

def process_book(
    path,
    book_hash
):

    filename = os.path.basename(
        path
    )

    category = get_category(
        path
    )

    print()
    print(
        "=" * 60
    )

    print(
        "KITAB:",
        filename
    )

    print(
        "KATEGORI:",
        category
    )

    print(
        "=" * 60
    )

    update_status(
        book=filename,
        message="Mengekstrak dan embedding"
    )

    if path.lower().endswith(
        ".txt"
    ):

        process_txt_book(
            path,
            book_hash,
            category
        )

    else:

        process_pdf_book(
            path,
            book_hash,
            category
        )


# ============================================================
# SYNC BOOKS
# ============================================================

def sync_books():

    global INDEX_READY
    global FAILED_BOOKS

    INDEX_READY = False

    FAILED_BOOKS = []

    update_status(
        status="indexing",
        message="Mula indexing"
    )

    manifest = load_manifest()

    files = find_books()

    print()
    print(
        "Jumlah kitab:",
        len(files)
    )

    current_files = {}

    # --------------------------------------------------------
    # HASH
    # --------------------------------------------------------

    for path in files:

        try:

            current_files[
                path
            ] = calculate_file_hash(
                path
            )

        except Exception as e:

            print(
                "Hash gagal:",
                path,
                e
            )

    # --------------------------------------------------------
    # DELETE REMOVED
    # --------------------------------------------------------

    for old_path in list(
        manifest.keys()
    ):

        if old_path not in current_files:

            old_hash = (
                manifest[
                    old_path
                ].get(
                    "hash"
                )
            )

            if old_hash:

                print(
                    "Kitab dibuang:",
                    old_path
                )

                delete_source(
                    old_hash
                )

            del manifest[
                old_path
            ]

    save_manifest(
        manifest
    )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    for path, book_hash in (
        current_files.items()
    ):

        filename = os.path.basename(
            path
        )

        old = manifest.get(
            path
        )

        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        if (
            old
            and old.get("hash")
            == book_hash
            and old.get("status")
            == "ready"
        ):

            print(
                "SKIP:",
                filename
            )

            continue

        # ----------------------------------------------------
        # CHANGED
        # ----------------------------------------------------

        if old:

            old_hash = old.get(
                "hash"
            )

            if (
                old_hash
                and old_hash != book_hash
            ):

                print(
                    "Kitab berubah:",
                    filename
                )

                delete_source(
                    old_hash
                )

        # ----------------------------------------------------
        # PROCESS
        # ----------------------------------------------------

        try:

            update_status(
                book=filename,
                page=0,
                total_pages=0,
                message=(
                    "Memproses "
                    + filename
                )
            )

            process_book(
                path,
                book_hash
            )

            manifest[path] = {
                "hash": book_hash,
                "status": "ready",
                "filename": filename,
                "category": get_category(
                    path
                ),
                "updated": time.time(),
            }

            save_manifest(
                manifest
            )

        except Exception as e:

            print()
            print(
                "KITAB GAGAL:",
                filename
            )

            print(
                "ERROR:",
                e
            )

            FAILED_BOOKS.append(
                filename
            )

            manifest[path] = {
                "hash": book_hash,
                "status": "failed",
                "filename": filename,
                "category": get_category(
                    path
                ),
                "error": str(e),
                "updated": time.time(),
            }

            save_manifest(
                manifest
            )

        finally:

            gc.collect()

    INDEX_READY = True

    update_status(
        status="ready",
        message="Index siap"
    )

    print()
    print(
        "=" * 60
    )

    print(
        "INDEX SIAP"
    )

    print(
        "=" * 60
    )


# ============================================================
# SEARCH SUPABASE
# ============================================================

def search_books(
    question,
    category=None
):

    embeddings = create_embeddings()

    query_vector = embeddings.embed_query(
        question
    )

    client = create_supabase()

    result = client.rpc(
        "match_kitab_chunks",
        {
            "query_embedding": query_vector,
            "match_count": SEARCH_K,
            "filter_category": category,
        }
    ).execute()

    return result.data or []


# ============================================================
# LLM
# ============================================================

def create_llm():

    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.1,
    )


# ============================================================
# ANSWER
# ============================================================

def generate_answer(
    question,
    category=None
):

    if not INDEX_READY:

        return (
            "📚 Sistem masih menyediakan "
            "kitab. Sila cuba semula "
            "sebentar lagi."
        )

    try:

        results = search_books(
            question,
            category
        )

        if not results:

            return (
                "Maaf, saya tidak menemui "
                "rujukan yang sesuai dalam "
                "kitab yang tersedia."
            )

        context_parts = []

        for item in results:

            context_parts.append(
                "SUMBER: "
                + str(
                    item.get(
                        "kitab_name",
                        "Tidak diketahui"
                    )
                )
                + "\n"
                + "KATEGORI: "
                + str(
                    item.get(
                        "category",
                        "-"
                    )
                )
                + "\n"
                + "HALAMAN: "
                + str(
                    item.get(
                        "page_number",
                        "-"
                    )
                )
                + "\n"
                + "CHUNK: "
                + str(
                    item.get(
                        "chunk_number",
                        "-"
                    )
                )
                + "\n"
                + "SIMILARITY: "
                + str(
                    round(
                        float(
                            item.get(
                                "similarity",
                                0
                            )
                        ),
                        4
                    )
                )
                + "\n"
                + "TEKS:\n"
                + str(
                    item.get(
                        "content",
                        ""
                    )
                )
            )

        context = (
            "\n\n---\n\n"
            .join(
                context_parts
            )
        )

        context = context[
            :MAX_CONTEXT_CHARS
        ]

        prompt = f"""
Anda ialah TanyaFiqhBot,
pembantu rujukan ilmu Islam.

Jawab soalan pengguna dalam
Bahasa Melayu yang mudah difahami.

PERATURAN PENTING:

1. Gunakan hanya maklumat daripada
   konteks kitab yang diberikan.

2. Jangan mereka-reka maklumat yang
   tiada dalam konteks.

3. Jika konteks tidak mencukupi,
   nyatakan bahawa rujukan yang
   ditemui tidak mencukupi.

4. Jika terdapat perbezaan pandangan,
   nyatakan berdasarkan sumber yang
   ditemui.

5. Jangan mendakwa jawapan ini
   sebagai fatwa rasmi.

6. Jawab terus soalan pengguna.

7. Gunakan format yang mudah dibaca.

8. Nyatakan nama kitab jika tersedia.

9. Nyatakan nombor halaman jika tersedia.

10. Jangan masukkan maklumat luar
    daripada konteks kitab.

SOALAN PENGGUNA:
{question}

KONTEKS KITAB:
{context}
"""

        llm = create_llm()

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

        answer = answer.strip()

        del llm
        del results
        del context

        gc.collect()

        return answer

    except Exception as e:

        print(
            "Generate answer error:",
            e
        )

        gc.collect()

        return (
            "Maaf, berlaku masalah "
            "ketika mencari jawapan. "
            "Sila cuba lagi."
        )


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    text = """
🤖 *TanyaFiqhBot*

Assalamualaikum.

Saya membantu mencari jawapan
berdasarkan kitab yang dimasukkan
ke dalam sistem.

Contoh:

• Apa hukum mandi wajib?
• Apakah rukun solat?
• Apa syarat sah puasa?
• Bagaimana cara wuduk?

*Command:*

/fiqh - Cari kitab Fiqh
/tauhid - Cari kitab Tauhid
/semua - Cari semua kitab
/status - Status sistem

Anda juga boleh terus taip
soalan tanpa command.
"""

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# ============================================================
# STATUS TELEGRAM
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    with INDEX_LOCK:

        status = INDEX_STATUS

        progress = dict(
            INDEX_PROGRESS
        )

        ready_state = INDEX_READY

        failed_state = list(
            FAILED_BOOKS
        )

    manifest = load_manifest()

    ready = []

    failed = []

    categories = {}

    for path, data in (
        manifest.items()
    ):

        status_value = data.get(
            "status"
        )

        if status_value == "ready":

            ready.append(
                path
            )

            category = data.get(
                "category",
                "LAIN-LAIN"
            )

            categories[
                category
            ] = (
                categories.get(
                    category,
                    0
                )
                + 1
            )

        elif status_value == "failed":

            failed.append(
                data.get(
                    "filename",
                    os.path.basename(
                        path
                    )
                )
            )

    text = (
        "📊 *Status TanyaFiqhBot*\n\n"
        f"Status: `{status}`\n"
        f"Index: `{ready_state}`\n\n"
        f"📚 Kitab siap: "
        f"`{len(ready)}`\n"
        f"❌ Kitab gagal: "
        f"`{len(failed)}`\n"
    )

    if progress.get(
        "book"
    ):

        text += (
            "\n📖 Kitab: "
            + progress["book"]
            + "\n"
        )

    if progress.get(
        "message"
    ):

        text += (
            "⚙️ "
            + progress["message"]
            + "\n"
        )

    if categories:

        text += (
            "\n📂 *Kategori:*\n"
        )

        for category in sorted(
            categories
        ):

            text += (
                "• "
                + category
                + ": "
                + str(
                    categories[
                        category
                    ]
                )
                + "\n"
            )

    if failed:

        text += (
            "\n❌ *Kitab gagal:*\n"
        )

        for filename in failed[
            :10
        ]:

            text += (
                "• "
                + filename
                + "\n"
            )

    if failed_state and not failed:

        text += (
            "\n❌ *Gagal:*\n"
        )

        for filename in failed_state[
            :10
        ]:

            text += (
                "• "
                + filename
                + "\n"
            )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# ============================================================
# CATEGORY QUESTION
# ============================================================

async def category_question(
    update,
    category
):

    if not update.message:

        return

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan "
            "kitab. Sila cuba semula "
            "sebentar lagi."
        )

        return

    message = (
        update.message.text
        or ""
    )

    parts = message.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await update.message.reply_text(
            "Sila masukkan soalan.\n\n"
            "Contoh:\n"
            "/fiqh apa hukum mandi wajib?"
        )

        return

    question = parts[1].strip()

    if not question:

        await update.message.reply_text(
            "Sila masukkan soalan."
        )

        return

    await update.message.reply_text(
        "🔎 Sedang mencari rujukan..."
    )

    answer = generate_answer(
        question,
        category
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# COMMANDS
# ============================================================

async def fiqh_command(
    update,
    context
):

    await category_question(
        update,
        "FIQH"
    )


async def tauhid_command(
    update,
    context
):

    await category_question(
        update,
        "TAUHID"
    )


async def semua_command(
    update,
    context
):

    if not update.message:

        return

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan "
            "kitab. Sila cuba semula "
            "sebentar lagi."
        )

        return

    message = (
        update.message.text
        or ""
    )

    parts = message.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await update.message.reply_text(
            "Contoh:\n"
            "/semua apa hukum mandi wajib?"
        )

        return

    question = parts[1].strip()

    if not question:

        await update.message.reply_text(
            "Sila masukkan soalan."
        )

        return

    await update.message.reply_text(
        "🔎 Sedang mencari semua kitab..."
    )

    answer = generate_answer(
        question
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# NORMAL MESSAGE
# ============================================================

async def normal_message(
    update,
    context
):

    if not update.message:

        return

    question = (
        update.message.text
        or ""
    ).strip()

    if not question:

        return

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan "
            "kitab. Sila cuba semula "
            "sebentar lagi."
        )

        return

    await update.message.reply_text(
        "🔎 Sedang mencari rujukan..."
    )

    answer = generate_answer(
        question
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# TELEGRAM
# ============================================================

def run_telegram():

    if not TELEGRAM_TOKEN:

        print(
            "TELEGRAM_TOKEN belum ditetapkan."
        )

        return

    print(
        "Telegram bot sedang dimulakan..."
    )

    try:

        telegram_app = (
            Application.builder()
            .token(
                TELEGRAM_TOKEN
            )
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

        telegram_app.add_handler(
            CommandHandler(
                "fiqh",
                fiqh_command
            )
        )

        telegram_app.add_handler(
            CommandHandler(
                "tauhid",
                tauhid_command
            )
        )

        telegram_app.add_handler(
            CommandHandler(
                "semua",
                semua_command
            )
        )

        telegram_app.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                normal_message
            )
        )

        print(
            "Telegram polling dimulakan."
        )

        telegram_app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            stop_signals=None
        )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )

        gc.collect()


# ============================================================
# BACKGROUND
# ============================================================

def start_background():

    def indexing():

        try:

            print(
                "Background indexing bermula..."
            )

            sync_books()

        except Exception as e:

            print(
                "INDEXING ERROR:",
                e
            )

            update_status(
                status="error",
                message=str(e)
            )

            gc.collect()

    indexing_thread = threading.Thread(
        target=indexing,
        daemon=True,
        name="indexing-thread"
    )

    indexing_thread.start()

    time.sleep(2)

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True,
        name="telegram-thread"
    )

    telegram_thread.start()


# ============================================================
# STARTUP
# ============================================================

print(
    "=" * 60
)

print(
    "TanyaFiqhBot"
)

print(
    "Database: Supabase pgvector"
)

print(
    "OCR Workers:",
    OCR_WORKERS
)

print(
    "OCR DPI:",
    OCR_DPI
)

print(
    "Embedding:",
    EMBEDDING_MODEL
)

print(
    "Embedding Dimension:",
    EMBEDDING_DIMENSION
)

print(
    "LLM:",
    LLM_MODEL
)

print(
    "Kitab:",
    KITAB_DIR
)

print(
    "Data:",
    DATA_DIR
)

print(
    "=" * 60
)


start_background()


# ============================================================
# LOCAL
# ============================================================

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
