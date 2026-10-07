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


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


# =========================================================
# GEMINI
# =========================================================

client = None

if GOOGLE_API_KEY:
    try:
        client = genai.Client(
            api_key=GOOGLE_API_KEY
        )

        print("✅ GEMINI CLIENT READY")

    except Exception as e:
        print(
            "❌ GEMINI CLIENT ERROR:",
            e
        )

else:
    print(
        "⚠️ GOOGLE_API_KEY tidak tersedia."
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
    "mandi selepas haid": "غسل الحيض",
    "mandi haid": "غسل الحيض",
    "mandi nifas": "غسل النفاس",

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


# =========================================================
# QUERY MAP
# =========================================================

def apply_query_map(query: str) -> str:
    original = str(query or "").strip()

    if not original:
        return ""

    lower = original.lower()

    if lower in QUERY_MAP:
        return QUERY_MAP[lower]

    for key in sorted(
        QUERY_MAP.keys(),
        key=len,
        reverse=True,
    ):
        if key in lower:
            return (
                f"{QUERY_MAP[key]} {original}"
            ).strip()

    return original


# =========================================================
# MADHHAB COMPARISON
# =========================================================

def is_madhhab_comparison(
    query: str,
) -> bool:

    q = str(query or "").lower()

    comparison_phrases = [
        "banding mazhab",
        "perbandingan mazhab",
        "beza mazhab",
        "perbezaan mazhab",
        "mazhab mana",
        "semua mazhab",
        "4 mazhab",
        "empat mazhab",
        "hanafi dan syafie",
        "hanafi dan maliki",
        "hanafi dan hanbali",
        "syafie dan maliki",
        "syafie dan hanbali",
        "maliki dan hanbali",
    ]

    if any(
        phrase in q
        for phrase in comparison_phrases
    ):
        return True

    madhhabs = [
        "hanafi",
        "maliki",
        "syafie",
        "syafi'i",
        "syafii",
        "hanbali",
    ]

    found = [
        m
        for m in madhhabs
        if m in q
    ]

    return len(found) >= 2


# =========================================================
# GEMINI GENERATE
# =========================================================

def gemini_generate(
    prompt: str,
    model: str | None = None,
) -> str:

    global client

    if client is None:
        raise RuntimeError(
            "Gemini client tidak tersedia."
        )

    selected_model = (
        model
        or LLM_MODEL
    )

    last_error = None

    models = [selected_model]

    if (
        FALLBACK_LLM_MODEL
        and FALLBACK_LLM_MODEL
        != selected_model
    ):
        models.append(
            FALLBACK_LLM_MODEL
        )

    for current_model in models:

        for attempt in range(
            GEMINI_RETRIES + 1
        ):

            try:

                response = client.models.generate_content(
                    model=current_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                    ),
                )

                text = (
                    getattr(
                        response,
                        "text",
                        None,
                    )
                    or ""
                ).strip()

                if text:
                    return text

                raise RuntimeError(
                    "Gemini menghasilkan response kosong."
                )

            except Exception as e:

                last_error = e

                print(
                    f"⚠️ GEMINI ERROR "
                    f"[{current_model}] "
                    f"attempt={attempt + 1}: "
                    f"{e}"
                )

                if attempt < GEMINI_RETRIES:
                    wait = (
                        GEMINI_INITIAL_WAIT
                        * (2 ** attempt)
                    )

                    time.sleep(wait)

    raise RuntimeError(
        f"Gemini gagal: {last_error}"
    )


# =========================================================
# TRANSLATE TO ARABIC
# =========================================================

def translate_to_arabic(
    question: str,
) -> str:

    mapped = apply_query_map(
        question
    )

    prompt = f"""
Anda ialah enjin carian fiqh untuk kitab Turath.

Soalan pengguna dalam Bahasa Melayu:

{question}

Kata kunci Turath yang telah dipetakan:

{mapped}

Tugas:
Tukarkan soalan kepada query Bahasa Arab yang
ringkas dan sangat sesuai untuk carian kitab fiqh.

Jangan jawab soalan.
Jangan beri penerangan.
Hanya keluarkan query Bahasa Arab.

Contoh:
"apa hukum qunut subuh"
→ القنوت في صلاة الصبح

"mandi wajib selepas haid"
→ غسل الحيض

"cara mandi wajib"
→ صفة الغسل
"""

    try:

        result = gemini_generate(
            prompt,
            model=ARABIC_QUERY_MODEL,
        )

        return result.strip()

    except Exception as e:

        print(
            "⚠️ Arabic translation gagal:",
            e,
        )

        return mapped


