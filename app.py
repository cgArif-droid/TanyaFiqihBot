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

HTTP_SESSION = requests.Session()

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

# Domain tambahan yang dikecualikan
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
    "kahwin": "النkاح",
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
    temperature: float = 0.25,
) -> str:
    """Panggil Gemini dengan kawalan cubaan semula dan suhu yang stabil."""

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
Anda ialah pengelas mesej bagi Telegram TanyaFiqihBot.

Tentukan SATU kategori untuk mesej pengguna.

GREETING:
Sapaan sahaja seperti hi, hai, hello, salam atau assalamualaikum tanpa pertanyaan lain.

FIQH_QUESTION:
Pertanyaan tentang hukum Islam, fiqh, ibadah, taharah, solat, puasa, zakat, haji, muamalat, nikah, talak, faraid, akidah, adab Islam, fatwa, dalil atau kitab agama.

GENERAL_QUESTION:
Pertanyaan tentang fungsi atau cara menggunakan bot, atau soalan bukan fiqh yang jelas.

UNCLEAR:
Mesej yang tidak jelas, tidak cukup konteks, atau bukan pertanyaan yang boleh dikenal pasti.

Arahan:
- Jangan jawab soalan.
- Jangan cari sumber.
- Pulangkan JSON sahaja: {{"category":"KATEGORI"}}

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
            "hi", "hii", "hiii", "hai", "hello", "helo",
            "assalamualaikum", "assalamualaikum wbt",
            "salam", "salam sejahtera",
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
            "Selamat datang ke *TanyaFiqihBot*.\n"
            "Boleh tanya saya soalan berkaitan fiqh Islam."
        )

    return (
        "Hai! 👋 Selamat datang ke *TanyaFiqihBot*.\n\n"
        "Saya membantu mencari jawapan berkaitan fiqh Islam "
        "berserta rujukan sumber. Apa yang anda ingin tanya?"
    )


def general_response() -> str:
    return (
        "📚 *Tentang TanyaFiqihBot*\n\n"
        "Bot ini membantu menjawab persoalan fiqh Islam "
        "dengan mencari sumber kitab dan sumber agama yang berkaitan.\n\n"
        "Contoh soalan:\n"
        "• Apakah hukum solat jamak ketika musafir?\n"
        "• Bagaimanakah cara sujud sahwi?\n"
        "• Apakah perkara yang membatalkan wuduk?\n\n"
        "Sila ajukan soalan fiqh yang ingin anda semak."
    )


# ============================================================
# TURATH QUERY PLANNER
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
Anda pakar membina kata kunci carian kitab fiqh Arab.

Tukarkan soalan pengguna kepada beberapa kata kunci ringkas
yang sesuai dicari dalam kitab turath Arab.

Peraturan:
- Jangan jawab soalan.
- Jangan membuat hukum sendiri.
- Sertakan kata kunci Arab yang relevan jika mampu.
- Pulangkan JSON sahaja dalam format: {{"queries":["kata kunci 1","kata kunci 2"]}}
- Maksimum 5 pertanyaan.

Soalan pengguna:
{question}
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


# ============================================================
# TURATH SEARCH
# ============================================================

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
                "results", "sources", "items", "data", "documents",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    return value

            if any(
                key in payload
                for key in (
                    "text", "content", "book", "title", "snippet",
                )
            ):
                return [payload]

        print("[TURATH] Respons tidak mengandungi senarai hasil.")
        return []

    except Exception as exc:
        print(f"[TURATH SEARCH ERROR] {exc}")
        return []


# ============================================================
# SOURCE NORMALIZATION
# ============================================================

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
        author = first_value(
            item,
            ("author", "author_name", "writer"),
        )
        text = first_value(
            item,
            ("text", "content", "passage", "body", "snippet", "matched_text", "excerpt"),
        )
        page = first_value(
            item,
            ("page", "page_number", "page_no", "volume_page"),
        )
        volume = first_value(
            item,
            ("volume", "vol", "volume_number"),
        )
        url = first_value(
            item,
            ("url", "link", "source_url"),
        )

        if not text:
            continue

        if not title:
            title = "Sumber Turath (maklumat kitab tidak lengkap)"

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
    question_words = {
        word.lower()
        for word in re.findall(r"\w+", question)
        if len(word) > 2
    }

    def score(source):
        text = (
            source.get("title", "")
            + " "
            + source.get("text", "")
        ).lower()

        overlap = sum(
            1 for word in question_words if word in text
        )

        has_text = bool(source.get("text"))
        has_book = bool(source.get("title"))
        has_page = bool(source.get("page"))

        return (
            overlap,
            int(has_text),
            int(has_book),
            int(has_page),
        )

    ranked = sorted(
        sources,
        key=score,
        reverse=True,
    )

    unique = []
    seen = set()

    for source in ranked:
        fingerprint = re.sub(
            r"\s+",
            " ",
            (
                source.get("title", "")
                + source.get("text", "")
            ).lower(),
        ).strip()

        if fingerprint in seen:
            continue

        seen.add(fingerprint)
        unique.append(source)

    return unique[:MAX_SOURCE_COUNT]


