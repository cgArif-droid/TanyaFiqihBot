# ============================================================
# TANYAFIQHBOT
# Full app.py
# Optimized for Render RAM ~515 MB
# ============================================================

import os
import glob
import json
import hashlib
import threading
import asyncio
import re
import time
import gc

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from langchain_community.vectorstores import Chroma

from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter
)


# ============================================================
# KONFIGURASI
# ============================================================

KITAB_DIR = os.environ.get(
    "KITAB_DIR",
    "kitab"
)

DATA_DIR = os.environ.get(
    "DATA_DIR",
    "/var/data"
)

CHROMA_DIR = os.path.join(
    DATA_DIR,
    "chroma_tanyafiqh_v2"
)

MANIFEST_FILE = os.path.join(
    DATA_DIR,
    "manifest_tanyafiqh_v2.json"
)

EXTRACTED_DIR = os.path.join(
    DATA_DIR,
    "extracted_text"
)

COLLECTION_NAME = (
    "tanyafiqh_gemini001_v2"
)

EMBEDDING_MODEL = (
    "models/gemini-embedding-001"
)

LLM_MODEL = (
    "gemini-2.5-flash"
)


# ============================================================
# OCR
# ============================================================

OCR_DPI = 200

# ------------------------------------------------------------
# 3 worker sesuai untuk RAM sekitar 515 MB.
#
# Jika masih berlaku OOM / Worker killed:
# Render Environment Variable:
#
# OCR_WORKERS=2
#
# ------------------------------------------------------------

OCR_WORKERS = int(
    os.environ.get(
        "OCR_WORKERS",
        "3"
    )
)

# Jangan benarkan nilai terlalu tinggi
# untuk server RAM kecil.

if OCR_WORKERS < 1:
    OCR_WORKERS = 1

if OCR_WORKERS > 3:
    print(
        "AMARAN: OCR_WORKERS melebihi 3. "
        "Untuk RAM 515 MB, maksimum disyorkan 3."
    )


# ------------------------------------------------------------
# Jika teks PDF kurang daripada 40 aksara,
# page akan dianggap kemungkinan scanned page.
# ------------------------------------------------------------

MIN_TEXT_CHARS = 40


# ------------------------------------------------------------
# Cache OCR setiap page
# ------------------------------------------------------------

OCR_PAGE_CACHE_DIR = os.path.join(
    EXTRACTED_DIR,
    "pages"
)


# ============================================================
# TEXT / RAG
# ============================================================

# Saiz chunk sederhana supaya penggunaan
# embedding tidak terlalu berat.

CHUNK_SIZE = 1200

CHUNK_OVERLAP = 200

RETRIEVER_K = 6


# ============================================================
# FOLDER
# ============================================================

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

os.makedirs(
    KITAB_DIR,
    exist_ok=True
)


# ============================================================
# API KEY
# ============================================================

GOOGLE_API_KEY = os.environ.get(
    "GOOGLE_API_KEY"
)

if not GOOGLE_API_KEY:

    print(
        "AMARAN: GOOGLE_API_KEY "
        "tidak dijumpai."
    )


TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN"
)

if not TELEGRAM_TOKEN:

    print(
        "AMARAN: TELEGRAM_TOKEN "
        "tidak dijumpai."
    )


# ============================================================
# EMBEDDING
# ============================================================

embeddings = GoogleGenerativeAIEmbeddings(
    model=EMBEDDING_MODEL,
    task_type="retrieval_document",
    google_api_key=GOOGLE_API_KEY,
)


# ============================================================
# CHROMA
# ============================================================

vectorstore = Chroma(
    collection_name=COLLECTION_NAME,
    persist_directory=CHROMA_DIR,
    embedding_function=embeddings,
)


# ============================================================
# TEXT SPLITTER
# ============================================================

text_splitter = (
    RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=[
            "\n\n",
            "\n",
            ". ",
            "؟ ",
            "، ",
            " ",
            "",
        ],
    )
)


# ============================================================
# LLM
# ============================================================

llm = ChatGoogleGenerativeAI(
    model=LLM_MODEL,
    temperature=0.2,
    google_api_key=GOOGLE_API_KEY,
)


# ============================================================
# LOCK
# ============================================================

index_lock = threading.Lock()

INDEX_READY = threading.Event()


# ============================================================
# STATUS
# ============================================================

INDEX_STATUS = {

    "status":
        "starting",

    "total_files":
        0,

    "processed":
        0,

    "current_file":
        "",

    "message":
        "Sistem sedang bermula...",

    # --------------------------------------------------------
    # OCR status
    # --------------------------------------------------------

    "ocr_total_pages":
        0,

    "ocr_completed_pages":
        0,

    "ocr_current_page":
        "",

    "ocr_workers":
        OCR_WORKERS,

    "ocr_progress":
        0,

    # --------------------------------------------------------
    # Current book
    # --------------------------------------------------------

    "current_book":
        "",

    "current_book_category":
        "",

    "current_book_total_pages":
        0,

    "current_book_ocr_pages":
        0,
}


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
            "Gagal membaca manifest:",
            e
        )

        return {}


def save_manifest(
    manifest
):

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
# HASH FILE
# ============================================================

