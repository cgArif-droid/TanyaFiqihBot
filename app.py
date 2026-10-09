import os
import re
import json
import time
import asyncio
import threading
import traceback
import httpx  # Ganti requests dengan httpx untuk Async

from urllib.parse import urlparse
from flask import Flask, jsonify
from google import genai
from google.genai import types # Tambahan untuk JSON Mode

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

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite").strip()
ARABIC_QUERY_MODEL = os.getenv("ARABIC_QUERY_MODEL", LLM_MODEL).strip()
TURATH_SERVICE_URL = os.getenv("TURATH_SERVICE_URL", "http://127.0.0.1:8765").strip().rstrip("/")
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
BRAVE_SEARCH_COUNT = int(os.getenv("BRAVE_SEARCH_COUNT", "6"))

GEMINI_RETRIES = int(os.getenv("GEMINI_RETRIES", "2"))
TELEGRAM_RESTART_WAIT = int(os.getenv("TELEGRAM_RESTART_WAIT", "5"))

TURATH_TIMEOUT = int(os.getenv("TURATH_TIMEOUT", "90"))
BRAVE_TIMEOUT = int(os.getenv("BRAVE_TIMEOUT", "20"))

MAX_SOURCE_COUNT = int(os.getenv("MAX_SOURCE_COUNT", "8"))
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "2500"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "14000"))

# Rate Limiter: Simpan rekod masa pengguna bertanya
USER_LAST_REQUEST = {}
RATE_LIMIT_SECONDS = 10  # Pengguna perlu tunggu 10 saat antara soalan

# Klien HTTP Async (Ganti requests.Session)
HTTPX_CLIENT = httpx.AsyncClient()

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
    "muftiwp.gov.my", "islam.gov.my", "jakim.gov.my",
    "e-smaf.islam.gov.my", "dar-alifta.org",
]

ISLAMIC_LIBRARY_DOMAINS = [
    "shamela.ws", "waqfeya.net",
]

ADDITIONAL_ISLAMIC_DOMAINS = [
    "islamqa.info", "islamweb.net", "binbaz.org.sa",
]

ALL_ISLAMIC_DOMAINS = (
    OFFICIAL_FATWA_DOMAINS + ISLAMIC_LIBRARY_DOMAINS + ADDITIONAL_ISLAMIC_DOMAINS
)

# ============================================================
# QUERY MAP
# ============================================================
QUERY_MAP = {
    "solat": "الصلاة", "wuduk": "الوضوء", "puasa": "الصيام", 
    "zakat": "الزكاة", "haji": "الحج", "nikah": "النكاح",
    "talak": "الطلاق", "faraid": "الفرائض", "jual beli": "البيع",
    # ... (Boleh masukkan senarai penuh anda di sini)
}

# ============================================================
# GEMINI UTILITIES (KINI ASYNC & SOKONG JSON NATIVE)
# ============================================================

async def gemini_generate(prompt: str, model: str = None, retries: int = None, is_json: bool = False) -> str:
    """Panggil Gemini secara tak segerak (async) dengan sokongan JSON mode."""
    if GEMINI_CLIENT is None:
        raise RuntimeError("GOOGLE_API_KEY belum ditetapkan.")

    selected_model = model or LLM_MODEL
    attempts = GEMINI_RETRIES if retries is None else retries
    last_error = None

    # Tetapan untuk format JSON natif jika is_json=True
    config = types.GenerateContentConfig()
    if is_json:
        config.response_mime_type = "application/json"

    for attempt in range(attempts + 1):
        try:
            # Gunakan asyncio.to_thread untuk elak blocking proses utama
            response = await asyncio.to_thread(
                GEMINI_CLIENT.models.generate_content,
                model=selected_model,
                contents=prompt,
                config=config
            )

            text = getattr(response, "text", None)
            if text and text.strip():
                return text.strip()

            raise ValueError("Gemini memulangkan jawapan kosong.")

        except Exception as exc:
            last_error = exc
            print(f"[GEMINI ERROR] Cubaan {attempt + 1}/{attempts + 1}: {exc}")
            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"Gemini gagal selepas beberapa cubaan: {last_error}")

# ============================================================
# MESSAGE CLASSIFIER
# ============================================================

