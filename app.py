import os
import re
import json
import time
import asyncio
import threading
import requests

from flask import Flask, jsonify
from google import genai

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

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.1-flash-lite"
)

ARABIC_QUERY_MODEL = os.getenv(
    "ARABIC_QUERY_MODEL",
    "gemini-3.1-flash-lite"
)

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765"
).rstrip("/")

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

TARGET_SOURCES = 10


# ============================================================
# STARTUP CHECK
# ============================================================

print("=" * 50)
print("🚀 TANYAFIQIHBOT STARTING")
print("=" * 50)

print(
    "🔑 GOOGLE_API_KEY:",
    "SET" if GOOGLE_API_KEY else "MISSING"
)

print(
    "🤖 TELEGRAM_TOKEN:",
    "SET" if TELEGRAM_TOKEN else "MISSING"
)

print(
    "📚 TURATH SERVICE:",
    TURATH_SERVICE_URL
)

print(
    "🧠 LLM MODEL:",
    LLM_MODEL
)

print("=" * 50)


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None

if GOOGLE_API_KEY:
    client = genai.Client(
        api_key=GOOGLE_API_KEY
    )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# QUERY MAP
# ============================================================

QUERY_MAP = {
    "hukum puasa": "أحكام الصيام",
    "batal puasa": "مفسدات الصيام",
    "membatalkan puasa": "مفسدات الصيام",
    "perkara batal puasa": "مفسدات الصيام",
    "syarat puasa": "شروط الصيام",
    "rukun puasa": "أركان الصيام",
    "niat puasa": "نية الصيام",
    "niat puasa ramadhan": "نية صيام رمضان",
    "puasa ramadhan": "صيام رمضان",
    "qada puasa": "قضاء الصيام",
    "qada ramadan": "قضاء رمضان",
    "fidiah": "الفدية",
    "fidyah": "الفدية",
    "kaffarah": "الكفارة في الصيام",
    "kafarah": "الكفارة في الصيام",
    "haid puasa": "الحيض والصيام",
    "haid ketika puasa": "الحيض والصيام",
    "musafir puasa": "صيام المسافر",
    "orang sakit puasa": "صيام المريض",
    "muntah puasa": "القيء والصيام",
    "gosok gigi puasa": "السواك وتنظيف الأسنان للصائم",
    "ubat puasa": "الدواء والصيام",
    "suntikan puasa": "الحقن والصيام",
    "bersetubuh ketika puasa": "الجماع في الصيام",
    "jima puasa": "الجماع في الصيام",
    "lupa makan puasa": "الأكل والشرب ناسيا في الصيام",
    "terlupa makan": "الأكل والشرب ناسيا في الصيام",

    "zakat": "أحكام الزكاة",
    "zakat fitrah": "زكاة الفطر",

    "solat": "أحكام الصلاة",
    "wuduk": "أحكام الوضوء",
    "hadas": "الحدث والطهارة",
    "najis": "أحكام النجاسة",
    "tayammum": "أحكام التيمم",
    "mandi wajib": "الغسل الواجب",

    "aurat": "أحكام العورة",
    "haid": "أحكام الحيض",
    "nifas": "أحكام النفاس",
    "istihadah": "أحكام الاستحاضة",
}


# ============================================================
# GREETING
# ============================================================

GREETING_WORDS = {
    "hi",
    "hai",
    "hello",
    "helo",
    "hey",
    "yo",
    "salam",
    "salamualaikum",
    "assalamualaikum",
    "assalamu alaikum",
    "assalamualaikum warahmatullahi wabarakatuh",
}


