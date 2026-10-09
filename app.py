
import os
import re
import json
import time
import asyncio
import logging
import threading
from typing import Any
from collections import Counter

import requests
from flask import Flask, jsonify
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from google import genai


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

TURATH_SEARCH_URL = os.getenv(
    "TURATH_SEARCH_URL",
    "http://127.0.0.1:8765/search",
).strip()

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

PORT = int(os.getenv("PORT", "5000"))
TURATH_TIMEOUT = int(os.getenv("TURATH_TIMEOUT", "15"))
BRAVE_TIMEOUT = int(os.getenv("BRAVE_TIMEOUT", "12"))
MAX_TURATH_QUERIES = int(os.getenv("MAX_TURATH_QUERIES", "4"))
MAX_TURATH_RESULTS = int(os.getenv("MAX_TURATH_RESULTS", "8"))
MAX_BRAVE_RESULTS = int(os.getenv("MAX_BRAVE_RESULTS", "5"))
MAX_CONCURRENT_QUESTIONS = int(
    os.getenv("MAX_CONCURRENT_QUESTIONS", "4")
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tanyafiqihbot")

app = Flask(__name__)

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    gemini_client = None

QUESTION_SEMAPHORE = threading.BoundedSemaphore(
    MAX_CONCURRENT_QUESTIONS
)

HTTP_HEADERS = {
    "User-Agent": "TanyaFiqihBot/1.0",
    "Accept": "application/json",
}

TELEGRAM_APPLICATION = None
TELEGRAM_THREAD = None
TELEGRAM_RUNNING = False
TELEGRAM_LOCK = threading.Lock()


# ============================================================
# GEMINI
# ============================================================

def gemini_generate(prompt: str) -> str:
    """Panggil Gemini secara segerak; fungsi ini dijalankan dalam thread."""
    if not gemini_client:
        raise RuntimeError("GEMINI_API_KEY belum ditetapkan.")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini tidak memulangkan teks.")

    return text.strip()


async def gemini_generate_async(prompt: str) -> str:
    return await asyncio.to_thread(gemini_generate, prompt)


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    return re.sub(r"\s+", " ", str(value)).strip()


def first_value(data: dict, keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return clean_text(value)

    return ""


def extract_result_list(payload: Any) -> list:
    """Sokong beberapa bentuk respons API carian."""
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in (
        "results",
        "result",
        "data",
        "items",
        "documents",
        "sources",
        "records",
        "hits",
    ):
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            nested = extract_result_list(value)
            if nested:
                return nested

    return []


def normalize_result(item: Any, source_type: str) -> dict | None:
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None

        return {
            "title": "Rujukan",
            "text": text,
            "url": "",
            "book": "",
            "author": "",
            "source_type": source_type,
        }

    if not isinstance(item, dict):
        return None

    title = first_value(
        item,
        ["title", "name", "heading", "label", "document_title"],
    )
    text = first_value(
        item,
        [
            "text",
            "content",
            "snippet",
            "passage",
            "excerpt",
            "body",
            "chunk",
            "matched_text",
            "description",
        ],
    )
    book = first_value(
        item,
        ["book", "book_title", "kitab", "source_book", "collection"],
    )
    author = first_value(
        item,
        ["author", "scholar", "pengarang", "writer"],
    )
    url = first_value(
        item,
        ["url", "link", "source_url"],
    )

    # Sesetengah API meletakkan teks dalam medan nested.
    if not text:
        for key in ("metadata", "document", "payload"):
            nested = item.get(key)
            if isinstance(nested, dict):
                text = first_value(
                    nested,
                    ["text", "content", "snippet", "passage", "body"],
                )
                if text:
                    book = book or first_value(
                        nested, ["book", "kitab", "title"]
                    )
                    author = author or first_value(
                        nested, ["author", "pengarang"]
                    )
                    break

    # Hasil tanpa kandungan tidak dikira sebagai rujukan berguna.
    if not text:
        return None

    return {
        "title": title or book or "Rujukan",
        "text": text,
        "url": url,
        "book": book,
        "author": author,
        "source_type": source_type,
    }


def normalize_results(payload: Any, source_type: str) -> list[dict]:
    output = []

    for item in extract_result_list(payload):
        normalized = normalize_result(item, source_type)
        if normalized:
            output.append(normalized)

    # Sesetengah API mengembalikan satu objek keputusan sahaja.
    if not output and isinstance(payload, dict):
        normalized = normalize_result(payload, source_type)
        if normalized:
            output.append(normalized)

    return output


def unique_results(results: list[dict]) -> list[dict]:
    seen = set()
    output = []

    for item in results:
        identity = (
            item.get("book", ""),
            item.get("title", ""),
            item.get("text", "")[:300],
            item.get("url", ""),
        )
        identity = tuple(str(x).lower().strip() for x in identity)

        if identity in seen:
            continue

        seen.add(identity)
        output.append(item)

    return output


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Elakkan mesej Telegram melebihi had."""
    text = text.strip()
    if not text:
        return ["Tiada jawapan teks diterima."]

    chunks = []

    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)

        if split_at < limit // 2:
            split_at = text.rfind(" ", 0, limit)

        if split_at < limit // 2:
            split_at = limit

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        chunks.append(text)

    return chunks


async def reply_long(
    update: Update,
    text: str,
    reply_markup=None,
) -> None:
    message = update.effective_message

    if message is None:
        return

    chunks = split_message(text)

    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            reply_markup=reply_markup if index == 0 else None,
            disable_web_page_preview=True,
        )


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath_once(query: str) -> list[dict]:
    """
    Hantar pertanyaan ke API Turath tempatan.

    Andaian lalai:
        POST /search
        JSON: {"query": "..."}
    Jika API Turath anda menggunakan nama medan lain, ubah
    payload di bawah mengikut format API sebenar.
    """
    response = requests.post(
        TURATH_SEARCH_URL,
        json={"query": query},
        headers=HTTP_HEADERS,
        timeout=TURATH_TIMEOUT,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        logger.warning("API Turath memulangkan respons bukan JSON.")
        return []

    return normalize_results(payload, "Turath")


def make_alternative_queries(question: str) -> list[str]:
    """
    Jana pertanyaan alternatif untuk Turath sahaja.
    Tidak membuat carian web umum di sini.
    """
    prompt = f"""
Anda membantu sistem carian kitab Turath.

Berdasarkan soalan pengguna, hasilkan maksimum 3 pertanyaan
carian alternatif yang ringkas dan sesuai untuk enjin carian kitab.

Syarat:
- Kekalkan isu fiqih atau istilah asal.
- Boleh gunakan istilah Arab jika sesuai.
- Jangan jawab soalan.
- Jangan sertakan penerangan.
- Pulangkan JSON sahaja dalam bentuk:
  {{"queries": ["pertanyaan 1", "pertanyaan 2"]}}

Soalan pengguna:
{question}
"""

    try:
        raw = gemini_generate(prompt)
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)

        if not match:
            return []

        data = json.loads(match.group(0))
        queries = data.get("queries", [])

        if not isinstance(queries, list):
            return []

        return [
            str(q).strip()
            for q in queries
            if str(q).strip()
        ][:3]

    except Exception:
        logger.exception("Gagal menjana pertanyaan alternatif Turath.")
        return []


def search_turath(question: str) -> list[dict]:
    """
    Keutamaan mutlak:
    1. Carian Turath asal.
    2. Jika tiada hasil berguna, cuba pertanyaan alternatif Turath.
    3. Pulangkan senarai kosong hanya jika semuanya tiada hasil.

    Fungsi ini TIDAK menjalankan carian umum.
    """
    queries = [question]

    # Pertanyaan alternatif hanya diperlukan jika carian pertama kosong.
    try:
        first_results = search_turath_once(question)
    except Exception:
        logger.exception("Carian Turath utama gagal.")
        first_results = []

    if first_results:
        logger.info(
            "Turath menemukan %s rujukan melalui pertanyaan utama.",
            len(first_results),
        )
        return unique_results(first_results)[:MAX_TURATH_RESULTS]

    alternative_queries = make_alternative_queries(question)

    for query in alternative_queries:
        query = query.strip()

        if not query or query.lower() in {
            item.lower() for item in queries
        }:
            continue

        queries.append(query)

        try:
            results = search_turath_once(query)
        except Exception:
            logger.exception(
                "Carian alternatif Turath gagal: %s", query
            )
            results = []

        if results:
            logger.info(
                "Turath menemukan %s rujukan melalui pertanyaan alternatif.",
                len(results),
            )
            return unique_results(results)[:MAX_TURATH_RESULTS]

        if len(queries) >= MAX_TURATH_QUERIES:
            break

    logger.info("Tiada rujukan berguna ditemui dalam Turath.")
    return []


# ============================================================
# BRAVE SEARCH
# ============================================================

def search_brave(question: str) -> list[dict]:
    """
    Carian umum hanya dipanggil selepas pengguna menekan
    butang persetujuan dalam Telegram.
    """
    if not BRAVE_API_KEY:
        logger.warning("BRAVE_API_KEY belum ditetapkan.")
        return []

    response = requests.get(
        BRAVE_SEARCH_URL,
        params={
            "q": question,
            "count": MAX_BRAVE_RESULTS,
        },
        headers={
            **HTTP_HEADERS,
            "X-Subscription-Token": BRAVE_API_KEY,
        },
        timeout=BRAVE_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    web_results = payload.get("web", {}).get("results", [])
    output = []

    for item in web_results[:MAX_BRAVE_RESULTS]:
        result = normalize_result(item, "Web")

        if result:
            output.append(result)

    return unique_results(output)


# ============================================================
# ANSWER GENERATION
# ============================================================

def format_sources(results: list[dict]) -> str:
    sections = []

    for index, item in enumerate(results, start=1):
        source_id = f"S{index}"
        title = item.get("title") or "Rujukan"
        book = item.get("book") or ""
        author = item.get("author") or ""
        url = item.get("url") or ""
        content = item.get("text") or ""

        metadata = []

        if book:
            metadata.append(f"Kitab: {book}")

        if author:
            metadata.append(f"Pengarang: {author}")

        if url:
            metadata.append(f"Pautan: {url}")

        metadata_text = "\n".join(metadata)

        sections.append(
            f"[{source_id}] {title}\n"
            f"{metadata_text}\n"
            f"Petikan/kandungan: {content[:3500]}"
        )

    return "\n\n".join(sections)


async def generate_answer(
    question: str,
    results: list[dict],
    source_type: str,
) -> str:
    sources = format_sources(results)

    if source_type == "Turath":
        instruction = """
Gunakan rujukan Turath yang diberikan sebagai asas jawapan.

Penting:
- Jawab dalam Bahasa Melayu yang jelas.
- Bezakan petikan sumber dengan huraian anda sendiri.
- Gunakan tanda [S1], [S2] dan seterusnya untuk dakwaan
  yang disokong oleh rujukan berkenaan.
- Jangan mereka-reka petikan, nama kitab, jilid, halaman,
  pengarang atau hukum yang tidak terkandung dalam sumber.
- Jika sumber tidak mencukupi untuk menentukan hukum,
  nyatakan batasan itu dengan jelas.
- Jika terdapat khilaf, jangan gambarkan satu pandangan
  sebagai ijmak melainkan sumber menyokongnya.
- Jangan anggap maklumat bibliografi yang tiada sebagai fakta.
- Jangan gunakan carian web umum.
"""
    else:
        instruction = """
Jawab dalam Bahasa Melayu berdasarkan hasil carian web yang diberikan.

Penting:
- Bezakan fakta yang disokong sumber dengan kesimpulan.
- Gunakan [S1], [S2] dan seterusnya untuk rujukan.
- Jangan mereka-reka maklumat atau mendakwa sesuatu sumber
  menyatakan perkara yang tidak terdapat dalam hasil carian.
- Bagi persoalan fiqih, jelaskan bahawa hasil web umum
  bukan pengganti semakan sumber ilmiah yang berautoriti.
- Jika sumber bercanggah atau tidak mencukupi, nyatakan batasannya.
"""

    prompt = f"""
Anda ialah TanyaFiqihBot, pembantu maklumat Islam yang berhati-hati.

{instruction}

SOALAN PENGGUNA:
{question}

SUMBER YANG DITEMUI:
{sources}

Sediakan jawapan yang berguna, tersusun dan tidak terlalu panjang.
Akhiri dengan bahagian "Rujukan" yang menyenaraikan sumber
yang benar-benar digunakan menggunakan label [S1], [S2] dan seterusnya.
"""

    try:
        answer = await gemini_generate_async(prompt)
        return answer
    except Exception:
        logger.exception("Gagal menjana jawapan dengan Gemini.")

        if source_type == "Turath":
            return (
                "Saya menemui rujukan Turath, tetapi gagal menjana "
                "huraian jawapan buat masa ini. Berikut ialah kandungan "
                "rujukan yang ditemui:\n\n"
                + format_sources(results)
            )

        return (
            "Carian umum telah selesai, tetapi saya gagal menjana "
            "huraian jawapan. Berikut ialah hasil carian yang ditemui:\n\n"
            + format_sources(results)
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = (
        "Assalamualaikum! Saya TanyaFiqihBot.\n\n"
        "Hantarkan soalan anda untuk mencari rujukan Turath.\n\n"
        "Aliran carian:\n"
        "1. Saya akan mencari dalam Turath terlebih dahulu.\n"
        "2. Jika ada sekurang-kurangnya satu rujukan, saya akan "
        "menjawab berdasarkan sumber itu.\n"
        "3. Jika tiada rujukan Turath selepas carian alternatif, "
        "saya akan bertanya sama ada anda mahu carian umum di web.\n"
        "4. Carian umum hanya dibuat jika anda bersetuju."
    )

    await reply_long(update, text)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await reply_long(
        update,
        "Hantarkan soalan fiqih atau soalan berkaitan Islam.\n\n"
        "Saya akan mencari sumber Turath dahulu. Jika tiada "
        "rujukan yang berguna selepas percubaan alternatif, "
        "anda boleh memilih sama ada mahu carian umum di web.",
    )


# ============================================================
# QUESTION FLOW
# ============================================================

async def handle_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or not message.text:
        return

    question = message.text.strip()

    if not question:
        return

    # Elakkan pengguna membuat terlalu banyak permintaan serentak.
    if not QUESTION_SEMAPHORE.acquire(blocking=False):
        await message.reply_text(
            "Bot sedang memproses beberapa soalan. "
            "Sila cuba semula sebentar lagi."
        )
        return

    try:
        await message.reply_text(
            "📚 Sedang mencari rujukan dalam Turath..."
        )

        turath_results = await asyncio.to_thread(
            search_turath,
            question,
        )

        # PENTING:
        # Jika ada walaupun satu rujukan, jawab terus.
        # Jangan tunjuk butang carian umum.
        if turath_results:
            answer = await generate_answer(
                question,
                turath_results,
                "Turath",
            )

            await reply_long(update, answer)
            return

        # Tiada rujukan berguna selepas carian Turath alternatif.
        # Simpan soalan asal untuk callback butang Telegram.
        context.user_data["pending_general_search_question"] = question
        context.user_data["pending_general_search_time"] = time.time()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔎 Ya, buat carian umum",
                        callback_data="general_search_yes",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Tidak, batalkan",
                        callback_data="general_search_no",
                    )
                ],
            ]
        )

        await message.reply_text(
            "Saya tidak menemui rujukan yang berguna dalam Turath "
            "selepas mencuba pertanyaan alternatif.\n\n"
            "Adakah anda mahu saya cuba carian umum di web?\n\n"
            "Carian umum hanya akan bermula jika anda menekan "
            "butang persetujuan di bawah.",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("Ralat ketika mengendalikan soalan.")
        await message.reply_text(
            "Maaf, berlaku ralat ketika memproses soalan anda. "
            "Sila cuba semula sebentar lagi."
        )

    finally:
        QUESTION_SEMAPHORE.release()


# ============================================================
# INLINE BUTTON CALLBACKS
# ============================================================

async def general_search_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    if query.data == "general_search_no":
        context.user_data.pop(
            "pending_general_search_question", None
        )
        context.user_data.pop(
            "pending_general_search_time", None
        )

        await query.edit_message_text(
            "Baik, carian umum dibatalkan. "
            "Tiada carian web dijalankan."
        )
        return

    if query.data != "general_search_yes":
        return

    question = context.user_data.get(
        "pending_general_search_question"
    )
    created_at = context.user_data.get(
        "pending_general_search_time"
    )

    if not question:
        await query.edit_message_text(
            "Soalan asal tidak ditemui atau permintaan ini "
            "telah selesai. Sila hantar semula soalan anda."
        )
        return

    # Tamat tempoh pilihan selepas 30 minit.
    if (
        created_at is not None
        and time.time() - float(created_at) > 1800
    ):
        context.user_data.pop(
            "pending_general_search_question", None
        )
        context.user_data.pop(
            "pending_general_search_time", None
        )

        await query.edit_message_text(
            "Pilihan carian ini telah tamat tempoh. "
            "Sila hantar semula soalan anda."
        )
        return

    # Kosongkan permintaan tertangguh supaya butang tidak
    # menjalankan carian yang sama berulang kali.
    context.user_data.pop(
        "pending_general_search_question", None
    )
    context.user_data.pop(
        "pending_general_search_time", None
    )

    if not BRAVE_API_KEY:
        await query.edit_message_text(
            "Carian umum belum tersedia kerana BRAVE_API_KEY "
            "belum ditetapkan dalam konfigurasi bot."
        )
        return

    if not QUESTION_SEMAPHORE.acquire(blocking=False):
        await query.edit_message_text(
            "Bot sedang sibuk. Sila hantar semula soalan "
            "anda sebentar lagi."
        )
        return

    try:
        await query.edit_message_text(
            "🔎 Anda telah bersetuju. Sedang membuat carian umum..."
        )

        web_results = await asyncio.to_thread(
            search_brave,
            question,
        )

        if not web_results:
            await query.message.reply_text(
                "Carian umum tidak menemukan hasil yang boleh "
                "digunakan, atau perkhidmatan carian tidak tersedia."
            )
            return

        answer = await generate_answer(
            question,
            web_results,
            "Web",
        )

        await reply_long(
            update,
            answer,
        )

    except Exception:
        logger.exception("Carian umum gagal.")

        await query.message.reply_text(
            "Maaf, carian umum gagal dilaksanakan. "
            "Sila cuba semula kemudian."
        )

    finally:
        QUESTION_SEMAPHORE.release()


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

def build_telegram_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN belum ditetapkan."
        )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(8)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(
        CallbackQueryHandler(
            general_search_callback,
            pattern=r"^general_search_(yes|no)$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_question,
        )
    )

    return application


def telegram_worker() -> None:
    """
    Jalankan polling dalam thread tersendiri supaya Flask
    boleh menyediakan endpoint kesihatan.
    """
    global TELEGRAM_RUNNING, TELEGRAM_APPLICATION

    while True:
        try:
            logger.info("Memulakan Telegram bot...")
            application = build_telegram_application()
            TELEGRAM_APPLICATION = application
            TELEGRAM_RUNNING = True

            application.run_polling(
                drop_pending_updates=False,
                close_loop=True,
            )

            logger.warning(
                "Telegram polling berhenti. Cuba mula semula."
            )

        except Exception:
            logger.exception(
                "Telegram worker gagal. Cuba mula semula."
            )

        finally:
            TELEGRAM_RUNNING = False
            TELEGRAM_APPLICATION = None

        time.sleep(5)


def start_telegram_background() -> None:
    global TELEGRAM_THREAD

    with TELEGRAM_LOCK:
        if TELEGRAM_THREAD and TELEGRAM_THREAD.is_alive():
            return

        TELEGRAM_THREAD = threading.Thread(
            target=telegram_worker,
            name="telegram-polling",
            daemon=True,
        )
        TELEGRAM_THREAD.start()


# ============================================================
# FLASK ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "service": "TanyaFiqihBot",
            "status": "running",
            "search_priority": [
                "Turath",
                "Ask user before general web search",
            ],
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "telegram_running": TELEGRAM_RUNNING,
            "gemini_configured": bool(GEMINI_API_KEY),
            "brave_configured": bool(BRAVE_API_KEY),
            "turath_url": TURATH_SEARCH_URL,
        }
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    start_telegram_background()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )
