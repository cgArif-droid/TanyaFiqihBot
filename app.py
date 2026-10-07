import os
import re
import gc
import json
import glob
import time
import hashlib
import threading
import asyncio
from pathlib import Path
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

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)

from langchain_community.vectorstores import Chroma

from langchain_text_splitters import RecursiveCharacterTextSplitter

from pypdf import PdfReader

from pdf2image import convert_from_path
import pytesseract


# ============================================================
# ENVIRONMENT
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

OCR_WORKERS = int(
    os.environ.get(
        "OCR_WORKERS",
        "3"
    )
)


# ============================================================
# MODEL
# ============================================================

LLM_MODEL = "gemini-2.5-flash"

EMBEDDING_MODEL = (
    "models/gemini-embedding-001"
)


# ============================================================
# PATH
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

MANIFEST_FILE = os.path.join(
    DATA_DIR,
    "manifest.json"
)

OCR_PAGE_CACHE_DIR = os.path.join(
    EXTRACTED_DIR,
    "pages"
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
    OCR_PAGE_CACHE_DIR,
    exist_ok=True
)


# ============================================================
# OCR SETTINGS
# ============================================================

OCR_DPI = 200

MIN_TEXT_CHARS = 40

OCR_LANG = None

OCR_LANG_LOCK = threading.Lock()


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

INDEX_LOCK = threading.Lock()

FAILED_BOOKS = []


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify(
        {
            "bot": "TanyaFiqhBot",
            "status": INDEX_STATUS,
            "index_ready": INDEX_READY,
        }
    )


@app.route("/health")
def health():
    with INDEX_LOCK:
        status = INDEX_STATUS
        progress = dict(INDEX_PROGRESS)

    return jsonify(
        {
            "status": status,
            "index_ready": INDEX_READY,
            "progress": progress,
            "failed_books": FAILED_BOOKS,
        }
    )


# ============================================================
# UTILITIES
# ============================================================

def set_status(
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
            INDEX_PROGRESS["book"] = book

        if page is not None:
            INDEX_PROGRESS["page"] = page

        if total_pages is not None:
            INDEX_PROGRESS["total_pages"] = total_pages

        if message is not None:
            INDEX_PROGRESS["message"] = message


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


def file_hash(path):
    sha = hashlib.sha256()

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

            sha.update(chunk)

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
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            "Manifest gagal dibaca:",
            e
        )

        return {}


def save_manifest(manifest):

    temp_file = (
        MANIFEST_FILE
        + ".tmp"
    )

    with open(
        temp_file,
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
        temp_file,
        MANIFEST_FILE
    )


# ============================================================
# PDF SCANNER
# ============================================================

def find_books():

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
        list(
            set(files)
        )
    )


def get_category(path):

    try:

        relative = os.path.relpath(
            path,
            KITAB_DIR
        )

        parts = Path(
            relative
        ).parts

        if len(parts) >= 2:
            return parts[0]

    except Exception:
        pass

    return "LAIN-LAIN"


# ============================================================
# OCR LANGUAGE
# ============================================================

def get_ocr_language():

    global OCR_LANG

    if OCR_LANG is not None:
        return OCR_LANG

    with OCR_LANG_LOCK:

        if OCR_LANG is not None:
            return OCR_LANG

        try:

            languages = pytesseract.get_languages(
                config=""
            )

            available = []

            for lang in [
                "msa",
                "ara",
                "eng",
            ]:

                if lang in languages:
                    available.append(
                        lang
                    )

            if available:

                OCR_LANG = "+".join(
                    available
                )

            else:

                OCR_LANG = "eng"

        except Exception as e:

            print(
                "Gagal semak bahasa OCR:",
                e
            )

            OCR_LANG = "eng"

    print(
        "Bahasa OCR:",
        OCR_LANG
    )

    return OCR_LANG


# ============================================================
# PAGE CACHE
# ============================================================

