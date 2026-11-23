# LeaseLens: FastAPI + ChromaDB + sentence-transformers + Groq
# Built for Hugging Face Spaces (Docker SDK), which serves on port 7860
# and runs the container as user 1000.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    PORT=7860

# chroma-hnswlib 0.7.5 is compiled from source, so it needs a C/C++ compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /app

# Install dependencies first so Docker can cache this layer.
# CPU-only torch keeps the image small (no GPU libraries).
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
RUN mkdir -p /app/.cache && chown -R user:user /app

USER user

# Download the embedding model at build time so the app starts fast.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

EXPOSE 7860
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