# ============================================================
# BRAVE WEB SEARCH FALLBACK
# ============================================================

def search_brave_web(question: str) -> list:
    if not BRAVE_SEARCH_API_KEY:
        print("[BRAVE] BRAVE_SEARCH_API_KEY belum ditetapkan.")
        return []

    endpoint = "https://api.search.brave.com/res/v1/web/search"

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }

    domain_query = " OR ".join(
        f"site:{domain}"
        for domain in ALL_ISLAMIC_DOMAINS
    )

    queries = [
        question,
        f"{question} ({domain_query})",
    ]

    sources = []
    seen_urls = set()

    for query in queries:
        try:
            response = HTTP_SESSION.get(
                endpoint,
                headers=headers,
                params={
                    "q": query,
                    "count": BRAVE_SEARCH_COUNT,
                    "country": "MY",
                    "search_lang": "ms",
                    "safesearch": "moderate",
                },
                timeout=BRAVE_TIMEOUT,
            )

            response.raise_for_status()
            payload = response.json()

            web_data = payload.get("web", {})
            results = web_data.get("results", [])

            for result in results:
                if not isinstance(result, dict):
                    continue

                title = str(result.get("title", "")).strip()
                description = str(result.get("description", "")).strip()
                url = str(result.get("url", "")).strip()

                if not description or not url:
                    continue

                if url in seen_urls:
                    continue

                seen_urls.add(url)
                domain = (urlparse(url).hostname or "").lower()

                sources.append(
                    {
                        "kind": "web",
                        "title": title or domain or "Sumber web",
                        "author": "",
                        "text": description[:MAX_SOURCE_CHARS],
                        "page": "",
                        "volume": "",
                        "url": url,
                        "domain": domain,
                    }
                )

        except Exception as exc:
            print(f"[BRAVE SEARCH ERROR] {exc}")

    return sources[:MAX_SOURCE_COUNT]


# ============================================================
# ANSWER GENERATION (MENGEKALKAN GAYA ASAL YANG NATURAL)
# ============================================================

def build_source_context(sources: list) -> str:
    blocks = []
    used_chars = 0

    for index, source in enumerate(sources, start=1):
        title = source.get("title", "Sumber tidak diketahui")
        author = source.get("author", "")
        text = source.get("text", "")
        page = source.get("page", "")
        volume = source.get("volume", "")
        url = source.get("url", "")

        metadata = [f"[S{index}]", f"Tajuk: {title}"]

        if author:
            metadata.append(f"Pengarang: {author}")
        if volume:
            metadata.append(f"Jilid: {volume}")
        if page:
            metadata.append(f"Muka surat: {page}")
        if url:
            metadata.append(f"URL: {url}")

        block = (
            "\n".join(metadata)
            + "\nPetikan:\n"
            + text
        )

        if used_chars + len(block) > MAX_CONTEXT_CHARS:
            break

        blocks.append(block)
        used_chars += len(block)

    return "\n\n---\n\n".join(blocks)


