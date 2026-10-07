import os
import re
import asyncio
import hashlib
import threading
import traceback
import time

import requests

from flask import Flask, request, jsonify

from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY",
    ""
).strip()

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
).strip()

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

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).strip()

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

TURATH_TIMEOUT = int(
    os.getenv(
        "TURATH_TIMEOUT",
        "45"
    )
)


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# GEMINI CLIENT
# =========================================================

gemini_client = None

if GOOGLE_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print(
            "✅ GEMINI CLIENT READY"
        )

    except Exception as e:
        print(
            "❌ GEMINI CLIENT ERROR:",
            e
        )

else:
    print(
        "⚠️ GOOGLE_API_KEY belum ditetapkan"
    )


# =========================================================
# CACHE
# =========================================================

ANSWER_CACHE = {}

CACHE_MAX = 500

CACHE_TTL = 60 * 60 * 6


# =========================================================
# LOCK
# =========================================================

cache_lock = threading.Lock()


# =========================================================
# BASIC HELPERS
# =========================================================

def normalize_question(
    question
):
    if question is None:
        return ""

    question = str(
        question
    )

    question = re.sub(
        r"\s+",
        " ",
        question
    )

    return question.strip()


def cache_key(
    question
):
    normalized = (
        normalize_question(
            question
        )
        .lower()
    )

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()


def truncate_text(
    text,
    max_chars
):
    text = str(
        text or ""
    ).strip()

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "\n...[dipendekkan]"
    )


def safe_get(
    obj,
    *keys
):
    if not isinstance(
        obj,
        dict
    ):
        return ""

    for key in keys:
        value = obj.get(
            key
        )

        if value is not None:
            value = str(
                value
            ).strip()

            if value:
                return value

    return ""


# =========================================================
# CACHE FUNCTIONS
# =========================================================

def get_cached_answer(
    question
):
    key = cache_key(
        question
    )

    with cache_lock:
        item = ANSWER_CACHE.get(
            key
        )

        if not item:
            return None

        timestamp = item.get(
            "timestamp",
            0
        )

        if (
            time.time()
            - timestamp
            > CACHE_TTL
        ):
            ANSWER_CACHE.pop(
                key,
                None
            )

            return None

        return item.get(
            "answer"
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
        if (
            len(ANSWER_CACHE)
            >= CACHE_MAX
        ):
            oldest_key = min(
                ANSWER_CACHE,
                key=lambda k:
                    ANSWER_CACHE[k].get(
                        "timestamp",
                        0
                    )
            )

            ANSWER_CACHE.pop(
                oldest_key,
                None
            )

        ANSWER_CACHE[key] = {
            "answer": answer,
            "timestamp": time.time()
        }


def clear_answer_cache():
    with cache_lock:
        ANSWER_CACHE.clear()


# =========================================================
# MADHHAB COMPARISON
# =========================================================

def is_madhhab_comparison(
    question
):
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
        "comparison",
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


# =========================================================
# GEMINI ERROR CHECK
# =========================================================

def is_retryable_error(
    error
):
    text = str(
        error or ""
    ).lower()

    retry_words = [
        "503",
        "429",
        "500",
        "502",
        "504",
        "unavailable",
        "resource exhausted",
        "temporarily",
        "deadline",
        "timeout",
        "overloaded",
        "internal server error",
    ]

    return any(
        word in text
        for word in retry_words
    )


# =========================================================
# GEMINI GENERATE
# =========================================================

def gemini_generate(
    model,
    prompt
):
    if not gemini_client:
        raise RuntimeError(
            "Gemini client belum tersedia."
        )

    last_error = None

    models = []

    if model:
        models.append(
            model
        )

    if (
        model == LLM_MODEL
        and FALLBACK_LLM_MODEL
    ):
        models.append(
            FALLBACK_LLM_MODEL
        )

    if (
        model == ARABIC_QUERY_MODEL
        and FALLBACK_ARABIC_MODEL
    ):
        models.append(
            FALLBACK_ARABIC_MODEL
        )

    if not models:
        models = [
            LLM_MODEL
        ]

    for current_model in models:

        for attempt in range(
            GEMINI_RETRIES + 1
        ):
            try:
                response = (
                    gemini_client.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.1
                        )
                    )
                )

                text = getattr(
                    response,
                    "text",
                    None
                )

                if text:
                    return text.strip()

                raise RuntimeError(
                    "Gemini tidak menghasilkan teks."
                )

            except Exception as e:
                last_error = e

                print(
                    f"❌ GEMINI ERROR "
                    f"model={current_model} "
                    f"attempt={attempt + 1}:",
                    e
                )

                if not is_retryable_error(
                    e
                ):
                    break

                if attempt < GEMINI_RETRIES:
                    wait_time = (
                        GEMINI_INITIAL_WAIT
                        * (
                            2 ** attempt
                        )
                    )

                    print(
                        f"⏳ GEMINI RETRY "
                        f"{wait_time}s..."
                    )

                    time.sleep(
                        wait_time
                    )

    raise last_error or RuntimeError(
        "Gemini gagal."
    )


