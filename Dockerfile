FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
RUN mkdir -p /home/user/app && chown user:user /home/user/app

USER user
WORKDIR /home/user/app

ENV PATH="/home/user/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

EXPOSE 8080

# Cloud Run setzt $PORT automatisch (Standard 8080) — Secrets (GROQ_API_KEY etc.)
# werden NICHT hier hineingebacken, sondern zur Laufzeit als Env-Vars/Secrets injiziert.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --log-level info"]
