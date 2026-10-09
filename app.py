
import os
import re
import json
import time
import asyncio
import logging
import threading
from typing import Any

import requests
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
BRAVE_TIMEOUT = int(os.getenv("BRAVE_TIMEOUT", "15"))

MAX_TURATH_QUERIES = int(os.getenv("MAX_TURATH_QUERIES", "4"))
MAX_TURATH_RESULTS = int(os.getenv("MAX_TURATH_RESULTS", "10"))
MAX_BRAVE_RESULTS = int(os.getenv("MAX_BRAVE_RESULTS", "5"))

MAX_CONCURRENT_QUESTIONS = int(
    os.getenv("MAX_CONCURRENT_QUESTIONS", "4")
)

PENDING_SEARCH_EXPIRY = 1800

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("tanyafiqihbot")
app = Flask(__name__)

gemini_client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)

QUESTION_SEMAPHORE = threading.BoundedSemaphore(
    MAX_CONCURRENT_QUESTIONS
)

TELEGRAM_RUNNING = False
TELEGRAM_APPLICATION = None
TELEGRAM_THREAD = None

HTTP_HEADERS = {
    "User-Agent": "TanyaFiqihBot/1.0",
    "Accept": "application/json",
}


# ============================================================
# GEMINI
# ============================================================

def gemini_generate(prompt: str) -> str:
    if gemini_client is None:
        raise RuntimeError("GEMINI_API_KEY belum ditetapkan.")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    result = getattr(response, "text", None)

    if not result:
        raise RuntimeError("Gemini tidak memulangkan teks.")

    return result.strip()


async def gemini_generate_async(prompt: str) -> str:
    return await asyncio.to_thread(gemini_generate, prompt)


# ============================================================
# RESULT NORMALIZATION
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return re.sub(r"\s+", " ", str(value)).strip()


def first_value(data: dict, keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)

        if value is not None and str(value).strip():
            return clean_text(value)

    return ""


def extract_result_list(payload: Any) -> list:
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


def normalize_result(
    item: Any,
    source_type: str,
) -> dict | None:

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

    # Semak struktur metadata bersarang.
    if not text:
        for key in ("metadata", "document", "payload"):
            nested = item.get(key)

            if not isinstance(nested, dict):
                continue

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

    # Keputusan tanpa kandungan tidak dikira sebagai rujukan.
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
    items = extract_result_list(payload)
    results = []

    for item in items:
        normalized = normalize_result(item, source_type)

        if normalized:
            results.append(normalized)

    # Sokong API yang memulangkan satu objek keputusan.
    if not results and isinstance(payload, dict):
        normalized = normalize_result(payload, source_type)

        if normalized:
            results.append(normalized)

    return results


def unique_results(results: list[dict]) -> list[dict]:
    seen = set()
    unique = []

    for item in results:
        identity = (
            item.get("book", ""),
            item.get("title", ""),
            item.get("text", "")[:300],
            item.get("url", ""),
        )

        identity = tuple(
            str(value).lower().strip()
            for value in identity
        )

        if identity in seen:
            continue

        seen.add(identity)
        unique.append(item)

    return unique


# ============================================================
# TURATH SEARCH
# ============================================================

def search_turath_once(query: str) -> list[dict]:
    """
    Format permintaan lalai:
        POST /search
        {"query": "soalan"}

    Jika API Turath anda menggunakan medan berlainan,
    sesuaikan payload dengan format API sebenar.
    """
    logger.info("[TURATH] Mencari: %s", query)

    response = requests.post(
        TURATH_SEARCH_URL,
        json={"query": query},
        headers=HTTP_HEADERS,
        timeout=TURATH_TIMEOUT,
    )

    response.raise_for_status()
    payload = response.json()

    results = normalize_results(payload, "Turath")

    logger.info(
        "[TURATH] Keputusan berguna: %s",
        len(results),
    )

    return results


