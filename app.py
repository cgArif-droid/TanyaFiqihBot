import os
import re
import asyncio
import hashlib
import threading
import traceback
import time

import requests

from flask import Flask, jsonify, request
from google import genai

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
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


# =========================================================
# GEMINI CONFIG
# =========================================================

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


# =========================================================
# TURATH CONFIG
# =========================================================

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).strip().rstrip("/")


# =========================================================
# LIMITS
# =========================================================

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

    except Exception as error:

        print(
            "❌ GEMINI CLIENT ERROR:",
            error
        )

else:

    print(
        "⚠️ GOOGLE_API_KEY tidak ditetapkan"
    )


# =========================================================
# CACHE
# =========================================================

ANSWER_CACHE = {}

CACHE_MAX_SIZE = 100


def normalize_question(question):

    return re.sub(
        r"\s+",
        " ",
        str(question or "")
    ).strip()


def cache_key(question):

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


def get_cached_answer(question):

    key = cache_key(
        question
    )

    return ANSWER_CACHE.get(
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

    if (
        len(ANSWER_CACHE)
        >= CACHE_MAX_SIZE
    ):

        oldest_key = next(
            iter(
                ANSWER_CACHE
            )
        )

        ANSWER_CACHE.pop(
            oldest_key,
            None
        )

    ANSWER_CACHE[key] = answer


def clear_cache():

    ANSWER_CACHE.clear()

    print(
        "🧹 CACHE DIBERSIHKAN"
    )


# =========================================================
# TEXT HELPERS
# =========================================================

def clean_text(text):

    if text is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(text)
    ).strip()


def strip_markdown(text):

    if not text:
        return ""

    text = str(text)

    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        text
    )

    text = re.sub(
        r"__(.*?)__",
        r"\1",
        text
    )

    text = re.sub(
        r"`(.*?)`",
        r"\1",
        text
    )

    text = re.sub(
        r"^#+\s*",
        "",
        text,
        flags=re.MULTILINE
    )

    return text.strip()


def telegram_chunks(
    text,
    max_chars=TELEGRAM_MAX_CHARS
):

    text = text or ""

    if len(text) <= max_chars:
        return [text]

    chunks = []

    while len(text) > max_chars:

        split_at = text.rfind(
            "\n",
            0,
            max_chars
        )

        if split_at < 1000:

            split_at = text.rfind(
                " ",
                0,
                max_chars
            )

        if split_at < 1000:

            split_at = max_chars

        chunks.append(
            text[:split_at]
        )

        text = text[
            split_at:
        ].lstrip()

    if text:
        chunks.append(
            text
        )

    return chunks


# =========================================================
# MADHHAB COMPARISON
# =========================================================

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

        "مقارنة",
        "قارن",
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
# GEMINI ERROR
# =========================================================

def is_retryable_gemini_error(error):

    message = str(
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
        word in message
        for word in retry_words
    )


# =========================================================
# GEMINI GENERATE
# =========================================================

def gemini_generate(
    model,
    prompt,
    fallback_model=""
):

    if not gemini_client:

        raise RuntimeError(
            "Gemini client tidak tersedia"
        )

    models_to_try = []

    if model:

        models_to_try.append(
            model
        )

    if (
        fallback_model
        and fallback_model
        not in models_to_try
    ):

        models_to_try.append(
            fallback_model
        )

    if not models_to_try:

        models_to_try.append(
            LLM_MODEL
        )

    last_error = None

    for current_model in models_to_try:

        for attempt in range(
            GEMINI_RETRIES + 1
        ):

            try:

                print(
                    f"🤖 GEMINI: {current_model} | ATTEMPT {attempt + 1}"
                )

                response = (
                    gemini_client
                    .models
                    .generate_content(
                        model=current_model,
                        contents=prompt
                    )
                )

                result = clean_text(
                    getattr(
                        response,
                        "text",
                        ""
                    )
                )

                if result:

                    return (
                        result,
                        True
                    )

                raise RuntimeError(
                    "Gemini mengembalikan jawapan kosong"
                )

            except Exception as error:

                last_error = error

                print(
                    "⚠️ GEMINI ERROR:",
                    error
                )

                if not is_retryable_gemini_error(
                    error
                ):

                    break

                if (
                    attempt
                    < GEMINI_RETRIES
                ):

                    wait_time = (
                        GEMINI_INITIAL_WAIT
                        * (
                            2 ** attempt
                        )
                    )

                    print(
                        f"⏳ Tunggu {wait_time} saat..."
                    )

                    time.sleep(
                        wait_time
                    )

    print(
        "❌ GEMINI GAGAL:",
        last_error
    )

    return (
        "",
        False
    )


