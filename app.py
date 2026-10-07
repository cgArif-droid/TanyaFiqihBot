import os
import re
import json
import asyncio
import hashlib
import threading
import traceback
import time
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

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY",
    ""
).strip()

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    ""
).strip()

DATA_DIR = Path(
    os.getenv(
        "DATA_DIR",
        "/var/data"
    )
)

OCR_DIR = DATA_DIR / "ocr_cache"

OCR_WORKERS = int(
    os.getenv(
        "OCR_WORKERS",
        "2"
    )
)


# ============================================================
# GEMINI MODELS
# ============================================================

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.1-flash-lite"
).strip()

ARABIC_QUERY_MODEL = os.getenv(
    "ARABIC_QUERY_MODEL",
    "gemini-3.1-flash-lite"
).strip()

FALLBACK_LLM_MODEL = os.getenv(
    "FALLBACK_LLM_MODEL",
    ""
).strip()

FALLBACK_ARABIC_MODEL = os.getenv(
    "FALLBACK_ARABIC_MODEL",
    ""
).strip()

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "gemini-embedding-001"
).strip()


# ============================================================
# TURATH
# ============================================================

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).strip().rstrip("/")


# ============================================================
# LIMITS
# ============================================================

SOURCE_MAX_CHARS = int(
    os.getenv(
        "SOURCE_MAX_CHARS",
        "4500"
    )
)

CONTEXT_MAX_CHARS = int(
    os.getenv(
        "CONTEXT_MAX_CHARS",
        "30000"
    )
)

TELEGRAM_MAX_CHARS = int(
    os.getenv(
        "TELEGRAM_MAX_CHARS",
        "3900"
    )
)

GEMINI_RETRIES = int(
    os.getenv(
        "GEMINI_RETRIES",
        "2"
    )
)

GEMINI_INITIAL_WAIT = float(
    os.getenv(
        "GEMINI_INITIAL_WAIT",
        "2"
    )
)

