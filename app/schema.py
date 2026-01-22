import strawberry
from app.resolvers.route import RouteQuery


schema = strawberry.Schema(query=RouteQuery)