async def classify_message(message: str) -> str:
    message = (message or "").strip()
    if not message:
        return "UNCLEAR"

    prompt = f"""
Anda ialah pengelas mesej bagi Telegram TanyaFiqihBot.
Tentukan SATU kategori untuk mesej pengguna.

GREETING: Sapaan sahaja (hi, hai, salam).
FIQH_QUESTION: Pertanyaan hukum Islam, fiqh, ibadah, dsb.
GENERAL_QUESTION: Pertanyaan fungsi bot.
UNCLEAR: Mesej tidak jelas/tiada kaitan.

Pulangkan JSON sahaja.
Contoh: {{"category":"FIQH_QUESTION"}}

Mesej pengguna: {message}
"""
    try:
        raw = await gemini_generate(prompt, retries=1, is_json=True)
        data = json.loads(raw) # Terus load JSON (tak perlu regex)
        category = str(data.get("category", "")).strip().upper()
        
        if category in {"GREETING", "FIQH_QUESTION", "GENERAL_QUESTION", "UNCLEAR"}:
            return category
        return "UNCLEAR"
    except Exception as exc:
        print(f"[CLASSIFIER ERROR] {exc}")
        # Fallback corak asas
        normalized = re.sub(r"[^a-zA-Z0-9\s]", "", message.lower()).strip()
        if normalized in {"hi", "hai", "hello", "salam", "assalamualaikum"}:
            return "GREETING"
        return "UNCLEAR"

# ============================================================
# TURATH & BRAVE SEARCH (ASYNC HTTPX)
# ============================================================

def fallback_turath_queries(question: str) -> list:
    question = question.strip()
    lowered = question.lower()
    queries = [question]
    for keyword, arabic in QUERY_MAP.items():
        if keyword in lowered:
            queries.append(arabic)
            queries.append(f"{arabic} حكم")
    return list(dict.fromkeys(queries))[:5] # Hilangkan pendua dan hadkan 5

async def plan_turath_queries(question: str) -> list:
    prompt = f"""
Tukarkan soalan pengguna kepada beberapa kata kunci carian kitab fiqh Arab.
Pulangkan JSON sahaja: {{"queries":["kata kunci 1","kata kunci 2"]}}
Maksimum 5 pertanyaan.

Soalan pengguna: {question}
"""
    try:
        raw = await gemini_generate(prompt, model=ARABIC_QUERY_MODEL, retries=1, is_json=True)
        data = json.loads(raw)
        queries = data.get("queries", [])
        
        cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        if question not in cleaned:
            cleaned.insert(0, question)
        return list(dict.fromkeys(cleaned))[:5]
    except Exception as exc:
        print(f"[TURATH PLANNER ERROR] {exc}")
        return fallback_turath_queries(question)

async def search_turath(queries: list) -> list:
    endpoint = f"{TURATH_SERVICE_URL}/search"
    try:
        response = await HTTPX_CLIENT.post(
            endpoint, json={"queries": queries}, timeout=TURATH_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("results", "sources", "items", "data", "documents"):
                if isinstance(payload.get(key), list):
                    return payload.get(key)
            if any(k in payload for k in ("text", "content", "book")):
                return [payload]
        return []
    except Exception as exc:
        print(f"[TURATH SEARCH ERROR] {exc}")
        return []

async def search_brave_web(question: str) -> list:
    if not BRAVE_SEARCH_API_KEY:
        return []

    endpoint = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_SEARCH_API_KEY}
    domain_query = " OR ".join(f"site:{domain}" for domain in ALL_ISLAMIC_DOMAINS)
    queries = [question, f"{question} ({domain_query})"]
    
    sources = []
    seen_urls = set()

    for query in queries:
        try:
            response = await HTTPX_CLIENT.get(
                endpoint, headers=headers, 
                params={"q": query, "count": BRAVE_SEARCH_COUNT, "country": "MY", "search_lang": "ms", "safesearch": "moderate"},
                timeout=BRAVE_TIMEOUT
            )
            response.raise_for_status()
            results = response.json().get("web", {}).get("results", [])

            for res in results:
                url = str(res.get("url", "")).strip()
                desc = str(res.get("description", "")).strip()
                if not url or not desc or url in seen_urls: continue
                seen_urls.add(url)
                
                sources.append({
                    "kind": "web", "title": res.get("title", "Sumber Web"),
                    "author": "", "text": desc[:MAX_SOURCE_CHARS],
                    "page": "", "volume": "", "url": url,
                    "domain": urlparse(url).hostname or ""
                })
        except Exception as exc:
            print(f"[BRAVE ERROR] {exc}")
    
    return sources[:MAX_SOURCE_COUNT]