START_BACKGROUND_SERVICES = (
    os.getenv(
        "START_BACKGROUND_SERVICES",
        "true"
    )
    .lower()
    in (
        "1",
        "true",
        "yes",
        "on"
    )
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

print("=" * 65)
print("🚀 TANYAFIQIHBOT STARTING")
print("=" * 65)

print("🤖 LLM MODEL:", LLM_MODEL)
print("🇸🇦 ARABIC QUERY MODEL:", ARABIC_QUERY_MODEL)
print(
    "🛟 FALLBACK LLM:",
    FALLBACK_LLM_MODEL or "DISABLED"
)
print(
    "🛟 FALLBACK ARABIC:",
    FALLBACK_ARABIC_MODEL or "DISABLED"
)
print("🧠 EMBEDDING:", EMBEDDING_MODEL)
print("📚 TURATH:", TURATH_SERVICE_URL)
print("🔄 GEMINI RETRIES:", GEMINI_RETRIES)

print("=" * 65)


# ============================================================
# SUPABASE
# ============================================================

def init_supabase():

    global supabase

    if not SUPABASE_URL:
        print("⚠️ SUPABASE_URL tidak ditetapkan")
        return

    if not SUPABASE_KEY:
        print("⚠️ SUPABASE_KEY tidak ditetapkan")
        return

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("✅ SUPABASE INITIALIZED")

    except Exception:

        print("❌ SUPABASE INIT ERROR")
        traceback.print_exc()

        supabase = None


# ============================================================
# GEMINI
# ============================================================

def init_gemini():

    global gemini_client

    if not GOOGLE_API_KEY:

        print(
            "⚠️ GOOGLE_API_KEY tidak ditetapkan"
        )

        return

    try:

        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print(
            "✅ GEMINI INITIALIZED"
        )

    except Exception:

        print(
            "❌ GEMINI INIT ERROR"
        )

        traceback.print_exc()

        gemini_client = None


init_supabase()
init_gemini()


# ============================================================
# TEXT
# ============================================================

def normalize_question(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "\r",
        " "
    )

    text = text.replace(
        "\n",
        " "
    )

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


# ============================================================
# TELEGRAM CLEANER
# ============================================================

def strip_markdown(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "**",
        ""
    )

    text = text.replace(
        "__",
        ""
    )

    text = text.replace(
        "*",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.replace(
        "`",
        ""
    )

    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text
    )

    return text.strip()


# ============================================================
# TELEGRAM CHUNKS
# ============================================================

def telegram_chunks(
    text,
    max_chars=TELEGRAM_MAX_CHARS
):

    text = strip_markdown(
        text
    )

    if len(text) <= max_chars:
        return [text]

    chunks = []

    current = ""

    paragraphs = text.split(
        "\n"
    )

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if (
            len(current)
            + len(paragraph)
            + 1
            <= max_chars
        ):

            if current:
                current += "\n"

            current += paragraph

        else:

            if current:
                chunks.append(
                    current
                )

            while len(paragraph) > max_chars:

                chunks.append(
                    paragraph[:max_chars]
                )

                paragraph = paragraph[
                    max_chars:
                ]

            current = paragraph

    if current:
        chunks.append(
            current
        )

    return chunks


# ============================================================
# CACHE
# ============================================================

def cache_key(question):

    normalized = normalize_question(
        question
    ).lower()

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()


def get_cached_answer(question):

    key = cache_key(
        question
    )

    with cache_lock:
        return answer_cache.get(
            key
        )


def set_cached_answer(
    question,
    answer
):

    if not answer:
        return

    key = cache_key(
        question
    )

    with cache_lock:

        answer_cache[key] = answer

        if len(answer_cache) > 100:

            first_key = next(
                iter(answer_cache)
            )

            del answer_cache[
                first_key
            ]


# ============================================================
# FIqh QUERY MAP
# ============================================================

FIQH_QUERY_MAP = {

    # ----------------------------
    # TAHARAH
    # ----------------------------

    "mandi wajib":
        "الغسل الواجب غسل الجنابة صفة الغسل فرائض الغسل",

    "mandi junub":
        "غسل الجنابة صفة الغسل",

    "mandi janabah":
        "غسل الجنابة",

    "junub":
        "الجنابة غسل الجنابة",

    "hadas besar":
        "الحدث الأكبر الغسل",

    "wajib mandi":
        "موجبات الغسل",

    "sebab mandi wajib":
        "موجبات الغسل",

    "rukun mandi wajib":
        "فرائض الغسل",

    "cara mandi wajib":
        "صفة الغسل",

    "sifat mandi":
        "صفة الغسل",

    "mandi haid":
        "غسل الحيض",

    "mandi nifas":
        "غسل النفاس",

    "mandi wiladah":
        "غسل الولادة",

    "haid":
        "الحيض",

    "nifas":
        "النفاس",

    "istihadah":
        "الاستحاضة",

    "wuduk":
        "الوضوء فرائض الوضوء",

    "wudhu":
        "الوضوء فرائض الوضوء",

    "rukun wuduk":
        "فرائض الوضوء",

    "batal wuduk":
        "نواقض الوضوء",

    "batal air sembahyang":
        "نواقض الوضوء",

    "tayamum":
        "التيمم فرائض التيمم",

    "rukun tayamum":
        "فرائض التيمم",

    "najis":
        "النجاسة أحكام النجاسة",

    "bersuci":
        "الطهارة",

    "taharah":
        "الطهارة",

    "air mutlak":
        "الماء الطهور",

    "air":
        "المياه أحكام الطهارة",

    # ----------------------------
    # SOLAT
    # ----------------------------

    "solat":
        "الصلاة أحكام الصلاة",

    "sembahyang":
        "الصلاة أحكام الصلاة",

    "rukun solat":
        "أركان الصلاة",

    "syarat solat":
        "شروط الصلاة",

    "batal solat":
        "مبطلات الصلاة",

    "qunut":
        "القنوت في صلاة الصبح",

    "qunut subuh":
        "القنوت في صلاة الصبح",

    "solat subuh":
        "صلاة الصبح القنوت",

    "solat jumaat":
        "صلاة الجمعة",

    "jumaat":
        "صلاة الجمعة",

    "jamak":
        "الجمع بين الصلاتين",

    "qasar":
        "القصر في الصلاة",

    "jamak qasar":
        "الجمع والقصر",

    "musafir":
        "السفر أحكام صلاة المسافر",

    "sujud sahwi":
        "سجود السهو",

    "sujud tilawah":
        "سجود التلاوة",

    "azan":
        "الأذان",

    "iqamah":
        "الإقامة",

    # ----------------------------
    # PUASA
    # ----------------------------

    "puasa":
        "الصيام أحكام الصيام",

    "puasa ramadan":
        "صيام رمضان",

    "ramadan":
        "صيام رمضان",

    "rukun puasa":
        "أركان الصيام",

    "syarat puasa":
        "شروط الصيام",

    "batal puasa":
        "مبطلات الصيام",

    "qada puasa":
        "قضاء الصيام",

    "ganti puasa":
        "قضاء الصيام",

    "fidyah":
        "الفدية",

    "kaffarah puasa":
        "كفارة الصيام",

    # ----------------------------
    # ZAKAT
    # ----------------------------

    "zakat":
        "الزكاة أحكام الزكاة",

    "zakat fitrah":
        "زكاة الفطر",

    "zakat harta":
        "زكاة المال",

    "zakat pendapatan":
        "زكاة المال الكسب",

    # ----------------------------
    # HAJI / UMRAH
    # ----------------------------

    "haji":
        "الحج أحكام الحج",

    "umrah":
        "العمرة أحكام العمرة",

    "ihram":
        "الإحرام",

    "miqat":
        "المواقيت",

    "talbiyah":
        "التلبية",

    "korban":
        "الأضحية",

    "aqiqah":
        "العقيقة",

    "akikah":
        "العقيقة",

    # ----------------------------
    # MUAMALAT
    # ----------------------------

    "jual beli":
        "البيع أحكام البيع",

    "jual-beli":
        "البيع أحكام البيع",

    "riba":
        "الربا",

    "hutang":
        "الدين أحكام الدين",

    "pinjaman":
        "القرض",

    "wakaf":
        "الوقف",

    "nazar":
        "النذر",

    "sumpah":
        "اليمين",

    # ----------------------------
    # MUNAKAHAT
    # ----------------------------

    "nikah":
        "النكاح أحكام النكاح",

    "kahwin":
        "النكاح",

    "perkahwinan":
        "النكاح",

    "mahar":
        "المهر",

    "mas kahwin":
        "المهر",

    "talak":
        "الطلاق",

    "cerai":
        "الطلاق",

    "rujuk":
        "الرجعة",

    "iddah":
        "العدة",

    "nafkah":
        "النفقة الزوجية",

}


def get_fiqh_mapped_query(
    question
):

    q = normalize_question(
        question
    ).lower()

    keys = sorted(
        FIQH_QUERY_MAP.keys(),
        key=len,
        reverse=True
    )

    for key in keys:

        if key in q:

            return FIQH_QUERY_MAP[
                key
            ]

    return ""


# ============================================================
# MADHHAB DETECTION
# ============================================================

def is_madhhab_comparison(question):

    q = normalize_question(
        question
    ).lower()

    explicit_phrases = [

        "bandingkan",
        "banding mazhab",
        "bandingkan mazhab",

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

        "قارن",
        "مقارنة",
        "الفرق بين",
        "المذاهب الأربعة",
    ]

    if any(
        phrase in q
        for phrase in explicit_phrases
    ):
        return True

    patterns = [

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
        for pattern in patterns
    )

    return count >= 2


# ============================================================
# GET REQUESTED MADHHAB
# ============================================================

def detect_requested_madhhab(
    question
):

    q = normalize_question(
        question
    ).lower()

    if (
        "syafie" in q
        or "syafi'i" in q
        or "syafi’i" in q
        or "شافعية" in q
    ):
        return "syafii"

    if (
        "hanafi" in q
        or "حنفية" in q
    ):
        return "hanafi"

    if (
        "maliki" in q
        or "مالكية" in q
    ):
        return "maliki"

    if (
        "hanbali" in q
        or "حنابلة" in q
    ):
        return "hanbali"

    return ""


# ============================================================
# GEMINI ERROR
# ============================================================

def is_retryable_gemini_error(
    error
):

    text = str(
        error
    ).upper()

    retry_words = [

        "503",
        "UNAVAILABLE",
        "HIGH DEMAND",

        "RESOURCE EXHAUSTED",
        "429",
        "RATE LIMIT",

        "TOO MANY REQUESTS",
        "INTERNAL",

        "DEADLINE",
        "TIMEOUT",
    ]

    return any(
        word in text
        for word in retry_words
    )


# ============================================================
# GEMINI GENERATE
# ============================================================

def gemini_generate_with_retry(
    model,
    contents,
    label="GEMINI"
):

    if not gemini_client:

        print(
            f"❌ {label}: Gemini client tidak tersedia"
        )

        return None

    last_error = None

    retries = max(
        1,
        GEMINI_RETRIES
    )

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            print(
                f"🤖 {label}: "
                f"model={model} "
                f"attempt={attempt}/{retries}"
            )

            response = (
                gemini_client
                .models
                .generate_content(
                    model=model,
                    contents=contents,
                )
            )

            if response:

                text = getattr(
                    response,
                    "text",
                    None
                )

                if text:

                    print(
                        f"✅ {label}: SUCCESS"
                    )

                    return text.strip()

            print(
                f"⚠️ {label}: response kosong"
            )

        except Exception as e:

            last_error = e

            print(
                f"⚠️ {label} ERROR attempt {attempt}:"
            )

            print(
                str(e)
            )

            if not is_retryable_gemini_error(
                e
            ):

                traceback.print_exc()

                break

            if attempt < retries:

                wait_seconds = (
                    GEMINI_INITIAL_WAIT
                    * (
                        2
                        ** (
                            attempt - 1
                        )
                    )
                )

                print(
                    f"⏳ Retry dalam {wait_seconds}s..."
                )

                time.sleep(
                    wait_seconds
                )

    print(
        f"❌ {label}: SEMUA RETRY GAGAL"
    )

    if last_error:

        print(
            "Last error:",
            str(last_error)
        )

    return None


def gemini_generate_resilient(
    primary_model,
    fallback_model,
    contents,
    label="GEMINI"
):

    result = gemini_generate_with_retry(
        model=primary_model,
        contents=contents,
        label=label
    )

    if result:
        return result

    if (
        fallback_model
        and fallback_model != primary_model
    ):

        result = gemini_generate_with_retry(
            model=fallback_model,
            contents=contents,
            label=f"{label} FALLBACK"
        )

        if result:
            return result

    return None


# ============================================================
# ARABIC QUERY
# ============================================================

def translate_to_arabic_query(
    question
):

    question = normalize_question(
        question
    )

    # --------------------------------------------------------
    # FIRST: SPECIAL FIqh MAP
    # --------------------------------------------------------

    mapped_query = get_fiqh_mapped_query(
        question
    )

    if mapped_query:

        print(
            "🗺️ FIqh QUERY MAP:",
            mapped_query
        )

        # Jika soalan menyebut mazhab,
        # tambah mazhab ke query.
        madhhab = detect_requested_madhhab(
            question
        )

        if madhhab == "syafii":
            mapped_query += " الشافعية"

        elif madhhab == "hanafi":
            mapped_query += " الحنفية"

        elif madhhab == "maliki":
            mapped_query += " المالكية"

        elif madhhab == "hanbali":
            mapped_query += " الحنابلة"

        return mapped_query

    # --------------------------------------------------------
    # GEMINI TRANSLATION
    # --------------------------------------------------------

    if not gemini_client:

        return question

    prompt = f"""
أنت محرك لتحويل أسئلة الفقه إلى استعلام بحث باللغة العربية.

المطلوب:
حوّل سؤال المستخدم إلى استعلام عربي مناسب للبحث في كتب الفقه والتراث الإسلامي.

القواعد:
1. لا تجب عن السؤال.
2. لا تشرح.
3. أخرج استعلام البحث العربي فقط.
4. استخدم المصطلحات الفقهية العربية الدقيقة.
5. إذا ذكر المستخدم مذهباً فاذكره.
6. ركّز على المسألة الفقهية المحددة.
7. لا تضف حكماً غير موجود في السؤال.
8. لا تستخدم علامات اقتباس.

سؤال المستخدم:
{question}

استعلام البحث العربي:
"""

    result = gemini_generate_resilient(
        primary_model=ARABIC_QUERY_MODEL,
        fallback_model=FALLBACK_ARABIC_MODEL,
        contents=prompt,
        label="ARABIC TRANSLATION"
    )

    if not result:

        return question

    arabic_query = normalize_question(
        result
    )

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

        return question

    print(
        "🇸🇦 ARABIC SEARCH QUERY:",
        arabic_query
    )

    return arabic_query


# ============================================================
# EMBEDDING
# ============================================================

def create_embedding_with_retry(
    question
):

    if not gemini_client:
        return None

    retries = max(
        1,
        GEMINI_RETRIES
    )

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            print(
                f"🧠 EMBEDDING ATTEMPT "
                f"{attempt}/{retries}"
            )

            result = (
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

            embeddings = getattr(
                result,
                "embeddings",
                None
            )

            if embeddings:

                vector = embeddings[
                    0
                ].values

                print(
                    "🧠 EMBEDDING DIMENSION:",
                    len(vector)
                )

                return vector

        except Exception as e:

            print(
                "⚠️ EMBEDDING ERROR:",
                str(e)
            )

            if not is_retryable_gemini_error(
                e
            ):
                break

            if attempt < retries:

                wait_seconds = (
                    GEMINI_INITIAL_WAIT
                    * (
                        2
                        ** (
                            attempt - 1
                        )
                    )
                )

                time.sleep(
                    wait_seconds
                )

    return None


# ============================================================
# LOCAL SEARCH
# ============================================================

def search_local(
    question,
    limit=5
):

    if not supabase:
        return []

    if not gemini_client:
        return []

    print(
        "🔍 LOCAL SEARCH"
    )

    vector = create_embedding_with_retry(
        question
    )

    if not vector:
        return []

    try:

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
                or row.get("passage")
                or ""
            )

            content = clean_text(
                content
            )

            if not content:
                continue

            source = (
                row.get("source")
                or row.get("book")
                or row.get("book_name")
                or row.get("book_title")
                or row.get("title")
                or row.get("kitab")
                or ""
            )

            category = (
                row.get("category")
                or row.get("category_name")
                or row.get("madhhab")
                or ""
            )

            page = (
                row.get("page")
                or row.get("page_number")
                or row.get("halaman")
                or ""
            )

            book_hash = (
                row.get("book_hash")
                or row.get("book_id")
                or row.get("bookId")
                or ""
            )

            author = (
                row.get("author")
                or row.get("book_author")
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
                    "book": str(
                        row.get("book")
                        or ""
                    ),
                    "book_title": str(
                        row.get("book_title")
                        or row.get("book_name")
                        or ""
                    ),
                    "author": str(author),
                    "category": str(category),
                    "page": str(page),
                    "page_number": str(page),
                    "book_hash": str(book_hash),
                    "book_id": str(book_hash),
                    "url": str(url),
                    "similarity": similarity,
                    "content": content[
                        :SOURCE_MAX_CHARS
                    ],
                    "origin": "local",
                }
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

    print("=" * 55)
    print("🔎 TURATH SEARCH")
    print(
        "❓ Query:",
        question
    )
    print(
        "⚖️ Comparison:",
        comparison
    )
    print("=" * 55)

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
            return []

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
                or item.get("book_title")
                or item.get("title")
                or ""
            )

            book = (
                item.get("book")
                or item.get("book_title")
                or item.get("book_name")
                or source
                or ""
            )

            book_title = (
                item.get("book_title")
                or item.get("book_name")
                or item.get("book")
                or source
                or ""
            )

            author = (
                item.get("author")
                or item.get("book_author")
                or item.get("author_name")
                or ""
            )

            page = (
                item.get("page")
                or item.get("page_number")
                or item.get("page_no")
                or item.get("halaman")
                or ""
            )

            book_id = (
                item.get("book_id")
                or item.get("bookId")
                or item.get("book_hash")
                or ""
            )

            result_id = (
                item.get("id")
                or item.get("chunk_id")
                or item.get("chunkId")
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
                or item.get("mazhab")
                or ""
            )

            category_id = (
                item.get("category_id")
                or item.get("categoryId")
                or ""
            )

            results.append(
                {
                    "source": str(source),
                    "book": str(book),
                    "book_title": str(book_title),
                    "author": str(author),
                    "page": str(page),
                    "page_number": str(page),
                    "book_id": str(book_id),
                    "id": str(result_id),
                    "url": str(url),
                    "category": str(category),
                    "category_id": str(category_id),
                    "query": str(
                        item.get("query")
                        or item.get("_query")
                        or arabic_query
                    ),
                    "content": content[
                        :SOURCE_MAX_CHARS
                    ],
                    "origin": "turath",
                }
            )

        print(
            "📚 TURATH NORMALIZED:",
            len(results)
        )

        if results:

            print(
                "🧪 FIRST TURATH SOURCE:"
            )

            print(
                json.dumps(
                    results[0],
                    ensure_ascii=False,
                    indent=2
                )[:5000]
            )

        return results

    except Exception:

        print(
            "❌ TURATH SEARCH ERROR:"
        )

        traceback.print_exc()

        return []


