# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: potrace and image codecs required by Pillow (PNG/JPEG/GIF/etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       potrace \
       libgl1 \
       libglib2.0-0 \
       build-essential \
       libjpeg62-turbo \
       zlib1g \
       libpng16-16 \
       libtiff6 \
       libwebp7 \
       libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install vtracer

# App code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


