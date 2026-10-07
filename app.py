# ============================================================
# OCR
# ============================================================

OCR_DPI = 200

# 3 worker serentak
# Sesuai untuk server RAM sekitar 515 MB
OCR_WORKERS = int(
    os.environ.get("OCR_WORKERS", "3")
)

# Jika teks kurang daripada jumlah ini,
# page akan dianggap mungkin scanned PDF
MIN_TEXT_CHARS = 40

# Cache OCR setiap page
OCR_PAGE_CACHE_DIR = os.path.join(
    EXTRACTED_DIR,
    "pages"
)