# ============================================================
# RELEVANCE FILTER
# ============================================================

def relevance_score(
    question,
    source
):

    q = normalize_question(
        question
    ).lower()

    content = clean_text(
        source.get(
            "content",
            ""
        )
    ).lower()

    metadata = " ".join(
        [
            str(
                source.get(
                    "source",
                    ""
                )
            ),
            str(
                source.get(
                    "book",
                    ""
                )
            ),
            str(
                source.get(
                    "book_title",
                    ""
                )
            ),
            str(
                source.get(
                    "category",
                    ""
                )
            ),
        ]
    ).lower()

    text = (
        content
        + " "
        + metadata
    )

    score = 0

    # --------------------------------------------------------
    # TOPIC KEYWORDS
    # --------------------------------------------------------

    topic_groups = {

        "mandi": [
            "غسل",
            "الجنابة",
            "الغسل",
            "موجبات الغسل",
            "فرائض الغسل",
            "صفة الغسل",
        ],

        "wuduk": [
            "وضوء",
            "الوضوء",
            "فرائض الوضوء",
            "نواقض الوضوء",
        ],

        "solat": [
            "صلاة",
            "الصلاة",
            "أركان الصلاة",
            "مبطلات الصلاة",
        ],

        "qunut": [
            "قنوت",
            "القنوت",
        ],

        "puasa": [
            "صيام",
            "الصيام",
            "رمضان",
        ],

        "zakat": [
            "زكاة",
            "الزكاة",
        ],

        "haji": [
            "حج",
            "الحج",
            "إحرام",
        ],

        "nikah": [
            "نكاح",
            "النكاح",
            "زواج",
        ],

        "talak": [
            "طلاق",
            "الطلاق",
        ],

        "riba": [
            "ربا",
            "الربا",
        ],
    }

    detected_topics = []

    if any(
        x in q
        for x in [
            "mandi wajib",
            "mandi junub",
            "junub",
            "hadas besar",
            "wajib mandi",
            "cara mandi"
        ]
    ):
        detected_topics.append(
            "mandi"
        )

    if any(
        x in q
        for x in [
            "wuduk",
            "wudhu",
            "batal wuduk"
        ]
    ):
        detected_topics.append(
            "wuduk"
        )

    if any(
        x in q
        for x in [
            "qunut"
        ]
    ):
        detected_topics.append(
            "qunut"
        )

    if any(
        x in q
        for x in [
            "solat",
            "sembahyang"
        ]
    ):
        detected_topics.append(
            "solat"
        )

    if any(
        x in q
        for x in [
            "puasa",
            "ramadan"
        ]
    ):
        detected_topics.append(
            "puasa"
        )

    if any(
        x in q
        for x in [
            "zakat"
        ]
    ):
        detected_topics.append(
            "zakat"
        )

    if any(
        x in q
        for x in [
            "haji",
            "umrah",
            "ihram"
        ]
    ):
        detected_topics.append(
            "haji"
        )

    if any(
        x in q
        for x in [
            "nikah",
            "kahwin",
            "perkahwinan"
        ]
    ):
        detected_topics.append(
            "nikah"
        )

    if any(
        x in q
        for x in [
            "talak",
            "cerai"
        ]
    ):
        detected_topics.append(
            "talak"
        )

    if any(
        x in q
        for x in [
            "riba"
        ]
    ):
        detected_topics.append(
            "riba"
        )

    for topic in detected_topics:

        keywords = topic_groups.get(
            topic,
            []
        )

        matched = sum(
            1
            for keyword in keywords
            if keyword in text
        )

        if matched:
            score += (
                matched * 25
            )

    # --------------------------------------------------------
    # MALAY TOPIC TERMS
    # --------------------------------------------------------

    malay_terms = {

        "mandi": [
            "mandi",
            "junub",
            "hadas besar",
        ],

        "wuduk": [
            "wuduk",
            "wudhu",
        ],

        "solat": [
            "solat",
            "sembahyang",
        ],

        "qunut": [
            "qunut",
        ],

        "puasa": [
            "puasa",
            "ramadan",
        ],

        "zakat": [
            "zakat",
        ],

        "haji": [
            "haji",
            "umrah",
        ],

        "nikah": [
            "nikah",
            "kahwin",
        ],

        "talak": [
            "talak",
            "cerai",
        ],
    }

    for terms in malay_terms.values():

        for term in terms:

            if term in q:

                # Tidak terlalu tinggi kerana content
                # biasanya Arab.
                if term in text:
                    score += 10

    # --------------------------------------------------------
    # MADHHAB
    # --------------------------------------------------------

    madhhab = detect_requested_madhhab(
        question
    )

    if madhhab == "syafii":

        if (
            "الشافعية" in text
            or "شافعية" in text
        ):
            score += 30

    elif madhhab == "hanafi":

        if "الحنفية" in text:
            score += 30

    elif madhhab == "maliki":

        if "المالكية" in text:
            score += 30

    elif madhhab == "hanbali":

        if "الحنابلة" in text:
            score += 30

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    if source.get("book_id"):
        score += 5

    if source.get("author"):
        score += 5

    if source.get("page"):
        score += 5

    return score


