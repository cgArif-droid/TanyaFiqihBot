
import os
import re
import json
import time
import asyncio
import threading
import logging
import requests

from flask import Flask, jsonify
from google import genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

MIN_TURATH_REFERENCES = int(
    os.getenv("MIN_TURATH_REFERENCES", "10")
)

TURATH_TIMEOUT = int(os.getenv("TURATH_TIMEOUT", "45"))
BRAVE_TIMEOUT = int(os.getenv("BRAVE_TIMEOUT", "20"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "24000"))
BRAVE_RESULTS_COUNT = int(os.getenv("BRAVE_RESULTS_COUNT", "10"))
TELEGRAM_MESSAGE_LIMIT = 3900
RETRY_SECONDS = 5

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("TanyaFiqihBot")

app = Flask(__name__)
http = requests.Session()

gemini_client = (
    genai.Client(api_key=GOOGLE_API_KEY)
    if GOOGLE_API_KEY
    else None
)

_telegram_thread = None
_telegram_lock = threading.Lock()


# ============================================================
# FLASK HEALTH CHECKS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "TanyaFiqihBot",
    })


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "telegram_configured": bool(TELEGRAM_TOKEN),
        "gemini_configured": bool(GOOGLE_API_KEY),
        "brave_configured": bool(BRAVE_API_KEY),
        "turath_service_url": TURATH_SERVICE_URL,
    })


# ============================================================
# GEMINI
# ============================================================

def gemini_text(prompt: str) -> str:
    if gemini_client is None:
        raise RuntimeError("GOOGLE_API_KEY belum ditetapkan.")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    result = getattr(response, "text", None)

    if not result:
        raise RuntimeError("Gemini tidak memulangkan teks.")

    return result.strip()


def extract_json(text: str):
    text = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])

    raise ValueError("JSON daripada Gemini tidak sah.")


# ============================================================
# TURATH QUERY PLANNING
# ============================================================

def plan_turath_queries(question: str) -> list[str]:
    prompt = f"""
Anda pembantu penyelidikan fiqh Islam.

Bina variasi kata kunci untuk mencari jawapan dalam kitab turath.

Soalan pengguna:
{question}

Arahan:
- Kekalkan maksud soalan.
- Sertakan istilah Arab dan istilah fiqh yang relevan.
- Jangan reka petikan kitab.
- Pulangkan JSON sahaja:
  {{"queries": ["query 1", "query 2"]}}
- Maksimum 8 variasi tambahan.
"""

    try:
        data = extract_json(gemini_text(prompt))
        items = data.get("queries", [])

        if isinstance(items, list):
            cleaned = [
                str(item).strip()
                for item in items
                if str(item).strip()
            ]
            return list(dict.fromkeys([question] + cleaned))[:9]

    except Exception:
        logger.exception("Gagal merancang query Turath.")

    return [question]


# ============================================================
# TURATH SEARCH SERVICE
# ============================================================

def extract_result_list(data) -> list:
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "results", "items", "data", "sources",
        "documents", "references", "hits",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            nested = extract_result_list(value)
            if nested:
                return nested

    if any(
        key in data
        for key in ("text", "content", "passage", "snippet")
    ):
        return [data]

    return []


