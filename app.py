
import os
import re
import json
import asyncio
import logging
import threading
import traceback
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
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# 1. CONFIGURATION
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.1-flash-lite",
).strip()

TURATH_SERVICE_URL = os.getenv(
    "TURATH_SERVICE_URL",
    "http://127.0.0.1:8765",
).strip().rstrip("/")

TURATH_SEARCH_URL = f"{TURATH_SERVICE_URL}/search"

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "35"))
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "60"))
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "10"))
MAX_TELEGRAM_LENGTH = 3500

# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    force=True,
)

logger = logging.getLogger("TanyaFiqihBot")

# ============================================================
# 3. FLASK HEALTH CHECK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "TanyaFiqihBot is running."


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "telegram_configured": bool(TELEGRAM_TOKEN),
        "google_configured": bool(GOOGLE_API_KEY),
        "turath_url": TURATH_SEARCH_URL,
    })


# ============================================================
# 4. GEMINI CLIENT
# ============================================================

gemini_client = (
    genai.Client(api_key=GOOGLE_API_KEY)
    if GOOGLE_API_KEY
    else None
)


def gemini_generate(prompt, timeout=None):
    """Panggil Gemini dan pulangkan teks jawapan."""

    if gemini_client is None:
        raise RuntimeError("GOOGLE_API_KEY belum ditetapkan.")

    logger.info("[GEMINI] Memulakan permintaan.")

    response = gemini_client.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config={
            "temperature": 0.2,
            "http_options": {
                "timeout": (timeout or GEMINI_TIMEOUT) * 1000,
            },
        },
    )

    answer = (response.text or "").strip()

    if not answer:
        raise RuntimeError("Gemini memulangkan jawapan kosong.")

    logger.info("[GEMINI] Jawapan diterima: %d aksara.", len(answer))
    return answer


def extract_json(text):
    """Ekstrak JSON jika Gemini membalutnya dengan markdown."""

    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


# ============================================================
# 5. PENGESAN SOALAN FIQH
# ============================================================

FIQH_KEYWORDS = [
    "hukum", "haram", "halal", "wajib", "sunat", "sunnah",
    "makruh", "harus", "sah", "batal", "berdosa", "dosa",
    "fiqh", "fikah", "fekah", "fatwa", "mazhab", "syafie",
    "syafi'i", "solat", "sembahyang", "puasa", "zakat",
    "haji", "umrah", "wuduk", "wudhu", "tayammum",
    "haid", "nifas", "junub", "mandi wajib", "nikah",
    "talak", "cerai", "pusaka", "faraid", "riba",
    "jual beli", "hutang", "mencuri", "curi", "pencuri",
    "zina", "arak", "judi", "sumpah", "nazar",
    "qada", "qadha", "jamak", "qasar", "sujud sahwi",
    "imam", "makmum", "azan", "iqamah", "korban",
    "aqiqah", "sedekah", "wakaf", "najis", "aurat",
    "sentuh", "bersentuhan", "bersuci", "masbuk",
]

GREETING_WORDS = [
    "assalamualaikum",
    "salam",
    "hai",
    "hello",
    "helo",
    "selamat pagi",
    "selamat petang",
    "selamat malam",
]


def fallback_is_fiqh_question(text):
    text_lower = text.lower().strip()

    if any(text_lower == word for word in GREETING_WORDS):
        return False

    if any(word in text_lower for word in FIQH_KEYWORDS):
        return True

    if re.search(
        r"\b(apakah hukum|apa hukum|bolehkah|adakah sah|"
        r"bagaimana hukum|apa pandangan islam|"
        r"menurut islam|dalam islam)\b",
        text_lower,
    ):
        return True

    return False