def generate_fiqh_answer(
    question: str,
    sources: list,
) -> str:
    """
    Jana jawapan dengan berpandukan sumber yang diberikan.
    Kekalkan etika ilmiah yang telus dan adab perbincangan fiqh.
    """

    if not sources:
        return (
            "Maaf, saya belum menemui sumber yang mencukupi "
            "untuk mengesahkan jawapan bagi soalan ini.\n\n"
            "Cuba ubah perkataan soalan atau nyatakan mazhab "
            "yang ingin dirujuk."
        )

    context = build_source_context(sources)

    if not context.strip():
        return (
            "Maaf, kandungan sumber yang diterima tidak mencukupi "
            "untuk menghasilkan jawapan yang boleh disemak."
        )

    prompt = f"""
Anda ialah pembantu penyelidikan fiqh Islam bagi TanyaFiqihBot.

Jawab soalan pengguna dalam bahasa Melayu yang jelas, santun, dan tersusun rapi.
Gunakan sumber yang disertakan sebagai asas jawapan.

PERATURAN PENTING:
1. Jangan mereka-reka petikan kitab, nama pengarang, nombor jilid, nombor halaman, hadis, ayat atau pautan.
2. Jangan mendakwa sumber menyatakan sesuatu jika perkara itu tidak terdapat dalam petikan sumber.
3. Gunakan penanda [S1], [S2] dan seterusnya untuk dakwaan yang benar-benar disokong oleh sumber berkenaan.
4. Jika sumber bercanggah, terangkan perbezaannya dengan cermat dan berhati-hati.
5. Jika ada perbezaan pandangan mazhab atau khilaf dalam petikan, terangkan secara adil dan ilmiah di bawah huraian.
6. Bezakan antara rukun/syarat wajib dengan amalan sunat mengikut konteks teks mazhab.
7. Gunakan gaya tulisan Markdown yang kemas (*bold* pada kata kunci dan senarai bernombor/bullet yang teratur).
8. JANGAN tambah senarai rujukan panjang atau asal penciptaan bot di hujung jawapan, ia akan disambung secara automatik oleh sistem.

Format yang digalakkan:
- Pernyataan hukum ringkas pada ayat pembukaan
- Huraian (termasuk rukun, syarat, dan perbahasan khilaf/perbezaan pandangan jika ada dalam sumber)
- Ringkasan pendek atau kesimpulan amali

Soalan pengguna:
{question}

SUMBER RUJUKAN:
{context}

Berikan jawapan berdasarkan sumber di atas sahaja.
"""

    try:
        return gemini_generate(prompt, temperature=0.25)

    except Exception as exc:
        print(f"[ANSWER GENERATION ERROR] {exc}")

        return (
            "Maaf, berlaku masalah semasa menghasilkan jawapan. "
            "Sumber telah dicari, tetapi jawapan tidak dapat "
            "disediakan buat masa ini. Sila cuba sebentar lagi."
        )


def format_source_reference(source: dict, index: int) -> str:
    """Format satu sumber untuk senarai rujukan Telegram."""

    title = source.get("title", "Sumber tidak diketahui")
    author = source.get("author", "")
    page = source.get("page", "")
    volume = source.get("volume", "")
    url = source.get("url", "")
    kind = source.get("kind", "")

    parts = [f"[S{index}] *{title}*"]

    if author:
        parts.append(f"Pengarang: {author}")
    if volume:
        parts.append(f"Jilid: {volume}")
    if page:
        parts.append(f"Hlm.: {page}")
    if kind == "web" and url:
        parts.append(f"Pautan: {url}")
    elif url:
        parts.append(f"Pautan: {url}")

    return "\n".join(parts)


def build_references(sources: list) -> str:
    if not sources:
        return ""

    references = [
        format_source_reference(source, index)
        for index, source in enumerate(sources, start=1)
    ]

    return "📚 *Rujukan yang diperoleh*\n\n" + "\n\n".join(
        references
    )


def get_bot_origin_footer() -> str:
    """Maklumat asal penciptaan bot yang ditambah di hujung jawapan."""
    return (
        "---\n"
        "ℹ️ *Asal Penciptaan Bot*\n"
        "_TanyaFiqihBot dibangunkan sebagai inisiatif penyelidikan digital fiqh Islam "
        "yang berteraskan rujukan kitab Turath muktamad serta fatwa autoriti rasmi, "
        "dijana dengan sokongan model kecerdasan buatan bagi memudahkan "
        "masyarakat menyemak sandaran hukum secara telus, beradab, dan berautoriti._"
    )


def answer_question(question: str) -> str:
    print(f"[QUESTION] {question}")

    queries = plan_turath_queries(question)
    print(f"[TURATH QUERIES] {queries}")

    raw_turath = search_turath(queries)
    turath_sources = normalize_turath_sources(raw_turath)

    turath_sources = rank_sources(
        turath_sources,
        question,
    )

    useful_turath = [
        source
        for source in turath_sources
        if source.get("text", "").strip()
    ]

    sources = useful_turath

    if not sources:
        print("[FALLBACK] Sumber Turath tidak mencukupi; mencari web.")
        sources = search_brave_web(question)
        sources = rank_sources(sources, question)

    if not sources:
        return (
            "Maaf, saya tidak menemui sumber yang mencukupi "
            "untuk mengesahkan jawapan ini.\n\n"
            "Anda boleh cuba:\n"
            "• Menulis semula soalan dengan lebih khusus.\n"
            "• Menyatakan mazhab yang ingin dirujuk.\n"
            "• Menyertakan konteks kejadian yang berkaitan."
        )

    answer = generate_fiqh_answer(question, sources)
    references = build_references(sources)
    footer = get_bot_origin_footer()

    # Susun jawapan akhir: Isi Kandungan -> Rujukan Kitab -> Footer Asal Penciptaan
    final_output = answer
    if references:
        final_output += "\n\n" + references
    final_output += "\n\n" + footer

    return final_output