# =========================================================
# TRANSLATE TO ARABIC QUERY
# =========================================================

def translate_to_arabic_query(
    question
):

    question = normalize_question(
        question
    )

    if not question:
        return ""

    if not gemini_client:

        return question

    prompt = f"""
Anda ialah enjin carian kitab fiqh Arab.

Tukar soalan Bahasa Melayu berikut kepada QUERY ARAB
yang sesuai digunakan untuk mencari perbahasan fiqh
dalam kitab-kitab turath.

PENTING:

1. Kekalkan maksud hukum.
2. Kekalkan topik khusus.
3. Kekalkan konteks mazhab jika disebut.
4. Jangan jawab soalan.
5. Jangan beri penerangan.
6. Jangan beri senarai.
7. Hanya keluarkan satu query Arab.
8. Gunakan istilah fiqh Arab yang biasa digunakan
   dalam kitab turath.

Contoh:

Soalan:
Apa hukum qunut Subuh menurut mazhab Syafie?

Query:
حكم القنوت في صلاة الصبح عند الشافعية

Soalan:
Apa hukum mandi wajib?

Query:
أحكام الغسل من الجنابة

Soalan:
Apa rukun wuduk?

Query:
فرائض الوضوء

Soalan:
{question}

Query Arab:
""".strip()

    models = [
        ARABIC_QUERY_MODEL
    ]

    if (
        FALLBACK_ARABIC_MODEL
        and FALLBACK_ARABIC_MODEL
        not in models
    ):

        models.append(
            FALLBACK_ARABIC_MODEL
        )

    for model in models:

        for attempt in range(
            GEMINI_RETRIES + 1
        ):

            try:

                print(
                    f"🌐 ARABIC QUERY MODEL: {model}"
                )

                response = (
                    gemini_client
                    .models
                    .generate_content(
                        model=model,
                        contents=prompt
                    )
                )

                result = clean_text(
                    getattr(
                        response,
                        "text",
                        ""
                    )
                )

                result = re.sub(
                    r"^```(?:arabic|text)?\s*",
                    "",
                    result,
                    flags=re.IGNORECASE
                )

                result = re.sub(
                    r"\s*```$",
                    "",
                    result
                )

                result = clean_text(
                    result
                )

                if result:

                    print(
                        f"🇸🇦 ARABIC QUERY: {result}"
                    )

                    return result

            except Exception as error:

                print(
                    "⚠️ ARABIC QUERY ERROR:",
                    error
                )

                if (
                    is_retryable_gemini_error(
                        error
                    )
                    and attempt
                    < GEMINI_RETRIES
                ):

                    wait_time = (
                        GEMINI_INITIAL_WAIT
                        * (
                            2 ** attempt
                        )
                    )

                    time.sleep(
                        wait_time
                    )

                else:

                    break

    print(
        "⚠️ Gagal menghasilkan query Arab."
    )

    print(
        "➡️ Menggunakan soalan asal."
    )

    return question


# =========================================================
# TURATH HEALTH
# =========================================================

def check_turath_status():

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/health",
            timeout=10
        )

        print(
            f"📡 TURATH STATUS: {response.status_code}"
        )

        return (
            response.status_code
            == 200
        )

    except Exception as error:

        print(
            "❌ TURATH HEALTH ERROR:",
            error
        )

        return False


# =========================================================
# TURATH SEARCH
# =========================================================

