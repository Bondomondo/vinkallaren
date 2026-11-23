"""Who may call: the family app (shared code), agent-tools (API key), the scheduler (cron secret)."""
import hmac

from fastapi import HTTPException, Request

from . import config


def _eq(a: str | None, b: str) -> bool:
    return bool(a and b) and hmac.compare_digest(a.strip().encode(), b.encode())


def kalla(request: Request) -> str:
    """Dependency: 'app' or 'agent'; 401 otherwise."""
    if _eq(request.headers.get("x-familj-kod"), config.familj_kod()):
        return "app"
    key = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization", "")
    if not key and auth.lower().startswith("bearer "):
        key = auth[7:]
    if _eq(key, config.api_key()):
        return "agent"
    raise HTTPException(401, "Fel eller saknad kod")


def schemalaggare_eller_app(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and _eq(auth[7:], config.cron_secret()):
        return "cron"
    return kalla(request)