def classify_question(text):
    """
    Gunakan Gemini untuk klasifikasi jika tersedia.
    Jika gagal, gunakan pengesanan kata kunci.
    """

    if fallback_is_fiqh_question(text):
        logger.info("[CLASSIFY] Dikesan sebagai soalan fiqh.")
        return True

    if not GOOGLE_API_KEY:
        logger.info("[CLASSIFY] Bukan soalan fiqh berdasarkan kata kunci.")
        return False

    prompt = f"""
Tentukan sama ada mesej ini meminta hukum, penjelasan atau rujukan
berkaitan fiqh Islam.

Mesej:
{text}

Balas JSON sahaja:
{{"is_fiqh": true}}

Jika mesej hanya ucapan biasa, sapaan atau perbualan umum,
pulangkan false.
"""

    try:
        result = extract_json(gemini_generate(prompt, timeout=20))
        value = result.get("is_fiqh", False)
        return value is True or str(value).lower() == "true"

    except Exception:
        logger.exception("[CLASSIFY] Gemini gagal.")
        return fallback_is_fiqh_question(text)


# ============================================================
# 6. PERANCANG KATA KUNCI TURATH
# ============================================================

ARABIC_KEYWORDS = {
    "mencuri": [
        "السرقة",
        "حكم السرقة",
        "حد السرقة",
        "السارق",
        "باب السرقة",
    ],
    "curi": [
        "السرقة",
        "حكم السرقة",
        "السارق",
    ],
    "pencuri": [
        "السارق",
        "السرقة",
        "حد السرقة",
    ],
    "zina": [
        "الزنا",
        "حد الزنا",
        "أحكام الزنا",
    ],
    "solat": [
        "كتاب الصلاة",
        "أحكام الصلاة",
        "صفة الصلاة",
    ],
    "sembahyang": [
        "كتاب الصلاة",
        "أحكام الصلاة",
    ],
    "puasa": [
        "كتاب الصيام",
        "أحكام الصيام",
    ],
    "zakat": [
        "كتاب الزكاة",
        "أحكام الزكاة",
    ],
    "wuduk": [
        "كتاب الطهارة",
        "الوضوء",
        "نواقض الوضوء",
    ],
    "wudhu": [
        "كتاب الطهارة",
        "الوضوء",
    ],
    "riba": [
        "الربا",
        "كتاب البيوع",
        "أحكام الربا",
    ],
    "nikah": [
        "كتاب النكاح",
        "أحكام النكاح",
    ],
    "talak": [
        "كتاب الطلاق",
        "أحكام الطلاق",
    ],
    "cerai": [
        "كتاب الطلاق",
        "أحكام الطلاق",
    ],
    "haid": [
        "كتاب الحيض",
        "أحكام الحيض",
    ],
    "najis": [
        "كتاب الطهارة",
        "إزالة النجاسة",
    ],
}


def unique_strings(items, limit=12):
    output = []
    seen = set()

    for item in items:
        if not isinstance(item, str):
            continue

        item = item.strip()

        if not item:
            continue

        key = item.casefold()

        if key not in seen:
            seen.add(key)
            output.append(item)

        if len(output) >= limit:
            break

    return output


def build_turath_queries(question):
    """
    Carian Turath diutamakan.
    Masukkan soalan asal, kata kunci khusus dan istilah Arab.
    """

    queries = [question.strip()]
    lowered = question.lower()

    for keyword, arabic_terms in ARABIC_KEYWORDS.items():
        if keyword in lowered:
            queries.extend(arabic_terms)

    if GOOGLE_API_KEY:
        prompt = f"""
Bina kata kunci carian kitab turath bagi soalan fiqh ini:

{question}

Sediakan:
- Istilah Arab yang tepat.
- Nama bab fiqh yang berkaitan.
- Istilah fiqh mazhab Syafie jika sesuai.

Jangan jawab hukum. Hasilkan JSON sahaja:
{{"queries":["istilah Arab 1","istilah Arab 2"]}}

Maksimum 6 kata kunci. Pastikan istilah relevan.
"""

        try:
            result = extract_json(
                gemini_generate(prompt, timeout=20)
            )
            extra = result.get("queries", [])

            if isinstance(extra, list):
                queries.extend(
                    item for item in extra
                    if isinstance(item, str)
                )

        except Exception:
            logger.exception("[QUERY] Gagal membina kata kunci Gemini.")

    final_queries = unique_strings(queries, limit=12)

    logger.info("[QUERY] Soalan asal: %s", question)
    logger.info("[QUERY] Jumlah kata kunci: %d", len(final_queries))

    for index, query in enumerate(final_queries, 1):
        logger.info("[QUERY %d] %s", index, query)

    return final_queries


