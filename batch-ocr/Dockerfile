# Workflow service image for batch-ocr: Python plus the OCR tools.
FROM python:3.14.10-slim-trixie

# Tesseract reads text from page images. Poppler's pdftoppm and pdfinfo turn
# PDF pages into images and count pages. tesseract-ocr includes English data.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# ocr_document runs one Tesseract process per CPU, so keep each one to a
# single thread.
ENV OMP_THREAD_LIMIT=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Docker-based workflow services still need a Start Command in the Dashboard
# (python main.py), even with this CMD.
CMD ["python", "main.py"]
