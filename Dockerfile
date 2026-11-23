# LeaseLens: FastAPI + ChromaDB + fastembed (ONNX) + Groq
# Small enough for a 512 MB server (no PyTorch). Works on Render, which sets
# the PORT environment variable.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_DIR=/app/.cache/fastembed \
    PORT=10000

# chroma-hnswlib 0.7.5 is compiled from source, so it needs a C/C++ compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /app

# Install dependencies first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
RUN mkdir -p /app/.cache && chown -R user:user /app

USER user

# Download the embedding model at build time so the app starts fast.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.cache/fastembed')"

EXPOSE 10000
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
