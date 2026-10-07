import os
import re
import json
import time
import asyncio
import threading
import traceback

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


# =========================================================
# GEMINI
# =========================================================

client = None

if GOOGLE_API_KEY:
    client = genai.Client(
        api_key=GOOGLE_API_KEY
    )


def gemini_generate(
    prompt,
    model=None,
    temperature=0.2
):
    if not client:
        raise RuntimeError(
            "GOOGLE_API_KEY belum ditetapkan."
        )

    model = model or LLM_MODEL

    last_error = None

    for attempt in range(
        GEMINI_RETRIES + 1
    ):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                ),
            )

            text = (
                response.text
                if response
                else ""
            )

            if text and text.strip():
                return text.strip()

        except Exception as e:
            last_error = e

            print(
                f"⚠️ GEMINI ERROR "
                f"{attempt + 1}: {e}"
            )

            if attempt < GEMINI_RETRIES:
                time.sleep(
                    GEMINI_INITIAL_WAIT
                    * (attempt + 1)
                )

    raise last_error or RuntimeError(
        "Gemini gagal menghasilkan jawapan."
    )


# =========================================================
# QUERY MAP
# =========================================================

QUERY_MAP = {
    "puasa": "الصيام",
    "puasa ramadan": "صيام رمضان",
    "zakat": "الزكاة",
    "zakat fitrah": "زكاة الفطر",

    "solat": "الصلاة",
    "sembahyang": "الصلاة",

    "wuduk": "الوضوء",
    "wudhu": "الوضوء",

    "taharah": "الطهارة",
    "bersuci": "الطهارة",

    "tayamum": "التيمم",

    "mandi wajib": "الغسل",
    "mandi junub": "غسل الجنابة",
    "mandi haid": "غسل الحيض",

    "junub": "الجنابة",
    "haid": "الحيض",
    "nifas": "النفاس",
    "istihadah": "الاستحاضة",

    "qunut": "القنوت",
    "qunut subuh": "القنوت في صلاة الصبح",

    "solat subuh": "صلاة الصبح",
    "solat jumaat": "صلاة الجمعة",
    "jumaat": "صلاة الجمعة",

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


def apply_query_map(text):
    result = text

    for malay, arabic in sorted(
        QUERY_MAP.items(),
        key=lambda x: len(x[0]),
        reverse=True
    ):
        result = re.sub(
            rf"\b{re.escape(malay)}\b",
            f" {arabic} ",
            result,
            flags=re.IGNORECASE
        )

    return result


# =========================================================
# GEMINI 1
# ANALISIS SOALAN + MULTI QUERY
# =========================================================

def build_query_planner_prompt(question):

    return f"""
Anda ialah perancang carian untuk bot fiqh Islam.

Soalan pengguna:

{question}

Tugas anda BUKAN menjawab soalan.

Anda mesti:
1. Fahami isu fiqh sebenar.
2. Kenal pasti hukum yang ingin dicari.
3. Kenal pasti istilah fiqh Arab yang paling tepat.
4. Pecahkan isu kepada beberapa aspek carian.
5. Hasilkan 3 hingga 8 query Arab yang sesuai untuk mencari
   kitab-kitab fiqh turath.
6. Jika terdapat kemungkinan khilaf, masukkan query untuk:
   - hukum utama
   - syarat
   - perkara yang membatalkan/tidak memadai
   - khilaf ulama
   - mazhab lain jika relevan.

Jangan jawab soalan.
Jangan cipta nama kitab.
Jangan cipta URL.
Jangan cipta halaman.

HANYA keluarkan JSON:

{{
  "isu": "ringkasan isu fiqh dalam Bahasa Melayu",
  "kata_kunci": [
    "istilah Arab",
    "istilah Arab"
  ],
  "queries": [
    "query Arab 1",
    "query Arab 2",
    "query Arab 3"
  ]
}}
"""


def parse_json_response(text):

    text = text.strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if match:
        text = match.group(0)

    return json.loads(text)


def plan_turath_queries(question):

    prompt = build_query_planner_prompt(
        question
    )

    raw = gemini_generate(
        prompt,
        model=ARABIC_QUERY_MODEL,
        temperature=0.1
    )

    print("\n🧠 GEMINI QUERY PLANNER:")
    print(raw)

    try:
        data = parse_json_response(raw)

    except Exception as e:
        print(
            f"⚠️ JSON PLANNER ERROR: {e}"
        )

        fallback = apply_query_map(
            question
        )

        return {
            "isu": question,
            "kata_kunci": [],
            "queries": [
                fallback,
                question
            ],
        }

    queries = data.get(
        "queries",
        []
    )

    if not isinstance(
        queries,
        list
    ):
        queries = []

    cleaned = []

    for q in queries:
        if not isinstance(q, str):
            continue

        q = q.strip()

        if q and q not in cleaned:
            cleaned.append(q)

    # Pastikan sekurang-kurangnya query
    # daripada istilah yang diketahui.
    mapped = apply_query_map(
        question
    )

    if mapped and mapped not in cleaned:
        cleaned.append(mapped)

    cleaned = cleaned[:8]

    data["queries"] = cleaned

    print(
        "\n🔎 QUERY TURATH:"
    )

    for i, q in enumerate(
        cleaned,
        1
    ):
        print(
            f"{i}. {q}"
        )

    return data


# =========================================================
# TURATH SEARCH
# =========================================================

def turath_search(
    queries,
    category="shafii"
):

    if not queries:
        return []

    url = (
        f"{TURATH_SERVICE_URL}/search"
    )

    payload = {
        "queries": queries,
        "category": category,
    }

    print(
        "\n📡 TURATH REQUEST:"
    )

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        )
    )

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=90
        )

        print(
            f"📡 TURATH STATUS: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        print(
            f"❌ TURATH REQUEST ERROR: {e}"
        )
        return []

    results = []

    for key in [
        "results",
        "sources",
        "data",
        "items",
        "hits",
    ]:
        value = data.get(key)

        if isinstance(
            value,
            list
        ):
            results.extend(value)

    # Deduplicate response arrays
    unique = []

    seen = set()

    for source in results:

        if not isinstance(
            source,
            dict
        ):
            continue

        text = str(
            source.get(
                "text",
                ""
            )
        ).strip()

        if not text:
            continue

        key = (
            source.get("book_id", ""),
            source.get("page", ""),
            text[:250],
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            source
        )

    print(
        f"📚 TURATH SOURCES: "
        f"{len(unique)}"
    )

    return unique[:TARGET_SOURCES]