# ============================================================
# 7. CARIAN SERVIS TURATH
# ============================================================

def extract_records(data):
    """Kenal pasti senarai sumber dalam pelbagai bentuk respons API."""

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "sources",
        "results",
        "documents",
        "items",
        "data",
        "matches",
        "records",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            nested = extract_records(value)

            if nested:
                return nested

    # Sesetengah servis memulangkan satu rekod sahaja.
    if any(
        key in data
        for key in ("text", "content", "page_content", "document")
    ):
        return [data]

    return []


def search_turath(queries):
    """
    Panggil servis Turath.
    Ubah format payload di sini sahaja jika API anda menggunakan
    nama medan yang berbeza.
    """

    logger.info("[TURATH] POST %s", TURATH_SEARCH_URL)

    payload = {"queries": queries}

    try:
        response = requests.post(
            TURATH_SEARCH_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        logger.info("[TURATH] HTTP STATUS: %s", response.status_code)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            logger.error("[TURATH] Respons bukan JSON: %s",
                         response.text[:500])
            return []

        records = extract_records(data)

        logger.info("[TURATH] Rekod diterima: %d", len(records))
        logger.info("[TURATH] Jenis respons: %s", type(data).__name__)

        if isinstance(data, dict):
            logger.info(
                "[TURATH] Medan respons: %s",
                list(data.keys()),
            )

        return records

    except requests.RequestException:
        logger.exception("[TURATH] Permintaan carian gagal.")
        return []


# ============================================================
# 8. NORMALISASI RUJUKAN KITAB
# ============================================================

def get_nested_dict(record, key):
    value = record.get(key)

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass

    return {}


def first_value(*values):
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()

    return ""


def normalize_source(record):
    if isinstance(record, str):
        text = record.strip()

        if not text:
            return None

        return {
            "title": "Kitab tidak dikenal",
            "author": "",
            "page": "",
            "url": "",
            "text": text,
        }

    if not isinstance(record, dict):
        return None

    metadata = get_nested_dict(record, "metadata")
    payload = get_nested_dict(record, "payload")

    document = record.get("document", {})

    if isinstance(document, str):
        document_text = document
        document_dict = {}
    elif isinstance(document, dict):
        document_text = ""
        document_dict = document
    else:
        document_text = ""
        document_dict = {}

    meta_doc = get_nested_dict(document_dict, "metadata")

    containers = [
        record,
        metadata,
        payload,
        document_dict,
        meta_doc,
    ]

    def pick(*keys):
        for container in containers:
            for key in keys:
                value = container.get(key)

                if value is not None and not isinstance(
                    value, (dict, list)
                ):
                    if str(value).strip():
                        return str(value).strip()

        return ""

    text = first_value(
        record.get("text"),
        record.get("content"),
        record.get("page_content"),
        record.get("chunk_text"),
        record.get("passage"),
        record.get("snippet"),
        document_dict.get("text"),
        document_dict.get("content"),
        document_text,
    )

    title = pick(
        "book", "book_title", "title", "kitab",
        "source", "name", "file_name",
    )

    author = pick(
        "author", "book_author", "pengarang", "muallif",
    )

    page = pick(
        "page", "page_number", "pages", "halaman",
        "volume_page", "jilid_halaman",
    )

    url = pick(
        "url", "link", "source_url", "reference_url",
    )

    if not text:
        return None

    return {
        "title": title or "Kitab tidak dikenal",
        "author": author,
        "page": page,
        "url": url,
        "text": text,
    }


def normalize_sources(records):
    normalized = []

    for record in records:
        source = normalize_source(record)

        if source:
            normalized.append(source)

    # Buang petikan yang sama.
    seen = set()
    unique = []

    for source in normalized:
        key = re.sub(
            r"\s+",
            " ",
            source["text"].strip().lower(),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(source)

    logger.info("[TURATH] Selepas normalisasi: %d", len(unique))

    return unique


def rank_sources(sources, question):
    """
    Susun secara ringkas berdasarkan perkataan soalan yang sepadan
    dengan teks sumber. Ini bukan pengesahan hukum.
    """

    words = {
        word.lower()
        for word in re.findall(r"\w+", question)
        if len(word) >= 3
    }

    def score(source):
        text = (
            source["title"] + " " + source["text"]
        ).lower()

        return sum(1 for word in words if word in text)

    return sorted(
        sources,
        key=score,
        reverse=True,
    )[:MAX_SOURCES]


# ============================================================
# 9. JANA JAWAPAN BERDASARKAN SUMBER TURATH
# ============================================================

def format_references(sources):
    lines = ["", "RUJUKAN SUMBER TURATH"]

    for index, source in enumerate(sources, 1):
        parts = [f"[S{index}] {source['title']}"]

        if source["author"]:
            parts.append(f"Pengarang: {source['author']}")

        if source["page"]:
            parts.append(f"Halaman: {source['page']}")

        if source["url"]:
            parts.append(f"Pautan: {source['url']}")

        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def generate_fiqh_answer(question, sources):
    snippets = []

    for index, source in enumerate(sources, 1):
        snippets.append(
            f"[S{index}]\n"
            f"Kitab: {source['title']}\n"
            f"Pengarang: {source['author'] or 'Tidak dinyatakan'}\n"
            f"Halaman: {source['page'] or 'Tidak dinyatakan'}\n"
            f"Petikan:\n{source['text'][:4500]}"
        )

    source_text = "\n\n".join(snippets)

    prompt = f"""
Anda ialah pembantu penyelidikan fiqh Islam.
Utamakan rujukan turath yang diberikan di bawah.

SOALAN:
{question}

SUMBER TURATH YANG DITEMUKAN:
{source_text}

ARAHAN WAJIB:
1. Jawab dalam Bahasa Melayu yang jelas.
2. Gunakan hanya maklumat yang benar-benar disokong petikan sumber.
3. Jangan reka nama kitab, pengarang, nombor halaman atau petikan.
4. Letakkan penanda [S1], [S2] dan seterusnya pada dakwaan yang
   disokong oleh sumber berkenaan.
5. Jika sumber tidak cukup untuk menentukan hukum, nyatakan dengan
   jujur bahawa petikan yang diperoleh belum mencukupi.
6. Bezakan petikan kitab dengan kesimpulan atau huraian anda.
7. Jangan mendakwa semua sumber mewakili pendapat muktamad mazhab.
8. Jika terdapat perbezaan pendapat dalam sumber, nyatakan secara
   berhati-hati.
9. Jangan mereka-reka teks Arab asal jika tiada dalam petikan.
10. Berikan jawapan terus kepada soalan pengguna.

Susunan jawapan:
- Jawapan ringkas
- Huraian dan dalil daripada sumber yang diberikan
- Catatan jika sumber tidak mencukupi

Jangan ulang senarai bibliografi dalam jawapan kerana sistem akan
menambah senarai rujukan secara berasingan.
"""

    return gemini_generate(prompt)


def answer_question_turath(question):
    """
    Pipeline penuh: bina query -> carian Turath -> normalisasi
    -> pilih sumber -> jana jawapan.
    """

    logger.info("[PIPELINE] MULA: %s", question)

    queries = build_turath_queries(question)
    raw_records = search_turath(queries)

    sources = normalize_sources(raw_records)
    sources = rank_sources(sources, question)

    logger.info("[PIPELINE] Sumber akhir: %d", len(sources))

    if not sources:
        logger.warning("[PIPELINE] Tiada sumber Turath ditemukan.")

        return {
            "found": False,
            "answer": "",
            "sources": [],
        }

    try:
        logger.info("[PIPELINE] Memulakan Gemini untuk jawapan.")

        answer = generate_fiqh_answer(question, sources)

        logger.info("[PIPELINE] Gemini selesai menjana jawapan.")

        references = format_references(sources)

        return {
            "found": True,
            "answer": answer + "\n\n" + references,
            "sources": sources,
        }

    except Exception:
        logger.exception("[PIPELINE] Gagal menjana jawapan.")

        # Jangan buang sumber yang telah ditemukan.
        return {
            "found": True,
            "answer": (
                "Carian Turath berjaya menemukan sumber, tetapi "
                "penjanaan jawapan automatik gagal.\n\n"
                "Sila rujuk petikan sumber berikut dan cuba semula."
                + references_from_sources(sources)
            ),
            "sources": sources,
        }


def references_from_sources(sources):
    return "\n\n" + "\n\n".join(
        f"[S{i}] {source['title']}\n"
        f"Pengarang: {source['author'] or 'Tidak dinyatakan'}\n"
        f"Halaman: {source['page'] or 'Tidak dinyatakan'}\n"
        f"Petikan: {source['text'][:1200]}"
        for i, source in enumerate(sources, 1)
    )


# ============================================================
# 10. CARIAN WEB: HANYA SELEPAS KEBENARAN PENGGUNA
# ============================================================

def search_brave(question):
    if not BRAVE_API_KEY:
        return (
            "Carian umum belum dikonfigurasikan. "
            "Sila tetapkan BRAVE_API_KEY jika mahu menggunakan "
            "carian web."
        )

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            params={
                "q": question,
                "count": 5,
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

        results = data.get("web", {}).get("results", [])

        if not results:
            return "Carian web tidak menemukan hasil yang sesuai."

        lines = [
            "HASIL CARIAN UMUM (WEB)",
            "Maklumat ini bukan pengganti semakan kitab turath.\n",
        ]

        for index, item in enumerate(results, 1):
            title = item.get("title", "Tanpa tajuk")
            description = item.get("description", "")
            url = item.get("url", "")

            lines.append(
                f"{index}. {title}\n"
                f"{description}\n"
                f"{url}"
            )

        return "\n\n".join(lines)

    except Exception:
        logger.exception("[WEB] Carian umum gagal.")
        return "Carian web gagal. Sila cuba lagi kemudian."


# ============================================================
# 11. TELEGRAM UTILITIES
# ============================================================

def split_message(text, limit=MAX_TELEGRAM_LENGTH):
    chunks = []

    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)

        if split_at < limit // 2:
            split_at = limit

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    if text:
        chunks.append(text)

    return chunks


async def send_long_answer(message, text, status_message=None):
    chunks = split_message(text)

    if not chunks:
        chunks = ["Tiada jawapan untuk dipaparkan."]

    first_sent = False

    if status_message is not None:
        try:
            await status_message.edit_text(chunks[0])
            first_sent = True
        except Exception:
            logger.exception("[SEND] Gagal mengedit status mesej.")

    if not first_sent:
        try:
            await message.reply_text(chunks[0])
        except Exception:
            logger.exception("[SEND] Gagal menghantar jawapan pertama.")
            return

    for chunk in chunks[1:]:
        try:
            await message.reply_text(chunk)
        except Exception:
            logger.exception("[SEND] Gagal menghantar sambungan.")
            break


def no_source_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔎 Benarkan carian umum",
                callback_data="web_yes",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Cuba Turath semula",
                callback_data="turath_retry",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Tidak, terima kasih",
                callback_data="web_no",
            ),
        ],
    ])