def request_turath(payload: dict) -> list[dict]:
    response = http.post(
        f"{TURATH_SERVICE_URL}/search",
        json=payload,
        timeout=TURATH_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()

    return [
        item for item in extract_result_list(data)
        if isinstance(item, dict)
    ]


def search_turath(queries: list[str]) -> list[dict]:
    """
    Format API yang dicuba:
      {"queries": ["...", "..."]}
    Kemudian fallback:
      {"query": "..."}

    Tiada had bilangan rujukan dikenakan di sini.
    """

    if not queries:
        return []

    try:
        results = request_turath({"queries": queries})

        if results:
            logger.info(
                "Turath mengembalikan %d hasil berkumpulan.",
                len(results),
            )
            return results

    except Exception:
        logger.warning(
            "Carian Turath berkumpulan gagal; cuba query individu.",
            exc_info=True,
        )

    results = []

    for query in queries:
        try:
            results.extend(
                request_turath({"query": query})
            )
        except Exception:
            logger.warning(
                "Carian Turath gagal untuk query: %s",
                query[:100],
                exc_info=True,
            )

    logger.info("Jumlah hasil mentah Turath: %d", len(results))
    return results


# ============================================================
# SOURCE NORMALIZATION
# ============================================================

def first_value(source: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)

        if value is not None and not isinstance(value, (dict, list)):
            value = str(value).strip()

            if value:
                return value

    return ""


def source_key(source: dict) -> str:
    url = source.get("url", "").strip().lower()

    if url:
        return url

    title = re.sub(
        r"\s+",
        " ",
        source.get("title", "").strip().lower(),
    )
    text = re.sub(
        r"\s+",
        " ",
        source.get("text", "").strip().lower(),
    )

    return f"{title}|{text[:500]}"


def rank_sources(sources: list[dict]) -> list[dict]:
    """Buang sumber berganda tanpa mengehadkan bilangan hasil."""

    unique = []
    seen = set()

    for source in sources:
        if not isinstance(source, dict):
            continue

        if not str(source.get("text", "")).strip():
            continue

        key = source_key(source)

        if not key or key in seen:
            continue

        seen.add(key)
        unique.append(source)

    unique.sort(
        key=lambda item: (
            int(bool(item.get("title"))),
            int(bool(item.get("author"))),
            int(bool(item.get("page"))),
            int(bool(item.get("url"))),
        ),
        reverse=True,
    )

    return unique


def normalize_turath_sources(results: list[dict]) -> list[dict]:
    sources = []

    for item in results:
        title = first_value(
            item,
            ("title", "book", "book_title", "source", "name"),
        )

        author = first_value(
            item,
            ("author", "book_author", "scholar"),
        )

        text = first_value(
            item,
            (
                "text", "content", "passage", "snippet",
                "excerpt", "body", "matched_text",
            ),
        )

        url = first_value(
            item,
            ("url", "link", "source_url"),
        )

        page = first_value(
            item,
            ("page", "page_number", "volume_page"),
        )

        if not text:
            continue

        sources.append({
            "kind": "turath",
            "title": title or "Rujukan Turath",
            "author": author,
            "text": text,
            "url": url,
            "page": page,
        })

    return rank_sources(sources)


def collect_turath_sources(question: str) -> list[dict]:
    queries = plan_turath_queries(question)
    results = search_turath(queries)
    sources = normalize_turath_sources(results)

    logger.info(
        "Rujukan Turath selepas penapisan: %d",
        len(sources),
    )

    return sources


# ============================================================
# BRAVE SEARCH
# ============================================================

def collect_external_sources(question: str) -> list[dict]:
    """
    Fungsi ini hanya dipanggil selepas pengguna memilih
    carian luar. Ia tidak dipanggil secara automatik semasa
    carian Turath.
    """

    if not BRAVE_API_KEY:
        logger.warning("BRAVE_API_KEY belum ditetapkan.")
        return []

    try:
        response = http.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            params={
                "q": question,
                "count": max(1, min(BRAVE_RESULTS_COUNT, 20)),
            },
            timeout=BRAVE_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

        results = data.get("web", {}).get("results", [])
        sources = []

        for item in results:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            url = str(item.get("url", "")).strip()

            text = description or title

            if not text:
                continue

            sources.append({
                "kind": "external",
                "title": title or "Sumber luar",
                "author": "",
                "text": text,
                "url": url,
                "page": "",
            })

        sources = rank_sources(sources)

        logger.info(
            "Brave Search memulangkan %d sumber.",
            len(sources),
        )

        return sources

    except Exception:
        logger.exception("Brave Search gagal.")
        return []


# ============================================================
# ANSWER AND REFERENCES
# ============================================================

def build_source_context(sources: list[dict]) -> str:
    parts = []
    used = 0

    for index, source in enumerate(sources, start=1):
        entry = (
            f"[Sumber {index}]\n"
            f"Jenis: {source.get('kind', 'unknown')}\n"
            f"Judul: {source.get('title', '')}\n"
            f"Pengarang: {source.get('author', '')}\n"
            f"Halaman: {source.get('page', '')}\n"
            f"URL: {source.get('url', '')}\n"
            f"Petikan:\n{source.get('text', '')}\n"
        )

        remaining = MAX_CONTEXT_CHARS - used

        if remaining <= 0:
            break

        entry = entry[:remaining]
        parts.append(entry)
        used += len(entry)

    return "\n\n".join(parts)


def build_references(sources: list[dict]) -> str:
    if not sources:
        return "Tiada rujukan ditemui."

    lines = ["RUJUKAN"]

    for index, source in enumerate(sources, start=1):
        line = f"{index}. {source.get('title') or 'Judul tidak diketahui'}"

        if source.get("author"):
            line += f"\n   Pengarang: {source['author']}"

        if source.get("page"):
            line += f"\n   Halaman: {source['page']}"

        if source.get("kind") == "external":
            line += "\n   Jenis: Sumber luar"
        else:
            line += "\n   Jenis: Turath"

        if source.get("url"):
            line += f"\n   URL: {source['url']}"

        lines.append(line)

    return "\n\n".join(lines)


def prepare_answer(question: str, sources: list[dict]) -> str:
    sources = rank_sources(sources)

    if not sources:
        return (
            "Tiada rujukan yang berjaya diperoleh untuk menjawab "
            "soalan ini. Sila cuba kata kunci yang lebih khusus."
        )

    source_types = {item.get("kind") for item in sources}

    if source_types == {"turath"}:
        policy = (
            "Gunakan rujukan Turath yang diberikan sahaja. "
            "Jangan dakwa menggunakan sumber luar."
        )
    elif source_types == {"external"}:
        policy = (
            "Gunakan sumber luar yang diberikan sahaja. "
            "Jangan dakwa petikan berasal daripada kitab Turath."
        )
    else:
        policy = (
            "Gunakan hanya sumber yang diberikan dan bezakan "
            "rujukan Turath daripada sumber luar."
        )

    prompt = f"""
Anda pembantu penyelidikan fiqh Islam.

SOALAN:
{question}

DASAR SUMBER:
{policy}

SUMBER:
{build_source_context(sources)}

ARAHAN:
1. Jawab dalam bahasa Melayu yang jelas.
2. Jawab soalan secara langsung.
3. Jangan reka nama kitab, pengarang, halaman, hadis atau petikan.
4. Jika sumber tidak mencukupi, nyatakan batasannya.
5. Nyatakan khilaf hanya apabila sumber yang diberikan menyokongnya.
6. Jangan anggap ringkasan carian sebagai petikan penuh kitab.
7. Jangan reka senarai bibliografi; sistem akan menambah rujukan.
8. Berikan huraian yang berhati-hati dan jangan mendakwa kepastian
   yang tidak disokong oleh sumber.

Susunan yang disarankan:
- Jawapan ringkas
- Huraian
- Dalil atau petikan yang disokong sumber
- Batasan jawapan jika perlu
"""

    try:
        answer = gemini_text(prompt)
    except Exception:
        logger.exception("Gemini gagal menjana jawapan.")

        return (
            "Maaf, berlaku masalah ketika menjana jawapan.\n\n"
            + build_references(sources)
        )

    return f"{answer}\n\n{build_references(sources)}"


# ============================================================
# TELEGRAM MESSAGE UTILITIES
# ============================================================

async def send_long_message(message, text: str):
    remaining = (text or "Tiada jawapan diterima.").strip()

    while remaining:
        if len(remaining) <= TELEGRAM_MESSAGE_LIMIT:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind(
                "\n", 0, TELEGRAM_MESSAGE_LIMIT
            )

            if split_at < TELEGRAM_MESSAGE_LIMIT // 2:
                split_at = remaining.rfind(
                    " ", 0, TELEGRAM_MESSAGE_LIMIT
                )

            if split_at < TELEGRAM_MESSAGE_LIMIT // 2:
                split_at = TELEGRAM_MESSAGE_LIMIT

            chunk = remaining[:split_at].strip()
            remaining = remaining[split_at:].strip()

        if chunk:
            await message.reply_text(
                chunk,
                disable_web_page_preview=True,
            )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Assalamualaikum! Saya TanyaFiqihBot.\n\n"
            "Hantar soalan fiqh untuk mencari rujukan Turath.\n\n"
            "• 10 rujukan atau lebih: jawab menggunakan Turath.\n"
            "• 1–9 rujukan: jawab menggunakan Turath.\n"
            "• 0 rujukan: anda boleh memilih carian luar atau "
            "Turath sahaja."
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Hantar soalan fiqh dalam bahasa Melayu atau Arab.\n"
            "Bot akan mencari Turath terlebih dahulu. Carian luar "
            "hanya dijalankan apabila tiada rujukan Turath dan "
            "anda memilih butang carian luar."
        )


