
import os
import re
import json
import time
import threading
import logging
import hashlib
import requests

from flask import Flask, jsonify
from google import genai

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
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
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "90"))

BRAVE_RESULTS_COUNT = int(os.getenv("BRAVE_RESULTS_COUNT", "10"))

# Had aksara konteks kepada Gemini, bukan had bilangan rujukan.
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "24000"))

TELEGRAM_MESSAGE_LIMIT = 3900
SUPERVISOR_RETRY_SECONDS = 5

# Lindungi daripada pengulangan thread dalam proses yang sama.
_telegram_thread = None
_telegram_lock = threading.Lock()


# ============================================================
# LOGGING, CLIENTS AND FLASK
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("TanyaFiqihBot")

gemini_client = (
    genai.Client(api_key=GOOGLE_API_KEY)
    if GOOGLE_API_KEY
    else None
)

http = requests.Session()

app = Flask(__name__)


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
# GEMINI HELPERS
# ============================================================

def gemini_text(prompt: str) -> str:
    """Panggil Gemini. Fungsi ini dijalankan dalam thread pekerja."""

    if gemini_client is None:
        raise RuntimeError("GOOGLE_API_KEY belum ditetapkan.")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={"temperature": 0.2},
    )

    result = getattr(response, "text", None)

    if not result:
        raise RuntimeError("Gemini tidak memulangkan teks.")

    return result.strip()


def extract_json(text: str):
    """Ekstrak objek JSON daripada respons model."""

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

    raise ValueError("JSON tidak sah daripada Gemini.")


# ============================================================
# QUERY PLANNING FOR TURATH
# ============================================================

def plan_turath_queries(question: str) -> list[str]:
    """
    Hasilkan variasi query untuk meningkatkan peluang menemui
    rujukan Turath yang relevan.
    """

    prompt = f"""
Anda pembantu penyelidikan fiqh Islam.

Sediakan variasi kata kunci untuk mencari jawapan kepada
soalan pengguna dalam kitab-kitab turath Islam.

Soalan:
{question}

Arahan:
- Kekalkan maksud soalan.
- Sertakan istilah Arab yang relevan jika sesuai.
- Boleh sertakan variasi istilah fiqh dan ejaan.
- Jangan reka petikan kitab.
- Pulangkan JSON sahaja dalam format:
  {{
    "queries": [
      "kata kunci 1",
      "kata kunci 2"
    ]
  }}
- Berikan sehingga 8 variasi yang berbeza.
"""

    try:
        data = extract_json(gemini_text(prompt))
        queries = data.get("queries", [])

        if isinstance(queries, list):
            cleaned = [
                str(item).strip()
                for item in queries
                if str(item).strip()
            ]

            # Soalan asal sentiasa dimasukkan.
            return list(dict.fromkeys([question] + cleaned))[:9]

    except Exception:
        logger.exception("Gagal merancang query Turath.")

    return [question]


# ============================================================
# TURATH SEARCH SERVICE
# ============================================================

def extract_result_list(data) -> list:
    """Kenal pasti senarai hasil daripada beberapa format JSON."""

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "results",
        "items",
        "data",
        "sources",
        "documents",
        "references",
        "hits",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            nested = extract_result_list(value)
            if nested:
                return nested

    # Respons satu dokumen.
    if any(
        key in data
        for key in ("text", "content", "passage", "snippet")
    ):
        return [data]

    return []


