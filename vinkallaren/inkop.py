"""The shopping list: wines to buy, from the assortment or by name. Ticking one off can put it in the cellar."""
import re

from fastapi import HTTPException

from . import db, kallare, katalog

FALT = "id,artikelnr,namn,producent,argang,typ,land,region,pris,antal,anteckning,kalla,kopt,kopt_datum,vin_id,skapad,uppdaterad"
KOPIERA = ("producent", "typ", "land", "region", "pris")


def visningsnamn(r: dict) -> str:
    return kallare.visningsnamn(r)


async def lista(*, alla: bool = False) -> dict:
    params = {"select": FALT, "order": "kopt.asc,skapad.asc"}
    if not alla:
        params["kopt"] = "eq.false"
    rows = await db.select("inkopslista", params)
    for r in rows:
        r["visningsnamn"] = visningsnamn(r)
    kvar = [r for r in rows if not r["kopt"]]
    summa = sum(float(r["pris"]) * r["antal"] for r in kvar if r.get("pris") is not None)
    return {"antal_viner": len(kvar), "flaskor": sum(r["antal"] for r in kvar), "uppskattad_summa_kr": round(summa) if kvar else 0,
            "utan_pris": sum(1 for r in kvar if r.get("pris") is None) or None, "viner": rows}


async def _rad(rad_id: str) -> dict:
    rows = await db.select("inkopslista", {"select": FALT, "id": f"eq.{rad_id}", "limit": "1"})
    if not rows:
        raise HTTPException(404, "Raden finns inte på inköpslistan")
    rows[0]["visningsnamn"] = visningsnamn(rows[0])
    return rows[0]


async def lagg_till(body: dict, kalla: str) -> dict:
    """{"artikelnr": "..."} from the assortment, {"namn": ...} by hand, or {"vin": "phrase"} (matched to the assortment
    when the hit is clear, otherwise added by name). The same unbought wine again adds to the count."""
    try:
        antal = int(body["antal"]) if body.get("antal") not in (None, "") else 1
    except (TypeError, ValueError):
        raise HTTPException(400, "Antal måste vara ett heltal")
    if not 1 <= antal <= 500:
        raise HTTPException(400, "Antal måste vara 1–500")
    artikelnr = str(body.get("artikelnr") or "").strip() or None
    namn = str(body.get("namn") or "").strip()
    kalla_text = None
    if not artikelnr and not namn and body.get("vin"):
        traff = await katalog.basta_traff(str(body["vin"]))
        if traff:
            artikelnr, kalla_text = traff["artikelnr"], "Systembolaget"
        else:
            namn, kalla_text = " ".join(str(body["vin"]).split())[:120], "eget namn (hittades inte säkert hos Systembolaget)"
    row: dict = {"antal": antal, "kalla": kalla}
    if artikelnr:
        p = await katalog.produkt(artikelnr)
        if not p:
            raise HTTPException(404, "Artikelnumret finns inte i sortimentet")
        row.update({"artikelnr": p["artikelnr"], "namn": " ".join(x for x in (p["namn"], p.get("namn2")) if x),
                    "argang": p.get("argang"), **{k: p.get(k) for k in KOPIERA}})
        existing = await db.select("inkopslista", {"select": "id,antal", "artikelnr": f"eq.{p['artikelnr']}", "kopt": "eq.false", "limit": "1"})
    elif namn:
        row["namn"] = namn
        for k in ("producent", "argang", "typ", "land", "region", "pris"):
            if body.get(k) not in (None, ""):
                row[k] = body[k]
        existing = await db.select("inkopslista", {"select": "id,antal", "namn": f"ilike.{namn}", "artikelnr": "is.null", "kopt": "eq.false", "limit": "1"})
    else:
        raise HTTPException(400, "Ange vin, artikelnummer eller namn")
    if body.get("anteckning"):
        row["anteckning"] = str(body["anteckning"])[:500]
    if existing:
        values = {"antal": existing[0]["antal"] + antal, "uppdaterad": kallare.now()}
        if row.get("anteckning"):
            values["anteckning"] = row["anteckning"]
        await db.update("inkopslista", {"id": f"eq.{existing[0]['id']}"}, values)
        out = await _rad(existing[0]["id"])
        out["fanns_redan"] = True
    else:
        out = await _rad((await db.insert("inkopslista", row))[0]["id"])
    if kalla_text:
        out["matchning"] = kalla_text
    return out


async def hitta(fras: str) -> dict:
    """The unbought row a phrase means; 404 none, 409 several."""
    words = [w for w in re.split(r"[\s,]+", (fras or "").lower()) if len(w) > 1]
    if not words:
        raise HTTPException(400, "Säg vilket vin det gäller")
    rows = (await lista())["viner"]
    scored = []
    for r in rows:
        hay = " ".join(str(r.get(k) or "") for k in ("namn", "producent", "region", "land", "argang", "typ")).lower()
        scored.append((sum(1 for w in words if w in hay) / len(words), r))
    scored = [(s, r) for s, r in sorted(scored, key=lambda x: -x[0]) if s >= 0.5]
    if not scored:
        raise HTTPException(404, f"Hittar inget vin på inköpslistan som matchar '{fras}'")
    top = [r for s, r in scored if s >= scored[0][0] - 0.05]
    if len(top) > 1:
        raise HTTPException(409, "Flera viner på listan matchar: " + "; ".join(r["visningsnamn"] for r in top[:5]))
    return top[0]


async def andra(rad_id: str, body: dict) -> dict:
    values = {}
    if "antal" in body:
        if not isinstance(body["antal"], int) or not 1 <= body["antal"] <= 500:
            raise HTTPException(400, "Antal måste vara 1–500")
        values["antal"] = body["antal"]
    if "anteckning" in body:
        values["anteckning"] = str(body["anteckning"] or "")[:500] or None
    if not values:
        raise HTTPException(400, "Inget att ändra")
    values["uppdaterad"] = kallare.now()
    if not await db.update("inkopslista", {"id": f"eq.{rad_id}"}, values):
        raise HTTPException(404, "Raden finns inte på inköpslistan")
    return await _rad(rad_id)


async def ta_bort(rad_id: str) -> dict:
    r = await _rad(rad_id)
    await db.delete("inkopslista", {"id": f"eq.{rad_id}"})
    return {"borttagen": r["visningsnamn"]}


async def kopt(rad_id: str, body: dict) -> dict:
    """Tick a row off. By default the bottles go into the cellar (till_kallaren=false skips that)."""
    r = await _rad(rad_id)
    if r["kopt"]:
        raise HTTPException(409, f"{r['visningsnamn']} är redan avbockad")
    antal = body.get("antal") if body.get("antal") not in (None, "") else r["antal"]
    try:
        antal = int(antal)
    except (TypeError, ValueError):
        raise HTTPException(400, "Antal måste vara ett heltal")
    vin = None
    if body.get("till_kallaren", True) is not False and antal > 0:
        add = {"antal": antal, "argang": body.get("argang") or r.get("argang")}
        if r.get("artikelnr"):
            add["artikelnr"] = r["artikelnr"]
        else:
            add.update({k: r.get(k) for k in ("namn", "producent", "typ", "land", "region", "pris") if r.get(k) not in (None, "")})
        vin = await kallare.lagg_till(add)
    await db.update("inkopslista", {"id": f"eq.{rad_id}"}, {"kopt": True, "kopt_datum": kallare.now(), "uppdaterad": kallare.now(),
                                                         "vin_id": vin["id"] if vin else None})
    out = await _rad(rad_id)
    if vin:
        out["i_kallaren"] = {"vin": vin["visningsnamn"], "antal_nu": vin["antal"]}
    return out