# ============================================================
# QUESTION HANDLER
# ============================================================

async def telegram_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None or not message.text:
        return

    question = message.text.strip()

    if not question:
        return

    context.user_data.pop("pending_question", None)

    status = await message.reply_text(
        "🔎 Sedang mencari rujukan Turath..."
    )

    try:
        # Carian Turath sahaja pada peringkat pertama.
        sources = await asyncio.to_thread(
            collect_turath_sources,
            question,
        )

        count = len(sources)

        logger.info("Bilangan rujukan Turath: %d", count)

        # 1–9 atau 10+: jawab terus menggunakan Turath sahaja.
        # Tiada Brave Search dalam laluan ini.
        if count >= 1:
            if count >= MIN_TURATH_REFERENCES:
                logger.info(
                    "Sasaran rujukan Turath tercapai: %d.",
                    count,
                )
            else:
                logger.info(
                    "Kurang daripada sasaran, tetapi rujukan wujud. "
                    "Jawab dengan Turath sahaja.",
                )

            answer = await asyncio.to_thread(
                prepare_answer,
                question,
                sources,
            )

            await status.delete()
            await send_long_message(message, answer)
            return

        # Hanya apabila bilangan Turath ialah sifar,
        # simpan soalan dan minta pilihan pengguna.
        context.user_data["pending_question"] = question

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🌐 Cari sumber luar",
                    callback_data="external_sources",
                )
            ],
            [
                InlineKeyboardButton(
                    "📚 Turath sahaja",
                    callback_data="turath_only",
                )
            ],
        ])

        await status.edit_text(
            "⚠️ Tiada rujukan Turath ditemui.\n\n"
            "Pilih tindakan seterusnya:",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("Ralat semasa memproses soalan.")

        try:
            await status.edit_text(
                "❌ Berlaku ralat ketika mencari rujukan. "
                "Sila cuba lagi."
            )
        except Exception:
            pass


# ============================================================
# CALLBACK: USER SOURCE CHOICE
# ============================================================

async def source_choice_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    question = context.user_data.get("pending_question")

    if not question:
        await query.edit_message_text(
            "Pilihan ini telah tamat tempoh. Sila hantar soalan semula."
        )
        return

    if query.data == "external_sources":
        await query.edit_message_text(
            "🌐 Anda memilih carian luar. Sedang mencari..."
        )

        # Brave Search hanya bermula di sini selepas pilihan pengguna.
        external_sources = await asyncio.to_thread(
            collect_external_sources,
            question,
        )

        answer = await asyncio.to_thread(
            prepare_answer,
            question,
            external_sources,
        )

    elif query.data == "turath_only":
        await query.edit_message_text(
            "📚 Anda memilih Turath sahaja. "
            "Tiada rujukan Turath ditemui untuk soalan ini."
        )

        answer = (
            "Tiada rujukan Turath ditemui untuk soalan ini. "
            "Carian luar tidak dijalankan kerana anda memilih "
            "Turath sahaja."
        )

    else:
        return

    context.user_data.pop("pending_question", None)

    await send_long_message(query.message, answer)


async def callback_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Ralat ketika mengendalikan kemas kini Telegram.",
        exc_info=context.error,
    )


