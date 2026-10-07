import os
import glob
import logging
import threading

from flask import Flask

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
)
from langchain_community.vectorstores import Chroma
from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough


# ============================================================
# 1. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# 2. ENVIRONMENT VARIABLES
# ============================================================

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not GOOGLE_API_KEY:
    raise RuntimeError(
        "GOOGLE_API_KEY tidak dijumpai. "
        "Sila masukkan GOOGLE_API_KEY dalam Render Environment Variables."
    )

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_TOKEN tidak dijumpai. "
        "Sila masukkan TELEGRAM_TOKEN dalam Render Environment Variables."
    )


# Pastikan library Google menggunakan API key ini
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY


# ============================================================
# 3. CONFIGURATION
# ============================================================

FOLDER_PATH = "kitab"

EMBEDDING_MODEL = "gemini-embedding-001"

LLM_MODEL = "gemini-2.5-flash"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

RETRIEVER_K = 4


# ============================================================
# 4. LOAD KITAB
# ============================================================

def load_kitab():
    logger.info("========================================")
    logger.info("MEMULAKAN PROSES MEMUAT NAIK KITAB")
    logger.info("========================================")

    docs = []

    if not os.path.exists(FOLDER_PATH):
        logger.warning(
            "Folder '%s' tidak dijumpai. Folder akan dicipta.",
            FOLDER_PATH
        )

        os.makedirs(FOLDER_PATH, exist_ok=True)

    # --------------------------------------------------------
    # LOAD PDF
    # --------------------------------------------------------

    pdf_files = glob.glob(
        os.path.join(FOLDER_PATH, "*.pdf")
    )

    logger.info(
        "Jumlah fail PDF dijumpai: %s",
        len(pdf_files)
    )

    for pdf_file in pdf_files:

        try:
            logger.info(
                "Membaca PDF: %s",
                os.path.basename(pdf_file)
            )

            loader = PyPDFLoader(pdf_file)

            pdf_docs = loader.load()

            # Simpan nama kitab dalam metadata
            for doc in pdf_docs:
                doc.metadata["source_file"] = os.path.basename(
                    pdf_file
                )

            docs.extend(pdf_docs)

            logger.info(
                "Berjaya membaca %s halaman daripada %s",
                len(pdf_docs),
                os.path.basename(pdf_file)
            )

        except Exception as e:

            logger.exception(
                "Gagal membaca PDF %s: %s",
                pdf_file,
                e
            )


    # --------------------------------------------------------
    # LOAD TXT
    # --------------------------------------------------------

    txt_files = glob.glob(
        os.path.join(FOLDER_PATH, "*.txt")
    )

    logger.info(
        "Jumlah fail TXT dijumpai: %s",
        len(txt_files)
    )

    for txt_file in txt_files:

        try:
            logger.info(
                "Membaca TXT: %s",
                os.path.basename(txt_file)
            )

            loader = TextLoader(
                txt_file,
                encoding="utf-8"
            )

            txt_docs = loader.load()

            for doc in txt_docs:
                doc.metadata["source_file"] = os.path.basename(
                    txt_file
                )

            docs.extend(txt_docs)

        except Exception as e:

            logger.exception(
                "Gagal membaca TXT %s: %s",
                txt_file,
                e
            )


    logger.info(
        "Jumlah dokumen asal: %s",
        len(docs)
    )

    return docs


# ============================================================
# 5. SPLIT DOCUMENT
# ============================================================

def split_documents(docs):

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=[
            "\n\n",
            "\n",
            ". ",
            "۔ ",
            " ",
            ""
        ]
    )

    splits = text_splitter.split_documents(docs)

    # Buang chunk kosong
    splits = [
        doc
        for doc in splits
        if doc.page_content and doc.page_content.strip()
    ]

    logger.info(
        "Jumlah pecahan teks: %s",
        len(splits)
    )

    return splits


# ============================================================
# 6. FALLBACK TEXT
# ============================================================

