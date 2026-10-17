FROM python:3.13-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

COPY --from=builder /install /usr/local

WORKDIR /app

COPY --chown=appuser:appuser . .

# entrypoint.sh: CRs entfernen (Defense-in-Depth - ein Windows-Checkout ohne
# passende .gitattributes liefert "#!/bin/bash\r", dann startet der Container
# nicht) und ausfuehrbar machen (git speichert die Datei als 100644).
RUN mkdir -p /app/data \
    && sed -i 's/\r$//' entrypoint.sh \
    && chmod 755 entrypoint.sh \
    && chown appuser:appuser /app/data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["./entrypoint.sh"]
