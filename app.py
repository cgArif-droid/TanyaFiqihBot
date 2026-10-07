import os
import re
import json
import time
import asyncio
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import pytesseract

from flask import Flask, jsonify

from pdf2image import convert_from_path

from supabase import create_client

from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

DATA_DIR = Path(
    os.getenv("DATA_DIR", "/var/data")
)

KITAB_DIR = Path(
    os.getenv("KITAB_DIR", "/app/kitab")
)

EXTRACTED_DIR = DATA_DIR / "extracted_text"

PAGE_CACHE_DIR = EXTRACTED_DIR / "pages"

MANIFEST_FILE = DATA_DIR / "manifest.json"

OCR_WORKERS = int(
    os.getenv("OCR_WORKERS", "1")
)

OCR_DPI = int(
    os.getenv("OCR_DPI", "200")
)

EMBED_BATCH_SIZE = int(
    os.getenv("EMBED_BATCH_SIZE", "16")
)

LOCAL_SEARCH_K = int(
    os.getenv("LOCAL_SEARCH_K", "8")
)

TURATH_SEARCH_K = int(
    os.getenv("TURATH_SEARCH_K", "8")
)

FINAL_CONTEXT_K = int(
    os.getenv("FINAL_CONTEXT_K", "8")
)

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
)

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN"
)

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY"
)

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY"
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


def normalize_category(
    value: str
) -> str:

    if not value:
        return "FIQH"

    value = str(
        value
    ).strip().upper()

    aliases = {
        "USUL": "USUL FIQH",
        "USULFIQH": "USUL FIQH",
        "HADITH": "HADIS",
        "AQIDAH": "TAUHID",
        "AKIDAH": "TAUHID",
    }

    if value in aliases:
        return aliases[value]

    if value in CATEGORIES:
        return value

    return "FIQH"


def detect_category_from_path(
    path: Path
) -> str:

    try:

        relative = path.relative_to(
            KITAB_DIR
        )

        if len(relative.parts) >= 2:

            return normalize_category(
                relative.parts[0]
            )

    except Exception:
        pass

    return "FIQH"


# ============================================================
# DIRECTORIES
# ============================================================

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True
)

EXTRACTED_DIR.mkdir(
    parents=True,
    exist_ok=True
)

PAGE_CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CLIENTS
# ============================================================

supabase = None

gemini_client = None


# ============================================================
# SUPABASE
# ============================================================

if SUPABASE_URL and SUPABASE_KEY:

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print(
            "✅ Supabase connected"
        )

    except Exception as error:

        print(
            "❌ Supabase connection error:",
            repr(error)
        )

else:

    print(
        "⚠️ SUPABASE_URL / SUPABASE_KEY "
        "tidak lengkap"
    )


# ============================================================
# GEMINI
# ============================================================

if GOOGLE_API_KEY:

    try:

        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print(
            f"✅ Gemini ready: {LLM_MODEL}"
        )

        print(
            f"🧠 Embedding model: {EMBEDDING_MODEL}"
        )

    except Exception as error:

        print(
            "❌ Gemini init error:",
            repr(error)
        )

else:

    print(
        "⚠️ GOOGLE_API_KEY tidak ditetapkan"
    )


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(
    value: Any
) -> str:

    if value is None:
        return ""

    text = str(
        value
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def truncate_text(
    text: str,
    max_chars: int = 5000
) -> str:

    text = clean_text(
        text
    )

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "..."
    )


