from app.otel_setup import setup_otel, instrument_fastapi

setup_otel()

# Les imports ci-dessous sont volontairement APRÈS setup_otel() : l'auto-
# instrumentation OpenTelemetry fonctionne en enveloppant les modules au moment
# de leur import. Importer FastAPI d'abord donne un service qui démarre
# normalement et n'instrumente rien — une panne silencieuse. D'où le noqa.
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from strawberry.fastapi import GraphQLRouter  # noqa: E402

from app.schema import schema  # noqa: E402
from app.services.lifecycle import BackgroundServices  # noqa: E402

background_services = BackgroundServices()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Prechargement OSM, consommateur RabbitMQ et couche transit demarrent en
    # tache de fond : le service repond immediatement.
    await background_services.start()
    try:
        yield
    finally:
        await background_services.stop()


app = FastAPI(title="graph-manager", lifespan=lifespan)

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")


@app.get("/health")
async def health() -> dict:
    """Etat des caches et du bus : utile pour verifier l'integration GTFS."""
    return {"status": "ok", **background_services.status()}


instrument_fastapi(app)
