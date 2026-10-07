import os
import re
import json
import time
import asyncio
import threading

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


# ============================================================
# CONFIG
# ============================================================

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
).strip().rstrip("/")

GEMINI_RETRIES = int(
    os.getenv("GEMINI_RETRIES", "2")
)

GEMINI_INITIAL_WAIT = float(
    os.getenv("GEMINI_INITIAL_WAIT", "2")
)

SOURCE_MAX_CHARS = int(
    os.getenv("SOURCE_MAX_CHARS", "4500")
)

CONTEXT_MAX_CHARS = int(
    os.getenv("CONTEXT_MAX_CHARS", "30000")
)

TELEGRAM_MAX_CHARS = int(
    os.getenv("TELEGRAM_MAX_CHARS", "3900")
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

print("✅ Flask app created")


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None

if GOOGLE_API_KEY:
    try:
        client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print("✅ Gemini client ready")

    except Exception as exc:
        print(
            f"⚠️ Gemini client gagal dimulakan: {exc}"
        )

else:
    print(
        "⚠️ GOOGLE_API_KEY belum ditetapkan."
    )


# ============================================================
# CACHE
# ============================================================

ANSWER_CACHE = {}

CACHE_LOCK = threading.Lock()

CACHE_TTL = 60 * 30


def normalize_question(text):
    if not text:
        return ""

    text = str(text).strip()

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


def cache_get(question):
    key = normalize_question(
        question
    ).lower()

    with CACHE_LOCK:

        item = ANSWER_CACHE.get(
            key
        )

        if not item:
            return None

        timestamp, value = item

        if (
            time.time() - timestamp
            > CACHE_TTL
        ):
            del ANSWER_CACHE[key]

            return None

        return value


def cache_set(
    question,
    value
):
    key = normalize_question(
        question
    ).lower()

    with CACHE_LOCK:

        ANSWER_CACHE[key] = (
            time.time(),
            value
        )


def cache_clear():

    with CACHE_LOCK:

        ANSWER_CACHE.clear()


# ============================================================
# QUERY MAP
# ============================================================

QUERY_MAP = {

    "puasa":
        "الصيام",

    "puasa ramadan":
        "صيام رمضان",

    "puasa ramadhan":
        "صيام رمضان",

    "zakat":
        "الزكاة",

    "zakat fitrah":
        "زكاة الفطر",

    "solat":
        "الصلاة",

    "sembahyang":
        "الصلاة",

    "wuduk":
        "الوضوء",

    "wudhu":
        "الوضوء",

    "ambil wuduk":
        "الوضوء",

    "taharah":
        "الطهارة",

    "bersuci":
        "الطهارة",

    "tayamum":
        "التيمم",

    "mandi wajib":
        "الغسل",

    "mandi junub":
        "غسل الجنابة",

    "mandi selepas haid":
        "غسل الحيض",

    "mandi selepas nifas":
        "غسل النفاس",

    "mandi haid":
        "غسل الحيض",

    "mandi nifas":
        "غسل النفاس",

    "junub":
        "الجنابة",

    "haid":
        "الحيض",

    "nifas":
        "النفاس",

    "istihadah":
        "الاستحاضة",

    "qunut":
        "القنوت",

    "qunut subuh":
        "القنوت في صلاة الصبح",

    "solat subuh":
        "صلاة الصبح",

    "solat jumaat":
        "صلاة الجمعة",

    "jumaat":
        "صلاة الجمعة",

    "azan":
        "الأذان",

    "azaan":
        "الأذان",

    "iqamah":
        "الإقامة",

    "nikah":
        "النكاح",

    "perkahwinan":
        "النكاح",

    "talak":
        "الطلاق",

    "cerai":
        "الطلاق",

    "faraid":
        "الفرائض",

    "pusaka":
        "المواريث",

    "haji":
        "الحج",

    "umrah":
        "العمرة",

    "korban":
        "الأضحية",

    "akikah":
        "العقيقة",

    "sembelihan":
        "الذبائح",

    "najis":
        "النجاسة",

    "aurat":
        "العورة",

    "mahar":
        "المهر",

    "mas kahwin":
        "المهر",

    "jual beli":
        "البيع",

    "riba":
        "الربا",

    "hutang":
        "الدين",

    "pinjaman":
        "القرض",

    "wakaf":
        "الوقف",

    "nazar":
        "النذر",

    "sumpah":
        "اليمين",

    "kaffarah":
        "الكفارة",

    "kafarah":
        "الكفارة",
}


def apply_query_map(question):

    q = normalize_question(
        question
    )

    lower = q.lower()

    matches = []

    for malay, arabic in sorted(
        QUERY_MAP.items(),
        key=lambda x: len(x[0]),
        reverse=True
    ):

        if malay in lower:

            matches.append(
                arabic
            )

    unique = []

    for item in matches:

        if item not in unique:

            unique.append(item)

    return " ".join(
        unique[:5]
    )


# ============================================================
# MAZHAB COMPARISON
# ============================================================

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


# ============================================================
# GEMINI
# ============================================================

def is_retryable_error(
    exc
):

    text = str(
        exc
    ).lower()

    retry_words = [

        "503",

        "429",

        "resource exhausted",

        "unavailable",

        "overloaded",

        "deadline",

        "timeout",

        "temporarily",

        "internal error",
    ]

    return any(
        word in text
        for word in retry_words
    )


def gemini_generate(
    prompt,
    model=None,
    temperature=0.2,
):

    if not client:

        raise RuntimeError(
            "GOOGLE_API_KEY tidak tersedia."
        )

    selected_model = (
        model
        or LLM_MODEL
    )

    last_error = None

    for attempt in range(
        GEMINI_RETRIES + 1
    ):

        try:

            response = (
                client.models.generate_content(
                    model=selected_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature
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
                "Gemini tidak mengembalikan teks."
            )

        except Exception as exc:

            last_error = exc

            if (
                attempt >= GEMINI_RETRIES
                or not is_retryable_error(
                    exc
                )
            ):

                break

            wait = (
                GEMINI_INITIAL_WAIT
                * (2 ** attempt)
            )

            print(
                f"⚠️ Gemini retry "
                f"{attempt + 1} "
                f"tunggu {wait}s"
            )

            time.sleep(
                wait
            )

    raise last_error


# ============================================================
# ARABIC QUERY
# ============================================================

def translate_to_arabic(
    question
):

    mapped = apply_query_map(
        question
    )

    prompt = f"""
Anda ialah pembantu penyelidikan kitab fiqh Arab.

Tukarkan soalan Bahasa Melayu kepada query carian
Bahasa Arab yang sesuai untuk kitab turath.

Soalan:
{question}

Istilah fiqh:
{mapped or "(tiada)"}

Peraturan:
1. Hanya keluarkan query Bahasa Arab.
2. Jangan jawab soalan.
3. Jangan beri penerangan.
4. Gunakan istilah fiqh klasik.
5. Jika berkaitan mazhab tertentu, masukkan nama mazhab.
6. Qunut Subuh:
   القنوت في صلاة الصبح
7. Mandi wajib:
   الغسل
8. Mandi junub:
   غسل الجنابة
9. Mandi selepas haid:
   غسل الحيض
10. Mandi selepas nifas:
   غسل النفاس
"""

    try:

        result = gemini_generate(
            prompt,
            model=ARABIC_QUERY_MODEL,
            temperature=0.1,
        )

        result = result.strip()

        result = re.sub(
            r"^```(?:arabic)?",
            "",
            result,
            flags=re.I
        )

        result = re.sub(
            r"```$",
            "",
            result
        )

        result = result.strip()

        if mapped:

            return (
                f"{result} {mapped}"
            )

        return result

    except Exception as exc:

        print(
            f"⚠️ Gagal translate Arab: {exc}"
        )

        return (
            mapped
            or question
        )


# ============================================================
# TURATH
# ============================================================

def turath_search(
    query,
    comparison=False
):

    url = (
        f"{TURATH_SERVICE_URL}/search"
    )

    payload = {

        "query":
            query,

        "comparison":
            comparison,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=60,
        )

        print(
            f"📡 TURATH STATUS: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:

        print(
            f"❌ TURATH ERROR: {exc}"
        )

        return {
            "results": [],
            "error": str(exc)
        }


# ============================================================
# SOURCE HELPERS
# ============================================================

def clean_text(
    value
):

    if value is None:

        return ""

    if isinstance(
        value,
        (dict, list)
    ):

        try:

            return json.dumps(
                value,
                ensure_ascii=False
            )

        except Exception:

            return str(value)

    return str(
        value
    ).strip()


def recursive_find(
    obj,
    keys,
    depth=0,
    max_depth=5
):

    if depth > max_depth:

        return None

    if isinstance(
        obj,
        dict
    ):

        for key in keys:

            if key in obj:

                value = obj[key]

                if value not in (
                    None,
                    "",
                    [],
                    {}
                ):

                    return value

        for value in obj.values():

            found = recursive_find(
                value,
                keys,
                depth + 1,
                max_depth
            )

            if found not in (
                None,
                "",
                [],
                {}
            ):

                return found

    elif isinstance(
        obj,
        list
    ):

        for value in obj:

            found = recursive_find(
                value,
                keys,
                depth + 1,
                max_depth
            )

            if found not in (
                None,
                "",
                [],
                {}
            ):

                return found

    return None


def extract_source(
    raw
):

    text = recursive_find(
        raw,
        [
            "text",
            "content",
            "snippet",
            "passage",
            "body",
            "excerpt"
        ]
    )

    book = recursive_find(
        raw,
        [
            "book",
            "book_name",
            "bookName",
            "title",
            "book_title",
            "bookTitle"
        ]
    )

    author = recursive_find(
        raw,
        [
            "author",
            "author_name",
            "authorName",
            "writer"
        ]
    )

    page = recursive_find(
        raw,
        [
            "page",
            "page_number",
            "pageNumber",
            "page_no",
            "pageNo"
        ]
    )

    url = recursive_find(
        raw,
        [
            "url",
            "link",
            "href",
            "book_url",
            "bookUrl"
        ]
    )

    source_id = recursive_find(
        raw,
        [
            "id",
            "source_id",
            "sourceId",
            "result_id"
        ]
    )

    book_id = recursive_find(
        raw,
        [
            "book_id",
            "bookId",
            "bookID"
        ]
    )

    category = recursive_find(
        raw,
        [
            "category",
            "madhhab",
            "mazhab"
        ]
    )

    return {

        "text":
            clean_text(text),

        "book":
            clean_text(book),

        "author":
            clean_text(author),

        "page":
            clean_text(page),

        "url":
            clean_text(url),

        "id":
            clean_text(source_id),

        "book_id":
            clean_text(book_id),

        "category":
            clean_text(category),

        "raw":
            raw,
    }


def normalize_sources(
    data
):

    if not isinstance(
        data,
        dict
    ):

        return []

    raw_results = (
        data.get("results")
        or data.get("data")
        or data.get("items")
        or []
    )

    if isinstance(
        raw_results,
        dict
    ):

        raw_results = (
            raw_results.get("results")
            or raw_results.get("items")
            or []
        )

    if not isinstance(
        raw_results,
        list
    ):

        raw_results = []

    sources = []

    for raw in raw_results:

        source = extract_source(
            raw
        )

        if not source["text"]:

            continue

        text = source["text"]

        if len(text) > SOURCE_MAX_CHARS:

            text = (
                text[:SOURCE_MAX_CHARS]
                + "..."
            )

        source["text"] = text

        sources.append(
            source
        )

    return sources


# ============================================================
# RELEVANCE
# ============================================================

def relevance_score(
    question,
    source
):

    q = normalize_question(
        question
    ).lower()

    text = (
        source.get(
            "text",
            ""
        ).lower()
    )

    if not text:

        return 0

    score = 0

    words = re.findall(
        r"[\w\u0600-\u06ff]+",
        q
    )

    stopwords = {
        "apa",
        "yang",
        "dan",
        "atau",
        "di",
        "ke",
        "dalam",
        "itu",
        "ini",
        "adakah",
        "bolehkah",
        "bagaimana",
        "menurut",
        "hukum",
        "saya",
        "nak",
        "mahu",
        "untuk",
        "dengan",
    }

    for word in words:

        if (
            len(word) >= 3
            and word not in stopwords
        ):

            if word in text:

                score += 2

    mapped = apply_query_map(
        question
    ).lower()

    for term in mapped.split():

        if (
            len(term) >= 3
            and term in text
        ):

            score += 3

    return score


def rank_sources(
    question,
    sources,
    limit=10
):

    scored = []

    for index, source in enumerate(
        sources
    ):

        score = relevance_score(
            question,
            source
        )

        scored.append(
            (
                score,
                index,
                source
            )
        )

    scored.sort(
        key=lambda x: (
            x[0],
            -x[1]
        ),
        reverse=True
    )

    return [
        item[2]
        for item in scored[:limit]
    ]


# ============================================================
# CONTEXT
# ============================================================

def build_source_context(
    sources
):

    chunks = []

    total = 0

    for i, source in enumerate(
        sources,
        start=1
    ):

        block = f"""
SUMBER TURATH #{i}

Kitab:
{source.get("book") or "Tidak diketahui"}

Pengarang:
{source.get("author") or "Tidak diketahui"}

Halaman:
{source.get("page") or "Tidak diketahui"}

Mazhab/Kategori:
{source.get("category") or "Tidak diketahui"}

Book ID:
{source.get("book_id") or "Tidak diketahui"}

URL:
{source.get("url") or "Tidak tersedia"}

Petikan:
{source.get("text") or ""}
""".strip()

        if (
            total + len(block)
            > CONTEXT_MAX_CHARS
        ):

            break

        chunks.append(
            block
        )

        total += len(block)

    return (
        "\n\n"
        + "\n\n".join(chunks)
    )


# ============================================================
# CITATION
# ============================================================

def source_label(
    source
):

    book = (
        source.get("book")
        or "Kitab tidak diketahui"
    )

    page = (
        source.get("page")
        or "?"
    )

    return (
        f"*{book}*, hlm. {page}"
    )


def replace_source_tags(
    answer,
    sources
):

    def repl(match):

        number = int(
            match.group(1)
        )

        if (
            number < 1
            or number > len(sources)
        ):

            return ""

        return (
            "("
            + source_label(
                sources[number - 1]
            )
            + ")"
        )

    return re.sub(
        r"\[S(\d+)\]",
        repl,
        answer,
        flags=re.I
    )


# ============================================================
# CLEAN ANSWER
# ============================================================

def clean_answer(
    text
):

    if not text:

        return ""

    text = text.strip()

    text = re.sub(
        r"^```(?:markdown|text)?",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    text = re.split(
        r"📚\s*\**RUJUKAN TURATH\**",
        text,
        flags=re.I
    )[0].strip()

    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r"\1",
        text
    )

    return text


# ============================================================
# REFERENCES
# ============================================================

def build_references(
    sources,
    cited_sources
):

    if not sources:

        return ""

    lines = [
        "📚 **RUJUKAN TURATH**",
        ""
    ]

    for i, source in enumerate(
        sources,
        start=1
    ):

        book = (
            source.get("book")
            or "Kitab tidak diketahui"
        )

        author = (
            source.get("author")
            or "Pengarang tidak diketahui"
        )

        page = (
            source.get("page")
            or "Tidak dinyatakan"
        )

        url = (
            source.get("url")
            or ""
        )

        lines.append(
            f"{i}. **{book}**"
        )

        lines.append(
            f"   ✍️ {author}"
        )

        lines.append(
            f"   📄 Hlm. {page}"
        )

        if url:

            lines.append(
                f"   🔗 {url}"
            )

        else:

            lines.append(
                "   🔗 Link tidak tersedia"
            )

        lines.append("")

        if i in cited_sources:

            lines.append(
                "   ➤ Dirujuk untuk: "
                "pernyataan yang berkaitan "
                "dalam jawapan."
            )

        else:

            lines.append(
                "   ➤ Sumber berkaitan "
                "yang ditemukan."
            )

        lines.append("")

    return "\n".join(
        lines
    ).strip()


# ============================================================
# GEMINI FIQH
# ============================================================

def generate_fiqh_answer(
    question,
    sources,
    comparison=False
):

    if not sources:

        return (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan huraian."
        )

    context = build_source_context(
        sources
    )

    if comparison:

        mode_instruction = """
SOALAN MEMINTA PERBANDINGAN MAZHAB.

Bandingkan hanya berdasarkan sumber yang diberikan.

Jangan masukkan pandangan yang tidak terdapat dalam
sumber.

Jika sumber sesuatu mazhab tidak mencukupi, nyatakan
bahawa maklumat tersebut tidak ditemukan.
"""

    else:

        mode_instruction = """
SOALAN BUKAN PERBANDINGAN MAZHAB.

Utamakan pandangan mazhab Syafie.

Jangan masukkan pandangan mazhab lain kecuali
benar-benar diperlukan dan disokong oleh sumber.
"""

    prompt = f"""
Anda ialah penyelidik fiqh Islam.

Jawapan WAJIB berdasarkan petikan kitab Turath
yang diberikan sahaja.

SOALAN:
{question}

{mode_instruction}

SUMBER TURATH:
{context}

==================================================
PERATURAN
==================================================

1. Jangan gunakan sumber luar.
2. Jangan reka nama kitab.
3. Jangan reka pengarang.
4. Jangan reka halaman.
5. Jangan reka URL.
6. Jangan reka hukum.
7. Jangan masukkan fakta yang tidak disokong.
8. Jika ada khilaf, hanya gunakan khilaf yang disokong.
9. Jika tiada khilaf, jangan buat bahagian khilaf.
10. Jangan tulis senarai rujukan sendiri.
11. Jangan tulis URL sendiri.

==================================================
CITATION
==================================================

Setiap fakta penting mesti mempunyai citation:

[S1]
[S2]
[S3]

Citation mesti berdasarkan sumber yang sebenar.

Contoh:

Menurut kitab tersebut, qunut Subuh merupakan
sunat ab'ad dalam mazhab Syafie. [S1]

Jika dua sumber menyokong:

Perkara tersebut turut disebut oleh ulama lain. [S1] [S3]

Jangan tulis nama kitab atau halaman selepas citation.
Sistem akan memasukkannya sendiri.

==================================================
FORMAT
==================================================

📖 **JAWAPAN**

Berikan hukum paling penting dahulu.

📚 **HURAIAN**

Terangkan hukum dalam perenggan yang kemas.

Masukkan citation terus selepas kenyataan:

... [S1]

... [S2]

🔹 **PERINCIAN**

1. **Tajuk**
   Huraian. [S1]

2. **Tajuk**
   Huraian. [S2]

⚖️ **PERBEZAAN PANDANGAN**

Hanya keluarkan jika benar-benar terdapat khilaf
yang disokong oleh sumber.

Jika tiada khilaf, jangan keluarkan tajuk ini.

==================================================
GAYA
==================================================

Bahasa Melayu Malaysia.

Kemas.

Mudah difahami.

Tidak terlalu panjang.

Tidak mengulang.

Jangan menyebut anda AI.

Hanya keluarkan jawapan.
"""

    try:

        answer = gemini_generate(
            prompt,
            model=(
                LLM_MODEL
                or FALLBACK_LLM_MODEL
            ),
            temperature=0.15
        )

        return answer

    except Exception as exc:

        print(
            f"❌ Gemini answer error: {exc}"
        )

        if FALLBACK_LLM_MODEL:

            try:

                return gemini_generate(
                    prompt,
                    model=FALLBACK_LLM_MODEL,
                    temperature=0.15
                )

            except Exception:

                pass

        raise


# ============================================================
# MAIN ANSWER
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

    cached = cache_get(
        question
    )

    if cached:

        print(
            "⚡ CACHE HIT"
        )

        return cached

    print(
        "\n=================================================="
    )

    print(
        f"❓ SOALAN: {question}"
    )

    comparison = (
        is_madhhab_comparison(
            question
        )
    )

    print(
        f"⚖️ COMPARISON: {comparison}"
    )

    # --------------------------------------------------------
    # ARABIC QUERY
    # --------------------------------------------------------

    arabic_query = translate_to_arabic(
        question
    )

    print(
        f"🔎 ARABIC QUERY: {arabic_query}"
    )

    # --------------------------------------------------------
    # TURATH
    # --------------------------------------------------------

    data = turath_search(
        arabic_query,
        comparison=comparison
    )

    # --------------------------------------------------------
    # SOURCES
    # --------------------------------------------------------

    sources = normalize_sources(
        data
    )

    print(
        f"📚 TURATH SOURCES: "
        f"{len(sources)}"
    )

    if sources:

        print(
            "\n🧪 FIRST NORMALIZED SOURCE:"
        )

        print(
            json.dumps(
                {
                    "book":
                        sources[0].get("book"),

                    "author":
                        sources[0].get("author"),

                    "page":
                        sources[0].get("page"),

                    "url":
                        sources[0].get("url"),

                    "book_id":
                        sources[0].get("book_id"),

                    "category":
                        sources[0].get("category"),

                    "text_preview":
                        sources[0].get(
                            "text",
                            ""
                        )[:500],
                },
                ensure_ascii=False,
                indent=2
            )
        )

    if not sources:

        result = (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan huraian.\n\n"
            "📚 **Rujukan:**\n"
            "• Tiada rujukan ditemui."
        )

        cache_set(
            question,
            result
        )

        return result

    # --------------------------------------------------------
    # RANK
    # --------------------------------------------------------

    sources = rank_sources(
        question,
        sources,
        limit=10
    )

    # --------------------------------------------------------
    # GEMINI ANSWER
    # --------------------------------------------------------

    raw_answer = generate_fiqh_answer(
        question,
        sources,
        comparison=comparison
    )

    # --------------------------------------------------------
    # SIMPAN SOURCE YANG DIRUJUK
    # SEBELUM [S1] DITUKAR
    # --------------------------------------------------------

    cited_sources = set()

    for match in re.finditer(
        r"\[S(\d+)\]",
        raw_answer,
        flags=re.I
    ):

        number = int(
            match.group(1)
        )

        if (
            1 <= number <= len(sources)
        ):

            cited_sources.add(
                number
            )

    # --------------------------------------------------------
    # REPLACE CITATIONS
    # --------------------------------------------------------

    answer = replace_source_tags(
        raw_answer,
        sources
    )

    answer = clean_answer(
        answer
    )

    # --------------------------------------------------------
    # REFERENCES
    # --------------------------------------------------------

    references = build_references(
        sources,
        cited_sources
    )

    if references:

        final_answer = (
            answer
            + "\n\n"
            + references
        )

    else:

        final_answer = answer

    final_answer = re.sub(
        r"\n{4,}",
        "\n\n\n",
        final_answer
    ).strip()

    cache_set(
        question,
        final_answer
    )

    return final_answer


# ============================================================
# TELEGRAM
# ============================================================

def split_telegram_message(
    text,
    max_len=TELEGRAM_MAX_CHARS
):

    if len(text) <= max_len:

        return [text]

    parts = []

    remaining = text

    while len(remaining) > max_len:

        cut = remaining.rfind(
            "\n",
            0,
            max_len
        )

        if cut < int(
            max_len * 0.5
        ):

            cut = remaining.rfind(
                " ",
                0,
                max_len
            )

        if cut <= 0:

            cut = max_len

        parts.append(
            remaining[:cut].strip()
        )

        remaining = (
            remaining[cut:].strip()
        )

    if remaining:

        parts.append(
            remaining
        )

    return parts


async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Assalamualaikum.\n\n"
        "Saya **TanyaFiqihBot**.\n\n"
        "Tanya soalan berkaitan fiqh dan saya akan "
        "mencari jawapan daripada sumber kitab Turath.\n\n"
        "Contoh:\n"
        "• Apa hukum qunut Subuh?\n"
        "• Bagaimana cara mandi wajib?\n"
        "• Apa hukum zakat fitrah?\n"
        "• Bandingkan qunut antara 4 mazhab.",
        parse_mode="Markdown"
    )


async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📚 **Cara menggunakan TanyaFiqihBot**\n\n"
        "Hantar sahaja soalan fiqh anda.",
        parse_mode="Markdown"
    )


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

    await update.message.chat.send_action(
        action="typing"
    )

    try:

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        parts = split_telegram_message(
            answer
        )

        for part in parts:

            try:

                await update.message.reply_text(
                    part,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )

            except Exception as markdown_error:

                print(
                    "⚠️ Markdown error, "
                    "hantar plain text:"
                    f" {markdown_error}"
                )

                await update.message.reply_text(
                    re.sub(
                        r"[*_`]",
                        "",
                        part
                    ),
                    disable_web_page_preview=True
                )

    except Exception as exc:

        print(
            f"❌ TELEGRAM ANSWER ERROR: {exc}"
        )

        await update.message.reply_text(
            "⚠️ Maaf, berlaku masalah ketika "
            "memproses soalan.\n\n"
            f"Ralat: {str(exc)[:500]}"
        )


# ============================================================
# TELEGRAM STARTUP
# ============================================================

telegram_app = None

telegram_thread = None

telegram_started = False

telegram_start_lock = threading.Lock()


def telegram_worker():

    global telegram_app

    try:

        print(
            "🤖 MEMULAKAN TELEGRAM BOT..."
        )

        telegram_app = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
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
                telegram_message
            )
        )

        print(
            "🧵 TELEGRAM THREAD STARTED"
        )

        print(
            "✅ TELEGRAM BOT READY"
        )

        telegram_app.run_polling(
            drop_pending_updates=True,
            stop_signals=None
        )

    except Exception as exc:

        print(
            f"❌ TELEGRAM START ERROR: {exc}"
        )


