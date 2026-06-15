FROM python:3.11-slim

# Git is needed for the Obsidian vault sync; build tools for lxml wheels fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql
COPY scripts ./scripts

RUN pip install --no-cache-dir .

# Runtime data (sessions, downloads, vault) is mounted as volumes in compose.
ENV PYTHONUNBUFFERED=1

CMD ["megan"]