def page_cache_path(
    book_hash,
    page_number
):

    book_dir = os.path.join(
        OCR_PAGE_CACHE_DIR,
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


def load_page_cache(
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

            text = f.read()

        text = normalize_text(
            text
        )

        if text:
            return text

    except Exception as e:

        print(
            "Gagal baca cache:",
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

    # Jangan cache jika OCR gagal.
    if not text:
        return False

    path = page_cache_path(
        book_hash,
        page_number
    )

    try:

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(text)

        return True

    except Exception as e:

        print(
            "Gagal simpan cache page:",
            e
        )

        return False


# ============================================================
# WHOLE BOOK TEXT CACHE
# ============================================================

def whole_text_path(
    book_hash
):

    return os.path.join(
        EXTRACTED_DIR,
        f"{book_hash}.txt"
    )


def load_whole_text(
    book_hash
):

    path = whole_text_path(
        book_hash
    )

    if not os.path.exists(path):
        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            text = f.read()

        text = normalize_text(
            text
        )

        if text:
            return text

    except Exception as e:

        print(
            "Gagal baca extracted text:",
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

    path = whole_text_path(
        book_hash
    )

    try:

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(text)

        return True

    except Exception as e:

        print(
            "Gagal simpan whole text:",
            e
        )

        return False


# ============================================================
# OCR ONE PAGE
# ============================================================

def ocr_page(
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

        lang = get_ocr_language()

        print(
            f"OCR page {page_number} "
            f"menggunakan {lang}"
        )

        text = pytesseract.image_to_string(
            image,
            lang=lang,
            config="--psm 6"
        )

        text = normalize_text(
            text
        )

        del image
        del images

        gc.collect()

        if text:

            save_page_cache(
                book_hash,
                page_number,
                text
            )

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
# PDF EXTRACTION
# ============================================================

def extract_pdf_text(
    pdf_path,
    book_hash
):

    cached_book = load_whole_text(
        book_hash
    )

    if cached_book:
        print(
            "Whole text cache dijumpai."
        )

        return cached_book

    print(
        "Membuka PDF:",
        pdf_path
    )

    reader = PdfReader(
        pdf_path
    )

    total_pages = len(
        reader.pages
    )

    set_status(
        book=os.path.basename(
            pdf_path
        ),
        page=0,
        total_pages=total_pages,
        message="Membaca PDF"
    )

    print(
        f"Jumlah halaman: {total_pages}"
    )

    page_texts = [
        ""
        for _ in range(total_pages)
    ]

    pages_needing_ocr = []

    # --------------------------------------------------------
    # Cuba ambil text layer dahulu.
    # --------------------------------------------------------

    for index in range(
        total_pages
    ):

        page_number = index + 1

        try:

            text = reader.pages[
                index
            ].extract_text()

            text = normalize_text(
                text
            )

        except Exception as e:

            print(
                f"Text extraction page "
                f"{page_number} gagal:",
                e
            )

            text = ""

        if len(text) >= MIN_TEXT_CHARS:

            page_texts[index] = text

        else:

            pages_needing_ocr.append(
                page_number
            )

        if (
            page_number % 20 == 0
            or page_number == total_pages
        ):

            set_status(
                page=page_number,
                message=(
                    "Semak text PDF "
                    f"{page_number}/{total_pages}"
                )
            )

    print(
        f"Perlu OCR: "
        f"{len(pages_needing_ocr)} halaman"
    )

    # --------------------------------------------------------
    # OCR
    # --------------------------------------------------------

    if pages_needing_ocr:

        set_status(
            message=(
                "OCR sedang berjalan"
            )
        )

        # Jangan terlalu tinggi untuk RAM kecil.
        workers = max(
            1,
            OCR_WORKERS
        )

        print(
            f"OCR workers: {workers}"
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            future_map = {}

            for page_number in pages_needing_ocr:

                future = executor.submit(
                    ocr_page,
                    pdf_path,
                    page_number,
                    book_hash
                )

                future_map[
                    future
                ] = page_number

            completed = 0

            for future in as_completed(
                future_map
            ):

                page_number = future_map[
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
                        f"Future OCR page "
                        f"{page_number} gagal:",
                        e
                    )

                completed += 1

                set_status(
                    page=completed,
                    total_pages=len(
                        pages_needing_ocr
                    ),
                    message=(
                        "OCR "
                        f"{completed}/"
                        f"{len(pages_needing_ocr)}"
                    )
                )

                if (
                    completed % 10 == 0
                ):
                    gc.collect()

    # --------------------------------------------------------
    # Gabungkan semua page
    # --------------------------------------------------------

    sections = []

    for index, text in enumerate(
        page_texts
    ):

        if not text:
            continue

        page_number = index + 1

        sections.append(
            f"\n[HALAMAN {page_number}]\n"
            f"{text}"
        )

    full_text = "\n".join(
        sections
    )

    full_text = normalize_text(
        full_text
    )

    print(
        f"Jumlah karakter teks: "
        f"{len(full_text)}"
    )

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
    del pages_needing_ocr

    gc.collect()

    return full_text


# ============================================================
# TXT EXTRACTION
# ============================================================

def extract_txt_text(
    path
):

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return normalize_text(
                f.read()
            )

    except UnicodeDecodeError:

        try:

            with open(
                path,
                "r",
                encoding="utf-8-sig"
            ) as f:

                return normalize_text(
                    f.read()
                )

        except Exception as e:

            print(
                "TXT gagal dibaca:",
                e
            )

            return ""

    except Exception as e:

        print(
            "TXT gagal dibaca:",
            e
        )

        return ""


# ============================================================
# CHROMA
# ============================================================

def get_embeddings():

    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )


def get_vectorstore():

    embeddings = get_embeddings()

    vectorstore = Chroma(
        collection_name="tanyafiqhbot",
        embedding_function=embeddings,
        persist_directory=CHROMA_DIR,
    )

    return vectorstore


# ============================================================
# SOURCE ID
# ============================================================

def make_chunk_id(
    book_hash,
    chunk_index
):

    return (
        f"{book_hash}_"
        f"{chunk_index}"
    )


# ============================================================
# DELETE BOOK FROM CHROMA
# ============================================================

def delete_source(
    book_hash
):

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
                f"Padam {len(ids)} "
                f"chunks untuk {book_hash}"
            )

        del vectorstore

        gc.collect()

    except Exception as e:

        print(
            "Gagal delete source:",
            e
        )


# ============================================================
# PROCESS ONE BOOK
# ============================================================

def process_book(
    pdf_path,
    book_hash
):

    filename = os.path.basename(
        pdf_path
    )

    category = get_category(
        pdf_path
    )

    print()
    print(
        "=" * 60
    )
    print(
        "PROCESS:",
        filename
    )
    print(
        "CATEGORY:",
        category
    )
    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # Extraction
    # --------------------------------------------------------

    if pdf_path.lower().endswith(
        ".txt"
    ):

        text = extract_txt_text(
            pdf_path
        )

    else:

        text = extract_pdf_text(
            pdf_path,
            book_hash
        )

    if not text:

        raise RuntimeError(
            "Tiada teks berjaya diperoleh."
        )

    # --------------------------------------------------------
    # Split
    # --------------------------------------------------------

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=200,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            "",
        ],
    )

    splits = splitter.split_text(
        text
    )

    print(
        f"Jumlah chunks: "
        f"{len(splits)}"
    )

    if not splits:

        raise RuntimeError(
            "Tiada chunk dihasilkan."
        )

    # --------------------------------------------------------
    # Documents
    # --------------------------------------------------------

    documents = []

    ids = []

    for index, chunk in enumerate(
        splits
    ):

        chunk = normalize_text(
            chunk
        )

        if not chunk:
            continue

        doc = Document(
            page_content=chunk,
            metadata={
                "source": filename,
                "path": pdf_path,
                "category": category,
                "book_hash": book_hash,
                "chunk": index,
            },
        )

        documents.append(
            doc
        )

        ids.append(
            make_chunk_id(
                book_hash,
                index
            )
        )

    if not documents:

        raise RuntimeError(
            "Documents kosong."
        )

    # --------------------------------------------------------
    # Vectorstore
    # --------------------------------------------------------

    vectorstore = get_vectorstore()

    total_chunks = len(
        documents
    )

    # 16 lebih ringan untuk RAM kecil.
    batch_size = 16

    try:

        for start in range(
            0,
            total_chunks,
            batch_size
        ):

            end = min(
                start + batch_size,
                total_chunks
            )

            batch_docs = documents[
                start:end
            ]

            batch_ids = ids[
                start:end
            ]

            print(
                "Embedding "
                f"{start + 1}-"
                f"{end}/"
                f"{total_chunks}"
            )

            vectorstore.add_documents(
                documents=batch_docs,
                ids=batch_ids
            )

            # Lepaskan batch selepas selesai.
            batch_docs = None
            batch_ids = None

            gc.collect()

    except Exception as e:

        print(
            "Embedding gagal."
        )

        # Buang vector separa
        # supaya proses boleh diulang
        # dengan bersih.

        delete_source(
            book_hash
        )

        raise e

    try:

        vectorstore.persist()

    except Exception:
        pass

    del documents
    del ids
    del splits
    del vectorstore

    gc.collect()

    print(
        "Selesai:",
        filename
    )


# ============================================================
# SYNC BOOKS
# ============================================================

def sync_books():

    global INDEX_READY
    global INDEX_STATUS
    global FAILED_BOOKS

    INDEX_READY = False

    set_status(
        status="indexing",
        message="Mula indexing"
    )

    FAILED_BOOKS = []

    manifest = load_manifest()

    files = find_books()

    print()
    print(
        "Jumlah kitab ditemui:",
        len(files)
    )

    current_hashes = {}

    # --------------------------------------------------------
    # Hash semua fail
    # --------------------------------------------------------

    for path in files:

        try:

            h = file_hash(
                path
            )

            current_hashes[
                path
            ] = h

        except Exception as e:

            print(
                "Gagal hash:",
                path,
                e
            )

    # --------------------------------------------------------
    # Buang kitab yang sudah tidak wujud
    # --------------------------------------------------------

    old_paths = list(
        manifest.keys()
    )

    for old_path in old_paths:

        if old_path not in current_hashes:

            old_hash = manifest[
                old_path
            ].get(
                "hash"
            )

            if old_hash:

                print(
                    "Kitab telah dibuang:",
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
    # Process books
    # --------------------------------------------------------

    for path in files:

        book_hash = current_hashes.get(
            path
        )

        if not book_hash:
            continue

        filename = os.path.basename(
            path
        )

        old_record = manifest.get(
            path
        )

        # ----------------------------------------------------
        # Jika hash sama, skip
        # ----------------------------------------------------

        if (
            old_record
            and old_record.get(
                "hash"
            ) == book_hash
            and old_record.get(
                "status"
            ) == "ready"
        ):

            print(
                "SKIP kitab lama:",
                filename
            )

            continue

        # ----------------------------------------------------
        # Jika kitab berubah
        # ----------------------------------------------------

        if old_record:

            old_hash = old_record.get(
                "hash"
            )

            if old_hash:
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

            set_status(
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
                "category": get_category(
                    path
                ),
                "filename": filename,
                "updated": time.time(),
            }

            save_manifest(
                manifest
            )

        except Exception as e:

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
                "category": get_category(
                    path
                ),
                "filename": filename,
                "error": str(e),
                "updated": time.time(),
            }

            save_manifest(
                manifest
            )

        finally:

            gc.collect()

    # --------------------------------------------------------
    # Final status
    # --------------------------------------------------------

    INDEX_READY = True

    if FAILED_BOOKS:

        set_status(
            status="ready",
            message=(
                "Index siap tetapi "
                f"{len(FAILED_BOOKS)} "
                "kitab gagal."
            )
        )

    else:

        set_status(
            status="ready",
            message="Semua kitab siap."
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
# RETRIEVER
# ============================================================

def search_documents(
    question,
    category=None
):

    vectorstore = get_vectorstore()

    if category:

        try:

            docs = vectorstore.similarity_search(
                question,
                k=6,
                filter={
                    "category": category
                }
            )

        except Exception as e:

            print(
                "Filter search gagal:",
                e
            )

            docs = vectorstore.similarity_search(
                question,
                k=6
            )

    else:

        docs = vectorstore.similarity_search(
            question,
            k=6
        )

    del vectorstore

    gc.collect()

    return docs


# ============================================================
# LLM
# ============================================================

def get_llm():

    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.1,
    )


# ============================================================
# ANSWER
# ============================================================

def answer_question(
    question,
    category=None
):

    if not INDEX_READY:

        return (
            "📚 Sistem masih menyediakan kitab. "
            "Sila cuba semula sebentar lagi."
        )

    try:

        docs = search_documents(
            question,
            category
        )

        if not docs:

            return (
                "Maaf, saya tidak menemui "
                "rujukan yang sesuai dalam "
                "kitab yang telah dimasukkan."
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

            page = metadata.get(
                "chunk",
                "-"
            )

            content = (
                doc.page_content
            )

            context_parts.append(
                f"""
SUMBER:
{source}

RUJUKAN CHUNK:
{page}

TEKS:
{content}
"""
            )

        context = "\n".join(
            context_parts
        )

        # Hadkan context supaya
        # RAM dan token tidak terlalu tinggi.
        context = context[:24000]

        prompt = f"""
Anda ialah TanyaFiqhBot, pembantu
rujukan ilmu Fiqh dan Islam.

Jawab soalan pengguna dalam Bahasa Melayu
yang mudah difahami.

PENTING:
1. Jawapan mesti berdasarkan teks rujukan
   yang diberikan sahaja.
2. Jangan mereka-reka hukum atau dalil
   yang tiada dalam konteks.
3. Jika konteks tidak mencukupi, nyatakan
   bahawa rujukan tidak mencukupi.
4. Jika terdapat perbezaan pandangan,
   nyatakan berdasarkan teks yang ditemui.
5. Jangan mendakwa jawapan ini sebagai
   fatwa rasmi.
6. Nyatakan sumber kitab jika boleh.
7. Jika ada nombor halaman yang jelas
   dalam teks, sebutkan halaman tersebut.
8. Jawab secara ringkas tetapi berisi.

SOALAN PENGGUNA:
{question}

KONTEKS KITAB:
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

        del llm
        del docs
        del context

        gc.collect()

        return answer.strip()

    except Exception as e:

        print(
            "Answer error:",
            e
        )

        gc.collect()

        return (
            "Maaf, berlaku masalah ketika "
            "mencari jawapan. Sila cuba lagi."
        )


# ============================================================
# TELEGRAM
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = """
🤖 *TanyaFiqhBot*

Assalamualaikum.

Saya boleh membantu mencari jawapan
berdasarkan kitab yang telah dimasukkan
ke dalam sistem.

Contoh soalan:

• Apa hukum mandi wajib?
• Apakah rukun solat?
• Apa syarat sah puasa?
• Bagaimana cara wuduk?

Gunakan:

/fiqh - Soalan Fiqh
/tauhid - Soalan Tauhid
/semua - Cari semua kitab
/status - Status sistem
"""

    await update.message.reply_text(
        message,
        parse_mode="Markdown"
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    with INDEX_LOCK:

        status = INDEX_STATUS
        progress = dict(
            INDEX_PROGRESS
        )

    manifest = load_manifest()

    ready_books = []

    failed_books = []

    categories = {}

    for path, data in manifest.items():

        if data.get(
            "status"
        ) == "ready":

            ready_books.append(
                path
            )

            category = data.get(
                "category",
                "LAIN-LAIN"
            )

            categories[
                category
            ] = categories.get(
                category,
                0
            ) + 1

        elif data.get(
            "status"
        ) == "failed":

            failed_books.append(
                data.get(
                    "filename",
                    os.path.basename(path)
                )
            )

    text = (
        "📊 *Status TanyaFiqhBot*\n\n"
        f"Status: `{status}`\n"
        f"Index ready: `{INDEX_READY}`\n\n"
        f"Kitab siap: `{len(ready_books)}`\n"
        f"Kitab gagal: `{len(failed_books)}`\n\n"
    )

    if progress.get("book"):

        text += (
            f"📖 Proses: "
            f"{progress['book']}\n"
        )

    if progress.get("message"):

        text += (
            f"⚙️ {progress['message']}\n"
        )

    if categories:

        text += "\n📚 *Kategori:*\n"

        for category in sorted(
            categories
        ):

            text += (
                f"• {category}: "
                f"{categories[category]}\n"
            )

    if failed_books:

        text += (
            "\n❌ *Kitab gagal:*\n"
        )

        for filename in failed_books[:10]:

            text += (
                f"• {filename}\n"
            )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


async def category_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category
):

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan kitab. "
            "Sila cuba semula sebentar lagi."
        )

        return

    question = (
        update.message.text
        or ""
    )

    # Buang command.
    parts = question.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await update.message.reply_text(
            "Sila masukkan soalan selepas command.\n\n"
            f"Contoh:\n"
            f"/{category.lower()} apa hukum mandi wajib?"
        )

        return

    question = parts[1].strip()

    if not question:

        await update.message.reply_text(
            "Sila masukkan soalan."
        )

        return

    await update.message.reply_text(
        "🔎 Sedang mencari dalam kitab..."
    )

    answer = answer_question(
        question,
        category
    )

    await update.message.reply_text(
        answer
    )


async def fiqh_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await category_command(
        update,
        context,
        "FIQH"
    )


async def tauhid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await category_command(
        update,
        context,
        "TAUHID"
    )


async def semua_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan kitab. "
            "Sila cuba semula sebentar lagi."
        )

        return

    question = (
        update.message.text
        or ""
    )

    parts = question.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await update.message.reply_text(
            "Contoh:\n"
            "/semua apa hukum mandi wajib?"
        )

        return

    question = parts[1].strip()

    await update.message.reply_text(
        "🔎 Sedang mencari semua kitab..."
    )

    answer = answer_question(
        question
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# NORMAL MESSAGE
# ============================================================

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

    if not INDEX_READY:

        await update.message.reply_text(
            "📚 Sistem masih menyediakan kitab. "
            "Sila cuba semula sebentar lagi."
        )

        return

    await update.message.reply_text(
        "🔎 Sedang mencari rujukan..."
    )

    answer = answer_question(
        question
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# TELEGRAM START
# ============================================================

def run_telegram():

    if not TELEGRAM_TOKEN:

        print(
            "TELEGRAM_TOKEN tidak ditetapkan."
        )

        return

    if not GOOGLE_API_KEY:

        print(
            "GOOGLE_API_KEY tidak ditetapkan."
        )

        return

    print(
        "Memulakan Telegram bot..."
    )

    application = (
        Application.builder()
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
        CommandHandler(
            "status",
            status_command
        )
    )

    application.add_handler(
        CommandHandler(
            "fiqh",
            fiqh_command
        )
    )

    application.add_handler(
        CommandHandler(
            "tauhid",
            tauhid_command
        )
    )

    application.add_handler(
        CommandHandler(
            "semua",
            semua_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            message_handler
        )
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# BACKGROUND START
# ============================================================

def start_background_tasks():

    def indexing_worker():

        try:

            print(
                "Background indexing bermula..."
            )

            sync_books()

        except Exception as e:

            global INDEX_STATUS

            print(
                "Indexing error:",
                e
            )

            INDEX_STATUS = "error"

            set_status(
                message=str(e)
            )

            gc.collect()

    thread = threading.Thread(
        target=indexing_worker,
        daemon=True
    )

    thread.start()

    # Tunggu sekejap supaya Flask
    # sempat hidup dahulu.
    time.sleep(2)

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True
    )

    telegram_thread.start()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "=" * 60
    )

    print(
        "TanyaFiqhBot starting..."
    )

    print(
        "OCR workers:",
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
        "Kitab directory:",
        KITAB_DIR
    )

    print(
        "Data directory:",
        DATA_DIR
    )

    print(
        "=" * 60
    )

    start_background_tasks()

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
else:

    # Untuk Gunicorn:
    # gunicorn app:app
    start_background_tasks()
