# kaixn web app — FastAPI UI + REST over the constitution engine.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# git is required at runtime to clone the repos users submit in the UI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (layer cache), then the source.
COPY pyproject.toml README.md ./
COPY src ./src
COPY agentkit ./agentkit
COPY migrations ./migrations
COPY queries ./queries
COPY scripts ./scripts

RUN pip install --upgrade pip \
 && pip install -e '.[web,postgres,anthropic,openai]'

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
