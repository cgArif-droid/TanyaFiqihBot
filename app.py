import os
import re
import gc
import json
import glob
import time
import hashlib
import threading

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

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

from langchain_community.vectorstores import Chroma

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter
)

from pypdf import PdfReader

from pdf2image import convert_from_path
import pytesseract


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

DATA_DIR = os.environ.get(
    "DATA_DIR",
    "/var/data"
)

# RAM sekitar 515 MB
# Gunakan 1 worker untuk kestabilan
OCR_WORKERS = int(
    os.environ.get(
        "OCR_WORKERS",
        "1"
    )
)

OCR_DPI = 200

MIN_TEXT_CHARS = 40

LLM_MODEL = "gemini-2.5-flash"

EMBEDDING_MODEL = (
    "models/gemini-embedding-001"
)

# Jumlah dokumen yang diambil
SEARCH_K = 6

# Saiz batch embedding
EMBEDDING_BATCH_SIZE = 16


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

CHROMA_DIR = os.path.join(
    DATA_DIR,
    "chroma"
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
    CHROMA_DIR,
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
# STATUS UPDATE
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

def calculate_file_hash(
    path
):

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


def save_manifest(
    manifest
):

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

def get_category(
    path
):

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

    # Jangan simpan cache kosong
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

        try:

            if os.path.exists(
                temp
            ):

                os.remove(
                    temp
                )

        except Exception:

            pass

        return False


# ============================================================
# WHOLE TEXT CACHE
# ============================================================

def get_whole_text_path(
    book_hash
):

    return os.path.join(
        EXTRACTED_DIR,
        f"{book_hash}.txt"
    )


def load_whole_text(
    book_hash
):

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

    # --------------------------------------------------------
    # Semak cache
    # --------------------------------------------------------

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

        # Simpan hanya jika berjaya
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
            f"OCR page {page_number} "
            f"gagal: {e}"
        )

        gc.collect()

        return (
            page_number,
            "",
            False
        )


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf(
    pdf_path,
    book_hash
):

    # --------------------------------------------------------
    # Semak whole text cache
    # --------------------------------------------------------

    cached = load_whole_text(
        book_hash
    )

    if cached:

        print(
            "Whole text cache digunakan."
        )

        return cached

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
    # Text layer
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
                f"Text page "
                f"{page_number} error:",
                e
            )

            text = ""

        if len(text) >= MIN_TEXT_CHARS:

            page_texts[
                index
            ] = text

        else:

            # Cuba page cache dahulu
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
                    "Semak text "
                    f"{page_number}/"
                    f"{total_pages}"
                )
            )

    print(
        f"Page perlu OCR: "
        f"{len(ocr_pages)}"
    )

    # --------------------------------------------------------
    # OCR
    # --------------------------------------------------------

    if ocr_pages:

        workers = max(
            1,
            OCR_WORKERS
        )

        print(
            f"OCR workers: {workers}"
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
                        "OCR "
                        f"{completed}/"
                        f"{len(ocr_pages)}"
                    )
                )

                if completed % 10 == 0:

                    gc.collect()

    # --------------------------------------------------------
    # Gabungkan teks
    # --------------------------------------------------------

    sections = []

    for index, text in enumerate(
        page_texts
    ):

        if not text:

            continue

        page_number = index + 1

        sections.append(
            f"[HALAMAN {page_number}]\n"
            f"{text}"
        )

    full_text = "\n\n".join(
        sections
    )

    full_text = normalize_text(
        full_text
    )

    print(
        "Jumlah karakter:",
        len(full_text)
    )

    # Whole cache hanya selepas
    # keseluruhan PDF selesai
    if full_text:

        save_whole_text(
            book_hash,
            full_text
        )

    try:

        reader.stream.close()

    except Exception:

        pass

    del reader
    del page_texts
    del ocr_pages

    gc.collect()

    return full_text


# ============================================================
# TXT EXTRACTION
# ============================================================

def extract_txt(
    path
):

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
# EMBEDDINGS
# ============================================================

def create_embeddings():

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )


# ============================================================
# VECTORSTORE
# ============================================================

def get_vectorstore():

    embeddings = create_embeddings()

    return Chroma(
        collection_name="tanyafiqhbot",
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )


# ============================================================
# DELETE SOURCE
# ============================================================