# ============================================================
# 12. COMMAND /start
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = (
        "السلام عليكم ورحمة الله وبركاته\n\n"
        "Selamat datang ke TanyaFiqihBot.\n\n"
        "Bot ini akan mencari sumber kitab turath terlebih dahulu "
        "sebelum menjana jawapan fiqh.\n\n"
        "Jika sumber turath tidak ditemukan, anda boleh memilih "
        "sama ada mahu membuat carian umum.\n\n"
        "Hantar soalan anda, contohnya:\n"
        "• Apakah hukum mencuri?\n"
        "• Apakah perkara yang membatalkan wuduk?\n"
        "• Bagaimana hukum jual beli secara hutang?\n\n"
        "Nota: Semak rujukan asal dan rujuk ahli ilmu bagi persoalan "
        "yang memerlukan fatwa khusus."
    )

    await update.effective_message.reply_text(text)


# ============================================================
# 13. PROSES SOALAN
# ============================================================

async def process_fiqh_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question=None,
):
    message = update.effective_message

    if message is None:
        return

    question = (question or message.text or "").strip()

    if not question:
        await message.reply_text("Sila tulis soalan anda.")
        return

    logger.info("[TELEGRAM] Soalan diterima: %s", question)

    try:
        status_message = await message.reply_text(
            "📚 Sedang mencari rujukan kitab turath dahulu..."
        )
    except Exception:
        logger.exception("[TELEGRAM] Gagal menghantar status.")
        return

    try:
        result = await asyncio.to_thread(
            answer_question_turath,
            question,
        )

        logger.info(
            "[TELEGRAM] Pipeline selesai. found=%s",
            result.get("found"),
        )

        if result.get("found"):
            await send_long_answer(
                message,
                result["answer"],
                status_message,
            )
            return

        await status_message.edit_text(
            "Saya belum menemukan petikan kitab turath yang "
            "dapat digunakan untuk menjawab soalan ini.\n\n"
            "Adakah anda mahu saya cuba carian umum di web?",
            reply_markup=no_source_keyboard(),
        )

        # Simpan soalan untuk tindakan susulan pengguna.
        context.user_data["pending_question"] = question

    except Exception:
        logger.exception("[TELEGRAM] Pemprosesan soalan gagal.")

        try:
            await status_message.edit_text(
                "Maaf, berlaku ralat ketika memproses soalan.\n"
                "Sila cuba lagi sebentar lagi."
            )
        except Exception:
            logger.exception("[TELEGRAM] Gagal memaparkan ralat.")


