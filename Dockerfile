FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ============================================================
# SYSTEM DEPENDENCIES
# ============================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-msa \
    tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# NODE.JS
# ============================================================

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    node --version && \
    npm --version

# ============================================================
# WORKDIR
# ============================================================

WORKDIR /app

# ============================================================
# PYTHON DEPENDENCIES
# ============================================================

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ============================================================
# NODE DEPENDENCIES
# ============================================================

COPY package.json .

RUN npm install

# ============================================================
# APPLICATION
# ============================================================

COPY . .

# ============================================================
# DATA DIRECTORY
# ============================================================

RUN mkdir -p /var/data

# ============================================================
# RENDER PORT
# ============================================================

EXPOSE 10000

# ============================================================
# START
#
# Node Turath:
#   127.0.0.1:8765
#
# Gunicorn:
#   0.0.0.0:$PORT
# ============================================================

CMD ["sh", "-c", "node turath_service.mjs & exec gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT} app:app"]