# =========================================================
# TURATH SEARCH
# =========================================================

def turath_search(
    question: str,
    comparison: bool = False,
):

    arabic_query = translate_to_arabic(
        question
    )

    print(
        f"🔎 TURATH QUERY: {arabic_query}"
    )

    params = {
        "q": arabic_query,
        "query": arabic_query,
    }

    if comparison:
        params["comparison"] = "true"

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/search",
            params=params,
            timeout=60,
        )

        print(
            f"📡 TURATH STATUS: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:

        print(
            "❌ TURATH SEARCH ERROR:",
            e,
        )

        return []


    # -----------------------------------------------------
    # DEBUG
    # -----------------------------------------------------

    try:
        print(
            "🧪 TURATH RESPONSE KEYS:",
            list(data.keys())
            if isinstance(data, dict)
            else type(data),
        )

    except Exception:
        pass


    # -----------------------------------------------------
    # SUPPORT SEMUA FORMAT
    # -----------------------------------------------------

    sources = []

    if isinstance(data, list):

        sources = data

    elif isinstance(data, dict):

        candidates = [
            data.get("results"),
            data.get("sources"),
            data.get("data"),
            data.get("items"),
            data.get("hits"),
        ]

        for candidate in candidates:

            if isinstance(
                candidate,
                list,
            ):
                sources = candidate
                break

            if (
                isinstance(candidate, dict)
                and isinstance(
                    candidate.get("results"),
                    list,
                )
            ):
                sources = candidate[
                    "results"
                ]
                break


    print(
        f"📚 TURATH SOURCES: "
        f"{len(sources)}"
    )


    # -----------------------------------------------------
    # NORMALIZE
    # -----------------------------------------------------

    normalized = normalize_sources(
        sources
    )

    print(
        f"📚 TURATH NORMALIZED: "
        f"{len(normalized)}"
    )

    return normalized


# =========================================================
# SAFE VALUE
# =========================================================

def first_value(
    obj,
    keys,
):

    if not isinstance(
        obj,
        dict,
    ):
        return None

    for key in keys:

        value = obj.get(key)

        if value not in (
            None,
            "",
        ):
            return value

    return None


# =========================================================
# NORMALIZE SOURCES
# =========================================================

def normalize_sources(
    sources,
):

    result = []

    if not isinstance(
        sources,
        list,
    ):
        return result


    for item in sources:

        if not isinstance(
            item,
            dict,
        ):
            continue


        # -------------------------------------------------
        # TEXT
        # -------------------------------------------------

        text = first_value(
            item,
            [
                "text",
                "content",
                "body",
                "snippet",
                "passage",
                "description",
            ],
        )

        if isinstance(
            text,
            dict,
        ):
            text = first_value(
                text,
                [
                    "text",
                    "content",
                    "body",
                ],
            )

        if isinstance(
            text,
            list,
        ):
            text = "\n".join(
                str(x)
                for x in text
                if x
            )

        text = str(
            text or ""
        ).strip()


        # -------------------------------------------------
        # BOOK
        # -------------------------------------------------

        book = first_value(
            item,
            [
                "book",
                "book_title",
                "bookTitle",
                "book_name",
                "bookName",
                "title_book",
            ],
        )

        if isinstance(
            book,
            dict,
        ):
            book = first_value(
                book,
                [
                    "title",
                    "name",
                    "book_title",
                ],
            )

        book = str(
            book or ""
        ).strip()


        # -------------------------------------------------
        # AUTHOR
        # -------------------------------------------------

        author = first_value(
            item,
            [
                "author",
                "author_name",
                "authorName",
                "writer",
            ],
        )

        if isinstance(
            author,
            dict,
        ):
            author = first_value(
                author,
                [
                    "name",
                    "title",
                    "author",
                ],
            )

        author = str(
            author or ""
        ).strip()


        # -------------------------------------------------
        # PAGE
        # -------------------------------------------------

        page = first_value(
            item,
            [
                "page",
                "page_number",
                "pageNumber",
                "pageno",
                "pageNo",
            ],
        )


        # -------------------------------------------------
        # BOOK ID
        # -------------------------------------------------

        book_id = first_value(
            item,
            [
                "book_id",
                "bookId",
                "bookID",
                "bookid",
            ],
        )


        # -------------------------------------------------
        # URL
        # -------------------------------------------------

        url = first_value(
            item,
            [
                "url",
                "link",
                "href",
                "source_url",
                "sourceUrl",
            ],
        )

        url = str(
            url or ""
        ).strip()


        # -------------------------------------------------
        # CATEGORY
        # -------------------------------------------------

        category = first_value(
            item,
            [
                "category",
                "madhhab",
                "mazhab",
            ],
        )

        category = str(
            category or ""
        ).strip()


        # -------------------------------------------------
        # IF NO TEXT, SKIP
        # -------------------------------------------------

        if not text:

            # cuba nested result
            nested = item.get(
                "result"
            )

            if isinstance(
                nested,
                dict,
            ):
                text = str(
                    first_value(
                        nested,
                        [
                            "text",
                            "content",
                            "body",
                            "snippet",
                        ],
                    )
                    or ""
                ).strip()

        if not text:
            continue


        # -------------------------------------------------
        # LIMIT SOURCE
        # -------------------------------------------------

        if len(text) > SOURCE_MAX_CHARS:
            text = text[
                :SOURCE_MAX_CHARS
            ]


        result.append(
            {
                "text": text,
                "book": book
                or "Kitab tidak diketahui",
                "author": author
                or "Pengarang tidak diketahui",
                "page": page,
                "book_id": book_id,
                "url": url,
                "category": category,
            }
        )


    return result


# =========================================================
# RELEVANCE
# =========================================================

def relevance_score(
    source,
    question,
):

    text = (
        source.get("text")
        or ""
    ).lower()

    q = str(
        question or ""
    ).lower()

    score = 0

    words = re.findall(
        r"[\w\u0600-\u06FF]+",
        q,
    )

    for word in words:

        if len(word) >= 3 and word in text:
            score += 1

    return score


def rank_sources(
    sources,
    question,
):

    return sorted(
        sources,
        key=lambda x:
            relevance_score(
                x,
                question,
            ),
        reverse=True,
    )


# =========================================================
# BUILD TURATH CONTEXT
# =========================================================

def build_source_context(
    sources,
):

    blocks = []

    total = 0

    for index, source in enumerate(
        sources,
        start=1,
    ):

        text = (
            source.get("text")
            or ""
        )

        block = f"""
SUMBER TURATH #{index}

KITAB:
{source.get("book", "")}

PENGARANG:
{source.get("author", "")}

HALAMAN:
{source.get("page", "Tidak diketahui")}

BOOK ID:
{source.get("book_id", "")}

KATEGORI:
{source.get("category", "")}

URL:
{source.get("url", "")}

TEKS:
{text}

ID SUMBER:
[S{index}]
""".strip()

        if (
            total + len(block)
            > CONTEXT_MAX_CHARS
        ):
            break

        blocks.append(block)

        total += len(block)

    return "\n\n".join(
        blocks
    )


# =========================================================
# SOURCE TAG REPLACEMENT
# =========================================================

def replace_source_tags(
    text,
    sources,
):

    if not text:
        return text

    def replace(match):

        number = int(
            match.group(1)
        )

        index = number - 1

        if index < 0 or index >= len(
            sources
        ):
            return ""

        source = sources[index]

        book = source.get(
            "book"
        ) or "Kitab tidak diketahui"

        page = source.get(
            "page"
        )

        if page:
            return (
                f"*{book}*, "
                f"hlm. {page}"
            )

        return f"*{book}*"

    return re.sub(
        r"\[S(\d+)\]",
        replace,
        text,
    )


# =========================================================
# CLEAN ANSWER
# =========================================================

def clean_answer(
    text,
):

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"```(?:text|markdown)?",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = text.replace(
        "```",
        "",
    )

    # Buang rujukan buatan Gemini.
    # Rujukan sebenar dibina Python.
    text = re.sub(
        r"📚\s*RUJUKAN TURATH.*",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return text.strip()


# =========================================================
# BUILD REFERENCES
# =========================================================

def build_references(
    sources,
):

    if not sources:
        return (
            "📚 RUJUKAN TURATH\n\n"
            "• Tiada rujukan ditemui."
        )

    lines = [
        "📚 RUJUKAN TURATH",
        "",
    ]

    for index, source in enumerate(
        sources,
        start=1,
    ):

        book = source.get(
            "book"
        ) or "Kitab tidak diketahui"

        author = source.get(
            "author"
        ) or "Pengarang tidak diketahui"

        page = source.get(
            "page"
        )

        url = source.get(
            "url"
        )

        lines.append(
            f"{index}. {book}"
        )

        lines.append(
            f"   ✍️ {author}"
        )

        if page:
            lines.append(
                f"   📄 Hlm. {page}"
            )

        if url:
            lines.append(
                f"   🔗 {url}"
            )

        lines.append("")

    return "\n".join(
        lines
    ).strip()


# =========================================================
# GENERATE FIQH ANSWER
# =========================================================

def generate_fiqh_answer(
    question,
    sources,
    comparison=False,
):

    if not sources:

        return (
            "⚠️ Tiada kandungan sumber "
            "yang mencukupi untuk "
            "menghasilkan huraian."
        )


    context = build_source_context(
        sources
    )

    comparison_instruction = ""

    if comparison:

        comparison_instruction = """
SOALAN INI MEMINTA PERBANDINGAN MAZHAB.

Jika sumber benar-benar menunjukkan
perbezaan pandangan, jelaskan perbezaan
antara mazhab berdasarkan sumber.

Jangan cipta perbezaan yang tiada dalam
sumber.
"""


    prompt = f"""
Anda ialah pembantu fiqh Bahasa Melayu
yang menggunakan kitab Turath sebagai
satu-satunya sumber hukum.

SOALAN PENGGUNA:

{question}


SUMBER TURATH:

{context}


PERATURAN PALING PENTING:

1. Hanya gunakan maklumat yang terdapat
   dalam SUMBER TURATH.

2. Jangan gunakan pengetahuan luar.

3. Jangan reka nama kitab.

4. Jangan reka nama pengarang.

5. Jangan reka nombor halaman.

6. Jangan reka URL.

7. Jika maklumat tidak mencukupi,
   nyatakan bahawa sumber tidak mencukupi.

8. Setiap dakwaan penting dalam HURAIAN
   mesti disokong dengan [S1], [S2],
   [S3] dan sebagainya.

9. Jangan tulis [S99] atau ID sumber
   yang tidak wujud.

10. Jangan buat bahagian RUJUKAN TURATH.
    Bahagian itu akan dibuat oleh sistem.

11. Jika sumber hanya daripada mazhab
    Syafie, jangan mendakwa pandangan
    mazhab lain kecuali sumber memang
    menyebutnya.

12. Bezakan antara hukum, syarat,
    sunat, makruh, batal dan sebagainya
    dengan tepat berdasarkan sumber.


FORMAT JAWAPAN:

📖 JAWAPAN

Berikan hukum paling penting
secara terus dan ringkas.

📚 HURAIAN

Terangkan hukum berdasarkan sumber
Turath.

Masukkan rujukan dalam bentuk:

Menurut *[S1]*, ...

atau:

Perkara ini turut dihuraikan dalam
*[S2]*.

Jangan reka nama kitab kerana [S1]
akan ditukar oleh sistem kepada nama
kitab sebenar.


🔹 PERINCIAN

1. Poin pertama
   Huraian. [S1]

2. Poin kedua
   Huraian. [S2]

3. Poin ketiga
   Huraian. [S1][S3]


⚖️ PERBEZAAN PANDANGAN

Hanya tulis bahagian ini jika memang
terdapat khilaf yang disokong oleh sumber.

{comparison_instruction}

Jangan masukkan bahagian kosong.
"""


    answer = gemini_generate(
        prompt
    )

    answer = clean_answer(
        answer
    )

    answer = replace_source_tags(
        answer,
        sources,
    )

    return answer


# =========================================================
# ANSWER QUESTION
# =========================================================

def answer_question(
    question,
):

    question = str(
        question or ""
    ).strip()

    if not question:
        return (
            "Sila masukkan soalan fiqh."
        )


    comparison = is_madhhab_comparison(
        question
    )

    print("")
    print(
        "=============================================="
    )
    print(
        f"❓ SOALAN: {question}"
    )
    print(
        f"⚖️ COMPARISON: {comparison}"
    )


    sources = turath_search(
        question,
        comparison=comparison,
    )


    if not sources:

        return (
            "⚠️ Tiada kandungan sumber "
            "yang mencukupi untuk "
            "menghasilkan huraian.\n\n"
            "📚 Rujukan:\n"
            "• Tiada rujukan ditemui."
        )


    sources = rank_sources(
        sources,
        question,
    )


    # Maksimum 10 sumber
    sources = sources[:10]


    print(
        f"📚 FINAL SOURCES: "
        f"{len(sources)}"
    )


    answer = generate_fiqh_answer(
        question,
        sources,
        comparison=comparison,
    )


    references = build_references(
        sources
    )


    final_answer = (
        f"{answer}\n\n"
        f"{references}"
    )


    return final_answer.strip()


# =========================================================
# TELEGRAM SPLIT
# =========================================================

def split_telegram_message(
    text,
    max_length=None,
):

    max_length = (
        max_length
        or TELEGRAM_MAX_CHARS
    )

    if len(text) <= max_length:
        return [text]

    chunks = []

    current = ""

    paragraphs = text.split(
        "\n\n"
    )

    for paragraph in paragraphs:

        candidate = (
            f"{current}\n\n{paragraph}"
            if current
            else paragraph
        )

        if len(candidate) <= max_length:

            current = candidate

        else:

            if current:
                chunks.append(
                    current
                )

            if len(paragraph) <= max_length:

                current = paragraph

            else:

                start = 0

                while start < len(
                    paragraph
                ):

                    chunks.append(
                        paragraph[
                            start:start + max_length
                        ]
                    )

                    start += max_length

                current = ""

    if current:
        chunks.append(current)

    return chunks


# =========================================================
# TELEGRAM HANDLERS
# =========================================================

async def telegram_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Assalamualaikum 👋\n\n"
        "Saya TanyaFiqihBot.\n"
        "Tanya soalan fiqh anda."
    )


async def telegram_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "📚 Contoh soalan:\n\n"
        "• Apa hukum qunut Subuh?\n"
        "• Bagaimana cara mandi wajib?\n"
        "• Apa hukum membaca al-Fatihah di belakang imam?\n"
        "• Apakah zakat fitrah wajib?\n\n"
        "Saya menggunakan sumber kitab Turath."
    )