# =========================================================
# ARABIC QUERY TRANSLATION
# =========================================================

def translate_to_arabic(
    question
):
    question = normalize_question(
        question
    )

    if not question:
        return ""

    prompt = f"""
Anda ialah penterjemah pertanyaan fiqh untuk enjin carian kitab Turath Arab.

Tugas:
Tukarkan soalan pengguna dalam Bahasa Melayu kepada kata kunci / frasa
Bahasa Arab yang paling sesuai untuk mencari perbahasan fiqh dalam kitab
Turath.

JANGAN jawab soalan.
JANGAN beri hukum.
JANGAN beri penjelasan.
Hanya keluarkan frasa Arab untuk carian.

Contoh:
"Apa hukum mandi wajib?"
→ الغسل

"Apa hukum mandi junub?"
→ غسل الجنابة

"Apa hukum mandi selepas haid?"
→ غسل الحيض

"Apa hukum wuduk?"
→ الوضوء

"Apa hukum qunut Subuh?"
→ القنوت في صلاة الصبح

"Apa hukum zakat fitrah?"
→ زكاة الفطر

Soalan:
{question}
"""

    try:
        result = gemini_generate(
            ARABIC_QUERY_MODEL,
            prompt
        )

        result = result.strip()

        result = re.sub(
            r"```.*?```",
            "",
            result,
            flags=re.S
        )

        result = result.strip(
            "` \n"
        )

        return result

    except Exception as e:
        print(
            "❌ ARABIC TRANSLATION ERROR:",
            e
        )

        return question


# =========================================================
# TURATH QUERY MAP
# =========================================================

TURATH_QUERY_MAP = {
    "mandi wajib": "الغسل",
    "mandi junub": "غسل الجنابة",
    "mandi selepas haid": "غسل الحيض",
    "mandi haid": "غسل الحيض",
    "mandi nifas": "غسل النفاس",

    "puasa ramadan": "صيام رمضان",
    "puasa": "الصيام",

    "zakat fitrah": "زكاة الفطر",
    "zakat": "الزكاة",

    "solat subuh": "صلاة الصبح",
    "solat jumaat": "صلاة الجمعة",
    "solat": "الصلاة",
    "sembahyang": "الصلاة",

    "wuduk": "الوضوء",
    "wudhu": "الوضوء",

    "taharah": "الطهارة",
    "bersuci": "الطهارة",

    "tayamum": "التيمم",
    "junub": "الجنابة",

    "haid": "الحيض",
    "nifas": "النفاس",
    "istihadah": "الاستحاضة",

    "qunut subuh": "القنوت في صلاة الصبح",
    "qunut": "القنوت",

    "azan": "الأذان",
    "iqamah": "الإقامة",

    "nikah": "النكاح",
    "perkahwinan": "النكاح",

    "talak": "الطلاق",
    "cerai": "الطلاق",

    "faraid": "الفرائض",
    "pusaka": "المواريث",

    "haji": "الحج",
    "umrah": "العمرة",

    "korban": "الأضحية",
    "akikah": "العقيقة",
    "sembelihan": "الذبائح",

    "najis": "النجاسة",
    "aurat": "العورة",

    "mahar": "المهر",
    "mas kahwin": "المهر",

    "jual beli": "البيع",
    "riba": "الربا",

    "hutang": "الدين",
    "pinjaman": "القرض",

    "wakaf": "الوقف",
    "nazar": "النذر",

    "sumpah": "اليمين",
    "kaffarah": "الكفارة",
    "kafarah": "الكفارة",
}