def search_turath(
    question,
    comparison=False,
    arabic_query=None
):

    question = normalize_question(
        question
    )

    if not question:
        return []

    if not arabic_query:

        arabic_query = (
            translate_to_arabic_query(
                question
            )
        )

    payload = {

        "query":
            arabic_query,

        "question":
            question,

        "comparison":
            comparison,

        "limit":
            10,
    }

    print(
        "\n=============================================="
    )

    print(
        "📚 TURATH SEARCH"
    )

    print(
        f"🇲🇾 SOALAN: {question}"
    )

    print(
        f"🇸🇦 QUERY: {arabic_query}"
    )

    print(
        f"⚖️ COMPARISON: {comparison}"
    )

    print(
        "=============================================="
    )

    try:

        response = requests.post(
            f"{TURATH_SERVICE_URL}/search",
            json=payload,
            timeout=180
        )

        print(
            f"📡 TURATH HTTP: {response.status_code}"
        )

        if (
            response.status_code
            != 200
        ):

            print(
                "❌ TURATH RESPONSE:",
                response.text[:5000]
            )

            return []

        data = response.json()

        passages = (
            data.get(
                "passages"
            )
            or data.get(
                "results"
            )
            or data.get(
                "data"
            )
            or []
        )

        if not isinstance(
            passages,
            list
        ):

            passages = []

        print(
            f"📚 TURATH RAW RESULTS: {len(passages)}"
        )

        normalized = []

        for index, item in enumerate(
            passages
        ):

            if not isinstance(
                item,
                dict
            ):

                continue

            content = clean_text(
                item.get(
                    "text"
                )
                or item.get(
                    "content"
                )
                or item.get(
                    "passage"
                )
                or item.get(
                    "snippet"
                )
                or item.get(
                    "snip"
                )
                or item.get(
                    "body"
                )
            )

            if not content:

                continue

            book = clean_text(
                item.get(
                    "book"
                )
                or item.get(
                    "book_title"
                )
                or item.get(
                    "book_name"
                )
                or item.get(
                    "title"
                )
            )

            author = clean_text(
                item.get(
                    "author"
                )
                or item.get(
                    "book_author"
                )
                or item.get(
                    "author_name"
                )
                or item.get(
                    "authorName"
                )
            )

            page = clean_text(
                item.get(
                    "page"
                )
                or item.get(
                    "page_number"
                )
                or item.get(
                    "pageNumber"
                )
                or item.get(
                    "page_no"
                )
                or item.get(
                    "pageNo"
                )
                or item.get(
                    "halaman"
                )
            )

            category = clean_text(
                item.get(
                    "category"
                )
                or item.get(
                    "category_name"
                )
                or item.get(
                    "categoryName"
                )
                or item.get(
                    "mazhab"
                )
            )

            book_id = clean_text(
                item.get(
                    "book_id"
                )
                or item.get(
                    "bookId"
                )
                or item.get(
                    "bookID"
                )
                or item.get(
                    "book_hash"
                )
                or item.get(
                    "bookHash"
                )
            )

            result_id = clean_text(
                item.get(
                    "id"
                )
                or item.get(
                    "chunk_id"
                )
                or item.get(
                    "chunkId"
                )
            )

            url = clean_text(
                item.get(
                    "url"
                )
                or item.get(
                    "link"
                )
                or item.get(
                    "href"
                )
            )

            source = clean_text(
                item.get(
                    "source"
                )
                or book
            )

            normalized.append({

                "source":
                    source,

                "book":
                    book,

                "book_title":
                    book,

                "author":
                    author,

                "page":
                    page,

                "page_number":
                    page,

                "category":
                    category,

                "category_id":
                    item.get(
                        "category_id"
                    ),

                "book_id":
                    book_id,

                "id":
                    result_id,

                "url":
                    url,

                "content":
                    content[
                        :SOURCE_MAX_CHARS
                    ],

                "origin":
                    "turath",

                "query":
                    arabic_query,
            })

            print(
                f"\n📖 TURATH #{index + 1}"
            )

            print(
                f"   📚 Kitab: {book or 'TIADA'}"
            )

            print(
                f"   ✍️ Pengarang: {author or 'TIADA'}"
            )

            print(
                f"   📄 Halaman: {page or 'TIADA'}"
            )

            print(
                f"   🆔 Book ID: {book_id or 'TIADA'}"
            )

            print(
                f"   ⚖️ Kategori: {category or 'TIADA'}"
            )

            print(
                f"   🔗 URL: {url or 'TIADA'}"
            )

        print(
            "\n=============================================="
        )

        print(
            f"📚 TURATH NORMALIZED: {len(normalized)}"
        )

        print(
            "==============================================\n"
        )

        return deduplicate_sources(
            normalized
        )

    except requests.Timeout:

        print(
            "❌ TURATH TIMEOUT"
        )

        return []

    except Exception as error:

        print(
            "❌ TURATH SEARCH ERROR:",
            error
        )

        traceback.print_exc()

        return []


