# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# potrace is the only system dependency; Pillow and vtracer ship as wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends potrace \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY studi0trace ./studi0trace
COPY bench ./bench
RUN pip install --upgrade pip && pip install .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "studi0trace.main:app", "--host", "0.0.0.0", "--port", "8000"]