def filter_relevant_sources(
    question,
    sources,
    minimum_score=10
):

    if not sources:
        return []

    scored = []

    for source in sources:

        score = relevance_score(
            question,
            source
        )

        source = dict(
            source
        )

        source[
            "_relevance_score"
        ] = score

        scored.append(
            source
        )

    scored.sort(
        key=lambda x: (
            x.get(
                "_relevance_score",
                0
            ),
            1
            if x.get(
                "origin"
            ) == "turath"
            else 0,
        ),
        reverse=True
    )

    relevant = [
        x
        for x in scored
        if x.get(
            "_relevance_score",
            0
        ) >= minimum_score
    ]

    print(
        "🎯 RELEVANT SOURCES:",
        len(relevant),
        "/",
        len(sources)
    )

    for source in scored[:10]:

        print(
            "   SCORE:",
            source.get(
                "_relevance_score"
            ),
            "|",
            source.get(
                "source"
            )
            or source.get(
                "book"
            )
            or "TIADA"
        )

    return relevant


# ============================================================
# DEDUPLICATE
# ============================================================

def deduplicate_sources(
    sources
):

    unique = []

    seen = set()

    sorted_sources = sorted(
        sources,
        key=lambda x: (
            0
            if x.get("origin") == "turath"
            else 1
        )
    )

    for source in sorted_sources:

        content = clean_text(
            source.get(
                "content",
                ""
            )
        )

        if not content:
            continue

        # Buang duplicate berdasarkan
        # kandungan dan metadata.
        key_text = (
            content[:3000]
            + "|"
            + str(
                source.get(
                    "book_id",
                    ""
                )
            )
            + "|"
            + str(
                source.get(
                    "page",
                    ""
                )
            )
        )

        key = hashlib.sha256(
            key_text
            .lower()
            .encode(
                "utf-8"
            )
        ).hexdigest()

        if key in seen:
            continue

        seen.add(
            key
        )

        unique.append(
            source
        )

    return unique


