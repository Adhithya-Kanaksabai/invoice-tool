# Bakes in Poppler — the real justification for Docker here. Poppler-not-on-PATH
# after a mid-session Windows install cost real debugging time (see GOD_FILE.md);
# src/ingest.py's _find_poppler_path Windows-winget fallback exists purely because
# of it. A container with poppler-utils on PATH via apt makes that whole class of
# problem disappear, rather than decorating the project with Docker for its own sake.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, for Docker layer caching — code changes shouldn't
# invalidate the (slow) pip install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# Migrate then serve. DATABASE_URL/GEMINI_API_KEY come from the environment
# (docker-compose's env_file), same "config, not code" pattern db.py already
# uses for local SQLite vs. this Postgres path.
CMD ["sh", "-c", "alembic upgrade head && streamlit run src/app.py --server.address=0.0.0.0 --server.port=8501"]