async def telegram_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    question = (
        update.message.text
        or ""
    ).strip()

    if not question:
        return

    message = await update.message.reply_text(
        "🔎 Sedang mencari dalam kitab Turath..."
    )

    try:

        # Jalankan kerja sync dalam thread
        # supaya event loop Telegram tidak tersekat.
        answer = await asyncio.to_thread(
            answer_question,
            question,
        )

        chunks = split_telegram_message(
            answer
        )

        if not chunks:
            chunks = [
                "⚠️ Tiada jawapan."
            ]

        await message.edit_text(
            chunks[0],
            parse_mode="Markdown",
        )

        for chunk in chunks[1:]:

            await update.message.reply_text(
                chunk,
                parse_mode="Markdown",
            )

    except Exception as e:

        print(
            "❌ TELEGRAM HANDLER ERROR:"
        )

        traceback.print_exc()

        try:

            await message.edit_text(
                "⚠️ Berlaku ralat ketika "
                "memproses soalan."
            )

        except Exception:
            pass


# =========================================================
# TELEGRAM WORKER
# =========================================================

telegram_thread = None
telegram_app = None


def telegram_worker():

    global telegram_app

    try:

        print(
            "🚀 TELEGRAM WORKER STARTING..."
        )

        telegram_app = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        telegram_app.add_handler(
            CommandHandler(
                "start",
                telegram_start,
            )
        )

        telegram_app.add_handler(
            CommandHandler(
                "help",
                telegram_help,
            )
        )

        telegram_app.add_handler(
            MessageHandler(
                filters.TEXT
                & ~filters.COMMAND,
                telegram_message,
            )
        )

        print(
            "✅ TELEGRAM APPLICATION READY"
        )

        telegram_app.run_polling(
            drop_pending_updates=True,
            stop_signals=None,
        )

    except Exception:

        print(
            "❌ TELEGRAM START ERROR:"
        )

        traceback.print_exc()