def map_turath_query(
    question
):
    q = normalize_question(
        question
    ).lower()

    keys = sorted(
        TURATH_QUERY_MAP.keys(),
        key=len,
        reverse=True
    )

    for key in keys:
        if key in q:
            return TURATH_QUERY_MAP[
                key
            ]

    return ""


# =========================================================
# TURATH SEARCH
# =========================================================

def search_turath(
    question,
    comparison=False
):
    question = normalize_question(
        question
    )

    if not question:
        return {
            "success": False,
            "count": 0,
            "passages": [],
            "error": "Soalan kosong."
        }

    mapped_query = map_turath_query(
        question
    )

    if mapped_query:
        arabic_query = mapped_query

    else:
        arabic_query = translate_to_arabic(
            question
        )

    print(
        f"🔎 TURATH ARABIC QUERY: {arabic_query}"
    )

    payload = {
        "query": arabic_query,
        "question": question,
        "comparison": comparison,
        "limit": 20,
    }

    url = (
        TURATH_SERVICE_URL.rstrip("/")
        + "/search"
    )

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=TURATH_TIMEOUT
        )

        print(
            f"📡 TURATH STATUS: {response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(
            data,
            dict
        ):
            return {
                "success": False,
                "count": 0,
                "passages": [],
                "error": "Respons Turath tidak sah."
            }

        passages = data.get(
            "passages",
            []
        )

        if not isinstance(
            passages,
            list
        ):
            passages = []

        normalized = []

        for item in passages:
            if not isinstance(
                item,
                dict
            ):
                continue

            text = safe_get(
                item,
                "text",
                "content",
                "snippet",
                "passage",
                "body"
            )

            book = safe_get(
                item,
                "book",
                "book_title",
                "book_name",
                "source"
            )

            author = safe_get(
                item,
                "author",
                "book_author",
                "author_name"
            )

            page = safe_get(
                item,
                "page",
                "page_number",
                "page_no"
            )

            book_id = safe_get(
                item,
                "book_id",
                "bookId",
                "book_hash"
            )

            category = safe_get(
                item,
                "category",
                "category_name",
                "mazhab"
            )

            url_value = safe_get(
                item,
                "url",
                "link",
                "href"
            )

            if not text:
                continue

            normalized.append({
                "text": truncate_text(
                    text,
                    SOURCE_MAX_CHARS
                ),
                "book": book,
                "book_title": book,
                "author": author,
                "page": page,
                "page_number": page,
                "book_id": book_id,
                "category": category,
                "url": url_value,
                "origin": "turath"
            })

        return {
            "success": True,
            "count": len(
                normalized
            ),
            "passages": normalized,
            "query": arabic_query,
            "comparison": comparison
        }

    except Exception as e:
        print(
            "❌ TURATH SEARCH ERROR:",
            e
        )

        traceback.print_exc()

        return {
            "success": False,
            "count": 0,
            "passages": [],
            "error": str(e)
        }


# =========================================================
# TURATH SOURCE RELEVANCE
# =========================================================

def source_relevance(
    question,
    source
):
    text = (
        str(
            source.get(
                "text",
                ""
            )
        )
        .lower()
    )

    q = normalize_question(
        question
    ).lower()

    score = 0

    keyword_groups = [
        [
            "mandi wajib",
            "mandi junub",
            "غسل",
            "الجنابة"
        ],
        [
            "wuduk",
            "wudhu",
            "الوضوء"
        ],
        [
            "qunut",
            "القنوت"
        ],
        [
            "puasa",
            "الصيام"
        ],
        [
            "zakat",
            "الزكاة"
        ],
        [
            "solat",
            "sembahyang",
            "الصلاة"
        ],
        [
            "haid",
            "الحيض"
        ],
        [
            "nifas",
            "النفاس"
        ],
        [
            "tayamum",
            "التيمم"
        ],
        [
            "najis",
            "النجاسة"
        ],
    ]

    for group in keyword_groups:
        question_hit = any(
            x in q
            for x in group
        )

        source_hit = any(
            x in text
            for x in group
        )

        if (
            question_hit
            and source_hit
        ):
            score += 10

    # Arabic query words
    arabic_words = re.findall(
        r"[\u0600-\u06FF]{3,}",
        q
    )

    for word in arabic_words:
        if word in text:
            score += 2

    return score