def get_file_hash(
    filepath
):

    sha256 = hashlib.sha256()

    with open(
        filepath,
        "rb"
    ) as f:

        while True:

            chunk = f.read(
                1024 * 1024
            )

            if not chunk:
                break

            sha256.update(
                chunk
            )

    return sha256.hexdigest()


# ============================================================
# NAMA KITAB
# ============================================================

def get_book_info(
    filepath
):

    path = Path(filepath)

    relative = path.relative_to(
        Path(KITAB_DIR)
    )

    parts = relative.parts

    filename = path.stem

    if len(parts) >= 2:

        category = parts[0]

    else:

        category = "LAIN-LAIN"

    category = category.upper()

    return {

        "book_name":
            filename,

        "category":
            category,

        "file_name":
            path.name,

        "relative_path":
            str(relative),
    }


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(
    text
):

    if not text:

        return ""

    text = text.replace(
        "\x00",
        " "
    )

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
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


# ============================================================
# OCR LANGUAGE
# ============================================================

_OCR_LANGUAGE = None


def get_ocr_language():

    global _OCR_LANGUAGE

    # Jangan panggil get_languages()
    # setiap page kerana ia tidak diperlukan.

    if _OCR_LANGUAGE:

        return _OCR_LANGUAGE

    try:

        import pytesseract

        available = set(
            pytesseract.get_languages(
                config=""
            )
        )

        languages = []

        if "msa" in available:

            languages.append(
                "msa"
            )

        if "ara" in available:

            languages.append(
                "ara"
            )

        if "eng" in available:

            languages.append(
                "eng"
            )

        if languages:

            _OCR_LANGUAGE = "+".join(
                languages
            )

        else:

            _OCR_LANGUAGE = "eng"

    except Exception as e:

        print(
            "Gagal mendapatkan "
            "bahasa OCR:",
            e
        )

        _OCR_LANGUAGE = "eng"

    print(
        f"OCR language: "
        f"{_OCR_LANGUAGE}"
    )

    return _OCR_LANGUAGE


# ============================================================
# OCR SATU PAGE
# ============================================================

def ocr_page(
    filepath,
    page_number
):

    from pdf2image import (
        convert_from_path
    )

    import pytesseract

    language = (
        get_ocr_language()
    )

    print(
        f"OCR page {page_number} "
        f"menggunakan {language}"
    )

    images = None

    try:

        images = convert_from_path(

            filepath,

            dpi=OCR_DPI,

            first_page=page_number,

            last_page=page_number,

            fmt="jpeg",

            grayscale=True,

            use_pdftocairo=True,

            thread_count=1,
        )

        if not images:

            return ""

        image = images[0]

        try:

            text = (
                pytesseract.image_to_string(

                    image,

                    lang=language,

                    config="--psm 6",
                )
            )

            return clean_text(
                text
            )

        finally:

            try:

                image.close()

            except Exception:

                pass

    finally:

        # Lepaskan reference image
        # secepat mungkin.

        images = None

        gc.collect()


# ============================================================
# PAGE CACHE
# ============================================================

def get_page_cache_dir(
    file_hash
):

    directory = os.path.join(
        OCR_PAGE_CACHE_DIR,
        file_hash
    )

    os.makedirs(
        directory,
        exist_ok=True
    )

    return directory


def get_page_cache_file(
    file_hash,
    page_number
):

    return os.path.join(

        get_page_cache_dir(
            file_hash
        ),

        f"page_{page_number:05d}.json"
    )


def save_page_cache(
    file_hash,
    page_number,
    text
):

    # --------------------------------------------------------
    # Jangan simpan cache kosong.
    #
    # Ini penting:
    # jika OCR gagal, page perlu dicuba semula
    # pada run seterusnya.
    # --------------------------------------------------------

    if not text:

        return False

    cache_file = (
        get_page_cache_file(
            file_hash,
            page_number
        )
    )

    temp_file = (
        cache_file
        + ".tmp"
    )

    data = {

        "page_number":
            page_number,

        "text":
            text,

        "saved_at":
            time.time(),
    }

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False
            )

        os.replace(
            temp_file,
            cache_file
        )

        return True

    except Exception as e:

        print(
            f"Gagal simpan cache "
            f"page {page_number}:",
            e
        )

        try:

            if os.path.exists(
                temp_file
            ):

                os.remove(
                    temp_file
                )

        except Exception:

            pass

        return False


def load_page_cache(
    file_hash,
    page_number
):

    cache_file = (
        get_page_cache_file(
            file_hash,
            page_number
        )
    )

    if not os.path.exists(
        cache_file
    ):

        return None

    try:

        with open(
            cache_file,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )

        text = data.get(
            "text"
        )

        if not text:

            return None

        return clean_text(
            text
        )

    except Exception as e:

        print(
            f"Cache page "
            f"{page_number} rosak:",
            e
        )

        return None


# ============================================================
# EXTRACT PDF
# ============================================================