def start_telegram():

    global telegram_thread

    if not TELEGRAM_TOKEN:

        print(
            "⚠️ TELEGRAM_TOKEN tidak tersedia."
        )

        return

    if (
        telegram_thread
        and telegram_thread.is_alive()
    ):

        print(
            "ℹ️ Telegram thread sudah berjalan."
        )

        return

    telegram_thread = threading.Thread(
        target=telegram_worker,
        daemon=True,
        name="telegram-bot",
    )

    telegram_thread.start()

    print(
        "🚀 Telegram thread dilancarkan."
    )


# =========================================================
# FLASK ROUTES
# =========================================================

@app.route(
    "/",
    methods=["GET"],
)
def index():

    return jsonify(
        {
            "ok": True,
            "service": "TanyaFiqihBot",
            "turath": TURATH_SERVICE_URL,
        }
    )


@app.route(
    "/health",
    methods=["GET"],
)
def health():

    turath_ok = False

    try:

        response = requests.get(
            f"{TURATH_SERVICE_URL}/health",
            timeout=5,
        )

        turath_ok = (
            response.status_code == 200
        )

    except Exception:
        turath_ok = False


    return jsonify(
        {
            "ok": True,
            "telegram": bool(
                TELEGRAM_TOKEN
            ),
            "gemini": bool(
                client
            ),
            "turath": turath_ok,
        }
    )


