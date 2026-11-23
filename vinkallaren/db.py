"""Supabase over HTTP: PostgREST for tables and functions, Storage for label photos."""
import asyncio
from typing import Any

import httpx
from fastapi import HTTPException

from . import config

_client: httpx.AsyncClient | None = None
_loop: asyncio.AbstractEventLoop | None = None


def client() -> httpx.AsyncClient:
    """Shared client, recreated if the event loop changes (serverless)."""
    global _client, _loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _loop is not loop:
        _client = httpx.AsyncClient(timeout=30, headers={"User-Agent": "vinkallaren/1.0"})
        _loop = loop
    return _client


def configured() -> bool:
    return bool(config.supabase_url() and config.supabase_key())


def _headers(extra: dict | None = None) -> dict:
    key = config.supabase_key()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    h.update(extra or {})
    return h


async def rest(method: str, path: str, *, params: dict | None = None, json: Any = None, prefer: str | None = None) -> Any:
    if not configured():
        raise HTTPException(503, "Databasen är inte konfigurerad (SUPABASE_URL och SUPABASE_SECRET_KEY)")
    headers = _headers({"Prefer": prefer} if prefer else None)
    try:
        r = await client().request(method, f"{config.supabase_url()}/rest/v1/{path}", params=params, json=json, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Kunde inte nå databasen ({type(exc).__name__})")
    if r.status_code >= 400:
        try:
            detail = r.json().get("message") or r.text
        except ValueError:
            detail = r.text
        raise HTTPException(502, f"Databasen svarade {r.status_code}: {detail[:300]}")
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


async def select(table: str, params: dict | None = None) -> list[dict]:
    return await rest("GET", table, params=params) or []


async def insert(table: str, rows: dict | list, *, upsert: bool = False) -> list[dict]:
    prefer = "return=representation" + (",resolution=merge-duplicates" if upsert else "")
    return await rest("POST", table, json=rows, prefer=prefer) or []


async def update(table: str, match: dict, values: dict) -> list[dict]:
    return await rest("PATCH", table, params=match, json=values, prefer="return=representation") or []


async def rpc(fn: str, args: dict) -> Any:
    return await rest("POST", f"rpc/{fn}", json=args)


# ── Storage ────────────────────────────────────────────────────────────────
BUCKET = "etiketter"


async def upload(path: str, data: bytes, content_type: str) -> str:
    try:
        r = await client().post(f"{config.supabase_url()}/storage/v1/object/{BUCKET}/{path}", content=data,
                                headers=_headers({"Content-Type": content_type, "x-upsert": "true"}))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Kunde inte spara bilden ({type(exc).__name__})")
    if r.status_code >= 400:
        raise HTTPException(502, f"Bildlagringen svarade {r.status_code}")
    return path


async def signed_urls(paths: list[str], seconds: int = 3600) -> dict[str, str]:
    """Temporary links to label photos (the bucket is private)."""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not paths:
        return {}
    try:
        r = await client().post(f"{config.supabase_url()}/storage/v1/object/sign/{BUCKET}", json={"expiresIn": seconds, "paths": paths},
                                headers=_headers())
        rows = r.json() if r.status_code < 400 else []
    except (httpx.HTTPError, ValueError):
        return {}
    out = {}
    for row in rows if isinstance(rows, list) else []:
        url = row.get("signedURL") or row.get("signedUrl")
        if url and row.get("path"):
            out[row["path"]] = f"{config.supabase_url()}/storage/v1{url}" if url.startswith("/") else url
    return out