# =========================================================
# SOURCE RANKING
# =========================================================

def source_score(source):

    score = 0

    text = str(
        source.get(
            "text",
            ""
        )
    )

    book = str(
        source.get(
            "book",
            ""
        )
    )

    author = str(
        source.get(
            "author",
            ""
        )
    )

    if len(text) >= 200:
        score += 3

    if len(text) >= 500:
        score += 2

    if book:
        score += 2

    if author:
        score += 2

    if source.get("page") not in [
        "",
        None,
    ]:
        score += 2

    if source.get("book_id"):
        score += 1

    if source.get("url"):
        score += 1

    return score


def rank_sources(sources):

    ranked = sorted(
        sources,
        key=source_score,
        reverse=True
    )

    return ranked[
        :TARGET_SOURCES
    ]


# =========================================================
# SOURCE CONTEXT
# =========================================================

def build_source_context(
    sources
):

    blocks = []

    total_chars = 0

    for index, source in enumerate(
        sources,
        1
    ):

        text = str(
            source.get(
                "text",
                ""
            )
        ).strip()

        if not text:
            continue

        text = text[
            :SOURCE_MAX_CHARS
        ]

        block = f"""
[S{index}]
KITAB: {source.get("book", "")}
PENGARANG: {source.get("author", "")}
HALAMAN: {source.get("page", "")}
BOOK_ID: {source.get("book_id", "")}
KATEGORI: {source.get("category", "")}

PETIKAN:
{text}
"""

        if (
            total_chars +
            len(block)
            >
            CONTEXT_MAX_CHARS
        ):
            break

        blocks.append(
            block.strip()
        )

        total_chars += len(block)

    return "\n\n".join(
        blocks
    )


# =========================================================
# GEMINI 2
# JAWAPAN BERDASARKAN TURATH
# =========================================================

def build_answer_prompt(
    question,
    planner,
    sources
):

    context = build_source_context(
        sources
    )

    return f"""
Anda ialah penyusun jawapan fiqh untuk TanyaFiqihBot.

SOALAN PENGGUNA:
{question}

ANALISIS SOALAN:
{planner.get("isu", "")}

SUMBER TURATH:
{context}

TUGAS:

Jawab soalan pengguna dalam Bahasa Melayu.

WAJIB:
1. Gunakan HANYA maklumat daripada SUMBER TURATH di atas.
2. Jangan gunakan pengetahuan luar sebagai sumber.
3. Jangan reka nama kitab.
4. Jangan reka nama pengarang.
5. Jangan reka nombor halaman.
6. Jangan reka URL.
7. Jika sesuatu pandangan tidak terdapat dalam sumber, jangan dakwa ia terdapat.
8. Jika terdapat khilaf yang disokong sumber, jelaskan.
9. Jika sumber tidak mencukupi untuk sesuatu dakwaan, nyatakan keterbatasan.
10. Setiap dakwaan penting yang berasal daripada sumber mesti disertakan
    tag seperti [S1], [S2], [S3].
11. Jangan buat bahagian "Rujukan Turath" sendiri.
    Sistem akan membinanya secara automatik.
12. Jangan tulis URL.

FORMAT:

📖 JAWAPAN

Jawapan hukum yang paling penting.

📚 HURAIAN

Huraian berdasarkan kitab Turath.

🔹 PERINCIAN

1. ...
   ...

2. ...
   ...

⚖️ PERBEZAAN PANDANGAN

Hanya jika benar-benar terdapat khilaf dalam sumber.

PENTING:
Tag [S1], [S2] dan seterusnya mesti dikekalkan tepat.
Jangan ubah tag tersebut.
"""


def generate_final_answer(
    question,
    planner,
    sources
):

    if not sources:
        return (
            "⚠️ Tiada kandungan Turath "
            "yang mencukupi untuk menghasilkan "
            "huraian."
        )

    prompt = build_answer_prompt(
        question,
        planner,
        sources
    )

    answer = gemini_generate(
        prompt,
        model=LLM_MODEL,
        temperature=0.2
    )

    return answer.strip()


# =========================================================
# REPLACE [S1]
# =========================================================

def replace_source_tags(
    answer,
    sources
):

    def replace(match):

        number = int(
            match.group(1)
        )

        index = number - 1

        if (
            index < 0
            or index >= len(sources)
        ):
            return match.group(0)

        source = sources[index]

        book = source.get(
            "book",
            "Kitab Turath"
        )

        page = source.get(
            "page",
            ""
        )

        citation = (
            f"({book}"
        )

        if page:
            citation += (
                f", hlm. {page}"
            )

        citation += ")"

        return citation

    return re.sub(
        r"\[S(\d+)\]",
        replace,
        answer
    )


# =========================================================
# REFERENCES
# =========================================================

def build_references(
    sources
):

    if not sources:
        return (
            "📚 RUJUKAN TURATH\n"
            "• Tiada rujukan ditemui."
        )

    lines = [
        "",
        "📚 RUJUKAN TURATH",
        "",
    ]

    for index, source in enumerate(
        sources,
        1
    ):

        book = str(
            source.get(
                "book",
                ""
            )
        ).strip()

        author = str(
            source.get(
                "author",
                ""
            )
        ).strip()

        page = str(
            source.get(
                "page",
                ""
            )
        ).strip()

        url = str(
            source.get(
                "url",
                ""
            )
        ).strip()

        if not book:
            book = "Kitab Turath"

        lines.append(
            f"{index}. {book}"
        )

        if author:
            lines.append(
                f"   ✍️ {author}"
            )

        if page:
            lines.append(
                f"   📄 Hlm. {page}"
            )

        # URL hanya dipaparkan jika ada.
        # Jangan tulis URL palsu.
        if url:
            lines.append(
                f"   🔗 {url}"
            )

        lines.append("")

    return "\n".join(
        lines
    ).strip()


# =========================================================
# MAIN ANSWER
# =========================================================

