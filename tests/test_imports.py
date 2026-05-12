def test_imports():
    # Ensure primary modules import without side-effects
    import importlib

    modules = [
        "app.config",
        "app.main",
        "app.schema",
        "app.resolvers.route",
    ]
    for m in modules:
        importlib.import_module(m)
