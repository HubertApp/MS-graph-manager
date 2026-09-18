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

# Utilisateur non privilegie. Le cache osmnx (./cache) doit lui appartenir,
# sinon graph_from_bbox echoue et le service bascule silencieusement sur OSRM.
RUN groupadd -r appgroup && useradd -r -g appgroup appuser \
    && mkdir -p /app/cache \
    && chown -R appuser:appgroup /app/cache
USER appuser

EXPOSE 3011

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