def answer_question(
    question
):

    print(
        "\n========================================"
    )

    print(
        "👤 SOALAN:",
        question
    )

    # -------------------------------------
    # 1. GEMINI ANALYSE
    # -------------------------------------

    planner = plan_turath_queries(
        question
    )

    queries = planner.get(
        "queries",
        []
    )

    # -------------------------------------
    # 2. TURATH
    # -------------------------------------

    sources = turath_search(
        queries,
        category="shafii"
    )

    # -------------------------------------
    # 3. RANK
    # -------------------------------------

    sources = rank_sources(
        sources
    )

    print(
        f"🏆 SUMBER AKHIR: "
        f"{len(sources)}"
    )

    # -------------------------------------
    # 4. GEMINI ANSWER
    # -------------------------------------

    if not sources:
        return (
            "⚠️ Tiada kandungan sumber yang "
            "mencukupi untuk menghasilkan "
            "huraian.\n\n"
            "📚 RUJUKAN TURATH\n"
            "• Tiada rujukan ditemui."
        )

    answer = generate_final_answer(
        question,
        planner,
        sources
    )

    # -------------------------------------
    # 5. REPLACE [S#]
    # -------------------------------------

    answer = replace_source_tags(
        answer,
        sources
    )

    # -------------------------------------
    # 6. REFERENCES
    # -------------------------------------

    references = build_references(
        sources
    )

    final = (
        answer.strip()
        + "\n\n"
        + references
    )

    print(
        "\n========================================"
    )

    print(final)

    print(
        "========================================"
    )

    return final


# =========================================================
# TELEGRAM
# =========================================================

telegram_app = None


async def telegram_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    question = (
        update.message.text or ""
    ).strip()

    if not question:
        return

    await update.message.reply_text(
        "🔎 Sedang memahami soalan dan mencari rujukan Turath..."
    )

    try:

        answer = await asyncio.to_thread(
            answer_question,
            question
        )

        # Telegram limit
        chunks = []

        while len(answer) > TELEGRAM_MAX_CHARS:
            cut = answer.rfind(
                "\n",
                0,
                TELEGRAM_MAX_CHARS
            )

            if cut <= 0:
                cut = TELEGRAM_MAX_CHARS

            chunks.append(
                answer[:cut]
            )

            answer = answer[cut:].lstrip()

        if answer:
            chunks.append(answer)

        for chunk in chunks:

            await update.message.reply_text(
                chunk
            )

    except Exception as e:

        print(
            "❌ TELEGRAM ERROR:"
        )

        traceback.print_exc()

        await update.message.reply_text(
            "⚠️ Berlaku ralat semasa "
            "memproses soalan."
        )


async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "Assalamualaikum 👋\n\n"
        "Selamat datang ke TanyaFiqihBot.\n"
        "Sila ajukan soalan fiqh anda."
    )


def start_telegram():

    global telegram_app

    if not TELEGRAM_TOKEN:
        print(
            "⚠️ TELEGRAM_TOKEN tiada."
        )
        return

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
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            telegram_answer
        )
    )

    print(
        "🚀 TELEGRAM BOT STARTING..."
    )

    telegram_app.run_polling(
        drop_pending_updates=True,
        stop_signals=None,
    )


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():

    return jsonify({
        "ok": True,
        "service": "TanyaFiqihBot",
        "turath": TURATH_SERVICE_URL,
        "target_sources": TARGET_SOURCES,
    })


@app.route("/health")
def health():

    turath_ok = False

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/health",
            timeout=5
        )

        turath_ok = (
            response.status_code == 200
        )

    except Exception:
        turath_ok = False

    return jsonify({
        "ok": True,
        "turath": turath_ok,
        "target_sources": TARGET_SOURCES,
    })


@app.route("/ask", methods=["POST"])
def ask():

    data = request.get_json(
        silent=True
    ) or {}

    question = str(
        data.get(
            "question",
            ""
        )
    ).strip()

    if not question:
        return jsonify({
            "ok": False,
            "error": "question diperlukan",
        }), 400

    try:

        answer = answer_question(
            question
        )

        return jsonify({
            "ok": True,
            "question": question,
            "answer": answer,
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/search", methods=["POST"])
def search_route():

    data = request.get_json(
        silent=True
    ) or {}

    question = str(
        data.get(
            "question",
            ""
        )
    ).strip()

    if not question:
        return jsonify({
            "ok": False,
            "error": "question diperlukan",
        }), 400

    planner = plan_turath_queries(
        question
    )

    sources = turath_search(
        planner["queries"]
    )

    sources = rank_sources(
        sources
    )

    return jsonify({
        "ok": True,
        "question": question,
        "planner": planner,
        "count": len(sources),
        "sources": sources,
    })


# =========================================================
# START TELEGRAM WHEN GUNICORN IMPORTS APP
# =========================================================

if TELEGRAM_TOKEN:

    telegram_thread = threading.Thread(
        target=start_telegram,
        daemon=True
    )

    telegram_thread.start()


# =========================================================
# LOCAL
# =========================================================

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
        debug=False
    )
