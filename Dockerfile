FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    MAX_UPLOAD_SIZE=20971520 \
    MAX_PDF_PAGES=1000 \
    MAX_PDF_ANALYSIS_SECONDS=60

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 decoder \
    && useradd --uid 10001 --gid decoder --no-create-home decoder \
    && mkdir -p /app/data/jobs \
    && chown -R decoder:decoder /app/data

USER decoder
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).close()"]
CMD ["uvicorn", "railway_turno_decoder.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