def make_alternative_queries(question: str) -> list[str]:
    if not gemini_client:
        # Fallback ringkas jika Gemini tidak tersedia.
        return []

    prompt = f"""
Hasilkan sehingga 3 pertanyaan alternatif untuk carian kitab Turath.

Peraturan:
- Kekalkan maksud dan isu asal.
- Gunakan kata kunci ringkas yang sesuai untuk carian kitab.
- Boleh sertakan istilah Arab jika sesuai.
- Jangan jawab soalan.
- Jangan buat carian web.
- Pulangkan JSON sahaja.

Format:
{{"queries": ["kata kunci 1", "kata kunci 2"]}}

Soalan:
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
            str(query).strip()
            for query in queries
            if str(query).strip()
        ][:3]

    except Exception:
        logger.exception(
            "[TURATH] Gagal menghasilkan pertanyaan alternatif."
        )
        return []


def search_turath(question: str) -> list[dict]:
    """
    Aliran:
    1. Cari soalan asal.
    2. Jika kosong, cari pertanyaan alternatif.
    3. Jika masih kosong, pulangkan [].

    Tiada carian umum dilakukan dalam fungsi ini.
    Walaupun hanya satu rujukan ditemui, ia tetap digunakan.
    """
    try:
        results = search_turath_once(question)

        if results:
            logger.info(
                "[TURATH] Menggunakan %s rujukan daripada carian utama.",
                len(results),
            )
            return unique_results(results)[:MAX_TURATH_RESULTS]

    except Exception:
        logger.exception("[TURATH] Carian utama gagal.")

    alternative_queries = make_alternative_queries(question)
    attempted = {question.lower()}
    queries_used = 1

    for alternative in alternative_queries:
        alternative = alternative.strip()

        if not alternative:
            continue

        if alternative.lower() in attempted:
            continue

        if queries_used >= MAX_TURATH_QUERIES:
            break

        attempted.add(alternative.lower())
        queries_used += 1

        try:
            results = search_turath_once(alternative)

            if results:
                logger.info(
                    "[TURATH] Menemukan %s rujukan alternatif.",
                    len(results),
                )
                return unique_results(results)[:MAX_TURATH_RESULTS]

        except Exception:
            logger.exception(
                "[TURATH] Carian alternatif gagal: %s",
                alternative,
            )

    logger.warning(
        "[TURATH] Tiada rujukan berguna selepas semua cubaan."
    )

    return []


# ============================================================
# BRAVE GENERAL WEB SEARCH
# ============================================================

def search_brave(question: str) -> list[dict]:
    """
    Fungsi ini hanya dipanggil selepas pengguna
    menekan butang persetujuan.
    """
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY belum ditetapkan.")

    logger.info("[WEB] Carian umum dimulakan selepas persetujuan.")

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
    results = []

    for item in web_results[:MAX_BRAVE_RESULTS]:
        normalized = normalize_result(item, "Web")

        if normalized:
            results.append(normalized)

    results = unique_results(results)

    logger.info("[WEB] Keputusan berguna: %s", len(results))

    return results


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

        sections.append(
            f"[{source_id}] {title}\n"
            + "\n".join(metadata)
            + f"\nKandungan: {content[:3500]}"
        )

    return "\n\n".join(sections)


async def generate_answer(
    question: str,
    results: list[dict],
    source_type: str,
) -> str:

    if source_type == "Turath":
        instruction = """
Jawab berdasarkan sumber Turath yang diberikan.

- Jawab dalam Bahasa Melayu yang jelas.
- Gunakan label [S1], [S2] dan seterusnya sebagai rujukan.
- Jangan reka petikan, jilid, halaman atau maklumat kitab.
- Jangan mendakwa sesuatu hukum disebut dalam sumber jika
  kandungan sumber tidak menyokong dakwaan tersebut.
- Jika maklumat tidak mencukupi, nyatakan dengan jujur.
- Jika terdapat khilaf, elakkan mendakwa ijmak tanpa bukti.
- Jangan menggunakan carian web umum.
"""
    else:
        instruction = """
Jawab berdasarkan hasil carian web yang diberikan.

- Jawab dalam Bahasa Melayu yang jelas.
- Gunakan label [S1], [S2] dan seterusnya sebagai rujukan.
- Jangan mereka-reka fakta atau kandungan sumber.
- Untuk persoalan fiqih, nyatakan batasan hasil web umum.
- Jika sumber tidak mencukupi atau bercanggah, nyatakan hal itu.
"""

    prompt = f"""
Anda ialah TanyaFiqihBot, pembantu maklumat Islam yang berhati-hati.

{instruction}

SOALAN:
{question}

SUMBER:
{format_sources(results)}

