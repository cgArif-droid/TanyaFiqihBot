import os
import re
import json
import asyncio
import hashlib
import threading
import traceback
from pathlib import Path

import requests
import pytesseract

from flask import Flask, jsonify, request
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
# ENVIRONMENT
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "/var/data"))
OCR_DIR = DATA_DIR / "ocr_cache"

OCR_WORKERS = int(os.getenv("OCR_WORKERS", "2"))

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.8-flash"
).strip()

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001"
).strip()

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).strip().rstrip("/")

SOURCE_MAX_CHARS = int(
    os.getenv("SOURCE_MAX_CHARS", "6000")
)

CONTEXT_MAX_CHARS = int(
    os.getenv("CONTEXT_MAX_CHARS", "60000")
)

TELEGRAM_MAX_CHARS = int(
    os.getenv("TELEGRAM_MAX_CHARS", "3900")
)

START_BACKGROUND_SERVICES = (
    os.getenv("START_BACKGROUND_SERVICES", "true")
    .lower()
    in ("1", "true", "yes", "on")
)


# ============================================================
# PATH
# ============================================================

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OCR_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# GLOBALS
# ============================================================

supabase = None
gemini_client = None
telegram_application = None

background_lock = threading.Lock()
background_started = False

answer_cache = {}
cache_lock = threading.Lock()


# ============================================================
# STARTUP LOG
# ============================================================

print("=" * 60)
print("🚀 TANYAFIQIHBOT STARTING")
print("=" * 60)

print(f"📁 DATA_DIR        : {DATA_DIR}")
print(f"🤖 LLM_MODEL       : {LLM_MODEL}")
print(f"🧠 EMBEDDING_MODEL : {EMBEDDING_MODEL}")
print(f"📚 TURATH URL      : {TURATH_SERVICE_URL}")
print(f"📏 SOURCE MAX      : {SOURCE_MAX_CHARS}")
print(f"📏 CONTEXT MAX     : {CONTEXT_MAX_CHARS}")
print("=" * 60)


# ============================================================
# INITIALIZE SUPABASE
# ============================================================

def init_supabase():

    global supabase

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ SUPABASE_URL / SUPABASE_KEY tidak lengkap")
        return

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ SUPABASE INITIALIZED")

    except Exception as e:

        print("❌ SUPABASE INIT ERROR:")
        traceback.print_exc()

        supabase = None


# ============================================================
# INITIALIZE GEMINI
# ============================================================

def init_gemini():

    global gemini_client

    if not GOOGLE_API_KEY:

        print("⚠️ GOOGLE_API_KEY tidak ditetapkan")
        return

    try:

        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print("✅ GEMINI INITIALIZED")

    except Exception:

        print("❌ GEMINI INIT ERROR:")
        traceback.print_exc()

        gemini_client = None


init_supabase()
init_gemini()


# ============================================================
# TEXT HELPERS
# ============================================================