def request_turath(payload: dict) -> list[dict]:
    """Panggil endpoint /search dengan satu format payload."""

    url = f"{TURATH_SERVICE_URL}/search"

    response = http.post(
        url,
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
    Cari melalui servis Turath.

    Percubaan pertama menggunakan {"queries": [...]}.
    Jika servis tidak menerima format itu atau tiada hasil,
    cuba query satu demi satu menggunakan {"query": "..."}.

    Tiada had bilangan rujukan dikenakan oleh fungsi ini.
    Jumlah hasil sebenar masih bergantung pada servis Turath.
    """

    if not queries:
        return []

    raw_results = []

    try:
        raw_results = request_turath({"queries": queries})
    except Exception:
        logger.warning(
            "Carian Turath berkumpulan gagal; cuba query individu.",
            exc_info=True,
        )

    # Jika format berkumpulan memberikan hasil, gunakannya.
    if raw_results:
        logger.info(
            "Turath memulangkan %d hasil berkumpulan.",
            len(raw_results),
        )
        return raw_results

    # Fallback untuk servis yang menerima satu query sahaja.
    for query in queries:
        try:
            results = request_turath({"query": query})
            raw_results.extend(results)
        except Exception:
            logger.warning(
                "Query Turath gagal: %s",
                query[:120],
                exc_info=True,
            )

    logger.info(
        "Turath memulangkan %d hasil mentah.",
        len(raw_results),
    )

    return raw_results


# ============================================================
# SOURCE NORMALIZATION AND DEDUPLICATION
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
    """
    Buang sumber berganda tanpa memotong jumlah rujukan.
    """

    unique = []
    seen = set()

    for source in sources:
        if not isinstance(source, dict):
            continue

        text = str(source.get("text", "")).strip()

        if not text:
            continue

        key = source_key(source)

        if not key or key in seen:
            continue

        seen.add(key)
        unique.append(source)

    # Susun sumber dengan maklumat bibliografi lebih lengkap dahulu.
    unique.sort(
        key=lambda source: (
            int(bool(source.get("title"))),
            int(bool(source.get("author"))),
            int(bool(source.get("url"))),
            int(bool(source.get("page"))),
        ),
        reverse=True,
    )

    return unique


def normalize_turath_sources(results: list[dict]) -> list[dict]:
    normalized = []

    for item in results:
        title = first_value(
            item,
            (
                "title",
                "book",
                "book_title",
                "source",
                "name",
            ),
        )

        author = first_value(
            item,
            ("author", "book_author", "scholar"),
        )

        text = first_value(
            item,
            (
                "text",
                "content",
                "passage",
                "snippet",
                "excerpt",
                "body",
                "matched_text",
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

        normalized.append({
            "kind": "turath",
            "title": title or "Rujukan Turath",
            "author": author,
            "text": text,
            "url": url,
            "page": page,
        })

    return rank_sources(normalized)


def collect_turath_sources(question: str) -> list[dict]:
    """Rancang query, cari Turath dan normalisasikan hasil."""

    queries = plan_turath_queries(question)
    results = search_turath(queries)
    sources = normalize_turath_sources(results)

    logger.info(
        "Jumlah rujukan Turath selepas penapisan: %d",
        len(sources),
    )

    return sources


# ============================================================
# BRAVE SEARCH
# ============================================================

def collect_external_sources(question: str) -> list[dict]:
    """
    Dipanggil hanya selepas pilihan carian luar dipilih oleh
    pengguna, atau apabila kod secara eksplisit memanggil fungsi ini.

    Brave Search tidak dipanggil dalam collect_turath_sources().
    """

    if not BRAVE_API_KEY:
        logger.warning("BRAVE_API_KEY belum ditetapkan.")
        return []

    url = "https://api.search.brave.com/res/v1/web/search"

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    params = {
        "q": question,
        "count": max(1, min(BRAVE_RESULTS_COUNT, 20)),
    }

    try:
        response = http.get(
            url,
            headers=headers,
            params=params,
            timeout=BRAVE_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

        web_data = data.get("web", {})
        results = web_data.get("results", [])

        normalized = []

        for item in results:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            result_url = str(item.get("url", "")).strip()

            if not description and not title:
                continue

            normalized.append({
                "kind": "external",
                "title": title or "Sumber luar",
                "author": "",
                "text": description or title,
                "url": result_url,
                "page": "",
            })

        sources = rank_sources(normalized)

        logger.info(
            "Brave Search memulangkan %d rujukan.",
            len(sources),
        )

        return sources

    except Exception:
        logger.exception("Carian Brave Search gagal.")
        return []


# ============================================================
# ANSWER GENERATION
# ============================================================

def build_source_context(sources: list[dict]) -> str:
    """
    Bina konteks untuk Gemini.

    Semua sumber dikekalkan untuk senarai rujukan. Had aksara
    ini hanya mengawal panjang konteks yang dihantar kepada model.
    """

    parts = []
    used_chars = 0

    for index, source in enumerate(sources, start=1):
        title = source.get("title", "Sumber tidak diketahui")
        author = source.get("author", "")
        page = source.get("page", "")
        url = source.get("url", "")
        text = source.get("text", "")

        entry = (
            f"[Sumber {index}]\n"
            f"Jenis: {source.get('kind', 'unknown')}\n"
            f"Judul: {title}\n"
            f"Pengarang: {author or 'Tidak dinyatakan'}\n"
            f"Halaman: {page or 'Tidak dinyatakan'}\n"
            f"URL: {url or 'Tiada'}\n"
            f"Petikan:\n{text}\n"
        )

        remaining = MAX_CONTEXT_CHARS - used_chars

        if remaining <= 0:
            break

        if len(entry) > remaining:
            entry = entry[:remaining]

        parts.append(entry)
        used_chars += len(entry)

        if used_chars >= MAX_CONTEXT_CHARS:
            break

    return "\n\n".join(parts)


def build_references(sources: list[dict]) -> str:
    if not sources:
        return "Tiada rujukan ditemui."

    lines = ["RUJUKAN YANG DISEMAK"]

    for index, source in enumerate(sources, start=1):
        title = source.get("title") or "Judul tidak diketahui"
        author = source.get("author")
        page = source.get("page")
        url = source.get("url")
        kind = source.get("kind", "unknown")

        details = [f"{index}. {title}"]

        if author:
            details.append(f"Pengarang: {author}")

        if page:
            details.append(f"Halaman: {page}")

        if kind == "external":
            details.append("Jenis: Sumber luar")
        else:
            details.append("Jenis: Turath")

        if url:
            details.append(f"URL: {url}")

        lines.append("\n".join(details))

    return "\n\n".join(lines)


def prepare_answer(question: str, sources: list[dict]) -> str:
    """
    Jawab berdasarkan sumber yang dibekalkan sahaja.

    Jika tiada sumber, bot tidak mereka-reka rujukan.
    """

    sources = rank_sources(sources)

    if not sources:
        return (
            "Maaf, tiada rujukan yang berjaya diperoleh untuk "
            "menjawab soalan ini.\n\n"
            "Sila cuba dengan istilah atau kata kunci yang lebih khusus."
        )

    context = build_source_context(sources)

    source_types = {
        source.get("kind")
        for source in sources
    }

    if source_types == {"turath"}:
        source_policy = """
Gunakan rujukan Turath yang diberikan sahaja.
Jangan gunakan atau dakwa anda telah menggunakan sumber luar.
"""
    elif source_types == {"external"}:
        source_policy = """
Gunakan sumber luar yang diberikan sahaja.
Jangan mendakwa petikan ini berasal daripada kitab Turath.
"""
    else:
        source_policy = """
Gunakan hanya rujukan yang diberikan, sama ada Turath atau sumber luar.
Bezakan dengan jelas antara petikan kitab dan sumber luar.
"""

    prompt = f"""
Anda ialah pembantu penyelidikan fiqh Islam yang berhati-hati.

SOALAN PENGGUNA:
{question}

DASAR SUMBER:
{source_policy}

SUMBER YANG DIPEROLEH:
{context}

ARAHAN MENJAWAB:
1. Jawab dalam bahasa Melayu yang jelas.
2. Berikan jawapan langsung kepada soalan.
3. Bezakan antara perkara yang disokong sumber dan perkara yang
   tidak dapat dipastikan daripada sumber.
4. Jangan mereka-reka nama kitab, pengarang, halaman, hadis,
   nombor jilid atau petikan.
5. Jangan anggap ringkasan hasil carian sebagai petikan penuh kitab.
6. Jika terdapat khilaf, nyatakan hanya jika bahan yang diberikan
   benar-benar menyokongnya.
7. Jika sumber tidak mencukupi, nyatakan keterbatasan itu.
8. Jangan mendakwa telah menyemak sumber yang tiada dalam konteks.
9. Jangan mengeluarkan fatwa muktamad bagi keadaan khusus tanpa
   maklumat yang mencukupi.
10. Susun jawapan dengan tajuk kecil jika membantu.

Format yang disarankan:
- Jawapan ringkas
- Huraian
- Dalil atau petikan yang benar-benar disokong sumber
- Catatan khilaf atau batasan, jika berkenaan

Senarai rujukan akan ditambah secara berasingan oleh sistem.
Jangan mereka-reka senarai bibliografi dalam jawapan.
"""

    try:
        answer = gemini_text(prompt)
    except Exception:
        logger.exception("Gemini gagal menjana jawapan.")

        return (
            "Maaf, berlaku masalah ketika menjana jawapan daripada "
            "sumber yang ditemui. Sila cuba semula.\n\n"
            + build_references(sources)
        )

    return (
        f"{answer.strip()}\n\n"
        f"{build_references(sources)}"
    )


# ============================================================
# TELEGRAM MESSAGE UTILITIES
# ============================================================

async def send_long_message(message, text: str):
    """
    Pecahkan jawapan panjang kepada beberapa mesej Telegram.
    Elakkan pemotongan di tengah perkataan jika boleh.
    """

    if not text:
        text = "Tiada jawapan diterima."

    remaining = text.strip()

    while remaining:
        if len(remaining) <= TELEGRAM_MESSAGE_LIMIT:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind(
                "\n",
                0,
                TELEGRAM_MESSAGE_LIMIT,
            )

            if split_at < TELEGRAM_MESSAGE_LIMIT // 2:
                split_at = remaining.rfind(
                    " ",
                    0,
                    TELEGRAM_MESSAGE_LIMIT,
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
            "Aliran carian:\n"
            "• 10 rujukan Turath atau lebih: jawab dengan Turath.\n"
            "• 1–9 rujukan Turath: jawab dengan Turath.\n"
            "• 0 rujukan Turath: anda boleh memilih carian luar "
            "atau Turath sahaja."
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Cara penggunaan:\n"
            "1. Hantar soalan fiqh dalam bahasa Melayu atau Arab.\n"
            "2. Bot akan mencari rujukan Turath dahulu.\n"
            "3. Jika tiada rujukan Turath, anda boleh memilih "
            "sama ada mahu mencari sumber luar.\n\n"
            "Nota: jawapan bergantung pada sumber yang berjaya "
            "ditemui dan bukan pengganti nasihat ulama yang "
            "berkelayakan untuk kes khusus."
        )


# ============================================================
# MAIN QUESTION HANDLER
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

    # Elakkan pertindihan soalan menunggu bagi pengguna sama.
    context.user_data.pop("pending_question", None)
    context.user_data.pop("pending_turath_sources", None)

    status = await message.reply_text(
        "🔎 Sedang mencari rujukan Turath..."
    )

    try:
        # LANGKAH 1: Cari Turath sahaja.
        turath_sources = await asyncio.to_thread(
            collect_turath_sources,
            question,
        )

        count = len(turath_sources)

        logger.info(
            "Soalan diterima. Jumlah rujukan Turath: %d",
            count,
        )

        # LANGKAH 2: Jika ada sekurang-kurangnya satu rujukan,
        # terus jawab menggunakan Turath sahaja.
        #
        # Ini meliputi 1–9 dan 10 atau lebih.
        # Tiada carian luar dibuat dalam laluan ini.
        if count >= 1:
            if count >= MIN_TURATH_REFERENCES:
                logger.info(
                    "Turath mencapai sasaran minimum: %d rujukan.",
                    count,
                )
            else:
                logger.info(
                    "Turath mempunyai %d rujukan; terus jawab "
                    "tanpa carian luar.",
                    count,
                )

            answer = await asyncio.to_thread(
                prepare_answer,
                question,
                turath_sources,
            )

            await status.delete()
            await send_long_message(message, answer)
            return

        # LANGKAH 3: Hanya 0 rujukan Turath memaparkan pilihan.
        # Brave Search BELUM dijalankan pada peringkat ini.
        context.user_data["pending_question"] = question
        context.user_data["pending_turath_sources"] = []

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
            "Adakah anda mahu mencari sumber luar, atau "
            "kekal menggunakan Turath sahaja?",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("Ralat dalam telegram_answer.")

        try:
            await status.edit_text(
                "❌ Maaf, berlaku ralat ketika mencari rujukan. "
                "Sila cuba lagi."
            )
        except Exception:
            await message.reply_text(
                "❌ Maaf, berlaku ralat ketika memproses soalan."
            )


# ============================================================
# SOURCE CHOICE CALLBACK
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
            "⚠️ Pilihan ini telah tamat tempoh. "
            "Sila hantar soalan semula."
        )
        return

    if query.data not in ("external_sources", "turath_only"):
        return

    try:
        if query.data == "external_sources":
            # Brave Search hanya dipanggil selepas pengguna memilih.
            await query.edit_message_text(
                "🌐 Anda memilih carian luar.\n"
                "Sedang mencari sumber luar..."
            )

            external_sources = await asyncio.to_thread(
                collect_external_sources,
                question,
            )

            answer = await asyncio.to_thread(
                prepare_answer,
                question,
                external_sources,
            )

        else:
            # Pengguna memilih Turath sahaja.
            # Tiada carian luar dijalankan.
            await query.edit_message_text(
                "📚 Anda memilih Turath sahaja.\n\n"
                "Tiada rujukan Turath ditemui untuk soalan ini."
            )

            answer = (
                "Tiada rujukan Turath ditemui untuk soalan ini. "
                "Seperti pilihan anda, carian luar tidak dijalankan."
            )

        context.user_data.pop("pending_question", None)
        context.user_data.pop("pending_turath_sources", None)

        await send_long_message(query.message, answer)

    except Exception:
        logger.exception("Ralat memproses pilihan sumber.")

        context.user_data.pop("pending_question", None)
        context.user_data.pop("pending_turath_sources", None)

        try:
            await query.message.reply_text(
                "❌ Gagal memproses pilihan sumber. Sila cuba lagi."
            )
        except Exception:
            pass


# ============================================================
# UNKNOWN COMMAND HANDLER
# ============================================================

async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Arahan tidak dikenali. Gunakan /start atau /help."
        )


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

def create_telegram_app() -> Application:
    application = ApplicationBuilder().token(
        TELEGRAM_TOKEN
    ).build()

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CallbackQueryHandler(
            source_choice_callback,
            pattern=r"^(external_sources|turath_only)$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            telegram_answer,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            unknown_command,
        )
    )

    return application


# ============================================================
# TELEGRAM SUPERVISOR
# ============================================================

def telegram_supervisor():
    """
    Jalankan polling dalam thread latar belakang.
    Cuba semula jika polling berhenti dengan ralat.
    """

    if not TELEGRAM_TOKEN:
        logger.error(
            "Telegram tidak dimulakan: TELEGRAM_TOKEN belum ditetapkan."
        )
        return

    while True:
        try:
            logger.info("Memulakan Telegram bot...")

            telegram_app = create_telegram_app()

            telegram_app.run_polling(
                drop_pending_updates=False,
                close_loop=True,
            )

            logger.warning(
                "Telegram polling telah berhenti. "
                "Akan cuba semula."
            )

        except Exception:
            logger.exception(
                "Telegram supervisor menerima ralat."
            )

        time.sleep(SUPERVISOR_RETRY_SECONDS)


def start_telegram_background():
    global _telegram_thread

    if not TELEGRAM_TOKEN:
        logger.warning(
            "Telegram tidak dimulakan kerana TELEGRAM_TOKEN kosong."
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

# Untuk deployment dengan Gunicorn, gunakan satu worker sahaja
# kerana proses import ini akan memulakan Telegram thread.
start_telegram_background()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=False,
        use_reloader=False,
    )