def extract_pdf(
    filepath,
    file_hash
):

    from pypdf import PdfReader

    reader = PdfReader(
        filepath
    )

    total_pages = len(
        reader.pages
    )

    info = get_book_info(
        filepath
    )

    INDEX_STATUS[
        "current_book"
    ] = info["book_name"]

    INDEX_STATUS[
        "current_book_category"
    ] = info["category"]

    INDEX_STATUS[
        "current_book_total_pages"
    ] = total_pages

    print(
        "\n================================"
    )

    print(
        f"PDF: {filepath}"
    )

    print(
        f"Jumlah halaman: "
        f"{total_pages}"
    )

    print(
        f"OCR workers: "
        f"{OCR_WORKERS}"
    )

    print(
        "================================\n"
    )

    # --------------------------------------------------------
    # Semua page
    # --------------------------------------------------------

    pages = {}

    # --------------------------------------------------------
    # Page perlu OCR
    # --------------------------------------------------------

    pages_needing_ocr = []

    # --------------------------------------------------------
    # Page cache yang sudah berjaya
    # --------------------------------------------------------

    cached_count = 0

    # --------------------------------------------------------
    # Extract text dahulu
    # --------------------------------------------------------

    for index, page in enumerate(
        reader.pages,
        start=1
    ):

        try:

            text = (
                page.extract_text()
                or ""
            )

        except Exception as e:

            print(
                f"Gagal extract "
                f"page {index}:",
                e
            )

            text = ""

        text = clean_text(
            text
        )

        # ----------------------------------------------------
        # Teks cukup panjang
        # ----------------------------------------------------

        if len(text) >= MIN_TEXT_CHARS:

            pages[index] = text

            continue

        # ----------------------------------------------------
        # Semak OCR cache
        # ----------------------------------------------------

        cached_text = (
            load_page_cache(
                file_hash,
                index
            )
        )

        if cached_text is not None:

            print(
                f"Page {index}: "
                f"GUNA CACHE OCR "
                f"({len(cached_text)} chars)"
            )

            if len(cached_text) > len(text):

                text = cached_text

            pages[index] = text

            cached_count += 1

            continue

        # ----------------------------------------------------
        # Perlu OCR
        # ----------------------------------------------------

        print(
            f"Page {index}: "
            f"teks sedikit "
            f"({len(text)} chars) "
            f"-> QUEUE OCR"
        )

        pages_needing_ocr.append(
            (
                index,
                text
            )
        )

    # --------------------------------------------------------
    # OCR STATUS
    # --------------------------------------------------------

    total_ocr = len(
        pages_needing_ocr
    )

    INDEX_STATUS[
        "ocr_total_pages"
    ] = total_ocr

    INDEX_STATUS[
        "ocr_completed_pages"
    ] = cached_count

    INDEX_STATUS[
        "current_book_ocr_pages"
    ] = total_ocr

    if total_ocr > 0:

        INDEX_STATUS[
            "ocr_progress"
        ] = int(
            (
                cached_count
                / (
                    total_ocr
                    + cached_count
                )
            ) * 100
        )

    else:

        INDEX_STATUS[
            "ocr_progress"
        ] = 100

    print(
        "\n================================"
    )

    print(
        f"Jumlah page perlu OCR: "
        f"{total_ocr}"
    )

    print(
        f"Cache OCR tersedia: "
        f"{cached_count}"
    )

    print(
        f"OCR workers: "
        f"{OCR_WORKERS}"
    )

    print(
        "================================\n"
    )

    # --------------------------------------------------------
    # PARALLEL OCR
    # --------------------------------------------------------

    if pages_needing_ocr:

        completed_ocr = cached_count

        with ThreadPoolExecutor(
            max_workers=OCR_WORKERS
        ) as executor:

            future_map = {}

            for (
                page_number,
                original_text
            ) in pages_needing_ocr:

                future = executor.submit(

                    ocr_page,

                    filepath,

                    page_number
                )

                future_map[
                    future
                ] = (
                    page_number,
                    original_text
                )

            for future in as_completed(
                future_map
            ):

                (
                    page_number,
                    original_text
                ) = future_map[
                    future
                ]

                try:

                    ocr_text = (
                        future.result()
                    )

                    # ------------------------------------------------
                    # Pilih teks paling panjang
                    # ------------------------------------------------

                    if len(ocr_text) > len(
                        original_text
                    ):

                        final_text = (
                            ocr_text
                        )

                    else:

                        final_text = (
                            original_text
                        )

                    pages[
                        page_number
                    ] = final_text

                    # ------------------------------------------------
                    # Hanya simpan cache jika ada teks
                    # ------------------------------------------------

                    if final_text:

                        save_page_cache(

                            file_hash,

                            page_number,

                            final_text
                        )

                    completed_ocr += 1

                    INDEX_STATUS[
                        "ocr_completed_pages"
                    ] = completed_ocr

                    total_done = (
                        total_ocr
                        + cached_count
                    )

                    if total_done > 0:

                        INDEX_STATUS[
                            "ocr_progress"
                        ] = int(
                            (
                                completed_ocr
                                / total_done
                            ) * 100
                        )

                    INDEX_STATUS[
                        "ocr_current_page"
                    ] = page_number

                    print(
                        f"✅ OCR selesai "
                        f"page {page_number} "
                        f"({completed_ocr}/"
                        f"{total_done})"
                    )

                except Exception as e:

                    print(
                        f"❌ OCR page "
                        f"{page_number} gagal:",
                        e
                    )

                    # ------------------------------------------------
                    # Guna text asal jika ada.
                    #
                    # JANGAN simpan cache kosong.
                    # Page ini akan dicuba semula
                    # pada restart akan datang.
                    # ------------------------------------------------

                    pages[
                        page_number
                    ] = original_text

                    completed_ocr += 1

                    INDEX_STATUS[
                        "ocr_completed_pages"
                    ] = completed_ocr

                    total_done = (
                        total_ocr
                        + cached_count
                    )

                    if total_done > 0:

                        INDEX_STATUS[
                            "ocr_progress"
                        ] = int(
                            (
                                completed_ocr
                                / total_done
                            ) * 100
                        )

    # --------------------------------------------------------
    # Susun page
    # --------------------------------------------------------

    result = []

    for page_number in sorted(
        pages.keys()
    ):

        text = clean_text(
            pages[page_number]
        )

        if not text:

            continue

        result.append({

            "page_number":
                page_number,

            "text":
                text,
        })

    # --------------------------------------------------------
    # Release reader
    # --------------------------------------------------------

    try:

        reader.stream.close()

    except Exception:

        pass

    reader = None

    gc.collect()

    INDEX_STATUS[
        "ocr_progress"
    ] = 100

    print(
        "\n================================"
    )

    print(
        f"Extraction selesai: "
        f"{len(result)}/"
        f"{total_pages} halaman "
        f"mempunyai teks."
    )

    print(
        "================================\n"
    )

    return result


