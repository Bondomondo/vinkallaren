"""Systembolaget's wine assortment: nightly sync into `produkter`, and search.

Systembolaget stopped publishing product data in its open API, so the source is a community mirror
of the assortment (config.KATALOG_KALLOR, first one that answers). The file is large (tens of MB), so
it is streamed to /tmp and parsed item by item; only wines are kept.
"""
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Iterator

import httpx
import ijson
from fastapi import HTTPException

from . import config, db

BATCH = 500
VIN_KATEGORI = "Vin"
SKIP_TYP = {"Vinlåda", "Glögg och Glühwein", "Drycker av flera typer"}


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _clock(p: dict, key: str) -> int | None:
    v = p.get(f"tasteClock{key}")
    if v in (None, 0):
        for c in p.get("tasteClocks") or []:
            if c.get("key") == f"TasteClock{key}":
                v = c.get("value")
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


# Systembolaget's serving advice ("Serveras vid 16°C till rätter av lamm- eller nötkött") → its pairing
# categories, for mirrors that leave out the tasteSymbols field.
ANVANDNING = [
    ("Lamm", r"lamm"), ("Nöt", r"nöt|mörkt kött|biff|kalv|oxfilé|entrecôte|grillat kött"), ("Fläsk", r"fläsk|gris|ljust kött|kallskuret|korv|skinka"),
    ("Fågel", r"fågel|kyckling|ljust kött|anka|kalkon"), ("Vilt", r"vilt|älg|hjort|rådjur|vildsvin|ren"),
    ("Fisk", r"fisk|lax|torsk"), ("Skaldjur", r"skaldjur|räk|musslor|ostron|hummer|kräft"),
    ("Grönsaker", r"vegetari|grönsak|svamp|sallad"), ("Ost", r"ost(?!ron)"), ("Dessert", r"dessert|efterrätt|choklad|bakverk|tårta"),
    ("Asiatiskt", r"asiatisk|thai|kinesisk|indisk|sushi"), ("Kryddstarkt", r"kryddstark|kryddig|het mat|chili"),
    ("Aperitif", r"aperitif|fördrink"), ("Sällskapsdryck", r"sällskapsdryck|sällskap"), ("Buffémat", r"buffé|småplock|plockmat|tapas"),
]


def kategorier_ur_text(text: str | None) -> list[str]:
    low = (text or "").lower()
    if "till" not in low and "som" not in low:
        return []
    return [kat for kat, pat in ANVANDNING if re.search(rf"(?<![a-zåäö])(?:{pat})", low)]


def _image(p: dict) -> str | None:
    for img in p.get("images") or []:
        url = img.get("imageUrl")
        if url:
            return f"{url}_400.png" if not url.endswith((".png", ".jpg")) else url
    return None


def normalisera(p: dict) -> dict | None:
    """One mirror record → a `produkter` row (None for anything that is not a wine)."""
    if (p.get("categoryLevel1") or "") != VIN_KATEGORI or not p.get("productNumber") or not p.get("productNameBold"):
        return None
    if p.get("categoryLevel2") in SKIP_TYP:
        return None
    launch = (p.get("productLaunchDate") or "")[:10] or None
    vol = _num(p.get("volume"))
    return {
        "artikelnr": str(p["productNumber"]),
        "namn": p["productNameBold"].strip(),
        "namn2": (p.get("productNameThin") or "").strip() or None,
        "producent": (p.get("producerName") or "").strip() or None,
        "typ": p.get("categoryLevel2"),
        "stil": p.get("categoryLevel3"),
        "land": p.get("country"),
        "region": p.get("originLevel1"),
        "region2": p.get("originLevel2"),
        "druvor": [g for g in (p.get("grapes") or []) if isinstance(g, str) and g.strip()],
        "argang": str(p["vintage"]) if p.get("vintage") else None,
        "pris": _num(p.get("price")),
        "volym_ml": int(vol) if vol else None,
        "alkohol": _num(p.get("alcoholPercentage")),
        "socker_g_l": _num(p.get("sugarContent")),
        "farg": p.get("color"),
        "smak": p.get("taste"),
        "anvandning": p.get("usage"),
        "passar_till": [s for s in (p.get("tasteSymbols") or []) if isinstance(s, str)] or kategorier_ur_text(p.get("usage")),
        "fyllighet": _clock(p, "Body"),
        "stravhet": _clock(p, "Roughness"),
        "fruktsyra": _clock(p, "Fruitacid"),
        "sotma": _clock(p, "Sweetness"),
        "sortiment": p.get("assortmentText"),
        "ekologisk": bool(p.get("isOrganic")),
        "bild_url": _image(p),
        "lanserad": launch,
    }


