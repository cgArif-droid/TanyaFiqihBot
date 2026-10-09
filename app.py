import os
import re
import json
import time
import asyncio
import threading
import traceback
import requests

from urllib.parse import urlparse
from flask import Flask, jsonify
from google import genai
from google.genai import types

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURATION
# ============================================================

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

LLM_MODEL = os.getenv(
    "LLM_MODEL", "gemini-2.5-flash"
).strip()

ARABIC_QUERY_MODEL = os.getenv(
    "ARABIC_QUERY_MODEL", LLM_MODEL
).strip()

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL", "http://127.0.0.1:8765"
).strip().rstrip("/")

BRAVE_SEARCH_API_KEY = os.getenv(
    "BRAVE_SEARCH_API_KEY", ""
).strip()

BRAVE_SEARCH_COUNT = int(
    os.getenv("BRAVE_SEARCH_COUNT", "6")
)

GEMINI_RETRIES = int(os.getenv("GEMINI_RETRIES", "2"))
TELEGRAM_RESTART_WAIT = int(
    os.getenv("TELEGRAM_RESTART_WAIT", "5")
)

TURATH_TIMEOUT = int(os.getenv("TURATH_TIMEOUT", "90"))
BRAVE_TIMEOUT = int(os.getenv("BRAVE_TIMEOUT", "20"))

MAX_SOURCE_COUNT = int(os.getenv("MAX_SOURCE_COUNT", "8"))
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "2500"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "14000"))

# Konfigurasi Session dengan Retry Policy untuk ketahanan rangkaian
HTTP_SESSION = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
HTTP_SESSION.mount("https://", HTTPAdapter(max_retries=retries))
HTTP_SESSION.mount("http://", HTTPAdapter(max_retries=retries))

# Client Gemini dikongsi oleh fungsi-fungsi aplikasi.
GEMINI_CLIENT = (
    genai.Client(api_key=GOOGLE_API_KEY)
    if GOOGLE_API_KEY
    else None
)

app = Flask(__name__)


# ============================================================
# DOMAIN SUMBER ISLAM
# ============================================================

OFFICIAL_FATWA_DOMAINS = [
    "muftiwp.gov.my",
    "islam.gov.my",
    "jakim.gov.my",
    "e-smaf.islam.gov.my",
    "dar-alifta.org",
]

ISLAMIC_LIBRARY_DOMAINS = [
    "shamela.ws",
    "waqfeya.net",
]

# Domain tambahan telah dibuang mengikut arahan
ADDITIONAL_ISLAMIC_DOMAINS = []

ALL_ISLAMIC_DOMAINS = (
    OFFICIAL_FATWA_DOMAINS
    + ISLAMIC_LIBRARY_DOMAINS
    + ADDITIONAL_ISLAMIC_DOMAINS
)


# ============================================================
# QUERY MAP
# ============================================================

QUERY_MAP = {
    "solat": "الصلاة",
    "sembahyang": "الصلاة",
    "wuduk": "الوضوء",
    "wudhu": "الوضوء",
    "tayammum": "التيمم",
    "puasa": "الصيام",
    "zakat": "الزكاة",
    "zakat fitrah": "زكاة الفطر",
    "haji": "الحج",
    "umrah": "العمرة",
    "haid": "الحيض",
    "nifas": "النفاس",
    "junub": "الجنابة",
    "mandi wajib": "الغسل",
    "nikah": "النكاح",
    "kahwin": "النكاح",
    "talak": "الطلاق",
    "cerai": "الطلاق",
    "rujuk": "الرجعة",
    "faraid": "الفرائض",
    "pusaka": "الميراث",
    "jual beli": "البيع",
    "riba": "الربا",
    "hutang": "الدين",
    "sedekah": "الصدقة",
    "wakaf": "الوقف",
    "korban": "الأضحية",
    "aqiqah": "العقيقة",
    "sujud sahwi": "سجود السهو",
    "imam": "الإمامة",
    "makmum": "الاقتداء في الصلاة",
    "jamak": "الجمع بين الصلاتين",
    "qasar": "قصر الصلاة",
    "jenazah": "صلاة الجنازة",
    "najis": "النجاسة",
    "istinja": "الاستنجاء",
    "istihadah": "الاستحاضة",
    "masjid": "المسجد",
    "azan": "الأذان",
    "iqamah": "الإقامة",
    "sah": "الصحة والبطلان في الفقه",
    "batal": "مبطلات العبادة",
    "haram": "الحرام",
    "halal": "الحلال",
}