# ============================================================
# EXTRACT TXT
# ============================================================

def extract_txt(
    filepath
):

    try:

        with open(
            filepath,
            "r",
            encoding="utf-8"
        ) as f:

            text = f.read()

    except UnicodeDecodeError:

        with open(
            filepath,
            "r",
            encoding="utf-8-sig"
        ) as f:

            text = f.read()

    text = clean_text(
        text
    )

    if not text:

        return []

    return [

        {
            "page_number":
                None,

            "text":
                text
        }

    ]


# ============================================================
# CACHE EXTRACTION
# ============================================================

def get_extraction_cache(
    hash_value
):

    return os.path.join(

        EXTRACTED_DIR,

        f"{hash_value}.json"
    )


def save_extraction_cache(
    hash_value,
    pages
):

    cache_file = (
        get_extraction_cache(
            hash_value
        )
    )

    temp_file = (
        cache_file
        + ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                pages,
                f,
                ensure_ascii=False
            )

        os.replace(
            temp_file,
            cache_file
        )

    except Exception as e:

        print(
            "Gagal simpan "
            "cache extraction:",
            e
        )

        try:

            if os.path.exists(
                temp_file
            ):

                os.remove(
                    temp_file
                )

        except Exception:

            pass


def load_extraction_cache(
    hash_value
):

    cache_file = (
        get_extraction_cache(
            hash_value
        )
    )

    if not os.path.exists(
        cache_file
    ):

        return None

    try:

        with open(
            cache_file,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(
                f
            )

    except Exception as e:

        print(
            "Cache extraction rosak:",
            e
        )

        return None


# ============================================================
# DAPATKAN SEMUA KITAB
# ============================================================

def get_all_books():

    files = []

    patterns = [

        "**/*.pdf",

        "**/*.PDF",

        "**/*.txt",

        "**/*.TXT",
    ]

    for pattern in patterns:

        files.extend(

            glob.glob(

                os.path.join(

                    KITAB_DIR,

                    pattern
                ),

                recursive=True
            )
        )

    return sorted(
        set(files)
    )


# ============================================================
# BUAT DOCUMENT
# ============================================================

def create_documents_for_book(
    filepath,
    file_hash
):

    info = get_book_info(
        filepath
    )

    extension = (
        Path(filepath)
        .suffix
        .lower()
    )

    # --------------------------------------------------------
    # Cuba whole-book cache dahulu
    # --------------------------------------------------------

    cached = (
        load_extraction_cache(
            file_hash
        )
    )

    if cached is not None:

        print(
            f"Gunakan cache extraction: "
            f"{info['book_name']}"
        )

        pages = cached

    else:

        if extension == ".pdf":

            pages = extract_pdf(

                filepath,

                file_hash
            )

        elif extension == ".txt":

            pages = extract_txt(
                filepath
            )

        else:

            return []

        # ----------------------------------------------------
        # Simpan whole-book cache
        # ----------------------------------------------------

        save_extraction_cache(

            file_hash,

            pages
        )

    # --------------------------------------------------------
    # LangChain documents
    # --------------------------------------------------------

    documents = []

    from langchain_core.documents import (
        Document
    )

    for page in pages:

        text = page.get(
            "text",
            ""
        )

        if not text.strip():

            continue

        metadata = {

            "source_file":
                info["file_name"],

            "source_path":
                info["relative_path"],

            "book_name":
                info["book_name"],

            "category":
                info["category"],

            "file_type":
                extension.replace(
                    ".",
                    ""
                ),

            "file_hash":
                file_hash,

            "source_id":
                file_hash,

            "page_number":
                page.get(
                    "page_number"
                ),
        }

        documents.append(

            Document(

                page_content=text,

                metadata=metadata
            )
        )

    # --------------------------------------------------------
    # Split
    # --------------------------------------------------------

    splits = (
        text_splitter
        .split_documents(
            documents
        )
    )

    # --------------------------------------------------------
    # Chunk index
    # --------------------------------------------------------

    for index, doc in enumerate(
        splits
    ):

        doc.metadata[
            "chunk_index"
        ] = index

    # --------------------------------------------------------
    # Release
    # --------------------------------------------------------

    documents = None

    pages = None

    gc.collect()

    return splits


# ============================================================
# ID CHUNK
# ============================================================

def create_chunk_id(
    file_hash,
    doc
):

    page = doc.metadata.get(
        "page_number",
        "none"
    )

    chunk_index = doc.metadata.get(
        "chunk_index",
        0
    )

    raw = (

        f"{file_hash}|"

        f"{page}|"

        f"{chunk_index}|"

        f"{doc.page_content}"
    )

    return hashlib.sha256(

        raw.encode(
            "utf-8"
        )

    ).hexdigest()


# ============================================================
# DELETE SOURCE
# ============================================================

def delete_source(
    source_id
):

    try:

        vectorstore.delete(

            where={

                "source_id":
                    source_id
            }
        )

        print(
            f"Vector lama dipadam: "
            f"{source_id}"
        )

    except Exception as e:

        print(
            "Gagal delete source:",
            e
        )


# ============================================================
# INDEX SATU KITAB
# ============================================================

def index_book(
    filepath,
    old_record=None
):

    info = get_book_info(
        filepath
    )

    print(
        "\n================================"
    )

    print(
        f"INDEX KITAB: "
        f"{info['book_name']}"
    )

    print(
        f"KATEGORI: "
        f"{info['category']}"
    )

    print(
        "================================"
    )

    INDEX_STATUS[
        "current_book"
    ] = info["book_name"]

    INDEX_STATUS[
        "current_book_category"
    ] = info["category"]

    file_hash = (
        get_file_hash(
            filepath
        )
    )

    # --------------------------------------------------------
    # Jika hash sama:
    # skip sepenuhnya
    # --------------------------------------------------------

    if (

        old_record

        and old_record.get(
            "hash"
        )

        == file_hash

    ):

        print(
            "Kitab tidak berubah. "
            "Langkau."
        )

        return {

            "hash":
                file_hash,

            "book_name":
                info["book_name"],

            "category":
                info["category"],

            "file_name":
                info["file_name"],

            "relative_path":
                info["relative_path"],

            "indexed_at":
                time.time(),

            "skipped":
                True,
        }

    # --------------------------------------------------------
    # Padam partial vector dengan hash baru
    # --------------------------------------------------------

    delete_source(
        file_hash
    )

    # --------------------------------------------------------
    # Reset OCR status
    # --------------------------------------------------------

    INDEX_STATUS[
        "ocr_total_pages"
    ] = 0

    INDEX_STATUS[
        "ocr_completed_pages"
    ] = 0

    INDEX_STATUS[
        "ocr_current_page"
    ] = ""

    INDEX_STATUS[
        "ocr_progress"
    ] = 0

    # --------------------------------------------------------
    # Extract
    # --------------------------------------------------------

    splits = (
        create_documents_for_book(

            filepath,

            file_hash
        )
    )

    if not splits:

        print(
            "TIADA TEKS DIJUMPAI:"
            f" {filepath}"
        )

        raise RuntimeError(

            "Kitab tidak mempunyai "
            "teks yang boleh diproses."
        )

    print(
        f"Jumlah chunk: "
        f"{len(splits)}"
    )

    # --------------------------------------------------------
    # Chunk IDs
    # --------------------------------------------------------

    ids = []

    for doc in splits:

        ids.append(

            create_chunk_id(

                file_hash,

                doc
            )
        )

    # --------------------------------------------------------
    # Embedding batch
    #
    # 16 lebih ringan untuk RAM kecil.
    # --------------------------------------------------------

    batch_size = 16

    try:

        total_chunks = len(
            splits
        )

        for start in range(

            0,

            total_chunks,

            batch_size
        ):

            end = (
                start
                + batch_size
            )

            batch_docs = splits[
                start:end
            ]

            batch_ids = ids[
                start:end
            ]

            print(

                f"Embedding "

                f"{start + 1}-"

                f"{min("
                    f"end, "
                    f"total_chunks"
                )}/"

                f"{total_chunks}"
            )

            vectorstore.add_documents(

                documents=batch_docs,

                ids=batch_ids
            )

            # ------------------------------------------------
            # Release temporary batch references
            # ------------------------------------------------

            batch_docs = None

            batch_ids = None

            gc.collect()

    except Exception as e:

        print(
            "Embedding gagal. "
            "Membersihkan vector "
            "separa..."
        )

        delete_source(
            file_hash
        )

        raise e

    # --------------------------------------------------------
    # Jika kitab berubah:
    # delete hash lama selepas index baharu berjaya.
    # --------------------------------------------------------

    if old_record:

        old_hash = (
            old_record.get(
                "hash"
            )
        )

        if (

            old_hash

            and old_hash != file_hash

        ):

            delete_source(
                old_hash
            )

    # --------------------------------------------------------
    # Release
    # --------------------------------------------------------

    splits = None

    ids = None

    gc.collect()

    print(
        f"BERJAYA INDEX: "
        f"{info['book_name']}"
    )

    return {

        "hash":
            file_hash,

        "book_name":
            info["book_name"],

        "category":
            info["category"],

        "file_name":
            info["file_name"],

        "relative_path":
            info["relative_path"],

        "chunks":
            total_chunks,

        "indexed_at":
            time.time(),

        "skipped":
            False,
    }


