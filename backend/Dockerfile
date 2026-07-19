FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./backend/

RUN mkdir -p /app/data /app/uploads /app/code_downloads && \
    addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser && \
    chown -R appuser:appgroup /app

ENV LLM_API_KEY=""
ENV LLM_BASE_URL="https://api.openai.com/v1"
ENV LLM_MODEL="gpt-4o-mini"
ENV DATABASE_URL="sqlite:///./data/essay_read.db"
ENV UPLOAD_DIR="./uploads"
ENV HOST="127.0.0.1"
ENV PORT="8000"
ENV FRONTEND_ORIGIN="http://localhost"

EXPOSE 8000

USER appuser

CMD ["python", "-m", "backend"]
