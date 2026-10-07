FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# ============================================================
# OCR + PDF
# ============================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-msa \
    tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# APP
# ============================================================

WORKDIR /app

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY . .

# Persistent data directory
RUN mkdir -p /var/data

# ============================================================
# PORT
# ============================================================

EXPOSE 10000

# ============================================================
# START
# ============================================================

CMD ["sh", "-c", "gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-10000} app:app"]