def sha256_file(
    path: Path
) -> str:

    hasher = hashlib.sha256()

    with path.open(
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            hasher.update(
                chunk
            )

    return hasher.hexdigest()


def load_manifest() -> Dict[str, Any]:

    if not MANIFEST_FILE.exists():
        return {}

    try:

        return json.loads(
            MANIFEST_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as error:

        print(
            "⚠️ Manifest read error:",
            repr(error)
        )

        return {}


def save_manifest(
    manifest: Dict[str, Any]
):

    try:

        MANIFEST_FILE.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    except Exception as error:

        print(
            "❌ Manifest save error:",
            repr(error)
        )


# ============================================================
# PDF / TXT EXTRACTION
# ============================================================

def extract_txt(
    path: Path
) -> str:

    try:

        return path.read_text(
            encoding="utf-8",
            errors="ignore"
        )

    except Exception as error:

        print(
            f"❌ TXT ERROR {path}:",
            repr(error)
        )

        return ""


def ocr_pdf(
    path: Path
) -> str:

    print(
        f"🧾 OCR PDF: {path.name}"
    )

    try:

        images = convert_from_path(
            str(path),
            dpi=OCR_DPI,
            thread_count=OCR_WORKERS
        )

    except Exception as error:

        print(
            "❌ PDF conversion error:",
            repr(error)
        )

        return ""

    book_hash = hashlib.sha256(
        str(path).encode(
            "utf-8"
        )
    ).hexdigest()[:16]

    cache_dir = (
        PAGE_CACHE_DIR
        / book_hash
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    pages = []

    for page_number, image in enumerate(
        images,
        start=1
    ):

        cache_file = (
            cache_dir
            / f"{page_number}.txt"
        )

        if cache_file.exists():

            try:

                text = cache_file.read_text(
                    encoding="utf-8",
                    errors="ignore"
                )

            except Exception:

                text = ""

        else:

            try:

                text = pytesseract.image_to_string(
                    image,
                    lang="ara+msa+eng"
                )

            except Exception as error:

                print(
                    f"❌ OCR PAGE "
                    f"{page_number}:",
                    repr(error)
                )

                text = ""

            try:

                cache_file.write_text(
                    text,
                    encoding="utf-8"
                )

            except Exception as error:

                print(
                    "⚠️ OCR cache error:",
                    repr(error)
                )

        pages.append(
            f"\n\n[PAGE {page_number}]\n{text}"
        )

    return "".join(
        pages
    )


def extract_book_text(
    path: Path
) -> str:

    suffix = path.suffix.lower()

    if suffix == ".txt":

        return extract_txt(
            path
        )

    if suffix == ".pdf":

        return ocr_pdf(
            path
        )

    return ""


# ============================================================
# CHUNKING
# ============================================================

def chunk_text(
    text: str,
    chunk_size: int = 1800,
    overlap: int = 250
) -> List[str]:

    text = clean_text(
        text
    )

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunk = text[
            start:end
        ]

        if chunk.strip():

            chunks.append(
                chunk.strip()
            )

        if end >= len(text):
            break

        start = (
            end - overlap
        )

    return chunks


# ============================================================
# GEMINI EMBEDDING
# ============================================================

def embed_text(
    text: str
) -> Optional[List[float]]:

    if not GOOGLE_API_KEY:
        return None

    if not gemini_client:
        return None

    text = clean_text(
        text
    )

    if not text:
        return None

    try:

        result = (
            gemini_client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(
                    output_dimensionality=EMBEDDING_DIM
                )
            )
        )

        embeddings = getattr(
            result,
            "embeddings",
            None
        )

        if not embeddings:
            return None

        first = embeddings[0]

        values = getattr(
            first,
            "values",
            None
        )

        if values:

            return list(
                values
            )

        return None

    except Exception as error:

        print(
            "❌ EMBEDDING ERROR:",
            repr(error)
        )

        return None


def embed_batch(
    texts: List[str]
) -> List[
    Optional[List[float]]
]:

    results = []

    for text in texts:

        results.append(
            embed_text(
                text
            )
        )

        time.sleep(
            0.05
        )

    return results


# ============================================================
# DISCOVER BOOKS
# ============================================================

def discover_books() -> List[Dict[str, Any]]:

    books = []

    if not KITAB_DIR.exists():

        print(
            f"⚠️ Kitab directory "
            f"tidak wujud: {KITAB_DIR}"
        )

        return books

    for path in KITAB_DIR.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in [
            ".pdf",
            ".txt"
        ]:
            continue

        books.append({

            "path":
                path,

            "category":
                detect_category_from_path(
                    path
                ),

            "name":
                path.stem

        })

    return books


# ============================================================
# SUPABASE SYNC
# ============================================================

def delete_book_chunks(
    book_hash: str
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
            f"🗑️ Supabase data deleted: "
            f"{book_hash}"
        )

    except Exception as error:

        print(
            "❌ DELETE SUPABASE ERROR:",
            repr(error)
        )


def sync_books():

    print("")
    print(
        "============================================================"
    )
    print(
        "🔄 SYNC KITAB"
    )
    print(
        "============================================================"
    )

    books = discover_books()

    print(
        f"📚 Jumlah kitab: {len(books)}"
    )

    manifest = load_manifest()

    for book in books:

        path = book["path"]

        try:

            book_hash = (
                sha256_file(
                    path
                )[:16]
            )

        except Exception as error:

            print(
                "❌ HASH ERROR:",
                repr(error)
            )

            continue

        manifest_key = str(
            path
        )

        old_entry = manifest.get(
            manifest_key,
            {}
        )

        old_hash = old_entry.get(
            "hash"
        )

        if old_hash == book_hash:

            print(
                f"⏭️ SKIP: {path.name}"
            )

            continue

        print("")
        print(
            "============================================================"
        )

        print(
            f"📚 PROCESS: {path.name}"
        )

        print(
            f"📂 CATEGORY: "
            f"{book['category']}"
        )

        print(
            f"🔑 HASH: {book_hash}"
        )

        text = extract_book_text(
            path
        )

        if not text:

            print(
                "⚠️ TIADA TEXT"
            )

            continue

        chunks = chunk_text(
            text
        )

        print(
            f"📦 Jumlah chunks: "
            f"{len(chunks)}"
        )

        delete_book_chunks(
            book_hash
        )

        for start in range(
            0,
            len(chunks),
            EMBED_BATCH_SIZE
        ):

            batch = chunks[
                start:
                start
                + EMBED_BATCH_SIZE
            ]

            print(
                f"🧠 EMBEDDING "
                f"{start + 1}-"
                f"{start + len(batch)}/"
                f"{len(chunks)}"
            )

            embeddings = embed_batch(
                batch
            )

            rows = []

            for index, (
                chunk,
                embedding
            ) in enumerate(
                zip(
                    batch,
                    embeddings
                )
            ):

                if not embedding:
                    continue

                rows.append({

                    "book_hash":
                        book_hash,

                    "book_name":
                        book["name"],

                    "category":
                        book["category"],

                    "chunk_index":
                        start + index,

                    "content":
                        chunk,

                    "embedding":
                        embedding

                })

            if not rows:
                continue

            if supabase:

                try:

                    (
                        supabase
                        .table(
                            "kitab_chunks"
                        )
                        .insert(
                            rows
                        )
                        .execute()
                    )

                    print(
                        f"💾 Supabase "
                        f"{start + 1}-"
                        f"{start + len(rows)}"
                    )

                except Exception as error:

                    print(
                        "❌ SUPABASE INSERT ERROR:",
                        repr(error)
                    )

        manifest[
            manifest_key
        ] = {

            "hash":
                book_hash,

            "category":
                book["category"],

            "name":
                book["name"],

            "updated":
                int(time.time())

        }

        save_manifest(
            manifest
        )

        print(
            f"✅ READY: "
            f"{book['name']}"
        )

    print(
        "🏁 SYNC SELESAI"
    )


# ============================================================
# LOCAL SUPABASE SEARCH
# ============================================================

def search_supabase(
    question: str,
    limit: int = LOCAL_SEARCH_K
) -> List[Dict[str, Any]]:

    if not supabase:
        return []

    embedding = embed_text(
        question
    )

    if not embedding:
        return []

    try:

        result = supabase.rpc(
            "match_kitab_chunks",
            {

                "query_embedding":
                    embedding,

                "match_threshold":
                    0.15,

                "match_count":
                    limit

            }
        ).execute()

        rows = (
            result.data
            or []
        )

        results = []

        for row in rows:

            content = clean_text(
                row.get(
                    "content",
                    ""
                )
            )

            if not content:
                continue

            score = row.get(
                "similarity",
                row.get(
                    "score",
                    0
                )
            )

            try:

                score = float(
                    score or 0
                )

            except Exception:

                score = 0.0

            results.append({

                "source_type":
                    "local",

                "content":
                    content,

                "kitab_name":
                    row.get(
                        "book_name",
                        "Kitab Tempatan"
                    ),

                "category":
                    row.get(
                        "category",
                        "FIQH"
                    ),

                "score":
                    score,

                "book_hash":
                    row.get(
                        "book_hash"
                    ),

                "chunk_index":
                    row.get(
                        "chunk_index"
                    )

            })

        return results

    except Exception as error:

        print(
            "❌ LOCAL SEARCH ERROR:",
            repr(error)
        )

        return []


# ============================================================
# TURATH REQUEST
# ============================================================

def turath_request(
    path: str,
    params: Dict[str, Any],
    timeout: int = 30
) -> Optional[Dict[str, Any]]:

    url = (
        TURATH_SERVICE_URL.rstrip("/")
        + path
    )

    try:

        response = requests.get(
            url,
            params=params,
            timeout=timeout
        )

        response.raise_for_status()

        return response.json()

    except Exception as error:

        print(
            "❌ TURATH REQUEST ERROR:",
            repr(error)
        )

        return None


# ============================================================
# TURATH QUERY MAPPING
# ============================================================

DIRECT_ARABIC_TERMS = {

    "mandi": [
        "غسل",
        "الاغتسال",
        "الغسل",
        "غسل الجنابة",
        "الجنابة",
        "الطهارة"
    ],

    "mandi wajib": [
        "غسل",
        "الغسل",
        "الاغتسال",
        "غسل الجنابة",
        "الجنابة"
    ],

    "mandi junub": [
        "غسل الجنابة",
        "الجنابة",
        "الغسل"
    ],

    "mandi janabah": [
        "غسل الجنابة",
        "الجنابة",
        "الغسل"
    ],

    "wuduk": [
        "وضوء",
        "الوضوء",
        "الطهارة"
    ],

    "wudhu": [
        "وضوء",
        "الوضوء",
        "الطهارة"
    ],

    "solat": [
        "الصلاة",
        "الصلوة"
    ],

    "sembahyang": [
        "الصلاة",
        "الصلوة"
    ],

    "puasa": [
        "الصيام",
        "الصوم"
    ],

    "zakat": [
        "الزكاة"
    ],

    "haji": [
        "الحج"
    ],

    "umrah": [
        "العمرة"
    ],

    "najis": [
        "النجاسة",
        "النجس"
    ],

    "hadas": [
        "الحدث",
        "الطهارة"
    ],

    "aurat": [
        "العورة",
        "عورة"
    ],

    "nikah": [
        "النكاح",
        "الزواج"
    ],

    "kahwin": [
        "النكاح",
        "الزواج"
    ],

    "cerai": [
        "الطلاق"
    ],

    "talak": [
        "الطلاق"
    ],

    "haid": [
        "الحيض"
    ],

    "nifas": [
        "النفاس"
    ],

    "istihadah": [
        "الاستحاضة"
    ],

    "tayammum": [
        "التيمم"
    ],

    "azan": [
        "الأذان"
    ],

    "iqamah": [
        "الإقامة"
    ],

    "riba": [
        "الربا"
    ],

    "sedekah": [
        "الصدقة"
    ],

    "wakaf": [
        "الوقف"
    ],

    "wasiat": [
        "الوصية"
    ],

    "faraid": [
        "الفرائض",
        "الميراث"
    ],

    "waris": [
        "الميراث",
        "الوارث"
    ],

    "hutang": [
        "الدين",
        "الديون"
    ],

    "jual beli": [
        "البيع",
        "الشراء"
    ],

    "quran": [
        "القرآن"
    ],

    "al quran": [
        "القرآن"
    ],

    "hadis": [
        "الحديث"
    ],

    "hadith": [
        "الحديث"
    ]
}


def build_turath_queries(
    question: str
) -> List[str]:

    q = clean_text(
        question
    ).lower()

    queries = []

    if q in DIRECT_ARABIC_TERMS:

        queries.extend(
            DIRECT_ARABIC_TERMS[q]
        )

    else:

        keyword_map = [

            ("mandi", "الغسل"),
            ("junub", "الجنابة"),
            ("janabah", "الجنابة"),
            ("wuduk", "الوضوء"),
            ("wudhu", "الوضوء"),
            ("solat", "الصلاة"),
            ("sembahyang", "الصلاة"),
            ("puasa", "الصيام"),
            ("zakat", "الزكاة"),
            ("haji", "الحج"),
            ("umrah", "العمرة"),
            ("najis", "النجاسة"),
            ("hadas", "الحدث"),
            ("aurat", "العورة"),
            ("tayammum", "التيمم"),
            ("haid", "الحيض"),
            ("nifas", "النفاس"),
            ("istihadah", "الاستحاضة"),
            ("azan", "الأذان"),
            ("iqamah", "الإقامة"),
            ("nikah", "النكاح"),
            ("kahwin", "الزواج"),
            ("cerai", "الطلاق"),
            ("talak", "الطلاق"),
            ("riba", "الربا"),
            ("sedekah", "الصدقة"),
            ("wakaf", "الوقف"),
            ("wasiat", "الوصية"),
            ("faraid", "الفرائض"),
            ("waris", "الميراث"),
            ("hutang", "الدين"),
            ("jual beli", "البيع"),
            ("hadis", "الحديث"),
            ("hadith", "الحديث"),
            ("quran", "القرآن"),
            ("al quran", "القرآن"),

        ]

        for keyword, arabic in keyword_map:

            if keyword in q:

                if arabic not in queries:

                    queries.append(
                        arabic
                    )

    if not queries:

        queries.append(
            question
        )

    unique = []

    for item in queries:

        item = clean_text(
            item
        )

        if item and item not in unique:

            unique.append(
                item
            )

    return unique[:6]


# ============================================================
# NORMALIZE TURATH
# ============================================================

def normalize_turath(
    item: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    meta = item.get(
        "meta",
        {}
    )

    if not isinstance(
        meta,
        dict
    ):

        meta = {}

    content = clean_text(
        item.get(
            "content",
            item.get(
                "text",
                item.get(
                    "snip",
                    item.get(
                        "snippet",
                        ""
                    )
                )
            )
        )
    )

    if not content:
        return None

    kitab_name = clean_text(
        item.get(
            "kitab_name",
            meta.get(
                "book_name",
                item.get(
                    "book_name",
                    "Turath"
                )
            )
        )
    )

    author = clean_text(
        item.get(
            "author",
            meta.get(
                "author_name",
                item.get(
                    "author_name",
                    ""
                )
            )
        )
    )

    book_id = item.get(
        "book_id"
    )

    page = item.get(
        "page",
        meta.get(
            "page"
        )
    )

    page_id = item.get(
        "page_id",
        meta.get(
            "page_id"
        )
    )

    volume = item.get(
        "vol",
        meta.get(
            "vol"
        )
    )

    url = item.get(
        "url"
    )

    if not url and book_id:

        url = (
            "https://app.turath.io/book/"
            + str(book_id)
        )

    return {

        "source_type":
            "turath",

        "content":
            content,

        "kitab_name":
            kitab_name or "Turath",

        "author":
            author,

        "book_id":
            book_id,

        "page":
            page,

        "page_id":
            page_id,

        "vol":
            volume,

        "url":
            url,

        "score":
            0.0

    }


# ============================================================
# TURATH SEARCH
# ============================================================

def turath_search(
    question: str,
    limit: int = TURATH_SEARCH_K
) -> List[Dict[str, Any]]:

    print(
        "🔎 TURATH ORIGINAL QUERY:",
        question
    )

    queries = build_turath_queries(
        question
    )

    print(
        "🌐 TURATH QUERIES:",
        " | ".join(
            queries
        )
    )

    all_passages = []

    seen = set()

    for query in queries:

        print(
            "🔎 TURATH SEARCH:",
            query
        )

        result = turath_request(
            "/search",
            {
                "q":
                    query[:500],

                "limit":
                    limit
            }
        )

        if not result:
            continue

        count = result.get(
            "count",
            0
        )

        data = result.get(
            "passages",
            []
        )

        if not data:

            raw_result = result.get(
                "result",
                {}
            )

            if isinstance(
                raw_result,
                dict
            ):

                data = raw_result.get(
                    "data",
                    []
                )

        print(
            f'📊 TURATH "{query}" COUNT: '
            f"{count}"
        )

        print(
            f'📊 TURATH "{query}" DATA: '
            f"{len(data)}"
        )

        for item in data:

            if not isinstance(
                item,
                dict
            ):
                continue

            normalized = normalize_turath(
                item
            )

            if not normalized:
                continue

            normalized[
                "matched_query"
            ] = query

            key = hashlib.md5(
                (
                    str(
                        normalized.get(
                            "book_id"
                        )
                    )
                    + "|"
                    +
                    str(
                        normalized.get(
                            "page"
                        )
                    )
                    + "|"
                    +
                    normalized.get(
                        "content",
                        ""
                    )[:1500]
                ).encode(
                    "utf-8",
                    errors="ignore"
                )
            ).hexdigest()

            if key in seen:
                continue

            seen.add(
                key
            )

            all_passages.append(
                normalized
            )

        if len(
            all_passages
        ) >= limit:

            break

    final = all_passages[
        :limit
    ]

    print(
        "📖 TURATH DIRECT PASSAGES:",
        len(final)
    )

    print(
        "📚 TURATH FINAL:",
        len(final),
        "passages"
    )

    return final


# ============================================================
# KEYWORD RANKING
# ============================================================

STOPWORDS = {

    "apa",
    "apakah",
    "adakah",
    "yang",
    "dan",
    "atau",
    "di",
    "ke",
    "dari",
    "daripada",
    "dengan",
    "untuk",
    "dalam",
    "itu",
    "ini",
    "saya",
    "kami",
    "kita",
    "nak",
    "mahu",
    "ingin",
    "boleh",
    "kah",
    "nya",
    "semasa",
    "selepas",
    "sebelum",
    "ketika",
    "kalau",
    "jika",
    "bila",
    "adalah"

}


def tokenize_question(
    text: str
) -> List[str]:

    text = text.lower()

    words = re.findall(
        r"[a-zA-ZÀ-ÿ]+",
        text
    )

    return [

        word

        for word in words

        if len(word) >= 3
        and word not in STOPWORDS

    ]


def keyword_score(
    question: str,
    content: str
) -> float:

    question_words = tokenize_question(
        question
    )

    if not question_words:
        return 0.0

    content_lower = content.lower()

    matched = 0

    for word in question_words:

        if word in content_lower:

            matched += 1

    return (
        matched
        /
        len(question_words)
    )


# ============================================================
# TURATH SCORE
# ============================================================

def calculate_turath_score(
    question: str,
    item: Dict[str, Any]
) -> float:

    score = 0.50

    content = item.get(
        "content",
        ""
    )

    keyword = keyword_score(
        question,
        content
    )

    score += (
        keyword * 0.20
    )

    if item.get(
        "book_id"
    ):

        score += 0.10

    if item.get(
        "page"
    ) is not None:

        score += 0.08

    if item.get(
        "kitab_name"
    ):

        score += 0.04

    if item.get(
        "author"
    ):

        score += 0.04

    if item.get(
        "matched_query"
    ):

        score += 0.04

    return min(
        score,
        1.0
    )


# ============================================================
# HYBRID RANKING
# ============================================================

def rank_sources(
    question: str,
    local_results: List[Dict[str, Any]],
    turath_results: List[Dict[str, Any]],
    limit: int = FINAL_CONTEXT_K
) -> List[Dict[str, Any]]:

    ranked = []

    # --------------------------------------------------------
    # LOCAL
    # --------------------------------------------------------

    for item in local_results:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        vector_score = float(
            item.get(
                "score",
                0
            )
            or 0
        )

        vector_score = max(
            0.0,
            min(
                vector_score,
                1.0
            )
        )

        keyword = keyword_score(
            question,
            content
        )

        final_score = (
            vector_score * 0.70
            +
            keyword * 0.30
        )

        ranked.append({

            **item,

            "score":
                final_score,

            "ranking_source":
                "local"

        })


    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    for item in turath_results:

        score = calculate_turath_score(
            question,
            item
        )

        ranked.append({

            **item,

            "score":
                score,

            "ranking_source":
                "turath"

        })


    # --------------------------------------------------------
    # DEDUPLICATE
    # --------------------------------------------------------

    unique = {}

    for item in ranked:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        key = hashlib.md5(
            content[:2500].encode(
                "utf-8",
                errors="ignore"
            )
        ).hexdigest()

        if key not in unique:

            unique[key] = item

        else:

            existing = unique[
                key
            ]

            if item.get(
                "score",
                0
            ) > existing.get(
                "score",
                0
            ):

                unique[key] = item


    ranked = list(
        unique.values()
    )


    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    ranked.sort(
        key=lambda item:
            float(
                item.get(
                    "score",
                    0
                )
                or 0
            ),
        reverse=True
    )

    return ranked[
        :limit
    ]


# ============================================================
# SOURCE LABEL
# ============================================================

def source_label(
    item: Dict[str, Any]
) -> str:

    source_type = item.get(
        "source_type"
    )

    kitab = clean_text(
        item.get(
            "kitab_name",
            "Tidak diketahui"
        )
    )

    if source_type == "turath":

        author = clean_text(
            item.get(
                "author",
                ""
            )
        )

        page = item.get(
            "page"
        )

        volume = item.get(
            "vol"
        )

        label = (
            kitab
            or "Turath"
        )

        if author:

            label += (
                f" — {author}"
            )

        if volume:

            label += (
                f", jilid {volume}"
            )

        if page is not None:

            label += (
                f", halaman {page}"
            )

        return label

    category = item.get(
        "category",
        "FIQH"
    )

    return (
        f"{kitab} [{category}]"
    )


# ============================================================
# CONTEXT BUILDER
# ============================================================

def build_context(
    ranked: List[Dict[str, Any]]
) -> str:

    blocks = []

    for index, item in enumerate(
        ranked,
        start=1
    ):

        label = source_label(
            item
        )

        content = truncate_text(
            item.get(
                "content",
                ""
            ),
            4500
        )

        blocks.append(
            "SUMBER "
            + str(index)
            + "\n"
            + label
            + "\n"
            + "Skor relevansi: "
            + f"{item.get('score', 0):.3f}"
            + "\n\n"
            + content
        )

    return "\n\n".join(
        blocks
    )


# ============================================================
# GEMINI ANSWER
# ============================================================

def generate_answer(
    question: str,
    ranked: List[Dict[str, Any]]
) -> str:

    if not gemini_client:

        return (
            "Maaf, sambungan Gemini belum "
            "berjaya dikonfigurasikan."
        )

    if not ranked:

        return (
            "Maaf, tiada sumber yang cukup "
            "untuk menjawab soalan ini."
        )

    context = build_context(
        ranked
    )

    prompt = f"""
Anda ialah TanyaFiqhBot, pembantu rujukan
Islam berbahasa Melayu.

Jawab soalan pengguna berdasarkan sumber
yang diberikan.

SOALAN PENGGUNA:

{question}

SUMBER RUJUKAN:

{context}

ARAHAN:

1. Jawab dalam Bahasa Melayu.

2. Gunakan sumber yang diberikan sebagai
   sumber utama.

3. Jangan mereka-reka hukum atau fakta
   yang tidak disokong oleh sumber.

4. Jika sumber tidak mencukupi, nyatakan
   bahawa maklumat tidak mencukupi.

5. Jika terdapat perbezaan pandangan,
   nyatakan secara neutral.

6. Jangan menyebut proses teknikal seperti
   database, embedding, vector search,
   ranking atau RAG.

7. Jangan mendakwa membaca keseluruhan
   kitab jika hanya petikan diberikan.

8. Untuk sumber Turath, gunakan nama kitab,
   pengarang dan halaman jika tersedia.

9. Jangan mencipta nombor halaman.

10. Jawapan hendaklah jelas, ringkas dan
    mudah difahami.

11. Jika soalan berkaitan hukum fiqh,
    jelaskan hukum berdasarkan sumber
    dan nyatakan rujukan.

12. Jangan memberikan kepastian palsu
    jika sumber tidak cukup.

Format:

Jawapan:
...

Penjelasan:
...

Rujukan:
...
"""

    try:

        print(
            "🤖 GEMINI GENERATING..."
        )

        response = (
            gemini_client
            .models
            .generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=1500,
                    candidate_count=1
                )
            )
        )

        text = getattr(
            response,
            "text",
            None
        )

        if text:

            print(
                "✅ GEMINI RESPONSE OK"
            )

            return text.strip()

        print(
            "⚠️ GEMINI RESPONSE TIADA TEXT"
        )

        print(
            "RAW RESPONSE:",
            repr(response)
        )

        return (
            "Maaf, Gemini tidak menghasilkan "
            "jawapan."
        )

    except Exception as error:

        print(
            "❌ GEMINI GENERATION ERROR:",
            repr(error)
        )

        return (
            "Maaf, berlaku masalah ketika "
            "menjana jawapan."
        )


# ============================================================
# MAIN RAG PIPELINE
# ============================================================

def answer_question(
    question: str
) -> Tuple[
    str,
    List[Dict[str, Any]]
]:

    print("")
    print(
        "============================================================"
    )

    print(
        f"❓ QUESTION: {question}"
    )

    # --------------------------------------------------------
    # LOCAL
    # --------------------------------------------------------

    local_results = search_supabase(
        question,
        LOCAL_SEARCH_K
    )

    print(
        f"📚 LOCAL SEARCH: "
        f"{len(local_results)} result"
    )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    turath_results = turath_search(
        question,
        TURATH_SEARCH_K
    )

    print(
        f"📖 TURATH: "
        f"{len(turath_results)}"
    )

    # --------------------------------------------------------
    # RANK
    # --------------------------------------------------------

    ranked = rank_sources(
        question,
        local_results,
        turath_results,
        FINAL_CONTEXT_K
    )

    print(
        f"🏆 HYBRID RANKING: "
        f"{len(ranked)} sources"
    )

    for index, item in enumerate(
        ranked,
        start=1
    ):

        print(
            f"   {index}. "
            f"{item.get('source_type')} | "
            f"{item.get('kitab_name')} | "
            f"{item.get('score', 0):.3f}"
        )

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    answer = generate_answer(
        question,
        ranked
    )

    return (
        answer,
        ranked
    )


# ============================================================
# TELEGRAM /START
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "Assalamualaikum 👋\n\n"
        "Saya TanyaFiqhBot.\n\n"
        "Sila masukkan persoalan berkaitan "
        "fiqh dan ilmu Islam."
    )