def start_telegram():

    global telegram_thread
    global telegram_started

    with telegram_start_lock:

        if telegram_started:

            return

        if not TELEGRAM_TOKEN:

            print(
                "⚠️ TELEGRAM_TOKEN tidak tersedia."
            )

            return

        telegram_started = True

        telegram_thread = threading.Thread(
            target=telegram_worker,
            daemon=True,
            name="telegram-bot"
        )

        telegram_thread.start()


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "name":
            "TanyaFiqihBot",

        "status":
            "running",

        "source":
            "Turath",

        "turath_service":
            TURATH_SERVICE_URL
    })


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    turath_status = "unknown"

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/health",
            timeout=10
        )

        turath_status = (
            "ok"
            if response.ok
            else
            f"http_{response.status_code}"
        )

    except Exception as exc:

        turath_status = (
            f"error: {str(exc)}"
        )

    return jsonify({

        "status":
            "ok",

        "turath":
            turath_status,

        "telegram":
            bool(TELEGRAM_TOKEN),

        "gemini":
            bool(GOOGLE_API_KEY),

        "model":
            LLM_MODEL
    })


@app.route(
    "/ask",
    methods=["POST"]
)
def ask():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    question = (
        data.get("question")
        or data.get("q")
        or ""
    )

    question = normalize_question(
        question
    )

    if not question:

        return jsonify({
            "ok":
                False,

            "error":
                "Soalan diperlukan."
        }), 400

    try:

        answer = answer_question(
            question
        )

        return jsonify({

            "ok":
                True,

            "question":
                question,

            "answer":
                answer
        })

    except Exception as exc:

        print(
            f"❌ /ask ERROR: {exc}"
        )

        return jsonify({

            "ok":
                False,

            "error":
                str(exc)
        }), 500