def normalize_question(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace("\r", " ")
    text = text.replace("\n", " ")

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def clean_text(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "\x00",
        ""
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def strip_markdown(text):

    if not text:
        return ""

    text = str(text)

    # Bold
    text = text.replace("**", "")

    # Italic
    text = text.replace("__", "")
    text = text.replace("*", "")

    # Code
    text = text.replace("```", "")
    text = text.replace("`", "")

    # Markdown links
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    return text.strip()


# ============================================================
# TELEGRAM TEXT CHUNK
# ============================================================

def telegram_chunks(
    text,
    max_chars=TELEGRAM_MAX_CHARS
):

    text = strip_markdown(text)

    if len(text) <= max_chars:
        return [text]

    chunks = []

    current = ""

    paragraphs = text.split("\n")

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if len(current) + len(paragraph) + 1 <= max_chars:

            if current:
                current += "\n"

            current += paragraph

        else:

            if current:
                chunks.append(current)

            # Paragraph itself too long
            while len(paragraph) > max_chars:

                chunks.append(
                    paragraph[:max_chars]
                )

                paragraph = paragraph[max_chars:]

            current = paragraph

    if current:
        chunks.append(current)

    return chunks


# ============================================================
# CACHE
# ============================================================

def cache_key(question):

    normalized = normalize_question(question).lower()

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def get_cached_answer(question):

    key = cache_key(question)

    with cache_lock:

        return answer_cache.get(key)


def set_cached_answer(
    question,
    answer
):

    key = cache_key(question)

    with cache_lock:

        answer_cache[key] = answer

        # Prevent unlimited growth
        if len(answer_cache) > 100:
            first_key = next(
                iter(answer_cache)
            )

            del answer_cache[first_key]


# ============================================================
# MADHHAB COMPARISON
# ============================================================

def is_madhhab_comparison(question):

    """
    True hanya jika pengguna benar-benar meminta
    perbandingan antara mazhab.

    Contoh:

    ❌ Apa hukum qunut menurut mazhab Syafie?
       -> False

    ❌ Hukum wuduk menurut mazhab Hanafi?
       -> False

    ✅ Bandingkan hukum qunut mazhab Syafie dan Hanafi
       -> True

    ✅ Apa perbezaan mazhab Syafie dan Hanafi?
       -> True
    """

    q = normalize_question(
        question
    ).lower()

    explicit_phrases = [

        "bandingkan",
        "bandingkan mazhab",
        "banding mazhab",
        "perbandingan",

        "perbezaan antara",
        "perbezaan mazhab",

        "beza antara",
        "bezanya antara",

        "berbeza antara",

        "keempat-empat mazhab",
        "empat mazhab",
        "semua mazhab",
        "4 mazhab",

        "four madhhabs",
        "compare madhhab",
        "compare madhhabs",
        "madhhab comparison",
        "comparison",

        "قارن",
        "مقارنة",
        "الفرق بين",
        "المذاهب الأربعة",
    ]

    for phrase in explicit_phrases:

        if phrase in q:
            return True

    madhhab_patterns = [

        r"\bsyafie\b",
        r"\bsyafi['’]i\b",

        r"\bhanafi\b",

        r"\bmaliki\b",

        r"\bhanbali\b",

        r"الشافعية",
        r"الحنفية",
        r"المالكية",
        r"الحنابلة",
    ]

    count = sum(
        bool(
            re.search(
                pattern,
                q
            )
        )
        for pattern in madhhab_patterns
    )

    return count >= 2


# ============================================================
# TRANSLATE QUESTION TO ARABIC SEARCH QUERY
# ============================================================

def translate_to_arabic_query(question):

    """
    Tukar soalan pengguna kepada query Bahasa Arab
    yang sesuai untuk pencarian kitab Turath.

    Gemini tidak diminta menjawab.
    Gemini hanya menghasilkan query carian Arab.
    """

    if not gemini_client:

        print(
            "⚠️ Gemini tidak tersedia - "
            "gunakan soalan asal"
        )

        return question

    question = normalize_question(
        question
    )

    if not question:
        return question

    prompt = f"""
أنت محرك لتحويل أسئلة الفقه إلى استعلام بحث باللغة العربية.

المطلوب:
حوّل سؤال المستخدم إلى جملة عربية مناسبة للبحث في كتب الفقه والتراث الإسلامي.

القواعد:
1. لا تجب عن السؤال.
2. لا تشرح.
3. أخرج استعلام البحث العربي فقط.
4. استخدم المصطلحات الفقهية العربية الدقيقة.
5. إذا ذكر المستخدم مذهباً فاذكره في الاستعلام.
6. إذا كان السؤال عن مسألة معينة فاجعل الاستعلام مركزاً على تلك المسألة.
7. لا تضف معلومات غير موجودة في السؤال.
8. لا تكتب أي مقدمة.
9. لا تستخدم علامات اقتباس.
10. اجعل الاستعلام واضحاً ومفيداً لمحرك البحث في كتب الفقه.

سؤال المستخدم:
{question}

استعلام البحث العربي:
"""

    try:

        response = gemini_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
        )

        arabic_query = (
            response.text
            if response
            else ""
        )

        arabic_query = normalize_question(
            arabic_query
        )

        # Buang label jika Gemini masih keluarkan
        arabic_query = re.sub(
            r"^(استعلام البحث العربي|query|arabic query)\s*[:：]\s*",
            "",
            arabic_query,
            flags=re.IGNORECASE
        )

        arabic_query = arabic_query.strip(
            "\"'“”"
        )

        if not arabic_query:

            print(
                "⚠️ Arabic query kosong - "
                "gunakan soalan asal"
            )

            return question

        print(
            "🇸🇦 ARABIC SEARCH QUERY:",
            arabic_query
        )

        return arabic_query

    except Exception:

        print(
            "❌ TRANSLATE TO ARABIC ERROR:"
        )

        traceback.print_exc()

        return question


# ============================================================
# BOOK HASH
# ============================================================

def get_book_hash(
    file_path
):

    try:

        path = Path(file_path)

        if not path.exists():
            return None

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

    except Exception:

        return None


# ============================================================
# OCR
# ============================================================

def ocr_page(
    image
):

    try:

        text = pytesseract.image_to_string(
            image,
            lang="ara+msa+eng"
        )

        return clean_text(text)

    except Exception:

        traceback.print_exc()

        return ""


# ============================================================
# LOCAL BOOK METADATA
# ============================================================

def get_local_book_metadata(
    book_hash=None
):

    if not supabase:
        return None

    if not book_hash:
        return None

    try:

        result = (
            supabase
            .table("kitab")
            .select("*")
            .eq(
                "book_hash",
                book_hash
            )
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception:

        print(
            "⚠️ LOCAL BOOK METADATA ERROR:"
        )

        traceback.print_exc()

    return None


# ============================================================
# LOCAL SUPABASE VECTOR SEARCH
# ============================================================

def search_local(
    question,
    limit=5
):

    """
    Cari sumber dalam Supabase.

    PENTING:
    RPC sebenar sekarang mempunyai signature:

    match_kitab_chunks(
        filter_category,
        match_count,
        query_embedding
    )

    Tiada parameter match_threshold.
    """

    if not supabase:

        print(
            "⚠️ LOCAL SEARCH SKIPPED: "
            "Supabase tidak tersedia"
        )

        return []

    if not gemini_client:

        print(
            "⚠️ LOCAL SEARCH SKIPPED: "
            "Gemini tidak tersedia"
        )

        return []

    print("🔍 LOCAL SEARCH")

    try:

        embedding_result = (
            gemini_client
            .models
            .embed_content(
                model=EMBEDDING_MODEL,
                contents=question,
                config=types.EmbedContentConfig(
                    output_dimensionality=3072
                )
            )
        )

        if not embedding_result:

            print(
                "❌ EMBEDDING RESULT KOSONG"
            )

            return []

        embeddings = getattr(
            embedding_result,
            "embeddings",
            None
        )

        if not embeddings:

            print(
                "❌ EMBEDDINGS KOSONG"
            )

            return []

        vector = embeddings[0].values

        print(
            "🧠 EMBEDDING DIMENSION:",
            len(vector)
        )

        # ====================================================
        # FIX:
        # match_kitab_chunks sebenar tidak menerima
        # match_threshold.
        #
        # Signature:
        # filter_category
        # match_count
        # query_embedding
        # ====================================================

        result = supabase.rpc(
            "match_kitab_chunks",
            {
                "filter_category": None,
                "query_embedding": vector,
                "match_count": max(
                    1,
                    int(limit)
                ),
            }
        ).execute()

        rows = (
            result.data
            if result
            else []
        )

        print(
            "📦 LOCAL RAW RESULTS:",
            len(rows)
        )

        normalized = []

        for row in rows:

            if not isinstance(
                row,
                dict
            ):
                continue

            content = (
                row.get("content")
                or row.get("text")
                or row.get("chunk_text")
                or row.get("chunk")
                or ""
            )

            content = clean_text(
                content
            )

            if not content:
                continue

            source = (
                row.get("book")
                or row.get("book_name")
                or row.get("title")
                or row.get("kitab")
                or "Sumber tempatan"
            )

            category = (
                row.get("category")
                or row.get("category_name")
                or ""
            )

            page = (
                row.get("page")
                or row.get("page_number")
                or ""
            )

            book_hash = (
                row.get("book_hash")
                or row.get("book_id")
                or ""
            )

            url = (
                row.get("url")
                or row.get("link")
                or ""
            )

            similarity = (
                row.get("similarity")
                or row.get("distance")
                or None
            )

            normalized.append(
                {
                    "source": str(source),
                    "category": str(category),
                    "page": str(page),
                    "book_hash": str(book_hash),
                    "url": str(url),
                    "similarity": similarity,
                    "content": content[
                        :SOURCE_MAX_CHARS
                    ],
                    "origin": "local",
                }
            )

        print(
            "📚 LOCAL NORMALIZED:",
            len(normalized)
        )

        return normalized

    except Exception:

        print(
            "❌ LOCAL SEARCH ERROR:"
        )

        traceback.print_exc()

        return []


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(
    question,
    comparison=False
):

    """
    Aliran:

    Soalan Melayu
        ↓
    Gemini
        ↓
    Query Arab
        ↓
    Turath service
        ↓
    passages
    """

    print("=" * 48)
    print("🔎 TURATH SEARCH")
    print("❓ Query:", question)
    print("⚖️ Comparison:", comparison)
    print(
        "🌐 URL:",
        TURATH_SERVICE_URL + "/search"
    )
    print("=" * 48)

    # ========================================================
    # Tukar kepada Bahasa Arab dahulu
    # ========================================================

    arabic_query = translate_to_arabic_query(
        question
    )

    print(
        "🇲🇾 ORIGINAL QUERY:",
        question
    )

    print(
        "🇸🇦 TURATH QUERY:",
        arabic_query
    )

    payload = {
        "query": arabic_query,
        "question": question,
        "comparison": comparison,
    }

    try:

        response = requests.post(
            TURATH_SERVICE_URL + "/search",
            json=payload,
            timeout=180,
        )

        print(
            "📡 TURATH STATUS:",
            response.status_code
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(
            data,
            dict
        ):

            print(
                "⚠️ TURATH RESPONSE bukan dict"
            )

            return []

        print(
            "📦 TURATH RESPONSE KEYS:",
            list(data.keys())
        )

        raw_results = (
            data.get("passages")
            or data.get("results")
            or data.get("data")
            or []
        )

        if not isinstance(
            raw_results,
            list
        ):

            raw_results = []

        print(
            "📚 TURATH RAW RESULTS:",
            len(raw_results)
        )

        results = []

        for item in raw_results:

            if not isinstance(
                item,
                dict
            ):
                continue

            content = (
                item.get("content")
                or item.get("text")
                or item.get("passage")
                or item.get("snippet")
                or ""
            )

            content = clean_text(
                content
            )

            if not content:
                continue

            source = (
                item.get("source")
                or item.get("book")
                or item.get("book_name")
                or item.get("title")
                or "Kitab Turath"
            )

            author = (
                item.get("author")
                or item.get("book_author")
                or ""
            )

            page = (
                item.get("page")
                or item.get("page_number")
                or ""
            )

            book_id = (
                item.get("book_id")
                or item.get("bookId")
                or ""
            )

            url = (
                item.get("url")
                or item.get("link")
                or ""
            )

            category = (
                item.get("category")
                or item.get("category_name")
                or ""
            )

            results.append(
                {
                    "source": str(source),
                    "author": str(author),
                    "page": str(page),
                    "book_id": str(book_id),
                    "url": str(url),
                    "category": str(category),
                    "content": content[
                        :SOURCE_MAX_CHARS
                    ],
                    "origin": "turath",
                }
            )

        print(
            "📚 TURATH SEARCH:",
            len(results),
            "sumber"
        )

        return results

    except requests.exceptions.RequestException:

        print(
            "❌ TURATH REQUEST ERROR:"
        )

        traceback.print_exc()

        return []

    except Exception:

        print(
            "❌ TURATH SEARCH ERROR:"
        )

        traceback.print_exc()

        return []


# ============================================================
# DEDUP SOURCES
# ============================================================

def deduplicate_sources(
    sources
):

    unique = []

    seen = set()

    for source in sources:

        content = clean_text(
            source.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        key = hashlib.sha256(
            content[:3000]
            .lower()
            .encode("utf-8")
        ).hexdigest()

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            source
        )

    return unique


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_context(
    sources
):

    if not sources:
        return ""

    blocks = []

    total_chars = 0

    for index, source in enumerate(
        sources,
        start=1
    ):

        content = clean_text(
            source.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        source_name = (
            source.get("source")
            or "Sumber tidak diketahui"
        )

        author = (
            source.get("author")
            or ""
        )

        page = (
            source.get("page")
            or ""
        )

        origin = (
            source.get("origin")
            or ""
        )

        header = (
            f"[SUMBER {index}]\n"
            f"Kitab: {source_name}"
        )

        if author:
            header += (
                f"\nPengarang: {author}"
            )

        if page:
            header += (
                f"\nHalaman: {page}"
            )

        if origin:
            header += (
                f"\nAsal: {origin}"
            )

        block = (
            header
            + "\n"
            + "Petikan:\n"
            + content
            + "\n"
        )

        if (
            total_chars
            + len(block)
            > CONTEXT_MAX_CHARS
        ):

            remaining = (
                CONTEXT_MAX_CHARS
                - total_chars
            )

            if remaining > 500:

                blocks.append(
                    block[:remaining]
                )

            break

        blocks.append(
            block
        )

        total_chars += len(block)

    return "\n".join(
        blocks
    )


# ============================================================
# FORMAT REFERENCES
# ============================================================

def format_references(
    sources
):

    if not sources:
        return (
            "📚 Rujukan:\n"
            "• Tiada rujukan ditemui."
        )

    lines = [
        "📚 Rujukan:"
    ]

    seen = set()

    for source in sources:

        name = (
            source.get("source")
            or "Kitab tidak diketahui"
        )

        author = (
            source.get("author")
            or ""
        )

        page = (
            source.get("page")
            or ""
        )

        url = (
            source.get("url")
            or ""
        )

        key = (
            name,
            author,
            page,
            url
        )

        if key in seen:
            continue

        seen.add(key)

        line = "• " + str(name)

        if author:
            line += (
                f" — {author}"
            )

        if page:
            line += (
                f", hlm. {page}"
            )

        if url:
            line += (
                f"\n  {url}"
            )

        lines.append(
            line
        )

    return "\n".join(
        lines
    )


# ============================================================
# GENERATE FINAL ANSWER
# ============================================================

def generate_answer(
    question,
    sources,
    comparison=False
):

    if not gemini_client:

        return (
            "Maaf, sistem AI tidak tersedia "
            "buat sementara waktu."
        )

    context = build_context(
        sources
    )

    if not context:

        return (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan "
            "huraian.\n\n"
            "📚 Rujukan:\n"
            "• Tiada rujukan ditemui."
        )

    if comparison:

        instruction = """
Soalan ini meminta perbandingan mazhab.

Bandingkan hanya berdasarkan sumber yang
diberikan. Jangan mereka-reka pandangan
mazhab yang tiada dalam sumber.
"""

    else:

        instruction = """
Soalan ini bukan permintaan perbandingan
antara semua mazhab.

Jawab berdasarkan mazhab atau konteks
yang diminta oleh pengguna.
"""

    prompt = f"""
Anda ialah pembantu ilmu fiqh.

Jawab soalan pengguna dalam Bahasa Melayu
yang jelas, tepat dan mudah difahami.

SOALAN PENGGUNA:
{question}

{instruction}

SUMBER KITAB YANG DIJUMPAI:
{context}

PERATURAN PENTING:

1. Jawab berdasarkan sumber yang diberikan.
2. Jangan mereka-reka fakta atau rujukan.
3. Jika sumber tidak mencukupi, nyatakan
   bahawa sumber yang ditemui tidak mencukupi.
4. Jika terdapat khilaf, jelaskan khilaf.
5. Jika pengguna bertanya menurut mazhab
   tertentu, utamakan mazhab tersebut.
6. Jangan menganggap semua mazhab sama.
7. Bezakan hukum, sunat, makruh, harus,
   wajib dan perkara lain dengan tepat.
8. Jika terdapat dalil atau petikan kitab,
   boleh sebut secara ringkas.
9. Jangan terlalu panjang.
10. Jawapan hendaklah dalam Bahasa Melayu.
11. Jangan gunakan Markdown table.
12. Jangan gunakan format yang menyebabkan
    Telegram gagal menghantar mesej.

Format jawapan:

Jawapan:
...

Penjelasan:
...

Jika sesuai:
Kesimpulan:
...

Jangan tulis senarai rujukan di akhir jawapan
kerana sistem akan tambah rujukan secara automatik.
"""

    try:

        response = gemini_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
        )

        answer = (
            response.text
            if response
            else ""
        )

        answer = strip_markdown(
            answer
        )

        if not answer:

            return (
                "Maaf, jawapan tidak dapat "
                "dihasilkan."
            )

        return answer.strip()

    except Exception:

        print(
            "❌ GENERATE ANSWER ERROR:"
        )

        traceback.print_exc()

        return (
            "Maaf, berlaku masalah ketika "
            "menghasilkan jawapan."
        )


# ============================================================
# ANSWER QUESTION
# ============================================================

def answer_question(
    question
):

    question = normalize_question(
        question
    )

    if not question:

        return (
            "Sila masukkan soalan fiqh."
        )

    cached = get_cached_answer(
        question
    )

    if cached:

        print(
            "⚡ CACHE HIT"
        )

        return cached

    print("=" * 60)
    print("❓ QUESTION:")
    print(question)
    print("=" * 60)

    comparison = is_madhhab_comparison(
        question
    )

    print(
        "⚖️ Comparison:",
        comparison
    )

    # ========================================================
    # LOCAL SEARCH
    # ========================================================

    local_sources = search_local(
        question,
        limit=5
    )

    # ========================================================
    # TURATH SEARCH
    # ========================================================

    turath_sources = search_turath(
        question,
        comparison=comparison
    )

    # ========================================================
    # COMBINE
    # ========================================================

    all_sources = (
        local_sources
        + turath_sources
    )

    all_sources = deduplicate_sources(
        all_sources
    )

    print(
        "📚 TOTAL SOURCES:",
        len(all_sources)
    )

    # ========================================================
    # GENERATE ANSWER
    # ========================================================

    answer = generate_answer(
        question,
        all_sources,
        comparison=comparison
    )

    # ========================================================
    # REFERENCES
    # ========================================================

    references = format_references(
        all_sources
    )

    final_answer = (
        answer
        + "\n\n"
        + references
    )

    final_answer = strip_markdown(
        final_answer
    )

    set_cached_answer(
        question,
        final_answer
    )

    return final_answer


# ============================================================
# TELEGRAM
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "Assalamualaikum.\n\n"
        "Saya TanyaFiqihBot.\n\n"
        "Sila hantar soalan fiqh anda."
    )


async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "Contoh soalan:\n\n"
        "• Apa hukum qunut Subuh menurut mazhab Syafie?\n"
        "• Adakah sentuh perempuan membatalkan wuduk?\n"
        "• Apa hukum jamak solat ketika musafir?\n"
        "• Bandingkan hukum qunut antara mazhab Syafie dan Hanafi."
    )


async def telegram_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if not message:
        return

    question = (
        message.text
        or ""
    ).strip()

    if not question:
        return

    print("=" * 60)
    print("📨 TELEGRAM QUESTION")
    print(question)
    print("=" * 60)

    try:

        await message.chat.send_action(
            action="typing"
        )

    except Exception:
        pass

    try:

        # answer_question ialah synchronous.
        # Jalankan dalam executor supaya event loop
        # Telegram tidak terganggu.

        loop = asyncio.get_running_loop()

        answer = await loop.run_in_executor(
            None,
            answer_question,
            question
        )

        chunks = telegram_chunks(
            answer,
            TELEGRAM_MAX_CHARS
        )

        for chunk in chunks:

            try:

                await message.reply_text(
                    chunk,
                    disable_web_page_preview=True
                )

            except Exception as e:

                print(
                    "❌ TELEGRAM MESSAGE ERROR:",
                    repr(e)
                )

                # Fallback paling selamat:
                # cuba hantar plain text lagi
                try:

                    await message.reply_text(
                        strip_markdown(
                            chunk
                        )
                    )

                except Exception:

                    traceback.print_exc()

    except Exception:

        print(
            "❌ TELEGRAM HANDLER ERROR:"
        )

        traceback.print_exc()

        try:

            await message.reply_text(
                "Maaf, berlaku masalah ketika "
                "memproses soalan."
            )

        except Exception:

            traceback.print_exc()


# ============================================================
# TELEGRAM STARTUP
# ============================================================

def telegram_worker():

    global telegram_application

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan"
        )

        return

    try:

        print(
            "🤖 STARTING TELEGRAM BOT..."
        )

        telegram_application = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        telegram_application.add_handler(
            CommandHandler(
                "start",
                telegram_start
            )
        )

        telegram_application.add_handler(
            CommandHandler(
                "help",
                telegram_help
            )
        )

        telegram_application.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                telegram_message
            )
        )

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(
            loop
        )

        loop.run_until_complete(
            telegram_application.initialize()
        )

        loop.run_until_complete(
            telegram_application.start()
        )

        loop.run_until_complete(
            telegram_application.updater.start_polling(
                drop_pending_updates=True
            )
        )

        print(
            "✅ TELEGRAM BOT RUNNING"
        )

        loop.run_forever()

    except Exception:

        print(
            "❌ TELEGRAM STARTUP ERROR:"
        )

        traceback.print_exc()