def create_fallback_document():

    dummy_text = """
    Sistem TanyaFiqhBot berjaya dihidupkan.

    Tiada kandungan kitab yang boleh dibaca ditemui.

    Sila masukkan kitab dalam format PDF berasaskan teks
    atau fail TXT ke dalam folder kitab.

    Jika PDF merupakan dokumen scan/gambar, teks mungkin
    tidak dapat dibaca secara automatik.
    """

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP
    )

    return text_splitter.create_documents(
        [dummy_text]
    )


# ============================================================
# 7. BUILD VECTOR DATABASE
# ============================================================

def build_vectorstore():

    docs = load_kitab()

    if not docs:

        logger.warning(
            "Tiada dokumen kitab dijumpai."
        )

        splits = create_fallback_document()

    else:

        splits = split_documents(docs)

        if not splits:

            logger.warning(
                "Dokumen dijumpai tetapi tiada teks boleh dibaca."
            )

            splits = create_fallback_document()


    logger.info(
        "Jumlah chunk untuk embedding: %s",
        len(splits)
    )


    # --------------------------------------------------------
    # GEMINI EMBEDDING
    # --------------------------------------------------------

    logger.info(
        "Menggunakan embedding model: %s",
        EMBEDDING_MODEL
    )

    embeddings = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        task_type="retrieval_document",
        google_api_key=GOOGLE_API_KEY,
    )


    # --------------------------------------------------------
    # CHROMA
    # --------------------------------------------------------

    logger.info(
        "Membina Chroma vector database..."
    )

    vectorstore = Chroma.from_documents(
        documents=splits,
        embedding=embeddings,
    )


    logger.info(
        "Chroma vector database berjaya dibina."
    )


    retriever = vectorstore.as_retriever(
        search_kwargs={
            "k": RETRIEVER_K
        }
    )

    return retriever


# ============================================================
# 8. BUILD RAG
# ============================================================

retriever = build_vectorstore()


# ============================================================
# 9. GEMINI LLM
# ============================================================

logger.info(
    "Menggunakan LLM model: %s",
    LLM_MODEL
)

llm = ChatGoogleGenerativeAI(
    model=LLM_MODEL,
    temperature=0.3,
    google_api_key=GOOGLE_API_KEY,
)


# ============================================================
# 10. SYSTEM PROMPT
# ============================================================

system_prompt = """
Anda adalah TanyaFiqhBot, pembantu rujukan ilmu fiqh
berasaskan Ahli Sunnah Wal Jamaah.

ARAHAN PENTING:

1. Jawab berdasarkan konteks kitab yang diberikan.

2. Jangan mereka-reka fakta, hukum, dalil atau rujukan
   yang tidak terdapat dalam konteks.

3. Jika jawapan tidak terdapat dalam konteks kitab,
   nyatakan dengan jelas:

   "Maaf, maklumat tersebut tidak ditemui dalam
   kitab yang tersedia dalam sistem."

4. Jika konteks menyebut nama kitab, gunakan nama kitab
   tersebut sebagai rujukan.

5. Gunakan bahasa Melayu yang sopan dan mudah difahami.

6. Jika terdapat perbezaan pendapat dalam konteks kitab,
   nyatakan perbezaan tersebut dengan jelas.

7. Jangan mendakwa sesuatu sebagai fatwa rasmi.

8. Untuk persoalan yang memerlukan keputusan hukum khusus
   atau melibatkan keadaan peribadi yang kompleks, nasihatkan
   pengguna merujuk ustaz/ustazah atau pihak berautoriti.

KONTEKS KITAB:
{context}

SOALAN PENGGUNA:
{question}

JAWAPAN:
"""


prompt = ChatPromptTemplate.from_template(
    system_prompt
)


# ============================================================
# 11. FORMAT DOCUMENT
# ============================================================

def format_docs(docs_list):

    formatted = []

    for doc in docs_list:

        source = doc.metadata.get(
            "source_file",
            "Kitab tidak diketahui"
        )

        page = doc.metadata.get(
            "page",
            None
        )

        if page is not None:
            page_text = f" | Halaman: {page + 1}"
        else:
            page_text = ""

        formatted.append(
            f"[Sumber: {source}{page_text}]\n"
            f"{doc.page_content}"
        )

    return "\n\n---\n\n".join(formatted)


