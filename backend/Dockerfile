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

# LLM_* defaults live in backend.config.Settings.  Keeping canonical values out
# of the image also lets runtime --env-file MIMO_*/OPENAI_*/ANTHROPIC_* aliases
# migrate cleanly instead of being shadowed by image-level LLM_* variables.
ENV DATABASE_URL="sqlite:///./data/essay_read.db"
ENV UPLOAD_DIR="./uploads"
ENV HOST="127.0.0.1"
ENV PORT="8000"
ENV FRONTEND_ORIGIN="http://localhost"

EXPOSE 8000

USER appuser

CMD ["python", "-m", "backend"]
