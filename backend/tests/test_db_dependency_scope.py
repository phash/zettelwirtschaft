"""Guard: jede Route bindet get_db mit scope="function" ein.

Mit dem Default-Scope "request" fuehrt FastAPI (>= 0.12x) den Commit nach dem
``yield`` erst NACH dem Senden der Response aus. Ein fehlschlagender Commit
(database is locked, FK-Verletzung) wuerde dann still zurueckgerollt, waehrend
der Client bereits 200 bekommen hat.
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.database import get_db
from app.main import app


def _api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):  # FastAPI >= 0.143: _IncludedRouter
            yield from _api_routes(route.original_router.routes)


def _get_db_dependants(dependant: Dependant):
    for dep in dependant.dependencies:
        if dep.call is get_db:
            yield dep
        yield from _get_db_dependants(dep)


def test_all_routes_use_function_scoped_db_session():
    routes = list(_api_routes(app.routes))
    assert routes, "keine API-Routen gefunden"
    offenders = [
        f"{', '.join(sorted(route.methods))} {route.path}"
        for route in routes
        for dep in _get_db_dependants(route.dependant)
        if dep.scope != "function"
    ]
    assert offenders == [], f'Depends(get_db) ohne scope="function": {offenders}'