def _items(path: str) -> Iterator[dict]:
    """Records from a mirror file: a top-level list, or {"products": [...]} / {"data": [...]}."""
    with open(path, "rb") as f:
        head = f.read(64).lstrip()
    prefix = "item" if head.startswith(b"[") else None
    with open(path, "rb") as f:
        if prefix:
            yield from ijson.items(f, prefix, use_float=True)
            return
        for key in ("products", "data", "items"):
            f.seek(0)
            found = False
            for item in ijson.items(f, f"{key}.item", use_float=True):
                found = True
                yield item
            if found:
                return


async def _download(url: str, dest: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), follow_redirects=True,
                                 headers={"User-Agent": "vinkallaren/1.0"}) as c:
        async with c.stream("GET", url) as r:
            if r.status_code != 200:
                raise HTTPException(502, f"{url.split('/')[2]} svarade {r.status_code}")
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)


async def synka() -> dict:
    """Fetch the assortment and upsert every wine. Tries each source until one yields wines."""
    started = time.time()
    fel = []
    for url in config.KATALOG_KALLOR:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            await _download(url, path)
            seen = datetime.now(config.TZ).isoformat(timespec="seconds")
            batch, count, total = [], 0, 0
            for item in _items(path):
                total += 1
                row = normalisera(item) if isinstance(item, dict) else None
                if not row:
                    continue
                row["senast_sedd"] = seen
                batch.append(row)
                if len(batch) >= BATCH:
                    await db.insert("produkter?on_conflict=artikelnr", batch, upsert=True)
                    count += len(batch)
                    batch = []
            if batch:
                await db.insert("produkter?on_conflict=artikelnr", batch, upsert=True)
                count += len(batch)
            if count == 0:
                fel.append(f"{url.split('/')[2]}: inga viner bland {total} poster")
                continue
            await db.insert("synk", {"kalla": url.split("/")[2], "antal": count, "anteckning": f"{total} poster"})
            return {"kalla": url.split("/")[2], "viner": count, "poster": total, "sekunder": round(time.time() - started, 1), "fel": fel or None}
        except HTTPException as exc:
            fel.append(f"{url.split('/')[2]}: {exc.detail}")
        except (httpx.HTTPError, ijson.JSONError, ValueError) as exc:
            fel.append(f"{url.split('/')[2]}: {type(exc).__name__}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    raise HTTPException(502, "Ingen sortimentskälla gick att läsa: " + "; ".join(fel))


async def senaste_synk() -> dict | None:
    rows = await db.select("synk", {"select": "kalla,antal,klar", "order": "klar.desc", "limit": "1"})
    return rows[0] if rows else None


async def sok(q: str, argang: str | None = None, antal: int = 8) -> list[dict]:
    q = " ".join((q or "").split())
    if len(q) < 2:
        return []
    rows = await db.rpc("sok_produkter", {"q": q, "arg": argang or None, "max_antal": antal}) or []
    for r in rows:
        r["likhet"] = round(float(r.get("likhet") or 0), 2)
    return rows


async def produkt(artikelnr: str) -> dict | None:
    rows = await db.select("produkter", {"artikelnr": f"eq.{artikelnr}", "limit": "1"})
    return rows[0] if rows else None