# ... [Fungsi normalize_turath_sources, rank_sources, build_source_context kekal sama] ...
# (Sila salin dari kod asal anda untuk fungsi di atas kerana ia memproses data sahaja, bukan I/O)

def rank_sources(sources: list, question: str) -> list:
    # (Salin dari kod asal)
    return sources[:MAX_SOURCE_COUNT]

def normalize_turath_sources(raw_sources: list) -> list:
    # (Salin dari kod asal)
    return raw_sources # letak kod normalize anda disini

def build_source_context(sources: list) -> str:
    # (Salin dari kod asal)
    return "\n\n".join([s.get("text", "") for s in sources]) # letak kod asal anda disini

# ============================================================
# ANSWER GENERATION
# ============================================================

async def generate_fiqh_answer(question: str, sources: list) -> str:
    if not sources:
        return "Maaf, saya belum menemui sumber yang mencukupi untuk mengesahkan jawapan bagi soalan ini."

    context = build_source_context(sources)
    prompt = f"""
Anda ialah pembantu penyelidikan fiqh Islam bagi TanyaFiqihBot.
Jawab soalan pengguna dalam bahasa Melayu yang jelas dan sopan.
Gunakan sumber yang disertakan sebagai asas jawapan.

Soalan pengguna: {question}
SUMBER RUJUKAN:
{context}

Berikan jawapan berdasarkan sumber di atas sahaja.
"""
    try:
        return await gemini_generate(prompt)
    except Exception as exc:
        print(f"[ANSWER GEN ERROR] {exc}")
        return "Maaf, berlaku masalah semasa menghasilkan jawapan. Sila cuba sebentar lagi."

async def answer_question(question: str) -> str:
    print(f"[QUESTION] {question}")

    # Semuanya kini dipanggil menggunakan await
    queries = await plan_turath_queries(question)
    raw_turath = await search_turath(queries)
    
    # Fungsi data processing kekal synchronous
    turath_sources = normalize_turath_sources(raw_turath)
    turath_sources = rank_sources(turath_sources, question)
    
    sources = [s for s in turath_sources if s.get("text", "").strip()]

    if not sources:
        sources = await search_brave_web(question)
        sources = rank_sources(sources, question)

    if not sources:
        return "Maaf, saya tidak menemui sumber yang mencukupi."

    answer = await generate_fiqh_answer(question, sources)
    
    # Bina rujukan (anda boleh salin build_references dari kod asal)
    # references = build_references(sources)
    # return answer + "\n\n" + references
    return answer

# ============================================================
# TELEGRAM HANDLERS
# ============================================================

async def telegram_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    question = update.message.text.strip()
    if not question: return

    # --- SISTEM RATE LIMITER ---
    user_id = update.effective_user.id
    now = time.time()
    last_req_time = USER_LAST_REQUEST.get(user_id, 0)
    
    if now - last_req_time < RATE_LIMIT_SECONDS:
        wait_time = int(RATE_LIMIT_SECONDS - (now - last_req_time))
        await update.message.reply_text(f"⏳ Sila tunggu {wait_time} saat lagi sebelum bertanya soalan seterusnya.")
        return
        
    USER_LAST_REQUEST[user_id] = now
    # ---------------------------

    try:
        category = await classify_message(question)
        if category == "GREETING":
            await update.message.reply_text("Waalaikumussalam! Selamat datang ke TanyaFiqihBot.")
            return
        if category == "GENERAL_QUESTION":
            await update.message.reply_text("Sila ajukan soalan fiqh yang ingin disemak.")
            return
        if category == "UNCLEAR":
            await update.message.reply_text("Maaf, mesej kurang jelas. Boleh nyatakan soalan fiqh anda?")
            return

        status_message = await update.message.reply_text("🔎 Saya sedang menyemak sumber Turath...")

        answer = await answer_question(question)

        # Chunking output Telegram
        max_length = 4000
        chunks = [answer[i:i + max_length] for i in range(0, len(answer), max_length)]

        if chunks:
            await status_message.edit_text(chunks[0], disable_web_page_preview=True)
            for chunk in chunks[1:]:
                await update.message.reply_text(chunk, disable_web_page_preview=True)
        else:
            await status_message.edit_text("Maaf, tiada jawapan yang dapat dihasilkan.")

    except Exception as exc:
        print(f"[TELEGRAM HANDLER ERROR] {exc}")
        await update.message.reply_text("Maaf, berlaku masalah semasa memproses mesej.")

# ... [Fungsi create_telegram_app & background supervisor kekal seperti asal] ...