@app.route(
    "/search",
    methods=["POST"]
)
def search_route():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    query = (
        data.get("query")
        or data.get("q")
        or ""
    )

    query = normalize_question(
        query
    )

    if not query:

        return jsonify({
            "ok":
                False,

            "error":
                "Query diperlukan."
        }), 400

    comparison = bool(
        data.get(
            "comparison",
            False
        )
    )

    result = turath_search(
        query,
        comparison=comparison
    )

    return jsonify(
        result
    )


@app.route(
    "/clear-cache",
    methods=[
        "GET",
        "POST"
    ]
)
def clear_cache_route():

    cache_clear()

    return jsonify({

        "ok":
            True,

        "message":
            "Cache telah dikosongkan."
    })


# ============================================================
# GUNICORN STARTUP
# ============================================================

def start_background_services():

    print(
        "=================================================="
    )

    print(
        "🚀 TANYAFIQIHBOT WORKER STARTING"
    )

    print(
        f"📚 TURATH SERVICE: "
        f"{TURATH_SERVICE_URL}"
    )

    print(
        f"🤖 GEMINI MODEL: "
        f"{LLM_MODEL}"
    )

    print(
        f"🔎 ARABIC MODEL: "
        f"{ARABIC_QUERY_MODEL}"
    )

    print(
        f"📡 TELEGRAM: "
        f"{'ON' if TELEGRAM_TOKEN else 'OFF'}"
    )

    print(
        "=================================================="
    )

    # Telegram perlu dimulakan apabila Gunicorn
    # mengimport app:app.
    start_telegram()


# ============================================================
# IMPORTANT:
# GUNICORN IMPORTS app.py
# ============================================================

try:

    start_background_services()

except Exception as exc:

    # Jangan biarkan masalah Telegram menyebabkan
    # Flask/Gunicorn worker gagal boot.
    print(
        f"⚠️ Background startup warning: {exc}"
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
