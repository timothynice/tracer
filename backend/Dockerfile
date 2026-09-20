# syntax=docker/dockerfile:1

# --- the Rust engine ---------------------------------------------------------
# Vexel's pipeline is a Rust extension module. Building it in its own stage
# keeps the runtime image a single container with no toolchain in it, and the
# wheel is abi3 so it does not have to be built against the exact interpreter
# that will load it.
FROM python:3.12-slim AS vexel
ENV PIP_NO_CACHE_DIR=1 \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
       | sh -s -- -y --default-toolchain 1.83.0 --profile minimal \
    && pip install 'maturin>=1.7,<2'
WORKDIR /src
COPY vexel-rs ./vexel-rs
RUN maturin build --release --out /wheels -m vexel-rs/Cargo.toml

# --- the service -------------------------------------------------------------
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
COPY --from=vexel /wheels /wheels
RUN pip install --upgrade pip && pip install . /wheels/vexel_rs-*.whl && rm -rf /wheels

# Fail the build rather than the first request if the extension did not land:
# without it the service falls back to the Python pipeline and every trace takes
# ten times as long, which is not something a health check would notice.
RUN python -c "from studi0trace.engines.vexel.engine import backend; assert backend() == 'rust', backend()"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "studi0trace.main:app", "--host", "0.0.0.0", "--port", "8000"]