# ============================================================
# 12. RAG CHAIN
# ============================================================

rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough()
    }
    | prompt
    | llm
    | StrOutputParser()
)


logger.info(
    "========================================"
)

logger.info(
    "TanyaFiqhBot RAG berjaya disediakan."
)

logger.info(
    "========================================"
)


# ============================================================
# 13. FLASK WEB SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "TanyaFiqhBot sedang aktif. "
        "Telegram bot berjalan."
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "TanyaFiqhBot"
    }


# ============================================================
# 14. TELEGRAM MESSAGE HANDLER
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    user_query = update.message.text.strip()

    if not user_query:
        return

    logger.info(
        "Soalan Telegram diterima: %s",
        user_query
    )


    # --------------------------------------------------------
    # Hantar mesej loading
    # --------------------------------------------------------

    try:

        processing_message = await update.message.reply_text(
            "🔎 Sedang mencari rujukan dalam kitab..."
        )

    except Exception as e:

        logger.error(
            "Gagal menghantar mesej loading: %s",
            e
        )

        processing_message = None


    # --------------------------------------------------------
    # RAG
    # --------------------------------------------------------

    try:

        answer = await rag_chain.ainvoke(
            user_query
        )

        if not answer:
            answer = (
                "Maaf, tiada jawapan dapat dihasilkan."
            )

    except Exception as e:

        logger.exception(
            "Ralat semasa memproses soalan: %s",
            e
        )

        answer = (
            "Maaf, berlaku masalah semasa mencari "
            "rujukan kitab. Sila cuba semula sebentar lagi."
        )


    # --------------------------------------------------------
    # Padam mesej loading
    # --------------------------------------------------------

    if processing_message:

        try:

            await processing_message.delete()

        except Exception:
            pass


    # --------------------------------------------------------
    # Telegram mempunyai had mesej
    # --------------------------------------------------------

    MAX_MESSAGE_LENGTH = 4000

    if len(answer) <= MAX_MESSAGE_LENGTH:

        try:

            await update.message.reply_text(
                answer
            )

        except Exception as e:

            logger.error(
                "Gagal menghantar jawapan Telegram: %s",
                e
            )

    else:

        # Pecahkan jawapan panjang
        chunks = [
            answer[i:i + MAX_MESSAGE_LENGTH]
            for i in range(
                0,
                len(answer),
                MAX_MESSAGE_LENGTH
            )
        ]

        for chunk in chunks:

            try:

                await update.message.reply_text(
                    chunk
                )

            except Exception as e:

                logger.error(
                    "Gagal menghantar chunk Telegram: %s",
                    e
                )


# ============================================================
# 15. TELEGRAM ERROR HANDLER
# ============================================================

async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Telegram error: %s",
        context.error
    )


# ============================================================
# 16. START TELEGRAM BOT
# ============================================================

def run_telegram_bot():

    logger.info(
        "Memulakan Telegram Bot..."
    )

    try:

        application = (
            ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .build()
        )

        application.add_handler(
            MessageHandler(
                filters.TEXT & (~filters.COMMAND),
                handle_message
            )
        )

        application.add_error_handler(
            telegram_error_handler
        )

        logger.info(
            "Telegram Bot sedang polling..."
        )

        # PENTING:
        # Jangan gunakan asyncio.run() di sini.
        application.run_polling(
            drop_pending_updates=True
        )

    except Exception as e:

        logger.exception(
            "Telegram bot berhenti kerana error: %s",
            e
        )


# ============================================================
# 17. START BOT THREAD
# ============================================================

bot_thread = threading.Thread(
    target=run_telegram_bot,
    name="telegram-bot",
    daemon=True
)

bot_thread.start()


# ============================================================
# 18. LOCAL RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    logger.info(
        "Flask server berjalan pada port %s",
        port
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