def rank_sources(
    question,
    passages
):
    scored = []

    for index, source in enumerate(
        passages
    ):
        score = source_relevance(
            question,
            source
        )

        scored.append(
            (
                score,
                -index,
                source
            )
        )

    scored.sort(
        key=lambda x: (
            x[0],
            x[1]
        ),
        reverse=True
    )

    return [
        item[2]
        for item in scored
    ]


# =========================================================
# DEDUPLICATE SOURCES
# =========================================================

def deduplicate_sources(
    passages
):
    seen = set()
    output = []

    for source in passages:
        text = str(
            source.get(
                "text",
                ""
            )
        ).strip()

        book_id = str(
            source.get(
                "book_id",
                ""
            )
        ).strip()

        page = str(
            source.get(
                "page",
                ""
            )
        ).strip()

        key = (
            book_id
            + "|"
            + page
            + "|"
            + text[:300]
        ).lower()

        if key in seen:
            continue

        seen.add(key)

        output.append(
            source
        )

    return output


# =========================================================
# BUILD TURATH CONTEXT
# =========================================================

def build_turath_context(
    question,
    passages
):
    if not passages:
        return ""

    ranked = rank_sources(
        question,
        passages
    )

    ranked = deduplicate_sources(
        ranked
    )

    blocks = []

    total_chars = 0

    for index, source in enumerate(
        ranked,
        start=1
    ):
        text = source.get(
            "text",
            ""
        )

        book = source.get(
            "book",
            ""
        )

        author = source.get(
            "author",
            ""
        )

        page = source.get(
            "page",
            ""
        )

        category = source.get(
            "category",
            ""
        )

        book_id = source.get(
            "book_id",
            ""
        )

        reference_lines = []

        if book:
            reference_lines.append(
                f"Kitab: {book}"
            )

        if author:
            reference_lines.append(
                f"Pengarang: {author}"
            )

        if page:
            reference_lines.append(
                f"Halaman: {page}"
            )

        if category:
            reference_lines.append(
                f"Kategori: {category}"
            )

        if book_id:
            reference_lines.append(
                f"Book ID: {book_id}"
            )

        header = (
            f"SUMBER TURATH #{index}\n"
            + "\n".join(
                reference_lines
            )
        )

        block = (
            header
            + "\n"
            + "Teks:\n"
            + text
        )

        if (
            total_chars
            + len(block)
            > CONTEXT_MAX_CHARS
        ):
            break

        blocks.append(
            block
        )

        total_chars += len(
            block
        )

    return "\n\n".join(
        blocks
    )


# =========================================================
# SOURCE REFERENCE FORMAT
# =========================================================

def format_references(
    passages
):
    if not passages:
        return (
            "📚 **Rujukan:**\n"
            "• Tiada rujukan Turath ditemui."
        )

    ranked = deduplicate_sources(
        passages
    )

    lines = [
        "📚 **Rujukan Turath:**"
    ]

    seen = set()

    for source in ranked:
        book = source.get(
            "book",
            ""
        ).strip()

        author = source.get(
            "author",
            ""
        ).strip()

        page = source.get(
            "page",
            ""
        ).strip()

        url = source.get(
            "url",
            ""
        ).strip()

        book_id = source.get(
            "book_id",
            ""
        ).strip()

        key = (
            book,
            author,
            page
        )

        if key in seen:
            continue

        seen.add(key)

        parts = []

        if book:
            parts.append(
                book
            )

        if author:
            parts.append(
                f"— {author}"
            )

        if page:
            parts.append(
                f", hlm. {page}"
            )

        if not book and book_id:
            parts.append(
                f"Book ID: {book_id}"
            )

        line = "• " + " ".join(
            parts
        )

        if url:
            line += (
                f"\n  🔗 {url}"
            )

        lines.append(
            line
        )

    if len(lines) == 1:
        lines.append(
            "• Maklumat kitab tidak lengkap."
        )

    return "\n".join(
        lines
    )


# =========================================================
# CHECK SUFFICIENT TURATH
# =========================================================

def has_sufficient_turath(
    passages
):
    if not passages:
        return False

    valid = 0

    for source in passages:
        text = str(
            source.get(
                "text",
                ""
            )
        ).strip()

        if len(text) >= 50:
            valid += 1

    return valid >= 1


