FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3011

RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Utilisateur non privilegie. Le cache osmnx (./cache) et le cache des regions
# preparees (./cache/graphs, volume en compose) doivent lui appartenir, sinon
# graph_from_bbox echoue et le service bascule silencieusement sur OSRM.
RUN groupadd -r appgroup && useradd -r -g appgroup appuser \
    && mkdir -p /app/cache \
    && chown -R appuser:appgroup /app/cache
USER appuser

EXPOSE 3011

# /health expose aussi l'etat des caches et de la connexion RabbitMQ.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" > /dev/null || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
