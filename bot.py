import os
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_community.vectorstores import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

# --- 1. SETUP RAG (BACA SEMUA KITAB DALAM FOLDER) ---
FOLDER_PATH = "kitab"

print(f"Sedang memproses semua kitab di dalam folder '{FOLDER_PATH}'...")
loader = PyPDFDirectoryLoader(FOLDER_PATH)
docs = loader.load()
print(f"Jumlah mukasurat/dokumen dikesan: {len(docs)}")

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
splits = text_splitter.split_documents(docs)

embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
vectorstore = Chroma.from_documents(documents=splits, embedding=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

# --- 2. SETUP MODEL AI & PROMPT KHAS FIQH ---
llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.3)

# Anda boleh ubah arahan / prompt di sini mengikut kesesuaian bot anda
system_prompt = (
    "Anda adalah TanyaFiqhBot, pembantu rujukan ilmu fiqh berasaskan Ahli Sunnah Wal Jamaah.\n"
    "Jawab soalan pengguna HANYA berdasarkan konteks kitab-kitab yang diberikan di bawah.\n"
    "Jika jawapan tiada dalam konteks, beritahu secara berhemah bahawa rujukan tiada dalam kitab.\n"
    "Gunakan bahasa Melayu yang sopan, jelas, dan sertakan rujukan nama kitab jika berkaitan.\n\n"
    "Konteks:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

question_answer_chain = create_stuff_documents_chain(llm, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)
print("Semua kitab dan prompt berjaya dimuat naik ke dalam sistem AI!")

# --- 3. SETUP WEB SERVER KECIL UNTUK RENDER (SUPAYA BOT HIDUP 24 JAM) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Fiqh sedang aktif 24 jam!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# --- 4. TELEGRAM BOT HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text
    print(f"Soalan diterima: {user_query}")
    
    try:
        # Cari jawapan menggunakan sistem RAG Kitab & Prompt
        response = rag_chain.invoke({"input": user_query})
        answer = response["answer"]
    except Exception as e:
        answer = "Maaf, berlaku ralat semasa memproses soalan anda."
        print(f"Ralat: {e}")
    
    await update.message.reply_text(answer)

async def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("Ralat: TELEGRAM_TOKEN tidak dijumpai di Environment Variables!")
        return

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot Telegram mula mendengar mesej...")
    await application.run_polling()

if __name__ == '__main__':
    # Jalankan web server Flask di latar belakang
    keep_alive()
    
    # Jalankan bot Telegram
    asyncio.run(main())