# ============================================================
# SORT SOURCES
# ============================================================

def sort_sources(
    sources
):

    def score(source):

        value = 0

        if (
            source.get(
                "origin"
            )
            == "turath"
        ):
            value += 100

        value += int(
            source.get(
                "_relevance_score",
                0
            )
            or 0
        )

        if source.get(
            "source"
        ):
            value += 20

        if source.get(
            "author"
        ):
            value += 10

        if source.get(
            "page"
        ):
            value += 10

        if source.get(
            "book_id"
        ):
            value += 10

        return value

    return sorted(
        sources,
        key=score,
        reverse=True
    )


# ============================================================
# SELECT BEST SOURCES
# ============================================================

def select_sources(
    question,
    turath_sources,
    local_sources,
    comparison=False
):

    # --------------------------------------------------------
    # DEDUP TURATH
    # --------------------------------------------------------

    turath_sources = deduplicate_sources(
        turath_sources
    )

    # --------------------------------------------------------
    # FILTER TURATH
    # --------------------------------------------------------

    relevant_turath = filter_relevant_sources(
        question,
        turath_sources,
        minimum_score=10
    )

    relevant_turath = sort_sources(
        relevant_turath
    )

    # --------------------------------------------------------
    # NORMAL QUESTION
    # --------------------------------------------------------

    if not comparison:

        # Jika Turath ada sumber yang relevan,
        # JANGAN campurkan local.
        if relevant_turath:

            print(
                "📚 MODE: TURATH ONLY"
            )

            return relevant_turath[:10]

        # Turath ada tetapi tidak relevan.
        # Cuba local sebagai fallback.
        print(
            "⚠️ TURATH TIADA SUMBER RELEVAN"
        )

        relevant_local = filter_relevant_sources(
            question,
            local_sources,
            minimum_score=10
        )

        if relevant_local:

            print(
                "📚 MODE: LOCAL FALLBACK"
            )

            return sort_sources(
                relevant_local
            )[:5]

        # Jika filter terlalu ketat,
        # guna Turath dahulu.
        if turath_sources:

            print(
                "📚 MODE: TURATH FALLBACK"
            )

            return sort_sources(
                turath_sources
            )[:5]

        return sort_sources(
            local_sources
        )[:5]

    # --------------------------------------------------------
    # COMPARISON
    # --------------------------------------------------------

    if comparison:

        # Untuk comparison, Turath semua mazhab
        # lebih utama.
        if relevant_turath:

            print(
                "⚖️ MODE: TURATH COMPARISON"
            )

            return relevant_turath[:16]

        relevant_local = filter_relevant_sources(
            question,
            local_sources,
            minimum_score=10
        )

        return sort_sources(
            relevant_local
        )[:8]