# ============================================================
# 14. HANDLE MESEJ MASUK
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if not text:
        return

    if any(text.lower() == word for word in GREETING_WORDS):
        await message.reply_text(
            "Waalaikumussalam. Silakan ajukan soalan fiqh anda."
        )
        return

    try:
        is_fiqh = await asyncio.to_thread(
            classify_question,
            text,
        )

        if is_fiqh:
            await process_fiqh_question(
                update,
                context,
                text,
            )
        else:
            await message.reply_text(
                "Saya membantu pencarian rujukan fiqh dan kitab "
                "turath. Sila ajukan soalan berkaitan hukum Islam."
            )

    except Exception:
        logger.exception("[TELEGRAM] Gagal mengendalikan mesej.")

        try:
            await message.reply_text(
                "Berlaku ralat semasa memproses mesej anda."
            )
        except Exception:
            logger.exception("[TELEGRAM] Gagal menghantar mesej ralat.")


# ============================================================
# 15. BUTANG PILIHAN CARIAN UMUM
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    action = query.data or ""
    question = context.user_data.get("pending_question", "")

    if action == "web_no":
        context.user_data.pop("pending_question", None)

        await query.edit_message_text(
            "Baik. Saya tidak akan membuat carian umum. "
            "Anda boleh menghantar soalan lain untuk carian Turath."
        )
        return

    if action == "web_yes":
        if not question:
            await query.edit_message_text(
                "Soalan asal tidak ditemukan. Sila hantar semula."
            )
            return

        if not BRAVE_API_KEY:
            await query.edit_message_text(
                "Carian umum belum dikonfigurasikan. "
                "Pentadbir perlu menetapkan BRAVE_API_KEY."
            )
            return

        await query.edit_message_text(
            "🌐 Anda telah membenarkan carian umum. Sedang mencari..."
        )

        result = await asyncio.to_thread(
            search_brave,
            question,
        )

        await send_long_answer(
            query.message,
            result,
        )

        context.user_data.pop("pending_question", None)
        return

    if action == "turath_retry":
        if not question:
            await query.edit_message_text(
                "Soalan asal tidak ditemukan. Sila hantar semula."
            )
            return

        await query.edit_message_text(
            "🔄 Mengulangi carian kitab turath..."
        )

        try:
            result = await asyncio.to_thread(
                answer_question_turath,
                question,
            )

            if result.get("found"):
                await send_long_answer(
                    query.message,
                    result["answer"],
                )
            else:
                await query.message.reply_text(
                    "Carian Turath masih belum menemukan sumber "
                    "yang boleh digunakan.",
                    reply_markup=no_source_keyboard(),
                )

        except Exception:
            logger.exception("[CALLBACK] Ulangan carian gagal.")
            await query.message.reply_text(
                "Carian gagal. Sila cuba semula."
            )