# =========================================================
# DEDUPLICATE
# =========================================================

def deduplicate_sources(
    sources
):

    seen = set()

    output = []

    for source in sources:

        content = clean_text(
            source.get(
                "content",
                ""
            )
        )

        key = (
            source.get(
                "book_id",
                ""
            )
            or source.get(
                "book",
                ""
            )
            or source.get(
                "source",
                ""
            )
        )

        key = (
            f"{key}|"
            f"{source.get('page', '')}|"
            f"{content[:300]}"
        ).lower()

        if key in seen:
            continue

        seen.add(
            key
        )

        output.append(
            source
        )

    return output


# =========================================================
# SORT SOURCES
# =========================================================

def sort_sources(
    sources
):

    def source_score(
        source
    ):

        score = 0

        if source.get(
            "book"
        ):
            score += 30

        if source.get(
            "author"
        ):
            score += 20

        if source.get(
            "page"
        ):
            score += 20

        if source.get(
            "book_id"
        ):
            score += 20

        if source.get(
            "category"
        ):
            score += 10

        if source.get(
            "content"
        ):
            score += 10

        return score

    return sorted(
        sources,
        key=source_score,
        reverse=True
    )


# =========================================================
# SOURCE VALIDATION
# =========================================================

def has_valid_turath_source(
    sources
):

    if not sources:
        return False

    for source in sources:

        content = clean_text(
            source.get(
                "content"
            )
        )

        if len(content) >= 50:

            return True

    return False


# =========================================================
# BUILD CONTEXT
# =========================================================

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

        book = (
            source.get(
                "book"
            )
            or source.get(
                "source"
            )
            or "Kitab Turath"
        )

        author = (
            source.get(
                "author"
            )
            or "Tidak dinyatakan"
        )

        page = (
            source.get(
                "page"
            )
            or "Tidak dinyatakan"
        )

        category = (
            source.get(
                "category"
            )
            or "Tidak dinyatakan"
        )

        content = (
            source.get(
                "content"
            )
            or ""
        )

        block = f"""
[SUMBER TURATH {index}]

Kitab:
{book}

Pengarang:
{author}

Halaman:
{page}

Mazhab/Kategori:
{category}

Petikan kitab:
{content}
""".strip()

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
# REFERENCES
# =========================================================

def format_references(
    sources
):

    if not sources:

        return (
            "📚 Rujukan Turath:\n"
            "• Tiada rujukan Turath ditemui."
        )

    lines = [
        "📚 Rujukan Turath:"
    ]

    seen = set()

    for source in sort_sources(
        sources
    ):

        book = clean_text(
            source.get(
                "book"
            )
            or source.get(
                "source"
            )
        )

        author = clean_text(
            source.get(
                "author"
            )
        )

        page = clean_text(
            source.get(
                "page"
            )
        )

        category = clean_text(
            source.get(
                "category"
            )
        )

        if not book:

            book = "Kitab Turath"

        parts = [
            book
        ]

        if author:

            parts.append(
                f" — {author}"
            )

        if page:

            parts.append(
                f", hlm. {page}"
            )

        if category:

            parts.append(
                f" [{category}]"
            )

        reference = "".join(
            parts
        )

        if reference in seen:
            continue

        seen.add(
            reference
        )

        lines.append(
            f"• {reference}"
        )

    return "\n".join(
        lines
    )


# =========================================================
# FALLBACK ANSWER
# =========================================================

def generate_source_fallback(
    sources
):

    if not sources:

        return (
            "⚠️ Sumber Turath tidak ditemui.\n\n"
            "Saya tidak dapat memberikan huraian "
            "berdasarkan rujukan Turath."
        )

    lines = [
        "Jawapan berdasarkan petikan Turath yang ditemui:"
    ]

    for index, source in enumerate(
        sort_sources(
            sources
        )[:5],
        start=1
    ):

        book = (
            source.get(
                "book"
            )
            or source.get(
                "source"
            )
            or "Kitab Turath"
        )

        content = clean_text(
            source.get(
                "content"
            )
        )

        lines.append(
            f"\n{index}. {book}"
        )

        if source.get(
            "author"
        ):

            lines.append(
                f"Pengarang: {source['author']}"
            )

        if source.get(
            "page"
        ):

            lines.append(
                f"Halaman: {source['page']}"
            )

        lines.append(
            f"Petikan: {content}"
        )

    return "\n".join(
        lines
    )