# ============================================================
# CONTEXT
# ============================================================

def build_context(
    sources
):

    if not sources:
        return ""

    sources = sort_sources(
        sources
    )

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
            source.get(
                "source"
            )
            or source.get(
                "book"
            )
            or source.get(
                "book_title"
            )
            or (
                "Kitab Turath"
                if source.get(
                    "origin"
                ) == "turath"
                else "Sumber tempatan"
            )
        )

        author = (
            source.get(
                "author"
            )
            or ""
        )

        page = (
            source.get(
                "page"
            )
            or source.get(
                "page_number"
            )
            or ""
        )

        category = (
            source.get(
                "category"
            )
            or ""
        )

        origin = (
            source.get(
                "origin"
            )
            or ""
        )

        relevance = (
            source.get(
                "_relevance_score",
                ""
            )
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

        if category:
            header += (
                f"\nMazhab/Kategori: {category}"
            )

        if origin:
            header += (
                f"\nAsal: {origin}"
            )

        if relevance != "":
            header += (
                f"\nSkor relevansi: {relevance}"
            )

        block = (
            header
            + "\nPetikan:\n"
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
                    block[
                        :remaining
                    ]
                )

            break

        blocks.append(
            block
        )

        total_chars += len(
            block
        )

    return "\n".join(
        blocks
    )


# ============================================================
# REFERENCES
# ============================================================

def format_references(
    sources
):

    if not sources:

        return (
            "📚 Rujukan:\n"
            "• Tiada rujukan ditemui."
        )

    sources = sort_sources(
        sources
    )

    lines = [
        "📚 Rujukan:"
    ]

    seen = set()

    for source in sources:

        origin = (
            source.get(
                "origin"
            )
            or ""
        )

        name = (
            source.get(
                "source"
            )
            or source.get(
                "book"
            )
            or source.get(
                "book_title"
            )
            or (
                "Kitab Turath"
                if origin == "turath"
                else "Sumber tempatan"
            )
        )

        author = (
            source.get(
                "author"
            )
            or ""
        )

        page = (
            source.get(
                "page"
            )
            or source.get(
                "page_number"
            )
            or ""
        )

        url = (
            source.get(
                "url"
            )
            or ""
        )

        category = (
            source.get(
                "category"
            )
            or ""
        )

        key = (
            str(name).strip(),
            str(author).strip(),
            str(page).strip(),
            str(url).strip(),
            str(origin).strip(),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        line = (
            "• "
            + str(name)
        )

        if author:
            line += (
                f" — {author}"
            )

        if page:
            line += (
                f", hlm. {page}"
            )

        if category:
            line += (
                f" [{category}]"
            )

        if url:
            line += (
                f"\n  {url}"
            )

        lines.append(
            line
        )

    if len(lines) == 1:

        return (
            "📚 Rujukan:\n"
            "• Tiada rujukan ditemui."
        )

    return "\n".join(
        lines
    )


# ============================================================
# SOURCE FALLBACK
# ============================================================

def generate_source_fallback(
    question,
    sources,
    comparison=False
):

    if not sources:

        return (
            "⚠️ Tiada sumber yang berjaya "
            "ditemui untuk soalan ini."
        )

    ordered = sort_sources(
        sources
    )

    lines = []

    lines.append(
        "⚠️ Model AI tidak dapat menghasilkan "
        "huraian buat sementara waktu."
    )

    lines.append(
        "Berikut ialah petikan sumber yang ditemui:"
    )

    lines.append("")

    for index, source in enumerate(
        ordered[:5],
        start=1
    ):

        source_name = (
            source.get("source")
            or source.get("book")
            or source.get("book_title")
            or "Kitab Turath"
        )

        author = (
            source.get(
                "author"
            )
            or ""
        )

        page = (
            source.get(
                "page"
            )
            or ""
        )

        content = clean_text(
            source.get(
                "content",
                ""
            )
        )

        header = (
            f"{index}. {source_name}"
        )

        if author:
            header += (
                f" — {author}"
            )

        if page:
            header += (
                f", hlm. {page}"
            )

        lines.append(
            header
        )

        lines.append(
            f"“{content[:1200]}”"
        )

        lines.append("")

    lines.append(
        "Sila cuba semula sebentar lagi."
    )

    return "\n".join(
        lines
    )


# ============================================================
# GENERATE ANSWER
# ============================================================

def generate_answer(
    question,
    sources,
    comparison=False
):

    if not sources:

        return (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan "
            "huraian.",
            False
        )

    if not gemini_client:

        return (
            generate_source_fallback(
                question,
                sources,
                comparison
            ),
            False
        )

    context = build_context(
        sources
    )

    if not context:

        return (
            generate_source_fallback(
                question,
                sources,
                comparison
            ),
            False
        )

    requested_madhhab = (
        detect_requested_madhhab(
            question
        )
    )

    if comparison:

        instruction = """
Soalan meminta perbandingan mazhab.

Bandingkan hanya pandangan yang benar-benar
disokong oleh sumber.

Jangan mereka-reka pandangan mazhab.

Jika sumber bagi mazhab tertentu tiada,
nyatakan sumber tersebut tidak mencukupi.
"""

    else:

        if requested_madhhab == "syafii":

            instruction = """
Pengguna meminta pandangan mazhab Syafie.

WAJIB utamakan sumber berlabel Syafie
dan jangan mencampurkan pandangan mazhab
lain kecuali perlu untuk menjelaskan khilaf.
"""

        elif requested_madhhab == "hanafi":

            instruction = """
Pengguna meminta pandangan mazhab Hanafi.

Utamakan sumber Hanafi.
"""

        elif requested_madhhab == "maliki":

            instruction = """
Pengguna meminta pandangan mazhab Maliki.

Utamakan sumber Maliki.
"""

        elif requested_madhhab == "hanbali":

            instruction = """
Pengguna meminta pandangan mazhab Hanbali.

Utamakan sumber Hanbali.
"""

        else:

            instruction = """
Jawab berdasarkan sumber fiqh yang diberikan.
"""

    prompt = f"""
Anda ialah pembantu ilmu fiqh.

Jawab soalan pengguna dalam Bahasa Melayu.

SOALAN:
{question}

{instruction}

SUMBER YANG DIBENARKAN:
{context}

PERATURAN SANGAT PENTING:

1. Gunakan HANYA sumber yang diberikan.
2. Jangan reka nama kitab.
3. Jangan reka pengarang.
4. Jangan reka halaman.
5. Jangan gunakan pengetahuan luar untuk
   mengisi kekosongan sumber.
6. Jika sumber tidak mencukupi, nyatakan
   bahawa sumber tidak mencukupi.
7. Jangan gunakan sumber yang tidak berkaitan.
8. Jangan campurkan hukum solat jika soalan
   sebenarnya tentang mandi wajib.
9. Jika soalan tentang mandi wajib, fokus
   kepada الغسل / غسل الجنابة / فرائض الغسل /
   صفة الغسل.
10. Jika soalan tentang wuduk, fokus kepada
    الوضوء.
11. Jika soalan tentang puasa, fokus kepada
    الصيام.
12. Jika soalan tentang qunut, fokus kepada
    القنوت.
13. Jika pengguna menyebut mazhab Syafie,
    jawab berdasarkan sumber Syafie.
14. Jika terdapat khilaf, jelaskan hanya jika
    disokong oleh sumber.
15. Jangan tulis bahagian "Rujukan".
    Sistem akan menambahnya.
16. Jangan gunakan Markdown yang kompleks.
17. Jangan sebut bahawa anda ialah AI.

FORMAT:

Jawapan:
...

Penjelasan:
...

Kesimpulan:
...
"""

    result = gemini_generate_resilient(
        primary_model=LLM_MODEL,
        fallback_model=FALLBACK_LLM_MODEL,
        contents=prompt,
        label="FINAL ANSWER"
    )

    if not result:

        return (
            generate_source_fallback(
                question,
                sources,
                comparison
            ),
            False
        )

    return (
        strip_markdown(
            result
        ),
        True
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

    print("=" * 65)
    print(
        "❓ QUESTION:",
        question
    )
    print("=" * 65)

    comparison = is_madhhab_comparison(
        question
    )

    print(
        "⚖️ COMPARISON:",
        comparison
    )

    # --------------------------------------------------------
    # TURATH FIRST
    # --------------------------------------------------------

    turath_sources = search_turath(
        question,
        comparison=comparison
    )

    print(
        "📚 TURATH FOUND:",
        len(turath_sources)
    )

    # --------------------------------------------------------
    # LOCAL SEARCH
    # --------------------------------------------------------
    # Local hanya digunakan sebagai fallback.
    # Untuk kurangkan API call, kita masih ambil local
    # selepas Turath.

    local_sources = []

    if not turath_sources:

        print(
            "⚠️ TURATH KOSONG → SEARCH LOCAL"
        )

        local_sources = search_local(
            question,
            limit=5
        )

    # --------------------------------------------------------
    # SELECT
    # --------------------------------------------------------

    selected_sources = select_sources(
        question,
        turath_sources,
        local_sources,
        comparison=comparison
    )

    selected_sources = deduplicate_sources(
        selected_sources
    )

    selected_sources = sort_sources(
        selected_sources
    )

    print(
        "=========================================="
    )

    print(
        "📚 SELECTED SOURCES:",
        len(selected_sources)
    )

    print(
        "📚 TURATH SELECTED:",
        sum(
            1
            for x in selected_sources
            if x.get("origin") == "turath"
        )
    )

    print(
        "📚 LOCAL SELECTED:",
        sum(
            1
            for x in selected_sources
            if x.get("origin") == "local"
        )
    )

    for source in selected_sources[:10]:

        print(
            "   •",
            source.get(
                "source"
            )
            or source.get(
                "book"
            )
            or "TIADA",
            "| score=",
            source.get(
                "_relevance_score"
            )
        )

    print(
        "=========================================="
    )

    # --------------------------------------------------------
    # ANSWER
    # --------------------------------------------------------

    answer, ai_success = generate_answer(
        question,
        selected_sources,
        comparison=comparison
    )

    # --------------------------------------------------------
    # REFERENCES
    # --------------------------------------------------------

    references = format_references(
        selected_sources
    )

    final_answer = (
        answer
        + "\n\n"
        + references
    )

    final_answer = strip_markdown(
        final_answer
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if ai_success:

        set_cached_answer(
            question,
            final_answer
        )

    return final_answer


# ============================================================
# TELEGRAM START
# ============================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        update.effective_message
    )

    if not message:
        return

    await message.reply_text(
        "Assalamualaikum.\n\n"
        "Saya TanyaFiqihBot.\n\n"
        "Sila hantar soalan fiqh anda."
    )


# ============================================================
# TELEGRAM HELP
# ============================================================

async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        update.effective_message
    )

    if not message:
        return

    await message.reply_text(
        "Contoh soalan:\n\n"
        "• Apa hukum mandi wajib menurut mazhab Syafie?\n"
        "• Apa hukum qunut Subuh menurut mazhab Syafie?\n"
        "• Adakah sentuh perempuan membatalkan wuduk?\n"
        "• Apa hukum jamak solat ketika musafir?\n"
        "• Bandingkan hukum qunut antara mazhab Syafie dan Hanafi."
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

async def telegram_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        update.effective_message
    )

    if not message:
        return

    question = (
        message.text
        or ""
    ).strip()

    if not question:
        return

    print("=" * 65)
    print(
        "📨 TELEGRAM QUESTION"
    )
    print(
        question
    )
    print("=" * 65)

    try:

        await message.chat.send_action(
            action="typing"
        )

    except Exception:
        pass

    try:

        loop = (
            asyncio.get_running_loop()
        )

        answer = (
            await loop.run_in_executor(
                None,
                answer_question,
                question
            )
        )

        chunks = telegram_chunks(
            answer,
            TELEGRAM_MAX_CHARS
        )

        for chunk in chunks:

            if not chunk:
                continue

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
# TELEGRAM WORKER
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
            Application
            .builder()
            .token(
                TELEGRAM_TOKEN
            )
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

        loop = (
            asyncio.new_event_loop()
        )

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

        if callable(
            sync_function
        ):

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
# BACKGROUND
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

    if TELEGRAM_TOKEN:

        telegram_thread = (
            threading.Thread(
                target=telegram_worker,
                daemon=True,
                name="telegram-worker"
            )
        )

        telegram_thread.start()

    sync_thread = (
        threading.Thread(
            target=run_optional_book_sync,
            daemon=True,
            name="book-sync-worker"
        )
    )

    sync_thread.start()


# ============================================================
# HOME
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
            "turath_service":
                TURATH_SERVICE_URL,
            "llm_model":
                LLM_MODEL,
            "arabic_query_model":
                ARABIC_QUERY_MODEL,
            "embedding_model":
                EMBEDDING_MODEL,
        }
    )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    turath_status = False

    try:

        response = requests.get(
            TURATH_SERVICE_URL
            + "/health",
            timeout=5
        )

        turath_status = (
            response.status_code
            == 200
        )

    except Exception:

        turath_status = False

    return jsonify(
        {
            "success": True,
            "service": "TanyaFiqihBot",
            "supabase":
                supabase is not None,
            "gemini":
                gemini_client is not None,
            "telegram":
                telegram_application is not None,
            "turath":
                turath_status,
            "llm_model":
                LLM_MODEL,
            "arabic_query_model":
                ARABIC_QUERY_MODEL,
            "embedding_model":
                EMBEDDING_MODEL,
        }
    )


