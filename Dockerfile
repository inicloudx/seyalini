# Seyalini: one image, two jobs (docker-compose.yml): the web app (gunicorn) and the worker (agents + clock).

# ---- Stage 1: build the Python packages ----
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --prefix=/install -r requirements.txt

# ---- Stage 2: the app ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings
WORKDIR /app

# gosu: the entrypoint fixes volume ownership as root, then runs the app as a normal user
# fonts-dejavu-core: the bold font for captions and end cards (tools/fonts.py)
# ffmpeg itself comes inside the imageio-ffmpeg Python package
RUN apt-get update && apt-get install -y --no-install-recommends gosu fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY . .
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser \
    && mkdir -p /app/data /app/staticfiles \
    && chown -R appuser:appgroup /app

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# one process, a few threads: small memory; Telegram replies run in these threads (CHAT_JOBS_MODE=thread)
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "6", \
     "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-"]
