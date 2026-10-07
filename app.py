import os
import re
import json
import time
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np

from flask import Flask, jsonify

from pdf2image import convert_from_path
import pytesseract

from supabase import create_client

import google.generativeai as genai

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

EXTRACTED_DIR = (
    DATA_DIR / "extracted_text"
)

PAGE_CACHE_DIR = (
    EXTRACTED_DIR / "pages"
)

MANIFEST_FILE = (
    DATA_DIR / "manifest.json"
)

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
    "models/gemini-embedding-001"
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


def normalize_category(value: str) -> str:

    if not value:
        return "FIQH"

    value = str(value).strip().upper()

    aliases = {
        "USUL": "USUL FIQH",
        "USULFIQH": "USUL FIQH",
        "USUL FIQH": "USUL FIQH",
        "HADITH": "HADIS",
        "AQIDAH": "TAUHID",
        "AKIDAH": "TAUHID",
    }

    return aliases.get(
        value,
        value if value in CATEGORIES else "FIQH"
    )


def detect_category_from_path(path: Path) -> str:

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

for directory in [
    DATA_DIR,
    EXTRACTED_DIR,
    PAGE_CACHE_DIR,
]:

    directory.mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# CLIENTS
# ============================================================

app = Flask(__name__)

supabase = None
gemini_model = None


if SUPABASE_URL and SUPABASE_KEY:

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ Supabase connected")

    except Exception as error:

        print(
            "❌ Supabase connection error:",
            error
        )


if GOOGLE_API_KEY:

    try:

        genai.configure(
            api_key=GOOGLE_API_KEY
        )

        gemini_model = genai.GenerativeModel(
            LLM_MODEL
        )

        print(
            f"✅ Gemini ready: {LLM_MODEL}"
        )

    except Exception as error:

        print(
            "❌ Gemini init error:",
            error
        )


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

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

    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "..."


def sha256_file(path: Path) -> str:

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

            hasher.update(chunk)

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

    except Exception:

        return {}


def save_manifest(
    manifest: Dict[str, Any]
):

    MANIFEST_FILE.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
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
            error
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
            f"❌ PDF IMAGE ERROR: {error}"
        )

        return ""

    pages = []

    for index, image in enumerate(
        images,
        start=1
    ):

        cache_dir = (
            PAGE_CACHE_DIR
            / hashlib.sha256(
                str(path).encode()
            ).hexdigest()[:16]
        )

        cache_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        cache_file = (
            cache_dir
            / f"{index}.txt"
        )

        if cache_file.exists():

            text = cache_file.read_text(
                encoding="utf-8",
                errors="ignore"
            )

        else:

            try:

                text = pytesseract.image_to_string(
                    image,
                    lang="ara+msa+eng"
                )

            except Exception as error:

                print(
                    f"❌ OCR page {index}:",
                    error
                )

                text = ""

            cache_file.write_text(
                text,
                encoding="utf-8"
            )

        pages.append(
            f"\n\n[PAGE {index}]\n{text}"
        )

    return "".join(pages)


def extract_book_text(
    path: Path
) -> str:

    suffix = path.suffix.lower()

    if suffix == ".txt":

        return extract_txt(path)

    if suffix == ".pdf":

        return ocr_pdf(path)

    return ""


# ============================================================
# CHUNKING
# ============================================================

def chunk_text(
    text: str,
    chunk_size: int = 1800,
    overlap: int = 250
) -> List[str]:

    text = clean_text(text)

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunk = text[start:end]

        if chunk.strip():

            chunks.append(
                chunk.strip()
            )

        if end >= len(text):
            break

        start = end - overlap

    return chunks


# ============================================================
# EMBEDDING
# ============================================================

def embed_text(
    text: str
) -> Optional[List[float]]:

    try:

        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            output_dimensionality=EMBEDDING_DIM
        )

        embedding = result.get(
            "embedding"
        )

        if embedding:
            return embedding

    except Exception as error:

        print(
            "❌ EMBEDDING ERROR:",
            error
        )

    return None


def embed_batch(
    texts: List[str]
) -> List[Optional[List[float]]]:

    results = []

    for text in texts:

        results.append(
            embed_text(text)
        )

        time.sleep(0.05)

    return results


# ============================================================
# BOOK DISCOVERY
# ============================================================