# =========================================================
# GENERATE ANSWER
# =========================================================

def generate_answer(
    question,
    sources,
    comparison=False
):

    if not has_valid_turath_source(
        sources
    ):

        return (
            "⚠️ Sumber Turath tidak mencukupi untuk menjawab soalan ini.",
            False
        )

    context = build_context(
        sources
    )

    if not context:

        return (
            "⚠️ Sumber Turath tidak mencukupi untuk menghasilkan huraian.",
            False
        )

    comparison_instruction = ""

    if comparison:

        comparison_instruction = """
SOALAN INI MELIBATKAN PERBANDINGAN MAZHAB.

Jika sumber menyediakan pandangan beberapa mazhab:

- Asingkan pandangan setiap mazhab.
- Nyatakan nama mazhab dengan jelas.
- Jangan campurkan pandangan mazhab.
- Jangan cipta pandangan mazhab.
- Jika sumber sesuatu mazhab tidak mencukupi,
  nyatakan perkara tersebut.
"""

    prompt = f"""
Anda ialah pembantu fiqh berbahasa Melayu.

TUGAS UTAMA:
Jawab soalan pengguna HANYA berdasarkan petikan kitab Turath
yang diberikan.

SUMBER:
Semua fakta hukum mesti datang daripada petikan Turath.

JANGAN:
- menggunakan pengetahuan luar;
- mencipta hukum;
- mencipta dalil;
- mencipta nama kitab;
- mencipta nama pengarang;
- mencipta nombor halaman;
- mencipta pandangan ulama;
- mencipta pandangan mazhab.

Jika sumber tidak membincangkan soalan dengan jelas,
jangan cuba meneka.

Jika sumber hanya membincangkan topik lain,
anggap sumber tersebut TIDAK RELEVAN.

Contoh:
Jika soalan mengenai mandi wajib tetapi petikan mengenai
rukun solat, JANGAN gunakan petikan rukun solat sebagai
jawapan kepada mandi wajib.

{comparison_instruction}

Gunakan Bahasa Melayu yang mudah difahami.

Struktur:

Jawapan:
...

Penjelasan:
...

Kesimpulan:
...

Jangan masukkan bahagian "Rujukan".
Sistem akan menambah rujukan secara automatik.

SOALAN:
{question}

PETIKAN TURATH:
{context}
""".strip()

    answer, success = gemini_generate(
        LLM_MODEL,
        prompt,
        FALLBACK_LLM_MODEL
    )

    if success and answer:

        return (
            strip_markdown(
                answer
            ),
            True
        )

    print(
        "⚠️ Gemini gagal menghasilkan huraian."
    )

    return (
        generate_source_fallback(
            sources
        ),
        False
    )