# =========================================================
# GENERATE FIqh ANSWER
# =========================================================

def generate_answer(
    question,
    passages,
    comparison=False
):
    if not has_sufficient_turath(
        passages
    ):
        return None

    context = build_turath_context(
        question,
        passages
    )

    if not context:
        return None

    if comparison:
        mode_instruction = """
SOALAN INI MEMINTA PERBANDINGAN MAZHAB.

Bandingkan pandangan mazhab berdasarkan sumber Turath yang diberikan.
Jangan mencipta pandangan mazhab yang tidak terdapat dalam sumber.
Jika sumber untuk sesuatu mazhab tidak mencukupi, nyatakan dengan jelas.
"""
    else:
        mode_instruction = """
SOALAN INI BUKAN SOALAN PERBANDINGAN.

Fokuskan jawapan kepada MAZHAB SYAFIE.
Jangan masukkan pandangan mazhab lain kecuali benar-benar diperlukan
untuk menjelaskan sesuatu dan disokong oleh sumber Turath.
"""

    prompt = f"""
Anda ialah pembantu ilmu fiqh berbahasa Melayu untuk TanyaFiqihBot.

SUMBER UTAMA ANDA HANYALAH PETIKAN KITAB TURATH YANG DIBERIKAN DI BAWAH.

{mode_instruction}

PERATURAN PALING PENTING:

1. Jangan menggunakan pengetahuan luar daripada sumber Turath yang diberikan
   untuk menetapkan hukum.
2. Jangan mereka-reka dalil, kitab, pengarang, halaman atau nombor muka surat.
3. Jangan menyatakan sesuatu sebagai pandangan kitab jika ia tidak terdapat
   dalam petikan sumber.
4. Jika sumber tidak mencukupi, katakan bahawa sumber Turath yang ditemui
   tidak mencukupi untuk memastikan jawapan.
5. Jawab dalam Bahasa Melayu yang jelas.
6. Berikan jawapan hukum secara terus pada awal jawapan.
7. Selepas itu berikan huraian ringkas berdasarkan sumber.
8. Jika ada perbezaan pendapat dalam sumber, nyatakan perbezaan tersebut.
9. Jangan masukkan "Sumber tempatan", "sumber umum", "internet" atau sumber
   selain Turath.
10. Rujukan kitab mesti berdasarkan metadata sumber yang diberikan.
11. Jangan reka nama pengarang atau halaman jika metadata tidak tersedia.
12. Jika teks Arab diberikan, anda boleh menerangkan maksudnya dalam Bahasa Melayu.
13. Jangan menganggap semua petikan semestinya menjawab soalan. Gunakan hanya
    petikan yang benar-benar berkaitan.

SOALAN PENGGUNA:
{question}

SUMBER TURATH:
{context}

FORMAT JAWAPAN:

**Jawapan:**
[Hukum/jawapan secara terus]

**Huraian:**
[Huraian berdasarkan sumber Turath]

Jika terdapat perbezaan pandangan:
**Perbezaan Pandangan:**
[Terangkan berdasarkan sumber]

Jangan tulis bahagian "Rujukan" kerana sistem akan menambah rujukan
berdasarkan metadata Turath selepas jawapan.
"""

    try:
        answer = gemini_generate(
            LLM_MODEL,
            prompt
        )

        if not answer:
            return None

        return answer.strip()

    except Exception as e:
        print(
            "❌ ANSWER GENERATION ERROR:",
            e
        )

        traceback.print_exc()

        return None


# =========================================================
# FINAL ANSWER
# =========================================================

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

    comparison = is_madhhab_comparison(
        question
    )

    print(
        f"⚖️ COMPARISON MODE: {comparison}"
    )

    print(
        f"❓ QUESTION: {question}"
    )

    turath = search_turath(
        question,
        comparison
    )

    passages = turath.get(
        "passages",
        []
    )

    if not has_sufficient_turath(
        passages
    ):
        print(
            "⚠️ TIADA SUMBER TURATH YANG MENCUKUPI"
        )

        return (
            "⚠️ **Tiada kandungan sumber Turath "
            "yang mencukupi untuk menghasilkan huraian.**\n\n"
            "Sila cuba soalan yang lebih khusus supaya "
            "carian kitab Turath dapat menemui perbahasan "
            "yang berkaitan.\n\n"
            "📚 **Rujukan:**\n"
            "• Tiada rujukan Turath ditemui."
        )

    answer = generate_answer(
        question,
        passages,
        comparison
    )

    if not answer:
        return (
            "⚠️ **Maaf, jawapan tidak dapat "
            "dihasilkan daripada sumber Turath "
            "yang ditemui.**\n\n"
            "📚 **Rujukan:**\n"
            "• Tiada huraian dapat dihasilkan."
        )

    references = format_references(
        passages
    )

    final_answer = (
        answer
        + "\n\n"
        + references
    )

    set_cached_answer(
        question,
        final_answer
    )

    return final_answer