def delete_source(
    book_hash
):

    if not book_hash:

        return

    try:

        vectorstore = get_vectorstore()

        collection = (
            vectorstore._collection
        )

        result = collection.get(
            where={
                "book_hash": book_hash
            }
        )

        ids = result.get(
            "ids",
            []
        )

        if ids:

            collection.delete(
                ids=ids
            )

            print(
                f"Padam {len(ids)} chunks."
            )

        del vectorstore

        gc.collect()

    except Exception as e:

        print(
            "Delete source error:",
            e
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
        message="Mengekstrak teks"
    )

    # --------------------------------------------------------
    # Extract
    # --------------------------------------------------------

    if path.lower().endswith(
        ".txt"
    ):

        full_text = extract_txt(
            path
        )

    else:

        full_text = extract_pdf(
            path,
            book_hash
        )

    if not full_text:

        raise RuntimeError(
            "Tiada teks berjaya diperoleh."
        )

    # --------------------------------------------------------
    # Split
    # --------------------------------------------------------

    update_status(
        message="Memecahkan teks kepada chunks"
    )

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

    print(
        "Jumlah chunks:",
        len(chunks)
    )

    if not chunks:

        raise RuntimeError(
            "Tiada chunks."
        )

    # --------------------------------------------------------
    # Vectorstore
    # --------------------------------------------------------

    update_status(
        message="Menyimpan embedding"
    )

    vectorstore = get_vectorstore()

    total = len(
        chunks
    )

    batch_size = EMBEDDING_BATCH_SIZE

    try:

        for start in range(
            0,
            total,
            batch_size
        ):

            end = min(
                start + batch_size,
                total
            )

            batch_documents = []

            batch_ids = []

            for index in range(
                start,
                end
            ):

                chunk = normalize_text(
                    chunks[index]
                )

                if not chunk:

                    continue

                doc = Document(
                    page_content=chunk,
                    metadata={
                        "source": filename,
                        "path": path,
                        "category": category,
                        "book_hash": book_hash,
                        "chunk": index,
                    },
                )

                batch_documents.append(
                    doc
                )

                batch_ids.append(
                    f"{book_hash}_{index}"
                )

            if not batch_documents:

                continue

            actual_end = min(
                end,
                total
            )

            print(
                f"Embedding "
                f"{start + 1}-"
                f"{actual_end}/"
                f"{total}"
            )

            vectorstore.add_documents(
                documents=batch_documents,
                ids=batch_ids
            )

            update_status(
                page=actual_end,
                total_pages=total,
                message=(
                    f"Embedding "
                    f"{actual_end}/"
                    f"{total}"
                )
            )

            del batch_documents
            del batch_ids

            gc.collect()

        try:

            vectorstore.persist()

        except Exception:

            pass

    except Exception as e:

        print(
            "Embedding gagal:",
            e
        )

        # Padam embedding separa
        delete_source(
            book_hash
        )

        raise

    del vectorstore
    del chunks
    del full_text

    gc.collect()

    print(
        "KITAB SELESAI:",
        filename
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
    # Hash
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
    # Delete removed books
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
    # Process
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
        # Tidak berubah
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
        # Changed / failed
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
        # Process
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

    # --------------------------------------------------------
    # Siap
    # --------------------------------------------------------

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
# SEARCH
# ============================================================

def search_books(
    question,
    category=None
):

    vectorstore = get_vectorstore()

    try:

        if category:

            try:

                docs = (
                    vectorstore
                    .similarity_search(
                        question,
                        k=SEARCH_K,
                        filter={
                            "category":
                                category
                        }
                    )
                )

            except Exception as e:

                print(
                    "Filter search gagal:",
                    e
                )

                docs = (
                    vectorstore
                    .similarity_search(
                        question,
                        k=SEARCH_K
                    )
                )

        else:

            docs = (
                vectorstore
                .similarity_search(
                    question,
                    k=SEARCH_K
                )
            )

        return docs

    finally:

        del vectorstore

        gc.collect()


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

        docs = search_books(
            question,
            category
        )

        if not docs:

            return (
                "Maaf, saya tidak menemui "
                "rujukan yang sesuai dalam "
                "kitab yang tersedia."
            )

        context_parts = []

        for doc in docs:

            metadata = (
                doc.metadata
                or {}
            )

            source = metadata.get(
                "source",
                "Tidak diketahui"
            )

            category_name = metadata.get(
                "category",
                "-"
            )

            chunk = metadata.get(
                "chunk",
                "-"
            )

            text = (
                doc.page_content
            )

            context_parts.append(
                "SUMBER: "
                + source
                + "\n"
                + "KATEGORI: "
                + str(
                    category_name
                )
                + "\n"
                + "CHUNK: "
                + str(chunk)
                + "\n"
                + "TEKS:\n"
                + text
            )

        context = (
            "\n\n---\n\n"
            .join(
                context_parts
            )
        )

        # Hadkan context
        context = context[
            :24000
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

8. Jika nama kitab tersedia,
   nyatakan nama kitab pada akhir
   jawapan.

9. Jika terdapat nombor halaman
   dalam konteks, nyatakan halaman
   tersebut.

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
        del docs
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
# TELEGRAM /START
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
# TELEGRAM STATUS
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
# FIQH
# ============================================================

async def fiqh_command(
    update,
    context
):

    await category_question(
        update,
        "FIQH"
    )


# ============================================================
# TAUHID
# ============================================================

async def tauhid_command(
    update,
    context
):

    await category_question(
        update,
        "TAUHID"
    )


# ============================================================
# SEMUA KITAB
# ============================================================

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
# TELEGRAM BOT
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

        # ----------------------------------------------------
        # Commands
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Normal text
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # PENTING
        #
        # run_telegram() berjalan dalam
        # background thread.
        #
        # stop_signals=None diperlukan
        # supaya python-telegram-bot tidak
        # cuba menggunakan signal handler
        # daripada thread.
        # ----------------------------------------------------

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
# BACKGROUND INDEXING
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

    # --------------------------------------------------------
    # Indexing thread
    # --------------------------------------------------------

    indexing_thread = threading.Thread(
        target=indexing,
        daemon=True,
        name="indexing-thread"
    )

    indexing_thread.start()

    # Beri sedikit masa Flask/indexing
    # untuk mula sebelum Telegram.
    time.sleep(2)

    # --------------------------------------------------------
    # Telegram thread
    # --------------------------------------------------------

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True,
        name="telegram-thread"
    )

    telegram_thread.start()


# ============================================================
# STARTUP INFORMATION
# ============================================================

print(
    "=" * 60
)

print(
    "TanyaFiqhBot"
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
    "Chroma:",
    CHROMA_DIR
)

print(
    "=" * 60
)


# ============================================================
# START BACKGROUND
# ============================================================

start_background()


# ============================================================
# LOCAL DEVELOPMENT
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
