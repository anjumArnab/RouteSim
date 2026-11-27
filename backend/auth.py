"""Bearer-token authentication. Each token identifies one agent."""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class Authenticator:
    def __init__(self, api_keys: dict[str, str]) -> None:
        """api_keys maps an agent name to its secret token."""
        self._api_keys = dict(api_keys)

    def authenticate(self, authorization: str | None) -> str | None:
        """Return the agent name for an Authorization header value, or None."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        provided = token.strip().encode()
        match = None
        for agent, expected in self._api_keys.items():
            # Compare against every key so timing does not reveal which matched.
            if hmac.compare_digest(provided, expected.encode()):
                match = agent
        return match


def unauthorized_response() -> JSONResponse:
    return JSONResponse(
        {"detail": "Missing or invalid bearer token"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


class BearerAuthMiddleware:
    """Protect an ASGI app and expose the caller as `request.state.agent`."""

    def __init__(self, app: ASGIApp, authenticator: Authenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        agent = self.authenticator.authenticate(authorization)
        if agent is None:
            await unauthorized_response()(scope, receive, send)
            return
        scope.setdefault("state", {})["agent"] = agent
        await self.app(scope, receive, send)
