FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./backend/

RUN mkdir -p /app/data /app/uploads /app/code_downloads

ENV MIMO_API_KEY=""
ENV DATABASE_URL="sqlite:///./data/essay_read.db"
ENV UPLOAD_DIR="./uploads"
ENV HOST="0.0.0.0"
ENV PORT="8000"
ENV FRONTEND_ORIGIN="http://localhost"

EXPOSE 8000

CMD ["python", "-m", "backend"]