@app.route(
    "/ask",
    methods=["GET", "POST"],
)
def ask():

    try:

        if request.method == "POST":

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

        else:

            question = (
                request.args.get(
                    "question"
                )
                or request.args.get(
                    "q"
                )
                or ""
            )


        answer = answer_question(
            question
        )

        return jsonify(
            {
                "ok": True,
                "question": question,
                "answer": answer,
            }
        )

    except Exception as e:

        traceback.print_exc()

        return jsonify(
            {
                "ok": False,
                "error": str(e),
            }
        ), 500


@app.route(
    "/search",
    methods=["GET"],
)
def search_route():

    query = (
        request.args.get("q")
        or request.args.get("query")
        or ""
    )

    comparison = (
        request.args.get(
            "comparison"
        )
        == "true"
    )

    sources = turath_search(
        query,
        comparison=comparison,
    )

    return jsonify(
        {
            "ok": True,
            "query": query,
            "count": len(sources),
            "results": sources,
        }
    )


@app.route(
    "/clear-cache",
    methods=["GET"],
)
def clear_cache():

    return jsonify(
        {
            "ok": True,
            "message": "Tiada cache digunakan.",
        }
    )


# =========================================================
# START TELEGRAM
# =========================================================

# PENTING:
#
# Gunicorn menggunakan:
#
# gunicorn app:app
#
# Oleh sebab itu __name__ bukan "__main__".
#
# Jadi Telegram mesti dimulakan ketika module
# diimport oleh Gunicorn.
#
# Dockerfile kita menggunakan:
#
# --workers 1
#
# supaya hanya ada satu Telegram bot.
#

if TELEGRAM_TOKEN:

    start_telegram()

else:

    print(
        "⚠️ TELEGRAM_TOKEN kosong. "
        "Telegram bot tidak dimulakan."
    )


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    print("")
    print(
        "=============================================="
    )
    print(
        "🚀 TANYAFIQIHBOT STARTED"
    )
    print(
        f"🌐 Flask: http://0.0.0.0:{port}"
    )
    print(
        f"📚 Turath: {TURATH_SERVICE_URL}"
    )
    print(
        "=============================================="
    )
    print("")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )
