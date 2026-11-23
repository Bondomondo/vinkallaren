"""The cellar: our wines with the number of bottles, and what we thought of them."""
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from . import config, db, katalog

KOPIERA = ("producent", "typ", "land", "region", "druvor", "passar_till", "fyllighet", "stravhet", "fruktsyra", "sotma",
           "pris", "smak", "anvandning")
VIN_FALT = "id,artikelnr,namn,producent,argang,typ,land,region,druvor,passar_till,fyllighet,stravhet,fruktsyra,sotma,pris,smak,anvandning,antal,plats,anteckning,etikett_bild,skapad,uppdaterad"


def now() -> str:
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def visningsnamn(v: dict) -> str:
    namn, prod = (v.get("namn") or "").strip(), (v.get("producent") or "").strip()
    name = f"{prod} {namn}" if prod and prod.lower() not in namn.lower() else namn
    return f"{name} {v['argang']}" if v.get("argang") else name


async def omdomen(vin_ids: list[str] | None = None) -> list[dict]:
    params = {"select": "id,vin_id,betyg,kommentar,mat,datum,kalla", "order": "datum.desc,skapad.desc"}
    if vin_ids is not None:
        if not vin_ids:
            return []
        params["vin_id"] = f"in.({','.join(vin_ids)})"
    return await db.select("omdomen", params)


def _summera(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for o in rows:
        s = out.setdefault(o["vin_id"], {"antal_omdomen": 0, "summa": 0, "senaste": None})
        s["antal_omdomen"] += 1
        s["summa"] += o["betyg"]
        if not s["senaste"]:
            s["senaste"] = {k: o.get(k) for k in ("betyg", "kommentar", "mat", "datum") if o.get(k) not in (None, "")}
    for s in out.values():
        s["snittbetyg"] = round(s.pop("summa") / s["antal_omdomen"], 1)
    return out


async def viner(*, bara_i_kallaren: bool = True, typ: str | None = None, sok: str | None = None, bilder: bool = False) -> list[dict]:
    params = {"select": VIN_FALT, "order": "typ.asc,namn.asc"}
    if bara_i_kallaren:
        params["antal"] = "gt.0"
    if typ:
        params["typ"] = f"ilike.*{typ}*"
    rows = await db.select("viner", params)
    if sok:
        words = [w for w in re.split(r"\s+", sok.lower()) if w]
        rows = [v for v in rows if all(w in " ".join(str(v.get(k) or "") for k in ("namn", "producent", "region", "land", "argang")).lower()
                                       or w in " ".join(v.get("druvor") or []).lower() for w in words)]
    stats = _summera(await omdomen([v["id"] for v in rows]))
    links = await db.signed_urls([v["etikett_bild"] for v in rows if v.get("etikett_bild")]) if bilder else {}
    for v in rows:
        v["visningsnamn"] = visningsnamn(v)
        v.update(stats.get(v["id"], {"antal_omdomen": 0}))
        if v.get("etikett_bild") in links:
            v["etikett_url"] = links[v["etikett_bild"]]
    return rows


async def vin(vin_id: str) -> dict:
    rows = await db.select("viner", {"select": VIN_FALT, "id": f"eq.{vin_id}", "limit": "1"})
    if not rows:
        raise HTTPException(404, "Vinet finns inte i källaren")
    v = rows[0]
    v["visningsnamn"] = visningsnamn(v)
    v["omdomen"] = await omdomen([vin_id])
    v.update(_summera(v["omdomen"]).get(vin_id, {"antal_omdomen": 0}))
    if v.get("etikett_bild"):
        v["etikett_url"] = (await db.signed_urls([v["etikett_bild"]])).get(v["etikett_bild"])
    return v


async def hitta(fras: str) -> dict:
    """The wine a spoken or typed phrase means ("Barolon från 2016"); 404 none, 409 several."""
    words = [w for w in re.split(r"[\s,]+", (fras or "").lower()) if len(w) > 1]
    if not words:
        raise HTTPException(400, "Säg vilket vin det gäller")
    alla = await viner(bara_i_kallaren=False)

    def score(v: dict) -> float:
        hay = " ".join(str(v.get(k) or "") for k in ("namn", "producent", "region", "land", "argang", "typ")).lower() + " " + " ".join(v.get("druvor") or []).lower()
        hits = sum(1 for w in words if w in hay)
        return hits / len(words) + (0.2 if v.get("antal", 0) > 0 else 0)

    ranked = sorted(((score(v), v) for v in alla), key=lambda x: -x[0])
    ranked = [(s, v) for s, v in ranked if s >= 0.5]
    if not ranked:
        raise HTTPException(404, f"Hittar inget vin i källaren som matchar '{fras}'")
    best = ranked[0][0]
    top = [v for s, v in ranked if s >= best - 0.05]
    if len(top) > 1:
        raise HTTPException(409, "Flera viner matchar: " + "; ".join(v["visningsnamn"] + (f" ({v['antal']} kvar)" if v.get("antal") else "") for v in top[:5]))
    return top[0]


async def lagg_till(body: dict) -> dict:
    """Add bottles: from the assortment (artikelnr) or by hand (namn ...). Same wine and vintage adds to the count."""
    antal = int(body.get("antal") or 1)
    if antal < 0 or antal > 500:
        raise HTTPException(400, "Antal måste vara 0–500")
    argang = str(body.get("argang") or "").strip() or None
    row: dict[str, Any] = {}
    if body.get("artikelnr"):
        p = await katalog.produkt(str(body["artikelnr"]))
        if not p:
            raise HTTPException(404, "Artikelnumret finns inte i sortimentet")
        row = {"artikelnr": p["artikelnr"], "namn": " ".join(x for x in (p["namn"], p.get("namn2")) if x), **{k: p.get(k) for k in KOPIERA}}
        argang = argang or p.get("argang")
        existing = await db.select("viner", {"select": "id,antal", "artikelnr": f"eq.{p['artikelnr']}",
                                             "argang": f"eq.{argang}" if argang else "is.null", "limit": "1"})
    else:
        namn = str(body.get("namn") or "").strip()
        if not namn:
            raise HTTPException(400, "Ange artikelnummer eller vinets namn")
        row = {"namn": namn}
        for k in ("producent", "typ", "land", "region", "druvor", "pris", "smak"):
            if body.get(k) not in (None, "", []):
                row[k] = body[k]
        existing = await db.select("viner", {"select": "id,antal", "namn": f"ilike.{namn}", "argang": f"eq.{argang}" if argang else "is.null",
                                             "artikelnr": "is.null", "limit": "1"})
    for k in ("plats", "anteckning", "etikett_bild"):
        if body.get(k):
            row[k] = body[k]
    if existing:
        v = existing[0]
        values = {"antal": v["antal"] + antal, "uppdaterad": now()}
        if body.get("etikett_bild"):
            values["etikett_bild"] = body["etikett_bild"]
        await db.update("viner", {"id": f"eq.{v['id']}"}, values)
        return await vin(v["id"])
    row.update({"argang": argang, "antal": antal})
    created = await db.insert("viner", row)
    return await vin(created[0]["id"])


async def andra(vin_id: str, body: dict) -> dict:
    values = {k: body[k] for k in ("antal", "plats", "anteckning", "argang", "namn", "producent") if k in body}
    if "antal" in values and (not isinstance(values["antal"], int) or values["antal"] < 0):
        raise HTTPException(400, "Antal måste vara ett heltal ≥ 0")
    if not values:
        raise HTTPException(400, "Inget att ändra")
    values["uppdaterad"] = now()
    if not await db.update("viner", {"id": f"eq.{vin_id}"}, values):
        raise HTTPException(404, "Vinet finns inte i källaren")
    return await vin(vin_id)


async def omdome(vin_id: str, body: dict, kalla: str) -> dict:
    try:
        betyg = int(body.get("betyg"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Betyg 1–5 behövs")
    if not 1 <= betyg <= 5:
        raise HTTPException(400, "Betyg 1–5 behövs")
    row = {"vin_id": vin_id, "betyg": betyg, "kalla": kalla}
    for k in ("kommentar", "mat", "datum"):
        if body.get(k):
            row[k] = str(body[k])[:1000]
    return (await db.insert("omdomen", row))[0]


async def drack(vin_id: str, body: dict, kalla: str) -> dict:
    """One bottle less; with a rating, also a review."""
    v = await vin(vin_id)
    if v["antal"] > 0:
        await db.update("viner", {"id": f"eq.{vin_id}"}, {"antal": v["antal"] - 1, "uppdaterad": now()})
    saved = await omdome(vin_id, body, kalla) if body.get("betyg") not in (None, "") else None
    out = await vin(vin_id)
    out["registrerat"] = {"flaska_drucken": v["antal"] > 0, "omdome": saved}
    return out