def discover_books() -> List[Dict[str, Any]]:

    books = []

    if not KITAB_DIR.exists():

        print(
            f"⚠️ KITAB DIR NOT FOUND: {KITAB_DIR}"
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

        category = detect_category_from_path(
            path
        )

        books.append({
            "path": path,
            "category": category,
            "name": path.stem
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
            f"🗑️ Supabase data deleted: {book_hash}"
        )

    except Exception as error:

        print(
            "❌ DELETE SUPABASE ERROR:",
            error
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

            book_hash = sha256_file(path)[
                :16
            ]

        except Exception as error:

            print(
                "❌ HASH ERROR:",
                error
            )

            continue

        manifest_key = str(path)

        old_hash = manifest.get(
            manifest_key,
            {}
        ).get("hash")

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
            f"📂 CATEGORY: {book['category']}"
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
            f"📦 Jumlah chunks: {len(chunks)}"
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
                start + EMBED_BATCH_SIZE
            ]

            print(
                f"🧠 EMBEDDING "
                f"{start + 1}-{start + len(batch)}"
                f"/{len(chunks)}"
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

            if supabase and rows:

                try:

                    (
                        supabase
                        .table("kitab_chunks")
                        .insert(rows)
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
                        error
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
            f"✅ READY: {book['name']}"
        )

    print(
        "🏁 SYNC SELESAI"
    )


# ============================================================
# LOCAL SEARCH
# ============================================================

def search_supabase(
    question: str,
    limit: int = LOCAL_SEARCH_K
) -> List[Dict[str, Any]]:

    if not supabase:

        return []

    query_embedding = embed_text(
        question
    )

    if not query_embedding:

        return []

    try:

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "query_embedding":
                    query_embedding,

                "match_threshold":
                    0.15,

                "match_count":
                    limit
            }
        ).execute()

        rows = result.data or []

        passages = []

        for row in rows:

            passages.append({

                "source_type":
                    "local",

                "content":
                    clean_text(
                        row.get(
                            "content",
                            ""
                        )
                    ),

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
                    float(
                        row.get(
                            "similarity",
                            row.get(
                                "score",
                                0
                            )
                        )
                        or 0
                    ),

                "book_hash":
                    row.get(
                        "book_hash"
                    ),

                "chunk_index":
                    row.get(
                        "chunk_index"
                    )

            })

        return passages

    except Exception as error:

        print(
            "❌ LOCAL SEARCH ERROR:",
            error
        )

        return []


# ============================================================
# TURATH HTTP
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

    print(
        "🌐 TURATH REQUEST:",
        url,
        params
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
            error
        )

        return None


# ============================================================
# TURATH QUERY
# ============================================================

def turath_search(
    question: str,
    limit: int = TURATH_SEARCH_K
) -> Dict[str, Any]:

    result = turath_request(
        "/search",
        {
            "q":
                question[:500],

            "limit":
                limit
        }
    )

    if not result:

        return {
            "passages": [],
            "candidates": []
        }

    passages = result.get(
        "passages",
        []
    )

    candidates = result.get(
        "candidates",
        []
    )

    print(
        "📊 TURATH COUNT:",
        result.get(
            "count",
            0
        )
    )

    print(
        "📖 TURATH DIRECT PASSAGES:",
        len(passages)
    )

    print(
        "📚 TURATH CANDIDATES:",
        len(candidates)
    )

    return {
        "passages":
            passages,

        "candidates":
            candidates
    }


# ============================================================
# TURATH NORMALIZATION
# ============================================================

def normalize_turath_passage(
    item: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    content = clean_text(
        item.get(
            "content",
            item.get(
                "text",
                item.get(
                    "snippet",
                    ""
                )
            )
        )
    )

    if not content:

        return None

    kitab_name = clean_text(
        item.get(
            "kitab_name",
            item.get(
                "book_name",
                item.get(
                    "title",
                    "Turath"
                )
            )
        )
    )

    author = clean_text(
        item.get(
            "author",
            item.get(
                "author_name",
                ""
            )
        )
    )

    page = item.get(
        "page"
    )

    page_id = item.get(
        "page_id"
    )

    book_id = item.get(
        "book_id"
    )

    url = item.get(
        "url"
    )

    if not url and book_id:

        url = (
            f"https://app.turath.io/book/"
            f"{book_id}"
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

        "page":
            page,

        "page_id":
            page_id,

        "book_id":
            book_id,

        "vol":
            item.get(
                "vol"
            ),

        "url":
            url,

        "score":
            0.0,

        "matched_query":
            item.get(
                "matched_query"
            )

    }


# ============================================================
# KEYWORD SCORING
# ============================================================

BM_STOPWORDS = {
    "apa",
    "adakah",
    "apakah",
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
    "nak",
    "mahu",
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
}


def tokenize_malay(
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
        if word not in BM_STOPWORDS
        and len(word) >= 3
    ]


def keyword_score(
    question: str,
    content: str
) -> float:

    question_words = tokenize_malay(
        question
    )

    if not question_words:

        return 0.0

    content_lower = content.lower()

    matched = 0

    for word in question_words:

        if word in content_lower:

            matched += 1

    return matched / len(
        question_words
    )


# ============================================================
# TURATH SCORE
# ============================================================

def turath_score(
    question: str,
    passage: Dict[str, Any]
) -> float:

    content = passage.get(
        "content",
        ""
    )

    score = 0.0

    /*
    Turath search sudah melakukan
    relevance ranking sendiri.
    */

    score += 0.55

    keyword = keyword_score(
        question,
        content
    )

    score += keyword * 0.20

    if passage.get(
        "kitab_name"
    ):

        score += 0.05

    if passage.get(
        "author"
    ):

        score += 0.05

    if passage.get(
        "page"
    ) is not None:

        score += 0.05

    if passage.get(
        "book_id"
    ):

        score += 0.10

    return min(
        score,
        1.0
    )


# ============================================================
# HYBRID RANKING
# ============================================================

def rank_hybrid_sources(
    question: str,
    local_results: List[Dict[str, Any]],
    turath_results: List[Dict[str, Any]],
    limit: int = FINAL_CONTEXT_K
) -> List[Dict[str, Any]]:

    ranked = []

    /*
    LOCAL
    */

    for item in local_results:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        similarity = float(
            item.get(
                "score",
                0
            )
            or 0
        )

        keyword = keyword_score(
            question,
            content
        )

        /*
        Local:
        embedding = 70%
        keyword = 30%
        */

        final_score = (
            similarity * 0.70
            +
            keyword * 0.30
        )

        ranked.append({

            **item,

            "score":
                final_score,

            "ranking":
                "local"

        })


    /*
    TURATH
    */

    for raw in turath_results:

        item = normalize_turath_passage(
            raw
        )

        if not item:
            continue

        item["score"] = turath_score(
            question,
            item
        )

        item["ranking"] = "turath"

        ranked.append(
            item
        )


    /*
    REMOVE DUPLICATE CONTENT
    */

    unique = {}

    for item in ranked:

        content = clean_text(
            item.get(
                "content",
                ""
            )
        )

        key = hashlib.md5(
            content[:2000].encode(
                "utf-8",
                errors="ignore"
            )
        ).hexdigest()

        if key not in unique:

            unique[key] = item

        else:

            if (
                item["score"]
                >
                unique[key]["score"]
            ):

                unique[key] = item


    ranked = list(
        unique.values()
    )


    /*
    SORT
    */

    ranked.sort(
        key=lambda item:
            item.get(
                "score",
                0
            ),
        reverse=True
    )


    /*
    FINAL
    */

    return ranked[:limit]


# ============================================================
# FORMAT SOURCE
# ============================================================

def format_source(
    item: Dict[str, Any],
    number: int
) -> str:

    source_type = item.get(
        "source_type"
    )

    kitab = item.get(
        "kitab_name",
        "Tidak diketahui"
    )

    if source_type == "turath":

        author = item.get(
            "author"
        )

        page = item.get(
            "page"
        )

        book_id = item.get(
            "book_id"
        )

        parts = [
            f"{number}. {kitab}"
        ]

        if author:
            parts.append(
                f"— {author}"
            )

        if page is not None:
            parts.append(
                f", hlm. {page}"
            )

        if book_id:
            parts.append(
                f" [Turath ID: {book_id}]"
            )

        return "".join(parts)


    category = item.get(
        "category",
        "FIQH"
    )

    return (
        f"{number}. {kitab}"
        f" [{category}]"
    )


def format_context(
    ranked_sources: List[Dict[str, Any]]
) -> str:

    blocks = []

    for index, item in enumerate(
        ranked_sources,
        start=1
    ):

        source_label = format_source(
            item,
            index
        )

        content = truncate_text(
            item.get(
                "content",
                ""
            ),
            4500
        )

        blocks.append(
            f"Sumber {index}\n"
            f"{source_label}\n"
            f"Relevance Score: "
            f"{item.get('score', 0):.3f}\n\n"
            f"{content}"
        )

    return "\n\n"
        .join(blocks)


# ============================================================
# GEMINI ANSWER
# ============================================================

def generate_answer(
    question: str,
    ranked_sources: List[Dict[str, Any]]
) -> str:

    if not gemini_model:

        return (
            "Gemini belum dikonfigurasikan."
        )

    if not ranked_sources:

        return (
            "Maaf, saya tidak menemui "
            "rujukan yang mencukupi untuk "
            "menjawab persoalan ini."
        )

    context = format_context(
        ranked_sources
    )

    prompt = f"""
Anda ialah TanyaFiqhBot, pembantu rujukan
Islam berasaskan kitab.

Soalan pengguna:

{question}

RUJUKAN YANG DITEMUI:

{context}

TUGAS:

Jawab soalan berdasarkan SUMBER yang diberikan.

Peraturan penting:

1. Jangan mereka-reka fakta yang tidak terdapat
   dalam sumber.

2. Utamakan sumber yang paling relevan.

3. Jika sumber tempatan dan Turath bercanggah,
   nyatakan percanggahan tersebut dengan jelas.

4. Jangan mengatakan sesuatu hukum itu pasti
   jika sumber tidak mencukupi.

5. Jika sumber tidak mencukupi, katakan dengan
   jelas bahawa rujukan yang ditemui belum
   mencukupi.

6. Untuk rujukan Turath, gunakan nama kitab,
   nama pengarang dan halaman jika tersedia.

7. Jangan masukkan citation palsu.

8. Jawapan hendaklah dalam Bahasa Melayu.

9. Gunakan format:

Jawapan:
...

Penjelasan:
...

Rujukan:
- ...

10. Jangan gunakan markdown table.

11. Jangan menyebut "AI", "database", "embedding",
    "vector", "ranking" atau proses teknikal
    kepada pengguna.

12. Jangan memberi fatwa berdasarkan pengetahuan
    luar jika ia tidak disokong oleh sumber yang
    diberikan.

"""

    try:

        response = gemini_model.generate_content(
            prompt
        )

        text = getattr(
            response,
            "text",
            None
        )

        if text:

            return text.strip()

        return (
            "Jawapan tidak dapat dijana."
        )

    except Exception as error:

        print(
            "❌ LLM error:",
            error
        )

        return (
            "Maaf, berlaku masalah ketika "
            "menjana jawapan."
        )


# ============================================================
# QUESTION PIPELINE
# ============================================================

def answer_question(
    question: str
) -> Tuple[str, List[Dict[str, Any]]]:

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

    turath_data = turath_search(
        question,
        TURATH_SEARCH_K
    )

    turath_results = turath_data.get(
        "passages",
        []
    )

    print(
        f"📖 TURATH: "
        f"{len(turath_results)}"
    )


    # --------------------------------------------------------
    # RANK
    # --------------------------------------------------------

    ranked = rank_hybrid_sources(
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
    # ANSWER
    # --------------------------------------------------------

    answer = generate_answer(
        question,
        ranked
    )


    return answer, ranked


# ============================================================
# TELEGRAM
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "Assalamualaikum 👋\n\n"
        "Saya TanyaFiqhBot.\n"
        "Sila ajukan persoalan fiqh atau "
        "persoalan berkaitan ilmu Islam."
    )


async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "Contoh:\n\n"
        "/fiqh apakah hukum mandi wajib?\n\n"
        "Atau terus taip:\n"
        "Apakah hukum mandi wajib selepas haid?"
    )


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

    if question.startswith("/start"):

        await telegram_start(
            update,
            context
        )

        return

    if question.startswith("/help"):

        await telegram_help(
            update,
            context
        )

        return


    if question.startswith("/fiqh"):

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


    answer, ranked = answer_question(
        question
    )


    # --------------------------------------------------------
    # SOURCE SUMMARY
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


    source_lines = []

    seen_sources = set()


    for item in ranked:

        source_type = item.get(
            "source_type"
        )

        if source_type == "turath":

            kitab = item.get(
                "kitab_name",
                "Turath"
            )

            author = item.get(
                "author"
            )

            page = item.get(
                "page"
            )

            source_key = (
                kitab,
                author,
                page
            )

            if source_key in seen_sources:
                continue

            seen_sources.add(
                source_key
            )

            text = f"• {kitab}"

            if author:
                text += (
                    f" — {author}"
                )

            if page is not None:
                text += (
                    f", hlm. {page}"
                )

            source_lines.append(
                text
            )

        else:

            kitab = item.get(
                "kitab_name",
                "Kitab Tempatan"
            )

            category = item.get(
                "category",
                "FIQH"
            )

            source_key = (
                "local",
                kitab,
                category
            )

            if source_key in seen_sources:
                continue

            seen_sources.add(
                source_key
            )

            source_lines.append(
                f"• {kitab} "
                f"[{category}]"
            )


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
        f"🔎 Sumber ditemui: "
        f"Kitab tempatan: {local_count} "
        f"| Turath: {turath_count}"
    )


    await update.message.reply_text(
        answer
    )


# ============================================================
# TELEGRAM POLLING
# ============================================================

def start_telegram():

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ada"
        )

        return


    async def runner():

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

            await __import__(
                "asyncio"
            ).sleep(3600)


    import asyncio

    asyncio.run(
        runner()
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
            EMBEDDING_MODEL

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
            bool(gemini_model),

        "turath":
            TURATH_SERVICE_URL

    })


@app.route("/test/turath")
def test_turath():

    query = (
        "غسل"
    )

    result = turath_search(
        query,
        5
    )

    return jsonify({
        "ok": True,
        "query": query,
        "result": result
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


    try:

        sync_books()

    except Exception as error:

        print(
            "❌ SYNC ERROR:",
            error
        )


    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True
    )

    telegram_thread.start()


# ============================================================
# RUN
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