# ============================================================
# SYNC SEMUA KITAB
# ============================================================

def sync_books():

    with index_lock:

        INDEX_STATUS[
            "status"
        ] = "indexing"

        INDEX_STATUS[
            "message"
        ] = (
            "Sedang memproses kitab..."
        )

        INDEX_READY.clear()

        try:

            manifest = (
                load_manifest()
            )

            files = (
                get_all_books()
            )

            INDEX_STATUS[
                "total_files"
            ] = len(files)

            INDEX_STATUS[
                "processed"
            ] = 0

            print(
                "\n================================"
            )

            print(
                f"JUMLAH KITAB/FAIL: "
                f"{len(files)}"
            )

            print(
                f"OCR WORKERS: "
                f"{OCR_WORKERS}"
            )

            print(
                "================================\n"
            )

            current_paths = set(

                os.path.abspath(f)

                for f in files
            )

            # ------------------------------------------------
            # Index new / changed
            # ------------------------------------------------

            for filepath in files:

                abs_path = (
                    os.path.abspath(
                        filepath
                    )
                )

                INDEX_STATUS[
                    "current_file"
                ] = os.path.basename(
                    filepath
                )

                relative_path = str(

                    Path(filepath)

                    .relative_to(
                        Path(KITAB_DIR)
                    )
                )

                old_record = (
                    manifest.get(
                        relative_path
                    )
                )

                try:

                    result = (
                        index_book(

                            filepath,

                            old_record
                        )
                    )

                    manifest[
                        relative_path
                    ] = result

                    # ------------------------------------------------
                    # Simpan manifest setiap kitab
                    # ------------------------------------------------

                    save_manifest(
                        manifest
                    )

                except Exception as e:

                    print(
                        f"GAGAL index "
                        f"{filepath}: {e}"
                    )

                INDEX_STATUS[
                    "processed"
                ] += 1

                gc.collect()

            # ------------------------------------------------
            # Buang kitab yang sudah tiada
            # ------------------------------------------------

            old_paths = list(
                manifest.keys()
            )

            for old_relative in (
                old_paths
            ):

                old_abs = (

                    os.path.abspath(

                        os.path.join(

                            KITAB_DIR,

                            old_relative
                        )
                    )
                )

                if (
                    old_abs
                    not in current_paths
                ):

                    print(

                        "Kitab sudah dipadam:"
                        f" {old_relative}"
                    )

                    old_record = (
                        manifest[
                            old_relative
                        ]
                    )

                    old_hash = (
                        old_record.get(
                            "hash"
                        )
                    )

                    if old_hash:

                        delete_source(
                            old_hash
                        )

                    del manifest[
                        old_relative
                    ]

                    save_manifest(
                        manifest
                    )

            # ------------------------------------------------
            # Selesai
            # ------------------------------------------------

            INDEX_STATUS[
                "status"
            ] = "ready"

            INDEX_STATUS[
                "message"
            ] = (
                "Semua kitab selesai "
                "diproses."
            )

            INDEX_STATUS[
                "current_file"
            ] = ""

            INDEX_STATUS[
                "current_book"
            ] = ""

            INDEX_READY.set()

            print(
                "\n================================"
            )

            print(
                "INDEXING SELESAI"
            )

            print(
                "================================\n"
            )

        except Exception as e:

            INDEX_STATUS[
                "status"
            ] = "error"

            INDEX_STATUS[
                "message"
            ] = str(e)

            print(
                "RALAT SYNC BESAR:",
                e
            )

            INDEX_READY.set()


