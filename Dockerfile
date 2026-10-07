FROM node:22-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 \
       python3-pip \
       python3-venv \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./

RUN npm install --omit=dev

COPY requirements.txt ./

RUN python3 -m venv /opt/venv

RUN /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY turath_service.mjs .

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production

ENV TURATH_HOST=127.0.0.1
ENV TURATH_PORT=8765
ENV TURATH_SERVICE_URL=http://127.0.0.1:8765

EXPOSE 10000

CMD ["sh", "-c", "node turath_service.mjs & exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120 app:app"]