# ============================================================
# /ASK
# ============================================================

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
                    "error":
                        "Soalan kosong"
                }
            ), 400

        answer = answer_question(
            question
        )

        return jsonify(
            {
                "success": True,
                "question":
                    question,
                "answer":
                    answer,
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
                "error":
                    "Internal server error"
            }
        ), 500


# ============================================================
# /SEARCH
# ============================================================

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
                    "error":
                        "Soalan kosong"
                }
            ), 400

        comparison = (
            is_madhhab_comparison(
                question
            )
        )

        turath_sources = (
            search_turath(
                question,
                comparison=comparison
            )
        )

        local_sources = []

        if not turath_sources:

            local_sources = search_local(
                question,
                limit=5
            )

        sources = select_sources(
            question,
            turath_sources,
            local_sources,
            comparison=comparison
        )

        sources = deduplicate_sources(
            sources
        )

        sources = sort_sources(
            sources
        )

        return jsonify(
            {
                "success": True,
                "question":
                    question,
                "comparison":
                    comparison,
                "count":
                    len(sources),
                "sources":
                    sources,
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
                "error":
                    "Internal server error"
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

    print("=" * 65)
    print(
        "🚀 TANYAFIQIHBOT READY"
    )
    print(
        f"🌐 Flask: http://{host}:{port}"
    )
    print(
        f"📚 Turath: {TURATH_SERVICE_URL}"
    )
    print(
        f"🤖 LLM: {LLM_MODEL}"
    )
    print(
        f"🇸🇦 Arabic Query: {ARABIC_QUERY_MODEL}"
    )
    print(
        f"🧠 Embedding: {EMBEDDING_MODEL}"
    )
    print("=" * 65)

    app.run(
        host=host,
        port=port,
        threaded=True
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()

else:

    start_background_services()