# ============================================================
# OPTIONAL BOOK SYNC
# ============================================================

def run_optional_book_sync():

    try:

        sync_function = globals().get(
            "sync_books"
        )

        if callable(sync_function):

            print(
                "📚 RUNNING BOOK SYNC..."
            )

            sync_function()

            print(
                "✅ BOOK SYNC COMPLETE"
            )

        else:

            print(
                "ℹ️ sync_books() tidak tersedia - skip"
            )

    except Exception:

        print(
            "❌ BOOK SYNC ERROR:"
        )

        traceback.print_exc()


# ============================================================
# BACKGROUND SERVICES
# ============================================================

def start_background_services():

    global background_started

    with background_lock:

        if background_started:

            return

        background_started = True

    if not START_BACKGROUND_SERVICES:

        print(
            "ℹ️ BACKGROUND SERVICES DISABLED"
        )

        return

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if TELEGRAM_TOKEN:

        telegram_thread = threading.Thread(
            target=telegram_worker,
            daemon=True,
            name="telegram-worker"
        )

        telegram_thread.start()

    # --------------------------------------------------------
    # Optional book sync
    # --------------------------------------------------------

    sync_thread = threading.Thread(
        target=run_optional_book_sync,
        daemon=True,
        name="book-sync-worker"
    )

    sync_thread.start()


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def index():

    return jsonify(
        {
            "success": True,
            "service": "TanyaFiqihBot",
            "status": "running",
            "turath_service": TURATH_SERVICE_URL,
        }
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    turath_status = False

    try:

        response = requests.get(
            TURATH_SERVICE_URL + "/health",
            timeout=5
        )

        turath_status = (
            response.status_code == 200
        )

    except Exception:
        turath_status = False

    return jsonify(
        {
            "success": True,
            "service": "TanyaFiqihBot",
            "supabase": supabase is not None,
            "gemini": gemini_client is not None,
            "telegram": telegram_application is not None,
            "turath": turath_status,
        }
    )


@app.route(
    "/ask",
    methods=["POST"]
)
def ask():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        question = (
            body.get("question")
            or body.get("query")
            or ""
        )

        question = normalize_question(
            question
        )

        if not question:

            return jsonify(
                {
                    "success": False,
                    "error": "Soalan kosong"
                }
            ), 400

        answer = answer_question(
            question
        )

        return jsonify(
            {
                "success": True,
                "question": question,
                "answer": answer,
            }
        )

    except Exception:

        print(
            "❌ /ask ERROR:"
        )

        traceback.print_exc()

        return jsonify(
            {
                "success": False,
                "error": "Internal server error"
            }
        ), 500


@app.route(
    "/search",
    methods=["POST"]
)
def search_endpoint():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        question = (
            body.get("question")
            or body.get("query")
            or ""
        )

        question = normalize_question(
            question
        )

        if not question:

            return jsonify(
                {
                    "success": False,
                    "error": "Soalan kosong"
                }
            ), 400

        comparison = is_madhhab_comparison(
            question
        )

        local_sources = search_local(
            question,
            limit=5
        )

        turath_sources = search_turath(
            question,
            comparison=comparison
        )

        sources = deduplicate_sources(
            local_sources
            + turath_sources
        )

        return jsonify(
            {
                "success": True,
                "question": question,
                "comparison": comparison,
                "count": len(sources),
                "sources": sources,
            }
        )

    except Exception:

        print(
            "❌ /search ERROR:"
        )

        traceback.print_exc()

        return jsonify(
            {
                "success": False,
                "error": "Internal server error"
            }
        ), 500


# ============================================================
# MAIN
# ============================================================

def main():

    start_background_services()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    host = os.getenv(
        "HOST",
        "0.0.0.0"
    )

    print("=" * 60)
    print("🚀 TANYAFIQIHBOT READY")
    print(
        f"🌐 Flask: http://{host}:{port}"
    )
    print(
        f"📚 Turath: {TURATH_SERVICE_URL}"
    )
    print("=" * 60)

    app.run(
        host=host,
        port=port,
        threaded=True
    )


# ============================================================
# START
# ============================================================

# Penting untuk Gunicorn:
# start_background_services() perlu dipanggil ketika
# module di-load kerana Gunicorn tidak memanggil main().

if __name__ == "__main__":

    main()

else:

    # Gunicorn import app:app
    start_background_services()