# =========================================================
# TELEGRAM TEXT SPLITTER
# =========================================================

def split_telegram_text(
    text,
    max_length=TELEGRAM_MAX_CHARS
):
    text = str(
        text or ""
    )

    if len(text) <= max_length:
        return [text]

    chunks = []

    while len(text) > max_length:

        cut = text.rfind(
            "\n",
            0,
            max_length
        )

        if cut < 500:
            cut = text.rfind(
                " ",
                0,
                max_length
            )

        if cut < 1:
            cut = max_length

        chunks.append(
            text[:cut].strip()
        )

        text = text[
            cut:
        ].strip()

    if text:
        chunks.append(
            text
        )

    return chunks


# =========================================================
# TELEGRAM /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if not message:
        return

    text = """
السلام عليكم ورحمة الله وبركاته

🤖 **TanyaFiqihBot**

Bot soal jawab fiqh berasaskan sumber kitab Turath.

🕌 Fokus utama:
• Mazhab Syafie
• Soalan fiqh harian
• Rujukan kitab Turath
• Perbandingan mazhab jika diminta

Contoh soalan:

👉 Apa hukum mandi wajib?

👉 Apa rukun wuduk?

👉 Apa hukum qunut Subuh menurut mazhab Syafie?

👉 Apa hukum zakat fitrah menurut mazhab Syafie?

👉 Bandingkan hukum qunut Subuh antara mazhab Syafie dan Hanafi.

Sila taip soalan anda.
"""

    await message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# TELEGRAM MESSAGE HANDLER
# =========================================================

async def handle_message(
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

    print(
        "\n======================================"
    )

    print(
        "📩 TELEGRAM QUESTION:",
        question
    )

    print(
        "======================================"
    )

    try:
        await message.chat.send_action(
            action="typing"
        )
    except Exception:
        pass

    try:
        # Jalankan kerja sync dalam thread supaya
        # event loop Telegram tidak tersekat.
        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        chunks = split_telegram_text(
            answer
        )

        for chunk in chunks:
            await message.reply_text(
                chunk,
                parse_mode="Markdown"
            )

    except Exception as e:
        print(
            "❌ TELEGRAM MESSAGE ERROR:",
            e
        )

        traceback.print_exc()

        try:
            await message.reply_text(
                "⚠️ Maaf, berlaku masalah ketika memproses soalan."
            )
        except Exception:
            pass


# =========================================================
# BUILD TELEGRAM APP
# =========================================================

def build_telegram_app():
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_TOKEN belum ditetapkan."
        )

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
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    return telegram_app


# =========================================================
# TELEGRAM RUNNER
# =========================================================

def run_telegram():
    try:
        print(
            "🤖 MEMULAKAN TELEGRAM BOT..."
        )

        telegram_app = build_telegram_app()

        print(
            "🧵 TELEGRAM THREAD STARTED"
        )

        print(
            "✅ TELEGRAM BOT READY"
        )

        # =================================================
        # PENTING:
        # stop_signals=None
        #
        # Telegram dijalankan dalam background thread.
        # python-telegram-bot secara default cuba memasang
        # OS signal handler dan akan menyebabkan:
        #
        # RuntimeError:
        # set_wakeup_fd only works in main thread
        #
        # stop_signals=None menghalang perkara tersebut.
        # =================================================

        telegram_app.run_polling(
            drop_pending_updates=True,
            stop_signals=None
        )

    except Exception as e:
        print(
            f"❌ TELEGRAM START ERROR: {e}"
        )

        traceback.print_exc()


# =========================================================
# TURATH HEALTH CHECK
# =========================================================

