from app.otel_setup import setup_otel, instrument_fastapi
 
setup_otel()
 
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter
 
from app.schema import schema
 
app = FastAPI(title="graph-manager")
 
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")
 
instrument_fastapi(app)
 