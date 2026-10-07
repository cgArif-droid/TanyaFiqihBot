import os
import glob
from langchain_community.document_loaders import PyPDFLoader, TextLoader

# --- 1. SETUP RAG (BACA SEMUA FAIL DALAM FOLDER KITAB) ---
FOLDER_PATH = "kitab"

print(f"Sedang memproses fail di dalam folder '{FOLDER_PATH}'...")
docs = []

# Baca fail .pdf jika ada
for pdf_file in glob.glob(f"{FOLDER_PATH}/*.pdf"):
    try:
        loader = PyPDFLoader(pdf_file)
        docs.extend(loader.load())
    except Exception as e:
        print(f"Gagal membaca {pdf_file}: {e}")

# Baca fail .txt jika ada
for txt_file in glob.glob(f"{FOLDER_PATH}/*.txt"):
    try:
        loader = TextLoader(txt_file, encoding='utf-8')
        docs.extend(loader.load())
    except Exception as e:
        print(f"Gagal membaca {txt_file}: {e}")

print(f"Jumlah dokumen/mukasurat dikesan: {len(docs)}")

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
splits = text_splitter.split_documents(docs)

splits = [doc for doc in splits if doc.page_content.strip()]
print(f"Jumlah pecahan teks yang mengandungi isi: {len(splits)}")

if len(splits) == 0:
    raise ValueError(
        "Ralat: Tiada teks yang berjaya diekstrak! "
        "Fail PDF berkemungkinan adalah imbasan gambar (scanned). "
        "Sila gunakan PDF teks digital atau masukkan fail .txt."
    )