# =========================================================
# ANSWER QUESTION
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

    print(
        "\n=============================================="
    )

    print(
        f"❓ SOALAN: {question}"
    )

    print(
        "=============================================="
    )

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    cached = get_cached_answer(
        question
    )

    if cached:

        print(
            "⚡ CACHE HIT"
        )

        return cached

    print(
        "🔎 CACHE MISS"
    )

    # -----------------------------------------------------
    # COMPARISON
    # -----------------------------------------------------

    comparison = (
        is_madhhab_comparison(
            question
        )
    )

    print(
        f"⚖️ COMPARISON: {comparison}"
    )

    # -----------------------------------------------------
    # ARABIC QUERY
    # -----------------------------------------------------

    arabic_query = (
        translate_to_arabic_query(
            question
        )
    )

    print(
        f"🇸🇦 FINAL SEARCH QUERY: {arabic_query}"
    )

    # -----------------------------------------------------
    # TURATH ONLY
    # -----------------------------------------------------

    turath_sources = search_turath(
        question=question,
        comparison=comparison,
        arabic_query=arabic_query
    )

    print(
        f"📚 TURATH SOURCES: {len(turath_sources)}"
    )

    # -----------------------------------------------------
    # NO SOURCE
    # -----------------------------------------------------

    if not has_valid_turath_source(
        turath_sources
    ):

        print(
            "⚠️ TIADA SUMBER TURATH YANG MENCUKUPI"
        )

        answer = (
            "⚠️ Sumber Turath tidak mencukupi untuk "
            "menjawab soalan ini.\n\n"
            "Saya tidak menggunakan sumber luar "
            "untuk menggantikan rujukan Turath."
        )

        # Jangan cache jawapan gagal.

        return answer

    # -----------------------------------------------------
    # GENERATE ANSWER
    # -----------------------------------------------------

    answer, ai_success = generate_answer(
        question,
        turath_sources,
        comparison
    )

    # -----------------------------------------------------
    # REFERENCES
    # -----------------------------------------------------

    references = format_references(
        turath_sources
    )

    final_answer = (
        f"{answer}\n\n"
        f"{references}"
    )

    # -----------------------------------------------------
    # CACHE CONTROL
    # -----------------------------------------------------

    lower_answer = (
        final_answer
        .lower()
    )

    insufficient_phrases = [

        "sumber turath tidak mencukupi",

        "sumber yang ditemui tidak mencukupi",

        "tiada sumber turath",

        "tidak dapat memberikan huraian",

        "tidak dapat menjawab",

        "maklumat tidak mencukupi",
    ]

    answer_is_insufficient = any(
        phrase in lower_answer
        for phrase in insufficient_phrases
    )

    if (
        ai_success
        and not answer_is_insufficient
        and has_valid_turath_source(
            turath_sources
        )
    ):

        set_cached_answer(
            question,
            final_answer
        )

        print(
            "💾 JAWAPAN DICACHE"
        )

    else:

        print(
            "⚠️ JAWAPAN TIDAK DICACHE"
        )

    print(
        "\n=============================================="
    )

    print(
        "✅ JAWAPAN SELESAI"
    )

    print(
        "==============================================\n"
    )

    return final_answer


# =========================================================
# TELEGRAM /START
# =========================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = """
Assalamualaikum 👋

Saya TanyaFiqihBot.

Saya mencari rujukan daripada kitab Turath untuk membantu menjawab soalan fiqh.

Contoh soalan:

• Apa hukum qunut Subuh menurut mazhab Syafie?

• Apa hukum mandi wajib?

• Apa rukun wuduk?

• Bandingkan hukum qunut antara mazhab Syafie dan Hanafi.