# ============================================================
# TELEGRAM /HELP
# ============================================================

async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "Contoh soalan:\n\n"
        "Apakah hukum mandi wajib?\n\n"
        "Apakah rukun wuduk?\n\n"
        "Bolehkah solat jamak ketika musafir?"
    )


# ============================================================
# TELEGRAM QUESTION
# ============================================================

async def telegram_question(
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

    if question.lower().startswith(
        "/fiqh"
    ):

        question = re.sub(
            r"^/fiqh\s*",
            "",
            question,
            flags=re.IGNORECASE
        ).strip()

    if not question:

        await update.message.reply_text(
            "Sila masukkan soalan."
        )

        return

    try:

        await update.message.chat.send_action(
            action="typing"
        )

    except Exception:
        pass

    try:

        answer, ranked = answer_question(
            question
        )

    except Exception as error:

        print(
            "❌ QUESTION PIPELINE ERROR:",
            repr(error)
        )

        await update.message.reply_text(
            "Maaf, berlaku masalah "
            "semasa memproses soalan."
        )

        return

    # --------------------------------------------------------
    # SOURCE COUNTS
    # --------------------------------------------------------

    local_count = sum(
        1
        for item in ranked
        if item.get(
            "source_type"
        ) == "local"
    )

    turath_count = sum(
        1
        for item in ranked
        if item.get(
            "source_type"
        ) == "turath"
    )

    # --------------------------------------------------------
    # SOURCES
    # --------------------------------------------------------

    source_lines = []

    seen = set()

    for item in ranked:

        source_type = item.get(
            "source_type"
        )

        kitab = clean_text(
            item.get(
                "kitab_name",
                "Tidak diketahui"
            )
        )

        if source_type == "turath":

            author = clean_text(
                item.get(
                    "author",
                    ""
                )
            )

            page = item.get(
                "page"
            )

            url = item.get(
                "url"
            )

            key = (
                "turath",
                kitab,
                author,
                page
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            line = (
                "• "
                + kitab
            )

            if author:

                line += (
                    f" — {author}"
                )

            if page is not None:

                line += (
                    f", halaman {page}"
                )

            if url:

                line += (
                    f"\n  {url}"
                )

            source_lines.append(
                line
            )

        else:

            category = item.get(
                "category",
                "FIQH"
            )

            key = (
                "local",
                kitab,
                category
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            source_lines.append(
                f"• {kitab} "
                f"[{category}]"
            )

    # --------------------------------------------------------
    # APPEND SOURCES
    # --------------------------------------------------------

    if source_lines:

        answer += (
            "\n\n📚 Rujukan:\n"
            +
            "\n".join(
                source_lines[:8]
            )
        )

    answer += (
        "\n\n"
        "🔎 Sumber ditemui: "
        f"Kitab tempatan: {local_count} "
        f"| Turath: {turath_count}"
    )

    await update.message.reply_text(
        answer
    )


# ============================================================
# TELEGRAM RUNNER
# ============================================================

async def telegram_runner():

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan"
        )

        return

    telegram_app = (
        Application
        .builder()
        .token(
            TELEGRAM_TOKEN
        )
        .build()
    )

    telegram_app.add_handler(
        CommandHandler(
            "start",
            telegram_start
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "help",
            telegram_help
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            telegram_question
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.COMMAND,
            telegram_question
        )
    )

    print(
        "✅ Telegram polling started"
    )

    await telegram_app.initialize()

    await telegram_app.start()

    await telegram_app.updater.start_polling(
        stop_signals=None
    )

    while True:

        await asyncio.sleep(
            3600
        )


def start_telegram():

    try:

        asyncio.run(
            telegram_runner()
        )

    except Exception as error:

        print(
            "❌ TELEGRAM ERROR:",
            repr(error)
        )


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def home():

    return jsonify({

        "ok":
            True,

        "service":
            "TanyaFiqhBot",

        "status":
            "running",

        "llm":
            LLM_MODEL,

        "embedding":
            EMBEDDING_MODEL,

        "turath":
            TURATH_SERVICE_URL

    })


@app.route("/health")
def health():

    return jsonify({

        "ok":
            True,

        "service":
            "TanyaFiqhBot",

        "supabase":
            bool(supabase),

        "gemini":
            bool(gemini_client),

        "llm_model":
            LLM_MODEL,

        "embedding_model":
            EMBEDDING_MODEL,

        "turath":
            TURATH_SERVICE_URL

    })


@app.route("/test/turath")
def test_turath():

    results = turath_search(
        "mandi",
        5
    )

    return jsonify({

        "ok":
            True,

        "query":
            "mandi",

        "results":
            results

    })


@app.route("/test/question")
def test_question():

    question = (
        "Apakah hukum mandi wajib?"
    )

    answer, ranked = answer_question(
        question
    )

    return jsonify({

        "ok":
            True,

        "question":
            question,

        "answer":
            answer,

        "sources":
            ranked

    })


# ============================================================
# STARTUP
# ============================================================

def startup():

    print("")
    print(
        "============================================================"
    )

    print(
        "🚀 STARTING TANYAFIQIHBOT"
    )

    print(
        "============================================================"
    )

    print(
        f"🤖 LLM: {LLM_MODEL}"
    )

    print(
        f"🧠 EMBEDDING: {EMBEDDING_MODEL}"
    )

    print(
        f"📚 KITAB DIR: {KITAB_DIR}"
    )

    print(
        f"🌐 TURATH: {TURATH_SERVICE_URL}"
    )

    try:

        sync_books()

    except Exception as error:

        print(
            "❌ SYNC ERROR:",
            repr(error)
        )

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True
    )

    telegram_thread.start()


# ============================================================
# START
# ============================================================

startup()


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
