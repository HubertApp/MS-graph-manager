from app.otel_setup import setup_otel, instrument_fastapi

setup_otel()

# Les imports ci-dessous sont volontairement APRÈS setup_otel() : l'auto-
# instrumentation OpenTelemetry fonctionne en enveloppant les modules au moment
# de leur import. Importer FastAPI d'abord donne un service qui démarre
# normalement et n'instrumente rien — une panne silencieuse. D'où le noqa.
from fastapi import FastAPI  # noqa: E402
from strawberry.fastapi import GraphQLRouter  # noqa: E402

from app.schema import schema  # noqa: E402

app = FastAPI(title="graph-manager")

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

instrument_fastapi(app)