# ============================================================
# GEMINI UTILITIES
# ============================================================

def gemini_generate(
    prompt: str,
    model: str = None,
    retries: int = None,
    temperature: float = 0.3,
) -> str:
    """Panggil Gemini dengan cubaan semula apabila berlaku ralat."""

    if GEMINI_CLIENT is None:
        raise RuntimeError("GOOGLE_API_KEY belum ditetapkan.")

    selected_model = model or LLM_MODEL
    attempts = GEMINI_RETRIES if retries is None else retries
    last_error = None

    config = types.GenerateContentConfig(temperature=temperature)

    for attempt in range(attempts + 1):
        try:
            response = GEMINI_CLIENT.models.generate_content(
                model=selected_model,
                contents=prompt,
                config=config,
            )

            text = getattr(response, "text", None)

            if text and text.strip():
                return text.strip()

            raise ValueError("Gemini memulangkan jawapan kosong.")

        except Exception as exc:
            last_error = exc
            print(
                f"[GEMINI ERROR] Cubaan {attempt + 1}/"
                f"{attempts + 1}: {exc}"
            )

            if attempt < attempts:
                time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(
        f"Gemini gagal selepas beberapa cubaan: {last_error}"
    )


def extract_json(text: str) -> dict:
    """Ekstrak objek JSON daripada respons model."""

    cleaned = (text or "").strip()

    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*```$", "", cleaned)

    match = re.search(
        r"\{.*\}",
        cleaned,
        flags=re.DOTALL,
    )

    if not match:
        raise ValueError("JSON tidak ditemukan.")

    result = json.loads(match.group(0))

    if not isinstance(result, dict):
        raise ValueError("Format JSON bukan objek.")

    return result


# ============================================================
# MESSAGE CLASSIFIER
# ============================================================

def classify_message(message: str) -> str:
    message = (message or "").strip()

    if not message:
        return "UNCLEAR"

    prompt = f"""
Anda ialah pengelas mesej bagi TanyaFiqihBot. Tentukan SATU kategori sahaja (GREETING, FIQH_QUESTION, GENERAL_QUESTION, UNCLEAR).
Pulangkan JSON sahaja dalam format: {{"category":"KATEGORI"}}

Mesej pengguna:
{message}
"""

    try:
        raw = gemini_generate(prompt, retries=1, temperature=0.1)
        data = extract_json(raw)

        category = str(
            data.get("category", "")
        ).strip().upper()

        allowed = {
            "GREETING",
            "FIQH_QUESTION",
            "GENERAL_QUESTION",
            "UNCLEAR",
        }

        if category in allowed:
            return category

        return "UNCLEAR"

    except Exception as exc:
        print(f"[CLASSIFIER ERROR] {exc}")

        normalized = re.sub(
            r"[^a-zA-Z0-9\s]",
            "",
            message.lower(),
        ).strip()

        greeting_patterns = {
            "hi",
            "hii",
            "hiii",
            "hai",
            "hello",
            "helo",
            "assalamualaikum",
            "assalamualaikum wbt",
            "salam",
            "salam sejahtera",
        }

        if normalized in greeting_patterns:
            return "GREETING"

        return "UNCLEAR"


def greeting_response(message: str) -> str:
    normalized = (message or "").lower()

    if (
        "assalamualaikum" in normalized
        or normalized.strip() == "salam"
    ):
        return (
            "Waalaikumussalam warahmatullahi wabarakatuh 😊\n\n"
            "Selamat datang ke TanyaFiqihBot.\n"
            "Boleh tanya saya soalan berkaitan fiqh Islam."
        )

    return (
        "Hai! 👋 Selamat datang ke TanyaFiqihBot.\n\n"
        "Saya membantu mencari jawapan berkaitan fiqh Islam "
        "berserta rujukan sumber. Apa yang anda ingin tanya?"
    )


def general_response() -> str:
    return (
        "📚 *Tentang TanyaFiqihBot*\n\n"
        "Bot ini membantu menjawab persoalan fiqh Islam "
        "dengan mencari sumber kitab dan sumber agama yang berkaitan.\n\n"
        "Sila ajukan soalan fiqh yang ingin anda semak."
    )


# ============================================================
# TURATH QUERY PLANNER & SEARCH
# ============================================================

def fallback_turath_queries(question: str) -> list:
    question = question.strip()
    lowered = question.lower()
    queries = [question]

    for keyword, arabic in QUERY_MAP.items():
        if keyword in lowered:
            queries.append(arabic)
            queries.append(f"{arabic} حكم")

    unique = []
    seen = set()

    for query in queries:
        query = query.strip()
        if query and query not in seen:
            seen.add(query)
            unique.append(query)

    return unique[:5]


def plan_turath_queries(question: str) -> list:
    prompt = f"""
Tukarkan soalan pengguna kepada kata kunci ringkas untuk carian kitab turath Arab.
Pulangkan JSON sahaja dalam format: {{"queries":["kata kunci 1","kata kunci 2"]}}
Maksimum 5 pertanyaan.

Soalan: {question}
"""

    try:
        raw = gemini_generate(
            prompt,
            model=ARABIC_QUERY_MODEL,
            retries=1,
            temperature=0.2,
        )

        data = extract_json(raw)
        queries = data.get("queries", [])

        if not isinstance(queries, list):
            raise ValueError("Medan queries bukan senarai.")

        cleaned = []
        seen = set()

        for query in queries:
            if not isinstance(query, str):
                continue
            query = query.strip()
            if query and query not in seen:
                cleaned.append(query)
                seen.add(query)

        if question not in seen:
            cleaned.insert(0, question)

        if cleaned:
            return cleaned[:5]

    except Exception as exc:
        print(f"[TURATH PLANNER ERROR] {exc}")

    return fallback_turath_queries(question)


def search_turath(queries: list) -> list:
    endpoint = f"{TURATH_SERVICE_URL}/search"

    try:
        response = HTTP_SESSION.post(
            endpoint,
            json={"queries": queries},
            timeout=TURATH_TIMEOUT,
        )

        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in (
                "results",
                "sources",
                "items",
                "data",
                "documents",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    return value

            if any(
                key in payload
                for key in (
                    "text",
                    "content",
                    "book",
                    "title",
                    "snippet",
                )
            ):
                return [payload]

        return []

    except Exception as exc:
        print(f"[TURATH SEARCH ERROR] {exc}")
        return []


def first_value(item: dict, keys: tuple) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float)):
            value = str(value).strip()
            if value:
                return value
    return ""


def normalize_turath_sources(raw_sources: list) -> list:
    normalized = []

    for item in raw_sources:
        if not isinstance(item, dict):
            continue

        title = first_value(
            item,
            ("book", "book_name", "bookTitle", "title", "name", "source"),
        )
        author = first_value(item, ("author", "author_name", "writer"))
        text = first_value(
            item,
            ("text", "content", "passage", "body", "snippet", "matched_text", "excerpt"),
        )
        page = first_value(item, ("page", "page_number", "page_no", "volume_page"))
        volume = first_value(item, ("volume", "vol", "volume_number"))
        url = first_value(item, ("url", "link", "source_url"))

        if not text:
            continue

        if not title:
            title = "Sumber Turath"

        normalized.append(
            {
                "kind": "turath",
                "title": title,
                "author": author,
                "text": text[:MAX_SOURCE_CHARS],
                "page": page,
                "volume": volume,
                "url": url,
                "domain": "",
            }
        )

    return normalized


def rank_sources(sources: list, question: str) -> list:
    return sources[:MAX_SOURCE_COUNT]


def search_brave_web(question: str) -> list:
    if not BRAVE_SEARCH_API_KEY:
        return []

    endpoint = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }

    try:
        response = HTTP_SESSION.get(
            endpoint,
            headers=headers,
            params={
                "q": question,
                "count": BRAVE_SEARCH_COUNT,
                "country": "MY",
                "search_lang": "ms",
                "safesearch": "moderate",
            },
            timeout=BRAVE_TIMEOUT,
        )

        response.raise_for_status()
        payload = response.json()
        results = payload.get("web", {}).get("results", [])

        sources = []
        for r in results:
            url = r.get("url", "")
            desc = r.get("description", "")
            title = r.get("title", "")

            if url and desc:
                domain = urlparse(url).hostname or ""
                sources.append(
                    {
                        "kind": "web",
                        "title": title or domain or "Sumber Web",
                        "author": "",
                        "text": desc[:MAX_SOURCE_CHARS],
                        "page": "",
                        "volume": "",
                        "url": url,
                        "domain": domain.lower(),
                    }
                )
        return sources

    except Exception as exc:
        print(f"[BRAVE SEARCH ERROR] {exc}")
        return []


# ============================================================
# ANSWER GENERATION
# ============================================================

def build_source_context(sources: list) -> str:
    blocks = []
    for index, source in enumerate(sources, start=1):
        title = source.get("title", "Sumber tidak diketahui")
        text = source.get("text", "")
        blocks.append(f"[S{index}] Tajuk: {title}\nPetikan:\n{text}")
    return "\n\n---\n\n".join(blocks)


def generate_fiqh_answer(question: str, sources: list) -> str:
    if not sources:
        return "Maaf, tiada sumber yang mencukupi untuk mengesahkan jawapan."

    context = build_source_context(sources)
    prompt = f"""
Anda ialah pembantu penyelidikan fiqh Islam bagi TanyaFiqihBot.
Jawab soalan pengguna dalam bahasa Melayu yang jelas dan sopan berpandukan sumber di bawah sahaja. Gunakan penanda [S1], [S2] dan seterusnya.

Soalan:
{question}

SUMBER RUJUKAN:
{context}
"""

    try:
        return gemini_generate(prompt, temperature=0.3)
    except Exception as exc:
        print(f"[ANSWER GENERATION ERROR] {exc}")
        return "Maaf, ralat berlaku semasa menjana jawapan."


def format_source_reference(source: dict, index: int) -> str:
    title = source.get("title", "Sumber tidak diketahui")
    author = source.get("author", "")
    page = source.get("page", "")
    volume = source.get("volume", "")
    url = source.get("url", "")

    parts = [f"[S{index}] *{title}*"]
    if author:
        parts.append(f"Pengarang: {author}")
    if volume:
        parts.append(f"Jilid: {volume}")
    if page:
        parts.append(f"Hlm.: {page}")
    if url:
        parts.append(f"Pautan: {url}")

    return "\n".join(parts)


def build_references(sources: list) -> str:
    if not sources:
        return ""

    references = [
        format_source_reference(source, index)
        for index, source in enumerate(sources, start=1)
    ]
    return "📚 *Rujukan yang diperoleh*\n\n" + "\n\n".join(references)


def answer_question(question: str) -> str:
    queries = plan_turath_queries(question)
    raw_turath = search_turath(queries)
    sources = normalize_turath_sources(raw_turath)

    if not sources:
        sources = search_brave_web(question)

    if not sources:
        return "Maaf, saya tidak menemui sumber yang mencukupi untuk mengesahkan jawapan ini."

    answer = generate_fiqh_answer(question, sources)
    references = build_references(sources)

    if references:
        return answer + "\n\n" + references

    return answer


# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "Assalamualaikum warahmatullahi wabarakatuh! 👋\n\n"
        "Selamat datang ke *TanyaFiqihBot*.\n\n"
        "Saya membantu mencari jawapan bagi persoalan fiqh "
        "Islam berserta rujukan sumber.\n\n"
        "Sila taip soalan anda."
    )

    if update.message:
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
        )


async def telegram_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()
    if not question:
        return

    try:
        category = await asyncio.to_thread(classify_message, question)

        if category == "GREETING":
            await update.message.reply_text(greeting_response(question))
            return

        if category == "GENERAL_QUESTION":
            await update.message.reply_text(
                general_response(),
                parse_mode="Markdown",
            )
            return

        if category == "UNCLEAR":
            await update.message.reply_text(
                "Maaf, saya kurang pasti maksud mesej anda. Boleh tulis soalan dengan lebih jelas?"
            )
            return

        status_message = await update.message.reply_text(
            "🔎 Saya sedang menyemak sumber rujukan... Sila tunggu sebentar..."
        )

        answer = await asyncio.to_thread(answer_question, question)

        max_length = 4000
        chunks = [
            answer[i : i + max_length]
            for i in range(0, len(answer), max_length)
        ]

        if chunks:
            await status_message.edit_text(
                chunks[0],
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )

            for chunk in chunks[1:]:
                await update.message.reply_text(
                    chunk,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
        else:
            await status_message.edit_text(
                "Maaf, tiada jawapan yang dapat dihasilkan."
            )

    except Exception as exc:
        print(f"[TELEGRAM HANDLER ERROR] {exc}")
        traceback.print_exc()

        try:
            await update.message.reply_text(
                "Maaf, berlaku masalah semasa memproses mesej. Sila cuba semula."
            )
        except Exception as reply_error:
            print(f"[TELEGRAM REPLY ERROR] {reply_error}")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "Maaf, arahan itu tidak dikenali. Taip /start untuk panduan."
        )


# ============================================================
# TELEGRAM APPLICATION LIFECYCLE
# ============================================================

def create_telegram_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN belum ditetapkan.")

    telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, telegram_answer)
    )
    telegram_app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    return telegram_app


async def run_telegram():
    telegram_app = create_telegram_app()

    try:
        await telegram_app.initialize()
        await telegram_app.start()

        if telegram_app.updater is None:
            raise RuntimeError("Telegram updater tidak tersedia.")

        await telegram_app.updater.start_polling(drop_pending_updates=False)
        print("[TELEGRAM] Polling bermula.")

        await asyncio.Event().wait()

    finally:
        print("[TELEGRAM] Sedang menutup polling...")
        try:
            if telegram_app.updater and telegram_app.updater.running:
                await telegram_app.updater.stop()
        except Exception as exc:
            print(f"[TELEGRAM STOP ERROR] {exc}")

        try:
            if telegram_app.running:
                await telegram_app.stop()
        except Exception as exc:
            print(f"[TELEGRAM APP STOP ERROR] {exc}")

        try:
            await telegram_app.shutdown()
        except Exception as exc:
            print(f"[TELEGRAM SHUTDOWN ERROR] {exc}")


def start_telegram():
    while True:
        try:
            print("[TELEGRAM] Memulakan servis...")
            asyncio.run(run_telegram())
            print("[TELEGRAM] Polling tamat. Cuba mulakan semula...")
        except Exception as exc:
            print(f"[TELEGRAM SUPERVISOR ERROR] {exc}")
            traceback.print_exc()

        time.sleep(max(TELEGRAM_RESTART_WAIT, 1))


def start_telegram_background():
    if not TELEGRAM_TOKEN:
        print("[WARNING] TELEGRAM_TOKEN tiada. Telegram polling tidak dimulakan.")
        return None

    thread = threading.Thread(
        target=start_telegram,
        name="telegram-supervisor",
        daemon=True,
    )
    thread.start()
    return thread


# ============================================================
# FLASK HEALTH ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({"service": "TanyaFiqihBot", "status": "running"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "TanyaFiqihBot",
            "gemini_configured": bool(GOOGLE_API_KEY),
            "telegram_configured": bool(TELEGRAM_TOKEN),
            "brave_configured": bool(BRAVE_SEARCH_API_KEY),
            "turath_service_url": TURATH_SERVICE_URL,
        }
    )


# ============================================================
# START BACKGROUND TELEGRAM SERVICE
# ============================================================

start_telegram_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