Berikan jawapan tersusun yang menjawab soalan.
Akhiri dengan bahagian Rujukan yang menyenaraikan sumber
yang benar-benar digunakan.
"""

    try:
        return await gemini_generate_async(prompt)

    except Exception:
        logger.exception("[GEMINI] Gagal menghasilkan jawapan.")

        return (
            "Maaf, rujukan telah ditemui tetapi jawapan automatik "
            "gagal dijana.\n\n"
            + format_sources(results)
        )


# ============================================================
# TELEGRAM MESSAGE HELPERS
# ============================================================

def split_message(text: str, limit: int = 3900) -> list[str]:
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
# TELEGRAM COMMAND HANDLERS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.info("[TELEGRAM] Arahan /start diterima.")

    await reply_long(
        update,
        "Assalamualaikum! Saya TanyaFiqihBot.\n\n"
        "Hantarkan soalan anda untuk mencari rujukan Turath.\n\n"
        "Saya akan mencari Turath dahulu. Jika ada sekurang-kurangnya "
        "satu rujukan berguna, saya akan terus menjawab berdasarkan "
        "sumber itu.\n\n"
        "Jika tiada rujukan selepas carian alternatif, saya akan "
        "bertanya sama ada anda mahu carian umum di web.",
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    await reply_long(
        update,
        "Hantarkan soalan anda dalam bentuk teks.\n\n"
        "Turath ialah carian utama. Carian web umum hanya dilakukan "
        "selepas anda memberi persetujuan melalui butang Telegram.",
    )


# ============================================================
# TELEGRAM QUESTION HANDLER
# ============================================================

async def handle_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    message = update.effective_message

    if message is None or not message.text:
        return

    question = message.text.strip()

    if not question:
        return

    # Log yang diminta.
    logger.info("📩 TELEGRAM MESSAGE: %s", question)

    if not QUESTION_SEMAPHORE.acquire(blocking=False):
        await message.reply_text(
            "Bot sedang sibuk memproses soalan lain. "
            "Sila cuba semula sebentar lagi."
        )
        return

    try:
        await message.reply_text(
            "📚 Sedang mencari rujukan dalam Turath..."
        )

        # Langkah pertama: Turath sahaja.
        turath_results = await asyncio.to_thread(
            search_turath,
            question,
        )

        # Walaupun satu rujukan ditemui, jangan tawarkan carian web.
        if turath_results:
            logger.info(
                "[BOT] Turath mempunyai %s rujukan; "
                "carian umum tidak diperlukan.",
                len(turath_results),
            )

            answer = await generate_answer(
                question,
                turath_results,
                "Turath",
            )

            await reply_long(update, answer)
            return

        # Hanya sampai ke sini jika semua carian Turath kosong.
        logger.info(
            "[BOT] Tiada rujukan Turath. Meminta persetujuan "
            "untuk carian umum."
        )

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
            "Carian web hanya bermula jika anda menekan butang "
            "persetujuan di bawah.",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("[BOT] Ralat semasa memproses soalan.")

        await message.reply_text(
            "Maaf, berlaku ralat semasa memproses soalan. "
            "Sila cuba semula sebentar lagi."
        )

    finally:
        QUESTION_SEMAPHORE.release()


# ============================================================
# TELEGRAM INLINE BUTTON CALLBACKS
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

        logger.info("[WEB] Pengguna membatalkan carian umum.")

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
            "Permintaan ini telah selesai atau soalan asal "
            "tidak ditemui. Sila hantar semula soalan anda."
        )
        return

    if (
        created_at is not None
        and time.time() - float(created_at) > PENDING_SEARCH_EXPIRY
    ):
        context.user_data.pop(
            "pending_general_search_question", None
        )
        context.user_data.pop(
            "pending_general_search_time", None
        )

        await query.edit_message_text(
            "Pilihan carian telah tamat tempoh. "
            "Sila hantar semula soalan anda."
        )
        return

    # Buang permintaan tertangguh supaya butang sama
    # tidak boleh menjalankan carian berulang kali.
    context.user_data.pop(
        "pending_general_search_question", None
    )
    context.user_data.pop(
        "pending_general_search_time", None
    )

    if not BRAVE_API_KEY:
        await query.edit_message_text(
            "Carian umum belum tersedia. "
            "BRAVE_API_KEY belum ditetapkan di Render."
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
            "🔎 Persetujuan diterima. Sedang membuat carian umum..."
        )

        logger.info(
            "[WEB] Pengguna bersetuju. Memulakan carian Brave."
        )

        web_results = await asyncio.to_thread(
            search_brave,
            question,
        )

        if not web_results:
            await query.message.reply_text(
                "Carian umum tidak menemukan hasil yang berguna."
            )
            return

        answer = await generate_answer(
            question,
            web_results,
            "Web",
        )

        await reply_long(update, answer)

    except Exception:
        logger.exception("[WEB] Carian umum gagal.")

        await query.message.reply_text(
            "Maaf, carian umum gagal dilaksanakan. "
            "Sila cuba semula kemudian."
        )

    finally:
        QUESTION_SEMAPHORE.release()


# ============================================================
# BUILD TELEGRAM APPLICATION
# ============================================================

def build_telegram_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN belum ditetapkan."
        )

    logger.info("🤖 Creating Telegram application...")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(8)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

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


# ============================================================
# TELEGRAM ASYNC LIFECYCLE
# ============================================================

async def telegram_main() -> None:
    """
    Mengurus kitaran hayat Telegram secara manual
    dalam event loop milik thread Telegram.

    Jangan panggil run_polling() di dalam fungsi ini.
    """
    global TELEGRAM_RUNNING, TELEGRAM_APPLICATION

    application = build_telegram_application()
    TELEGRAM_APPLICATION = application

    initialized = False
    started = False
    polling_started = False

    try:
        logger.info("🤖 TELEGRAM_TOKEN: %s",
                    "SET" if TELEGRAM_BOT_TOKEN else "MISSING")

        logger.info("🤖 TELEGRAM: initializing...")
        await application.initialize()
        initialized = True

        logger.info("🤖 TELEGRAM: starting...")
        await application.start()
        started = True

        if application.updater is None:
            raise RuntimeError(
                "Telegram updater tidak tersedia."
            )

        logger.info("🤖 TELEGRAM: starting polling...")

        await application.updater.start_polling(
            drop_pending_updates=False,
        )

        polling_started = True
        TELEGRAM_RUNNING = True

        logger.info("✅ TELEGRAM BOT POLLING STARTED")
        logger.info("[TELEGRAM] Polling bermula.")

        # Kekalkan event loop hidup selagi bot berjalan.
        await asyncio.Event().wait()

    finally:
        TELEGRAM_RUNNING = False

        if application.updater and polling_started:
            try:
                await application.updater.stop()
            except Exception:
                logger.exception("[TELEGRAM] Gagal menghentikan updater.")

        if started:
            try:
                await application.stop()
            except Exception:
                logger.exception("[TELEGRAM] Gagal menghentikan aplikasi.")

        if initialized:
            try:
                await application.shutdown()
            except Exception:
                logger.exception("[TELEGRAM] Gagal menutup aplikasi.")

        TELEGRAM_APPLICATION = None


def telegram_worker() -> None:
    """
    Thread supervisor Telegram.
    Jika polling terhenti akibat ralat, cuba mula semula.
    """
    logger.info("🚀 Starting Telegram background thread...")
    logger.info("🚀 Telegram supervisor bermula")

    while True:
        try:
            logger.info("🔄 Cuba memulakan Telegram bot...")

            asyncio.run(telegram_main())

        except Exception:
            logger.exception(
                "[TELEGRAM] Polling gagal; cuba semula."
            )

        finally:
            global TELEGRAM_RUNNING
            TELEGRAM_RUNNING = False

        logger.warning(
            "[TELEGRAM] Polling berhenti. Cuba semula dalam 5 saat."
        )
        time.sleep(5)


def start_telegram_background() -> None:
    global TELEGRAM_THREAD

    if TELEGRAM_THREAD and TELEGRAM_THREAD.is_alive():
        logger.info("[TELEGRAM] Thread sudah berjalan.")
        return

    TELEGRAM_THREAD = threading.Thread(
        target=telegram_worker,
        name="telegram-background",
        daemon=True,
    )

    TELEGRAM_THREAD.start()


# ============================================================
# FLASK HEALTH ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "TanyaFiqihBot",
        "status": "running",
        "telegram_running": TELEGRAM_RUNNING,
        "turath_configured": bool(TURATH_SEARCH_URL),
        "gemini_configured": bool(GEMINI_API_KEY),
        "brave_configured": bool(BRAVE_API_KEY),
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok" if TELEGRAM_RUNNING else "starting",
        "telegram_running": TELEGRAM_RUNNING,
        "gemini_configured": bool(GEMINI_API_KEY),
        "brave_configured": bool(BRAVE_API_KEY),
        "turath_search_url": TURATH_SEARCH_URL,
    }), 200


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    logger.info("[TELEGRAM] Memulakan servis...")

    logger.info(
        "🤖 TELEGRAM_TOKEN: %s",
        "SET" if TELEGRAM_BOT_TOKEN else "MISSING",
    )

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Sila tetapkan TELEGRAM_BOT_TOKEN di Render."
        )

    # Telegram berada dalam thread latar.
    start_telegram_background()

    # Flask berjalan pada thread utama.
    # Gunakan Start Command: python app.py
    logger.info(
        "[FLASK] Memulakan server pada port %s...",
        PORT,
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )
