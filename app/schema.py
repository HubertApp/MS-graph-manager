import strawberry
from strawberry.extensions.tracing import OpenTelemetryExtension

from app.resolvers.route import RouteQuery

schema = strawberry.federation.Schema(
    query=RouteQuery,
    federation_version="2.3",
    extensions=[OpenTelemetryExtension],
)