async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Arahan tidak dikenali. Gunakan /start atau /help."
        )


# ============================================================
# CREATE TELEGRAM APPLICATION
# ============================================================

def create_telegram_app():
    telegram_app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
    )

    telegram_app.add_handler(
        CommandHandler("help", help_command)
    )

    telegram_app.add_handler(
        CallbackQueryHandler(
            source_choice_callback,
            pattern=r"^(external_sources|turath_only)$",
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            telegram_answer,
        )
    )

    telegram_app.add_handler(
        MessageHandler(
            filters.COMMAND,
            unknown_command,
        )
    )

    telegram_app.add_error_handler(callback_error_handler)

    return telegram_app


# ============================================================
# TELEGRAM ASYNC SUPERVISOR
# ============================================================

def telegram_supervisor():
    """
    Jalankan event loop tersendiri dalam thread latar belakang.

    Jangan gunakan Application.run_polling() di sini kerana
    run_polling() mengurus signal handler yang biasanya memerlukan
    main thread.
    """

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN belum ditetapkan.")
        return

    async def telegram_main():
        telegram_app = create_telegram_app()

        try:
            await telegram_app.initialize()
            await telegram_app.start()

            if telegram_app.updater is None:
                raise RuntimeError("Telegram updater tidak tersedia.")

            await telegram_app.updater.start_polling(
                drop_pending_updates=False,
            )

            logger.info("TELEGRAM BOT POLLING STARTED")

            # Kekalkan event loop aktif sehingga berlaku ralat.
            await asyncio.Event().wait()

        finally:
            # Tutup komponen dengan tertib apabila loop berhenti.
            try:
                if (
                    telegram_app.updater is not None
                    and telegram_app.updater.running
                ):
                    await telegram_app.updater.stop()
            except Exception:
                logger.exception("Gagal menghentikan updater.")

            try:
                if telegram_app.running:
                    await telegram_app.stop()
            except Exception:
                logger.exception("Gagal menghentikan aplikasi Telegram.")

            try:
                await telegram_app.shutdown()
            except Exception:
                logger.exception("Gagal menutup aplikasi Telegram.")

    while True:
        try:
            logger.info("Memulakan Telegram bot...")
            asyncio.run(telegram_main())

        except Exception:
            logger.exception(
                "Telegram supervisor menerima ralat."
            )

        logger.warning(
            "Telegram polling berhenti. Cuba semula dalam %d saat.",
            RETRY_SECONDS,
        )

        time.sleep(RETRY_SECONDS)


def start_telegram_background():
    global _telegram_thread

    if not TELEGRAM_TOKEN:
        logger.warning(
            "Telegram tidak dimulakan: TELEGRAM_TOKEN kosong."
        )
        return

    with _telegram_lock:
        if _telegram_thread and _telegram_thread.is_alive():
            logger.info("Telegram thread sudah berjalan.")
            return

        _telegram_thread = threading.Thread(
            target=telegram_supervisor,
            name="telegram-supervisor",
            daemon=True,
        )

        _telegram_thread.start()

        logger.info("Telegram supervisor thread dimulakan.")


# ============================================================
# STARTUP
# ============================================================

start_telegram_background()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=False,
        use_reloader=False,
    )