# ============================================================
# FORMAT RUJUKAN
# ============================================================

def format_sources(
    documents
):

    if not documents:

        return ""

    seen = set()

    sources = []

    for doc in documents:

        metadata = doc.metadata

        book = metadata.get(
            "book_name",
            "Tidak diketahui"
        )

        category = metadata.get(
            "category",
            "LAIN-LAIN"
        )

        page = metadata.get(
            "page_number"
        )

        key = (
            book,
            page,
            category
        )

        if key in seen:

            continue

        seen.add(key)

        if page:

            sources.append(

                f"📖 {book} "
                f"— {category} "
                f"— halaman {page}"
            )

        else:

            sources.append(

                f"📖 {book} "
                f"— {category}"
            )

    if not sources:

        return ""

    return (

        "\n\n"

        "📚 RUJUKAN:\n"

        + "\n".join(
            sources
        )
    )


# ============================================================
# FORMAT CONTEXT
# ============================================================

def format_context(
    documents
):

    output = []

    for doc in documents:

        metadata = doc.metadata

        book = metadata.get(
            "book_name",
            "Tidak diketahui"
        )

        category = metadata.get(
            "category",
            "LAIN-LAIN"
        )

        page = metadata.get(
            "page_number"
        )

        if page:

            source = (

                f"[Kitab: {book} | "
                f"Kategori: {category} | "
                f"Halaman: {page}]"
            )

        else:

            source = (

                f"[Kitab: {book} | "
                f"Kategori: {category}]"
            )

        output.append(

            source

            + "\n"

            + doc.page_content
        )

    return "\n\n---\n\n".join(
        output
    )


# ============================================================
# CARI KITAB
# ============================================================

def search_documents(
    question,
    category=None
):

    search_kwargs = {

        "k":
            RETRIEVER_K
    }

    if category:

        search_kwargs[
            "filter"
        ] = {

            "category":
                category.upper()
        }

    retriever = (

        vectorstore

        .as_retriever(

            search_kwargs=
                search_kwargs
        )
    )

    return retriever.invoke(
        question
    )


# ============================================================
# JAWAB SOALAN
# ============================================================

def answer_question(
    question,
    category=None
):

    documents = (
        search_documents(

            question,

            category
        )
    )

    if not documents:

        return (

            "Maaf, saya tidak menemui "
            "rujukan yang sesuai dalam "
            "kitab yang telah dimasukkan."
        )

    context = (
        format_context(
            documents
        )
    )

    category_instruction = ""

    if category:

        category_instruction = (

            f"\nSoalan ini diminta "
            f"dicari khusus dalam "
            f"kategori "
            f"{category.upper()}."
        )

    prompt = f"""

Anda ialah TanyaFiqhBot,
pembantu rujukan ilmu Islam
berasaskan kitab.

PENTING:

1. Jawab berdasarkan KONTEXT KITAB
   yang diberikan sahaja.

2. Jangan reka fakta, hadis,
   hukum, nama kitab atau rujukan
   yang tidak terdapat dalam konteks.

3. Jika konteks tidak mencukupi,
   nyatakan bahawa maklumat tersebut
   tidak ditemui dalam kitab yang
   sedang dirujuk.

4. Jika terdapat perbezaan pandangan
   antara kitab, nyatakan perbezaan
   tersebut dengan jelas.

5. Jangan mendakwa jawapan anda
   sebagai fatwa rasmi.

6. Gunakan Bahasa Melayu yang mudah,
   jelas dan sopan.

7. Jika sesuai, nyatakan nama kitab
   dan halaman berdasarkan konteks.

8. Jangan mencipta nombor halaman.

9. Untuk soalan hukum, terangkan
   jawapan berdasarkan teks kitab
   yang diberikan.

10. Jika teks OCR kelihatan tidak jelas,
    jangan meneka perkataan yang hilang.

{category_instruction}

KONTEKS KITAB:
----------------

{context}

----------------

SOALAN PENGGUNA:
{question}

Berikan jawapan yang jelas dan
ringkas tetapi mencukupi.

"""

    try:

        response = (
            llm.invoke(
                prompt
            )
        )

        answer = response.content

        if isinstance(
            answer,
            list
        ):

            answer = "".join(

                str(x)

                for x in answer
            )

        answer = str(
            answer
        ).strip()

    except Exception as e:

        print(
            "LLM ERROR:",
            e
        )

        return (

            "Maaf, berlaku ralat "
            "semasa menghasilkan "
            "jawapan."
        )

    source_text = (
        format_sources(
            documents
        )
    )

    return (
        answer
        + source_text
    )


# ============================================================
# TELEGRAM START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = """

🤖 *TanyaFiqhBot*

Assalamualaikum.

Saya membantu mencari jawapan
berdasarkan kitab yang telah
dimasukkan ke dalam sistem.

📚 Kategori yang tersedia:

/fiqh
→ Cari dalam kitab FIQH

/tauhid
→ Cari dalam kitab TAUHID

/semua
→ Cari dalam semua kitab

Contoh:

/fiqh Apakah hukum wuduk?

/tauhid Apakah maksud tauhid rububiyyah?

/semua Apakah hukum membaca al-Quran?

"""

    await update.message.reply_text(

        message,

        parse_mode="Markdown"
    )


# ============================================================
# TELEGRAM FIQH
# ============================================================

async def fiqh_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    question = " ".join(
        context.args
    ).strip()

    if not question:

        await update.message.reply_text(

            "Contoh:\n\n"
            "/fiqh Apakah hukum wuduk?"
        )

        return

    await process_question(

        update,

        question,

        "FIQH"
    )


# ============================================================
# TELEGRAM TAUHID
# ============================================================

async def tauhid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    question = " ".join(
        context.args
    ).strip()

    if not question:

        await update.message.reply_text(

            "Contoh:\n\n"

            "/tauhid Apakah maksud "
            "tauhid rububiyyah?"
        )

        return

    await process_question(

        update,

        question,

        "TAUHID"
    )


# ============================================================
# TELEGRAM SEMUA
# ============================================================

async def semua_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    question = " ".join(
        context.args
    ).strip()

    if not question:

        await update.message.reply_text(

            "Contoh:\n\n"

            "/semua Apakah hukum membaca "
            "al-Quran?"
        )

        return

    await process_question(

        update,

        question,

        None
    )


# ============================================================
# PROCESS SOALAN
# ============================================================

async def process_question(
    update,
    question,
    category
):

    if not INDEX_READY.is_set():

        await update.message.reply_text(

            "📚 Sistem masih menyediakan "
            "kitab. Sila cuba semula "
            "sebentar lagi."
        )

        return

    loading = (

        await update.message.reply_text(

            "🔎 Sedang mencari jawapan "
            "dalam kitab..."
        )
    )

    try:

        answer = await asyncio.to_thread(

            answer_question,

            question,

            category
        )

    except Exception as e:

        print(
            "QUESTION ERROR:",
            e
        )

        answer = (

            "Maaf, berlaku ralat "
            "semasa mencari jawapan."
        )

    try:

        await loading.delete()

    except Exception:

        pass

    # Telegram maksimum hampir 4096 chars.
    max_length = 3900

    if len(answer) <= max_length:

        await update.message.reply_text(
            answer
        )

        return

    chunks = []

    current = ""

    for paragraph in answer.split(
        "\n\n"
    ):

        if (

            len(current)

            + len(paragraph)

            + 2

            > max_length

        ):

            if current:

                chunks.append(
                    current
                )

            current = paragraph

        else:

            if current:

                current += "\n\n"

            current += paragraph

    if current:

        chunks.append(
            current
        )

    for chunk in chunks:

        await update.message.reply_text(
            chunk
        )


# ============================================================
# TELEGRAM MESSAGE BIASA
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

    print(
        f"Soalan diterima: "
        f"{question}"
    )

    await process_question(

        update,

        question,

        None
    )


# ============================================================
# TELEGRAM BOT
# ============================================================

def run_telegram():

    if not TELEGRAM_TOKEN:

        print(

            "Telegram tidak dijalankan "
            "kerana TELEGRAM_TOKEN tiada."
        )

        return

    try:

        async def telegram_main():

            application = (

                ApplicationBuilder()

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
                    & (
                        ~filters.COMMAND
                    ),

                    handle_message
                )
            )

            print(
                "Telegram bot sedang "
                "berjalan..."
            )

            await application.initialize()

            await application.start()

            await (
                application
                .updater
                .start_polling()
            )

            while True:

                await asyncio.sleep(
                    3600
                )

        asyncio.run(
            telegram_main()
        )

    except Exception as e:

        print(
            "Telegram ERROR:",
            e
        )


# ============================================================
# FLASK
# ============================================================

app = Flask(
    __name__
)


@app.route("/")
def home():

    return (

        "TanyaFiqhBot sedang aktif. "
        "Kategori: FIQH, TAUHID."
    )


@app.route("/health")
def health():

    return {

        "status":
            INDEX_STATUS[
                "status"
            ],

        "message":
            INDEX_STATUS[
                "message"
            ],

        "total_files":
            INDEX_STATUS[
                "total_files"
            ],

        "processed":
            INDEX_STATUS[
                "processed"
            ],

        "current_file":
            INDEX_STATUS[
                "current_file"
            ],

        "current_book":
            INDEX_STATUS[
                "current_book"
            ],

        "current_book_category":
            INDEX_STATUS[
                "current_book_category"
            ],

        "ocr_workers":
            OCR_WORKERS,

        "ocr_total_pages":
            INDEX_STATUS[
                "ocr_total_pages"
            ],

        "ocr_completed_pages":
            INDEX_STATUS[
                "ocr_completed_pages"
            ],

        "ocr_current_page":
            INDEX_STATUS[
                "ocr_current_page"
            ],

        "ocr_progress":
            INDEX_STATUS[
                "ocr_progress"
            ],
    }


@app.route("/status")
def status():

    return {

        "bot":
            "TanyaFiqhBot",

        "index":
            INDEX_STATUS,

        "ready":
            INDEX_READY.is_set(),

        "ocr_workers":
            OCR_WORKERS,

        "categories":
            get_categories(),
    }


# ============================================================
# KATEGORI AUTOMATIK
# ============================================================

def get_categories():

    categories = set()

    try:

        for filepath in get_all_books():

            info = get_book_info(
                filepath
            )

            categories.add(
                info["category"]
            )

    except Exception as e:

        print(
            "Gagal mendapatkan kategori:",
            e
        )

    return sorted(
        categories
    )


# ============================================================
# START BACKGROUND
# ============================================================

def start_background_tasks():

    # --------------------------------------------------------
    # Index kitab
    # --------------------------------------------------------

    indexing_thread = (
        threading.Thread(

            target=sync_books,

            daemon=True
        )
    )

    indexing_thread.start()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    telegram_thread = (
        threading.Thread(

            target=run_telegram,

            daemon=True
        )
    )

    telegram_thread.start()


# ============================================================
# START
# ============================================================

start_background_tasks()


if __name__ == "__main__":

    port = int(

        os.environ.get(

            "PORT",

            8080
        )
    )

    app.run(

        host="0.0.0.0",

        port=port
    )