def normalize_text(text):

    text = str(text).lower().strip()

    text = re.sub(
        r"[^\w\s]",
        "",
        text,
        flags=re.UNICODE
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def is_simple_greeting(text):

    normalized = normalize_text(text)

    return normalized in {
        normalize_text(word)
        for word in GREETING_WORDS
    }


def greeting_response(text):

    normalized = normalize_text(text)

    if (
        "assalamualaikum" in normalized
        or normalized == "salam"
    ):
        return (
            "Waalaikumussalam warahmatullahi wabarakatuh 🌷\n\n"
            "Saya TanyaFiqihBot.\n\n"
            "Silakan ajukan soalan fiqh anda. "
            "Saya akan membantu menyemak rujukan Turath "
            "yang berkaitan."
        )

    return (
        "Hai 👋 Saya TanyaFiqihBot.\n\n"
        "Ada soalan fiqh yang anda ingin tanyakan?\n\n"
        "Saya akan membantu mencari jawapan "
        "berdasarkan rujukan Turath."
    )


# ============================================================
# GEMINI
# ============================================================

def gemini_generate(
    prompt,
    model=None,
    retries=None
):

    if client is None:
        raise RuntimeError(
            "GOOGLE_API_KEY belum ditetapkan."
        )

    if model is None:
        model = LLM_MODEL

    if retries is None:
        retries = GEMINI_RETRIES

    last_error = None

    for attempt in range(retries + 1):

        try:

            response = client.models.generate_content(
                model=model,
                contents=prompt
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

        except Exception as error:

            last_error = error

            print(
                f"⚠️ GEMINI ERROR "
                f"attempt={attempt + 1}:",
                repr(error)
            )

            if attempt < retries:

                wait_time = (
                    GEMINI_INITIAL_WAIT
                    * (2 ** attempt)
                )

                time.sleep(wait_time)

    raise last_error


# ============================================================
# JSON
# ============================================================

def extract_json(text):

    if not text:
        return {}

    text = text.strip()

    text = re.sub(
        r"^```json",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```",
        "",
        text
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if match:

        try:
            return json.loads(
                match.group(0)
            )
        except Exception:
            pass

    return {}


# ============================================================
# GEMINI PLAN QUERY
# ============================================================

def plan_turath_queries(question):

    prompt = f"""
Anda ialah perancang carian untuk bot fiqh Islam.

Soalan pengguna:

{question}

Tugas:

1. Fahami maksud sebenar soalan.
2. Kenal pasti isu fiqh utama.
3. Kenal pasti istilah fiqh Arab.
4. Hasilkan 3 hingga 8 query carian bahasa Arab.
5. Query mesti sesuai untuk mencari kitab fiqh Turath.
6. Jika ada khilaf mazhab, cuba cari perbincangan tersebut.
7. Jangan jawab soalan.
8. Jangan reka hukum.

Output JSON sahaja:

{{
  "isu": "ringkasan isu",
  "kata_kunci": [
    "istilah Arab"
  ],
  "queries": [
    "query Arab 1",
    "query Arab 2",
    "query Arab 3"
  ]
}}
"""

    result = gemini_generate(
        prompt,
        model=ARABIC_QUERY_MODEL
    )

    data = extract_json(result)

    queries = data.get(
        "queries",
        []
    )

    if not isinstance(queries, list):
        queries = []

    queries = [
        str(q).strip()
        for q in queries
        if str(q).strip()
    ]

    normalized_question = (
        question.lower()
    )

    mapped_queries = []

    for key, arabic in QUERY_MAP.items():

        if key in normalized_question:

            if arabic not in mapped_queries:
                mapped_queries.append(arabic)

    final_queries = []

    for query in (
        mapped_queries
        + queries
    ):

        if query and query not in final_queries:
            final_queries.append(query)

    final_queries = final_queries[:8]

    if not final_queries:

        final_queries = [
            question
        ]

    print(
        "🔎 TURATH QUERIES:",
        final_queries
    )

    return {
        "isu": data.get(
            "isu",
            ""
        ),
        "kata_kunci": data.get(
            "kata_kunci",
            []
        ),
        "queries": final_queries
    }


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath(queries):

    payload = {
        "queries": queries
    }

    print(
        "📚 TURATH REQUEST:",
        payload
    )

    response = requests.post(
        f"{TURATH_SERVICE_URL}/search",
        json=payload,
        timeout=90
    )

    response.raise_for_status()

    data = response.json()

    results = []

    for key in [
        "results",
        "sources",
        "data",
        "items",
        "hits"
    ]:

        value = data.get(key)

        if isinstance(value, list):
            results.extend(value)

    print(
        "📚 RAW TURATH SOURCES:",
        len(results)
    )

    unique = []
    seen = set()

    for item in results:

        if not isinstance(item, dict):
            continue

        book_id = str(
            item.get("book_id")
            or item.get("bookId")
            or ""
        )

        page = str(
            item.get("page")
            or ""
        )

        text = str(
            item.get("text")
            or item.get("content")
            or item.get("snippet")
            or ""
        )

        key = (
            book_id,
            page,
            text[:250]
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(item)

    return unique[:TARGET_SOURCES]


# ============================================================
# SOURCE HELPERS
# ============================================================

def clean_value(value):

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float)):
        return str(value)

    return str(value).strip()


def get_source_text(source):

    for key in [
        "text",
        "content",
        "snippet",
        "passage",
        "quote",
        "body"
    ]:

        value = source.get(key)

        if value:
            return clean_value(value)

    return ""


def get_source_book(source):

    for key in [
        "book",
        "book_name",
        "bookName",
        "book_title",
        "bookTitle",
        "title"
    ]:

        value = source.get(key)

        if isinstance(value, dict):

            for nested_key in [
                "name",
                "title",
                "book_name",
                "bookName"
            ]:

                nested = value.get(
                    nested_key
                )

                if nested:
                    return clean_value(
                        nested
                    )

        elif value:

            return clean_value(value)

    return ""


def get_source_author(source):

    for key in [
        "author",
        "author_name",
        "authorName",
        "writer",
        "muallif"
    ]:

        value = source.get(key)

        if isinstance(value, dict):

            for nested_key in [
                "name",
                "author_name",
                "authorName"
            ]:

                nested = value.get(
                    nested_key
                )

                if nested:
                    return clean_value(
                        nested
                    )

        elif value:

            return clean_value(value)

    return ""


def get_source_page(source):

    for key in [
        "page",
        "page_number",
        "pageNumber",
        "pageno",
        "pageNo"
    ]:

        value = source.get(key)

        if value is not None:
            return clean_value(value)

    return ""


def get_source_book_id(source):

    for key in [
        "book_id",
        "bookId",
        "id"
    ]:

        value = source.get(key)

        if value is not None:
            return clean_value(value)

    return ""


def get_source_url(source):

    for key in [
        "url",
        "link",
        "book_url",
        "bookUrl"
    ]:

        value = source.get(key)

        if value:
            return clean_value(value)

    book_id = get_source_book_id(
        source
    )

    if book_id:

        return (
            "https://app.turath.io/book/"
            + book_id
        )

    return ""


# ============================================================
# NORMALIZE SOURCES
# ============================================================

def normalize_sources(sources):

    normalized = []

    for source in sources:

        if not isinstance(source, dict):
            continue

        item = dict(source)

        item["book"] = get_source_book(
            source
        )

        item["author"] = get_source_author(
            source
        )

        item["page"] = get_source_page(
            source
        )

        item["book_id"] = get_source_book_id(
            source
        )

        item["url"] = get_source_url(
            source
        )

        item["text"] = get_source_text(
            source
        )

        normalized.append(item)

    return normalized


# ============================================================
# RANK
# ============================================================

def rank_sources(
    sources,
    question
):

    question_words = set(
        re.findall(
            r"\w+",
            question.lower()
        )
    )

    scored = []

    for source in sources:

        text = (
            source.get("text")
            or ""
        ).lower()

        score = 0

        for word in question_words:

            if (
                len(word) >= 3
                and word in text
            ):
                score += 1

        if source.get("book"):
            score += 1

        if source.get("author"):
            score += 1

        if source.get("page"):
            score += 1

        if source.get("book_id"):
            score += 1

        scored.append(
            (
                score,
                source
            )
        )

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return [
        source
        for _, source in scored
    ][:TARGET_SOURCES]


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_source_context(
    sources
):

    blocks = []
    total_chars = 0

    for index, source in enumerate(
        sources,
        start=1
    ):

        text = source.get(
            "text",
            ""
        )

        if not text:
            continue

        text = text[
            :SOURCE_MAX_CHARS
        ]

        book = (
            source.get("book")
            or "Nama kitab tidak tersedia"
        )

        author = (
            source.get("author")
            or "Pengarang tidak tersedia"
        )

        page = (
            source.get("page")
            or "Tidak diketahui"
        )

        book_id = (
            source.get("book_id")
            or "Tidak diketahui"
        )

        block = f"""
[S{index}]
KITAB: {book}
PENGARANG: {author}
HALAMAN: {page}
BOOK_ID: {book_id}

PETIKAN:
{text}
""".strip()

        if (
            total_chars
            + len(block)
            > CONTEXT_MAX_CHARS
        ):
            break

        blocks.append(block)

        total_chars += len(block)

    return "\n\n".join(
        blocks
    )


# ============================================================
# FINAL ANSWER
# ============================================================

def generate_fiqh_answer(
    question,
    sources
):

    context = build_source_context(
        sources
    )

    if not context:

        return (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan huraian.\n\n"
            "📚 **Rujukan:**\n"
            "• Tiada rujukan ditemui."
        )

    prompt = f"""
Anda ialah pembantu fiqh Islam bernama TanyaFiqihBot.

SOALAN:
{question}

SUMBER TURATH:
{context}

Jawab dalam Bahasa Melayu.

PERATURAN:

1. Gunakan HANYA sumber Turath yang diberikan.
2. Jangan gunakan pengetahuan luar.
3. Jangan reka hukum.
4. Jangan reka nama kitab.
5. Jangan reka pengarang.
6. Jangan reka halaman.
7. Jangan reka URL.
8. Jika ada khilaf, nyatakan berdasarkan sumber.
9. Jika sumber tidak mencukupi, nyatakan dengan jujur.
10. Setiap fakta penting mesti menggunakan tag [S1], [S2],
    [S3] dan seterusnya.
11. Jangan buat bahagian RUJUKAN TURATH.
12. Jangan tulis URL.

FORMAT:

📖 JAWAPAN

Jawapan terus.

📚 HURAIAN

Huraian berdasarkan kitab.

🔹 PERINCIAN

Perincian jika diperlukan.

⚖️ PERBEZAAN PANDANGAN

Gunakan hanya jika terdapat khilaf yang jelas.

Jawapan mesti natural dan tidak terlalu keras atau robotik.
"""

    return gemini_generate(
        prompt,
        model=LLM_MODEL
    )


# ============================================================
# REPLACE SOURCE TAGS
# ============================================================

def replace_source_tags(
    answer,
    sources
):

    pattern = r"\[S(\d+)\]"

    def replace(match):

        index = (
            int(match.group(1))
            - 1
        )

        if (
            index < 0
            or index >= len(sources)
        ):
            return match.group(0)

        source = sources[index]

        book = (
            source.get("book")
            or "Rujukan Turath"
        )

        page = (
            source.get("page")
            or ""
        )

        if page:

            return (
                f"({book}, hlm. {page})"
            )

        return f"({book})"

    return re.sub(
        pattern,
        replace,
        answer
    )


# ============================================================
# REFERENCES
# ============================================================

def build_references(
    sources
):

    lines = [
        "",
        "📚 RUJUKAN TURATH"
    ]

    count = 0

    for source in sources:

        book = (
            source.get("book")
            or ""
        ).strip()

        author = (
            source.get("author")
            or ""
        ).strip()

        page = (
            source.get("page")
            or ""
        ).strip()

        url = (
            source.get("url")
            or ""
        ).strip()

        if not book and not url:
            continue

        count += 1

        if not book:
            book = "Rujukan Turath"

        lines.append(
            f"{count}. {book}"
        )

        if author:

            lines.append(
                f"   👤 {author}"
            )

        if page:

            lines.append(
                f"   📄 Hlm. {page}"
            )

        if url:

            lines.append(
                f"   🔗 {url}"
            )

    if count == 0:

        lines.append(
            "• Tiada metadata kitab yang tersedia."
        )

    return "\n".join(lines)


# ============================================================
# COMPLETE ANSWER PIPELINE
# ============================================================

def answer_question(
    question
):

    print("=" * 50)
    print(
        "❓ SOALAN:",
        question
    )

    # 1. Gemini faham soalan
    plan = plan_turath_queries(
        question
    )

    queries = plan.get(
        "queries",
        []
    )

    if not queries:
        queries = [question]

    # 2. Turath
    raw_sources = search_turath(
        queries
    )

    # 3. Normalize
    sources = normalize_sources(
        raw_sources
    )

    # 4. Rank
    sources = rank_sources(
        sources,
        question
    )

    print(
        "📚 FINAL SOURCES:",
        len(sources)
    )

    for i, source in enumerate(
        sources,
        start=1
    ):

        print(
            f"[S{i}]",
            source.get("book"),
            "|",
            source.get("author"),
            "|",
            source.get("page"),
            "|",
            source.get("book_id")
        )

    # 5. Gemini jawab
    answer = generate_fiqh_answer(
        question,
        sources
    )

    # 6. Citation
    answer = replace_source_tags(
        answer,
        sources
    )

    # 7. References
    references = build_references(
        sources
    )

    print("=" * 50)

    return (
        answer
        + "\n\n"
        + references
    )


# ============================================================
# TELEGRAM LONG MESSAGE
# ============================================================

async def send_long_message(
    update,
    text
):

    if not text:
        return

    while len(text) > TELEGRAM_MAX_CHARS:

        split_at = text.rfind(
            "\n",
            0,
            TELEGRAM_MAX_CHARS
        )

        if split_at <= 0:
            split_at = TELEGRAM_MAX_CHARS

        chunk = text[
            :split_at
        ]

        text = text[
            split_at:
        ].lstrip()

        await update.message.reply_text(
            chunk
        )

    if text:

        await update.message.reply_text(
            text
        )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "📩 /start received from Telegram"
    )

    await update.message.reply_text(
        "السلام عليكم 👋\n\n"
        "Selamat datang ke TanyaFiqihBot.\n\n"
        "📚 Saya membantu mencari jawapan "
        "soalan fiqh berdasarkan rujukan Turath.\n\n"
        "Silakan taip soalan fiqh anda."
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

async def telegram_answer(
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
        "📩 TELEGRAM MESSAGE:",
        question
    )

    # ----------------------------------------
    # GREETING
    # ----------------------------------------

    if is_simple_greeting(question):

        print(
            "👋 GREETING DETECTED"
        )

        await update.message.reply_text(
            greeting_response(question)
        )

        return

    # ----------------------------------------
    # STATUS
    # ----------------------------------------

    status_message = await update.message.reply_text(
        "🔎 Baik, saya sedang memahami soalan anda "
        "dan menyemak rujukan Turath yang berkaitan..."
    )

    try:

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        await send_long_message(
            update,
            answer
        )

    except Exception as error:

        print(
            "❌ ANSWER ERROR:",
            repr(error)
        )

        await update.message.reply_text(
            "⚠️ Maaf, berlaku masalah ketika "
            "mencari rujukan Turath.\n\n"
            "Sila cuba semula sebentar lagi."
        )

    finally:

        try:

            await status_message.delete()

        except Exception:
            pass


# ============================================================
# TELEGRAM ERROR
# ============================================================

async def telegram_error_handler(
    update,
    context
):

    print(
        "❌ TELEGRAM ERROR:",
        repr(context.error)
    )


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

telegram_app = None


def create_telegram_app():

    if not TELEGRAM_TOKEN:

        raise RuntimeError(
            "TELEGRAM_TOKEN belum ditetapkan."
        )

    print(
        "🤖 Creating Telegram application..."
    )

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            telegram_answer
        )
    )

    application.add_error_handler(
        telegram_error_handler
    )

    return application


# ============================================================
# TELEGRAM POLLING
# ============================================================

async def run_telegram():

    global telegram_app

    telegram_app = create_telegram_app()

    print(
        "🤖 TELEGRAM: initializing..."
    )

    await telegram_app.initialize()

    print(
        "🤖 TELEGRAM: starting..."
    )

    await telegram_app.start()

    print(
        "🤖 TELEGRAM: starting polling..."
    )

    await telegram_app.updater.start_polling(
        drop_pending_updates=True
    )

    print(
        "✅ TELEGRAM BOT POLLING STARTED"
    )

    # Pastikan thread kekal hidup
    while True:

        await asyncio.sleep(
            3600
        )


def start_telegram():

    try:

        asyncio.run(
            run_telegram()
        )

    except Exception as error:

        print(
            "❌ TELEGRAM START ERROR:",
            repr(error)
        )


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({
        "status": "ok",
        "service": "TanyaFiqihBot"
    })


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({
        "status": "healthy",
        "turath_service": TURATH_SERVICE_URL
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "🚀 Starting Telegram background thread..."
    )

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True
    )

    telegram_thread.start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        f"🌐 Flask starting on port {port}"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