# ============================================================
# 16. RALAT TELEGRAM
# ============================================================

async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "[TELEGRAM ERROR] %s",
        context.error,
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )


# ============================================================
# 17. START BOT
# ============================================================

bot_thread = None
bot_lock = threading.Lock()


async def telegram_main():
    if not TELEGRAM_TOKEN:
        logger.error(
            "TELEGRAM_TOKEN kosong. "
            "Tetapkan token bot Telegram terlebih dahulu."
        )
        return

    telegram_app = None

    try:
        logger.info("[STARTUP] Membina Telegram Application...")

        telegram_app = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        # Daftarkan handler sebelum polling.
        telegram_app.add_handler(
            CommandHandler("start", start_command)
        )

        telegram_app.add_handler(
            CallbackQueryHandler(callback_handler)
        )

        telegram_app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_message,
            )
        )

        telegram_app.add_error_handler(telegram_error_handler)

        logger.info("[STARTUP] Menguji token Telegram...")

        bot_info = await telegram_app.bot.get_me()

        logger.info(
            "[STARTUP] Token sah. Bot: @%s (ID %s)",
            bot_info.username,
            bot_info.id,
        )

        await telegram_app.initialize()
        await telegram_app.start()

        if telegram_app.updater is None:
            raise RuntimeError(
                "Updater tidak tersedia. Semak pemasangan "
                "python-telegram-bot."
            )

        logger.info("[STARTUP] Memulakan polling...")

        await telegram_app.updater.start_polling(
            drop_pending_updates=False,
        )

        logger.info(
            "============================================"
        )
        logger.info("TANYAFIQIHBOT BERJAYA DIMULAKAN")
        logger.info("@%s", bot_info.username)
        logger.info("Menunggu mesej Telegram...")
        logger.info(
            "============================================"
        )

        await asyncio.Event().wait()

    except Exception:
        logger.error(
            "[STARTUP FAILED]\n%s",
            traceback.format_exc(),
        )

    finally:
        if telegram_app is not None:
            try:
                if (
                    telegram_app.updater is not None
                    and telegram_app.updater.running
                ):
                    await telegram_app.updater.stop()

                if telegram_app.running:
                    await telegram_app.stop()

                await telegram_app.shutdown()

            except Exception:
                logger.exception(
                    "[SHUTDOWN] Gagal menutup Telegram dengan sempurna."
                )


def telegram_worker():
    try:
        asyncio.run(telegram_main())
    except Exception:
        logger.exception("[THREAD] Thread Telegram terhenti.")


def start_bot_once():
    global bot_thread

    with bot_lock:
        if bot_thread is not None and bot_thread.is_alive():
            logger.info("[STARTUP] Thread bot sudah berjalan.")
            return

        bot_thread = threading.Thread(
            target=telegram_worker,
            name="TanyaFiqihBot-Worker",
            daemon=True,
        )

        bot_thread.start()
        logger.info("[STARTUP] Thread bot dilancarkan.")


# ============================================================
# 18. MAIN
# ============================================================

if __name__ == "__main__":
    logger.info("============================================")
    logger.info("Memulakan TanyaFiqihBot...")
    logger.info("Turath URL: %s", TURATH_SEARCH_URL)
    logger.info("Gemini model: %s", LLM_MODEL)
    logger.info("Google API key tersedia: %s", bool(GOOGLE_API_KEY))
    logger.info("Telegram token tersedia: %s", bool(TELEGRAM_TOKEN))
    logger.info("Brave API key tersedia: %s", bool(BRAVE_API_KEY))
    logger.info("============================================")

    start_bot_once()

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
