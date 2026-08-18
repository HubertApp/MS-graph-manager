from app.otel_setup import setup_otel, instrument_fastapi

setup_otel()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from app.schema import schema
from app.services.transit_events import build_broker


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = build_broker()
    if broker is not None:
        await broker.start()
    yield
    if broker is not None:
        await broker.stop()


app = FastAPI(title="graph-manager", lifespan=lifespan)

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

instrument_fastapi(app)
