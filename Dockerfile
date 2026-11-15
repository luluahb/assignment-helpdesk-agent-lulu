# Dockerfile IT Helpdesk Assistant
# Pola Sesi 14.2: FROM python -> WORKDIR -> COPY -> RUN pip -> CMD

FROM python:3.11-slim

# Output Python langsung muncul di log container, dan tidak membuat file .pyc
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements.txt terlebih dahulu, lalu install dependencies.
# Layer ini di-cache Docker selama requirements.txt tidak berubah.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code dan data (file .env TIDAK ikut, lihat .dockerignore).
# API key diberikan saat runtime: docker run --env-file .env ...
COPY app/ ./app/
COPY data/ ./data/

EXPOSE 8000

# host 0.0.0.0 agar endpoint dapat diakses dari luar container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