def check_turath():
    try:
        url = (
            TURATH_SERVICE_URL.rstrip("/")
            + "/health"
        )

        response = requests.get(
            url,
            timeout=10
        )

        print(
            f"📡 TURATH STATUS: {response.status_code}"
        )

        if response.ok:
            try:
                print(
                    "📚 TURATH HEALTH:",
                    response.json()
                )
            except Exception:
                pass

            return True

        return False

    except Exception as e:
        print(
            "❌ TURATH HEALTH ERROR:",
            e
        )

        return False


# =========================================================
# FLASK ROOT
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():
    return jsonify({
        "success": True,
        "service": "TanyaFiqihBot",
        "status": "running",
        "turath_service": TURATH_SERVICE_URL,
        "llm_model": LLM_MODEL,
        "arabic_query_model": ARABIC_QUERY_MODEL,
        "source": "Turath only"
    })


# =========================================================
# FLASK HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():
    turath_ok = check_turath()

    return jsonify({
        "success": True,
        "flask": "healthy",
        "turath": (
            "healthy"
            if turath_ok
            else "unhealthy"
        ),
        "telegram": (
            "configured"
            if TELEGRAM_TOKEN
            else "not_configured"
        ),
        "gemini": (
            "configured"
            if GOOGLE_API_KEY
            else "not_configured"
        )
    })


# =========================================================
# FLASK /ASK
# =========================================================

@app.route(
    "/ask",
    methods=["POST"]
)
def ask():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        question = normalize_question(
            data.get(
                "question",
                ""
            )
        )

        if not question:
            return jsonify({
                "success": False,
                "error": "question diperlukan"
            }), 400

        answer = answer_question(
            question
        )

        return jsonify({
            "success": True,
            "question": question,
            "answer": answer
        })

    except Exception as e:
        print(
            "❌ /ask ERROR:",
            e
        )

        traceback.print_exc()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =========================================================
# FLASK /SEARCH
# =========================================================

@app.route(
    "/search",
    methods=["POST"]
)
def search_route():
    try:
        data = request.get_json(
            silent=True
        ) or {}

        question = normalize_question(
            data.get(
                "question",
                data.get(
                    "query",
                    ""
                )
            )
        )

        if not question:
            return jsonify({
                "success": False,
                "error": "question/query diperlukan"
            }), 400

        comparison = is_madhhab_comparison(
            question
        )

        result = search_turath(
            question,
            comparison
        )

        return jsonify({
            "success": True,
            "question": question,
            "comparison": comparison,
            "result": result
        })

    except Exception as e:
        print(
            "❌ /search ERROR:",
            e
        )

        traceback.print_exc()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =========================================================
# FLASK /CLEAR-CACHE
# =========================================================

@app.route(
    "/clear-cache",
    methods=["GET", "POST"]
)
def clear_cache_route():
    clear_answer_cache()

    return jsonify({
        "success": True,
        "message": "Answer cache telah dikosongkan."
    })


# =========================================================
# STARTUP
# =========================================================

def startup():
    print(
        "\n=============================================="
    )

    print(
        "🚀 TANYAFIQIHBOT STARTING"
    )

    print(
        "=============================================="
    )

    print(
        f"🤖 LLM MODEL: {LLM_MODEL}"
    )

    print(
        f"🔎 ARABIC QUERY MODEL: {ARABIC_QUERY_MODEL}"
    )

    print(
        f"📚 TURATH SERVICE: {TURATH_SERVICE_URL}"
    )

    print(
        "📖 SOURCE: TURATH ONLY"
    )

    print(
        "🕌 DEFAULT MADHHAB: SHAFII"
    )

    print(
        "=============================================="
    )

    # -----------------------------------------------
    # Check Turath
    # -----------------------------------------------

    check_turath()

    # -----------------------------------------------
    # Start Telegram
    # -----------------------------------------------

    if TELEGRAM_TOKEN:
        telegram_thread = threading.Thread(
            target=run_telegram,
            name="telegram-bot",
            daemon=True
        )

        telegram_thread.start()

    else:
        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan."
        )

    print(
        "=============================================="
    )

    print(
        "✅ APPLICATION STARTUP COMPLETE"
    )

    print(
        "=============================================="
    )


# =========================================================
# STARTUP ON IMPORT
# =========================================================

startup()


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":
    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        f"🌐 FLASK STARTING ON 0.0.0.0:{port}"
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