# ============================================================
# TELEGRAM HANDLERS (DENGAN SOKONGAN MARKDOWN SELAMAT)
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        "Assalamualaikum warahmatullahi wabarakatuh! 👋\n\n"
        "Selamat datang ke *TanyaFiqihBot*.\n\n"
        "Saya membantu mencari jawapan bagi persoalan fiqh "
        "Islam berserta rujukan sumber kitab Turath.\n\n"
        "Contoh:\n"
        "• Apakah hukum solat jamak ketika musafir?\n"
        "• Bagaimanakah cara sujud sahwi?\n"
        "• Apakah perkara yang membatalkan wuduk?\n\n"
        "Sila taip soalan fiqh anda."
    )

    if update.message:
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
        )


async def telegram_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()

    if not question:
        return

    try:
        category = await asyncio.to_thread(
            classify_message,
            question,
        )

        print(f"[MESSAGE CATEGORY] {category}: {question}")

        if category == "GREETING":
            await update.message.reply_text(
                greeting_response(question),
                parse_mode="Markdown",
            )
            return

        if category == "GENERAL_QUESTION":
            await update.message.reply_text(
                general_response(),
                parse_mode="Markdown",
            )
            return

        if category == "UNCLEAR":
            await update.message.reply_text(
                "Maaf, saya kurang pasti maksud mesej anda. 😊\n\n"
                "Boleh tulis soalan dengan lebih jelas? "
                "Jika berkaitan fiqh, nyatakan persoalan yang "
                "ingin diketahui."
            )
            return

        status_message = await update.message.reply_text(
            "🔎 Saya sedang menyemak sumber Turath dan rujukan "
            "yang berkaitan. Sila tunggu sebentar..."
        )

        answer = await asyncio.to_thread(
            answer_question,
            question,
        )

        max_length = 4000
        chunks = [
            answer[i:i + max_length]
            for i in range(0, len(answer), max_length)
        ]

        if chunks:
            # Gunakan parse_mode="Markdown" supaya gaya tulisan bold dan tajuk kemas di Telegram
            try:
                await status_message.edit_text(
                    chunks[0],
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception:
                # Fallback ke plain text jika terdapat simbol luar biasa
                await status_message.edit_text(
                    chunks[0],
                    disable_web_page_preview=True,
                )

            for chunk in chunks[1:]:
                try:
                    await update.message.reply_text(
                        chunk,
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    await update.message.reply_text(
                        chunk,
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
                "Maaf, berlaku masalah semasa memproses mesej. "
                "Sila cuba semula sebentar lagi."
            )
        except Exception as reply_error:
            print(f"[TELEGRAM REPLY ERROR] {reply_error}")


async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message:
        await update.message.reply_text(
            "Maaf, arahan itu tidak dikenali. "
            "Taip /start untuk melihat panduan penggunaan."
        )


# ============================================================
# TELEGRAM APPLICATION LIFECYCLE
# ============================================================

def create_telegram_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN belum ditetapkan.")

    telegram_app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    telegram_app.add_handler(
        CommandHandler("start", start_command)
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

    return telegram_app


async def run_telegram():
    telegram_app = create_telegram_app()

    try:
        await telegram_app.initialize()
        await telegram_app.start()

        if telegram_app.updater is None:
            raise RuntimeError(
                "Telegram updater tidak tersedia."
            )

        await telegram_app.updater.start_polling(
            drop_pending_updates=False,
        )

        print("[TELEGRAM] Polling bermula.")
        await asyncio.Event().wait()

    finally:
        print("[TELEGRAM] Sedang menutup polling...")

        try:
            if (
                telegram_app.updater is not None
                and telegram_app.updater.running
            ):
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

            print(
                "[TELEGRAM] Polling tamat. "
                "Cuba mulakan semula..."
            )

        except Exception as exc:
            print(f"[TELEGRAM SUPERVISOR ERROR] {exc}")
            traceback.print_exc()

        time.sleep(max(TELEGRAM_RESTART_WAIT, 1))


def start_telegram_background():
    if not TELEGRAM_TOKEN:
        print(
            "[WARNING] TELEGRAM_TOKEN tiada. "
            "Telegram polling tidak dimulakan."
        )
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
    return jsonify(
        {
            "service": "TanyaFiqihBot",
            "status": "running",
        }
    )


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