📚 Sumber rujukan: Turath sahaja.
""".strip()

    await update.message.reply_text(
        message
    )


# =========================================================
# TELEGRAM MESSAGE
# =========================================================

async def telegram_message(
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

    try:

        await update.message.reply_text(
            "🔎 Sedang mencari rujukan kitab Turath..."
        )

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        answer = strip_markdown(
            answer
        )

        chunks = telegram_chunks(
            answer
        )

        for chunk in chunks:

            await update.message.reply_text(
                chunk,
                disable_web_page_preview=True
            )

    except Exception as error:

        print(
            "❌ TELEGRAM ERROR:",
            error
        )

        traceback.print_exc()

        try:

            await update.message.reply_text(
                "⚠️ Maaf, berlaku masalah ketika mencari rujukan Turath."
            )

        except Exception:
            pass


# =========================================================
# TELEGRAM ERROR HANDLER
# =========================================================

async def telegram_error_handler(
    update,
    context
):

    print(
        "❌ TELEGRAM HANDLER ERROR:",
        context.error
    )

    if context.error:

        traceback.print_exception(
            type(
                context.error
            ),
            context.error,
            context.error.__traceback__
        )


# =========================================================
# RUN TELEGRAM
# =========================================================

def run_telegram():

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak ditetapkan."
        )

        return

    try:

        print(
            "🤖 MEMULAKAN TELEGRAM BOT..."
        )

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
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                telegram_message
            )
        )

        telegram_app.add_error_handler(
            telegram_error_handler
        )

        print(
            "✅ TELEGRAM BOT READY"
        )

        telegram_app.run_polling(
            drop_pending_updates=True
        )

    except Exception as error:

        print(
            "❌ TELEGRAM START ERROR:",
            error
        )

        traceback.print_exc()


# =========================================================
# FLASK HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({

        "success":
            True,

        "service":
            "TanyaFiqihBot",

        "source":
            "Turath sahaja",

        "status":
            "running",

        "turath_service":
            TURATH_SERVICE_URL,

    })


# =========================================================
# FLASK HEALTH
# =========================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    turath_ok = (
        check_turath_status()
    )

    return jsonify({

        "success":
            True,

        "status":
            "healthy",

        "turath":
            turath_ok,

        "turath_url":
            TURATH_SERVICE_URL,

        "gemini":
            bool(
                gemini_client
            ),

        "source":
            "Turath sahaja",

    })


# =========================================================
# ASK API
# =========================================================

@app.route(
    "/ask",
    methods=["POST"]
)
def ask():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        question = normalize_question(
            data.get(
                "question"
            )
            or data.get(
                "q"
            )
            or ""
        )

        if not question:

            return jsonify({

                "success":
                    False,

                "error":
                    "Soalan diperlukan",

            }), 400

        answer = answer_question(
            question
        )

        return jsonify({

            "success":
                True,

            "question":
                question,

            "answer":
                answer,

            "source":
                "Turath",

        })

    except Exception as error:

        print(
            "❌ /ask ERROR:",
            error
        )

        traceback.print_exc()

        return jsonify({

            "success":
                False,

            "error":
                str(
                    error
                ),

        }), 500


# =========================================================
# SEARCH API
# =========================================================

@app.route(
    "/search",
    methods=["POST"]
)
def search_api():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        question = normalize_question(
            data.get(
                "question"
            )
            or data.get(
                "query"
            )
            or ""
        )

        if not question:

            return jsonify({

                "success":
                    False,

                "error":
                    "query diperlukan",

            }), 400

        comparison = (
            is_madhhab_comparison(
                question
            )
        )

        arabic_query = (
            translate_to_arabic_query(
                question
            )
        )

        sources = search_turath(
            question=question,
            comparison=comparison,
            arabic_query=arabic_query
        )

        return jsonify({

            "success":
                True,

            "question":
                question,

            "arabic_query":
                arabic_query,

            "comparison":
                comparison,

            "source":
                "Turath",

            "count":
                len(
                    sources
                ),

            "passages":
                sources,

        })

    except Exception as error:

        print(
            "❌ /search ERROR:",
            error
        )

        traceback.print_exc()

        return jsonify({

            "success":
                False,

            "error":
                str(
                    error
                ),

        }), 500


# =========================================================
# CLEAR CACHE
# =========================================================

@app.route(
    "/clear-cache",
    methods=[
        "GET",
        "POST"
    ]
)
def clear_cache_api():

    clear_cache()

    return jsonify({

        "success":
            True,

        "message":
            "Cache jawapan telah dibersihkan.",

    })


# =========================================================
# BACKGROUND SERVICE
# =========================================================

_background_started = False

_background_lock = threading.Lock()


def start_background_services():

    global _background_started

    with _background_lock:

        if _background_started:
            return

        _background_started = True

    print(
        "\n=============================================="
    )

    print(
        "🚀 TANYAFIQIHBOT STARTING"
    )

    print(
        f"📚 SOURCE: TURATH SAHAJA"
    )

    print(
        f"📚 TURATH SERVICE: {TURATH_SERVICE_URL}"
    )

    print(
        f"🤖 LLM MODEL: {LLM_MODEL}"
    )

    print(
        f"🌐 ARABIC MODEL: {ARABIC_QUERY_MODEL}"
    )

    print(
        "==============================================\n"
    )

    # -----------------------------------------------------
    # TURATH HEALTH CHECK
    # -----------------------------------------------------

    try:

        check_turath_status()

    except Exception as error:

        print(
            "⚠️ Turath health check gagal:",
            error
        )

    # -----------------------------------------------------
    # TELEGRAM THREAD
    # -----------------------------------------------------

    telegram_thread = threading.Thread(
        target=run_telegram,
        daemon=True,
        name="telegram-bot"
    )

    telegram_thread.start()

    print(
        "🧵 TELEGRAM THREAD STARTED"
    )


# =========================================================
# START BACKGROUND SERVICES
# =========================================================

start_background_services()


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    host = os.getenv(
        "HOST",
        "0.0.0.0"
    )

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        "\n=============================================="
    )

    print(
        "🌐 FLASK STARTING"
    )

    print(
        f"📡 HOST: {host}"
    )

    print(
        f"📡 PORT: {port}"
    )

    print(
        "==============================================\n"
    )

    app.run(
        host=host,
        port=port,
        debug=False
    )
