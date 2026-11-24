"""Vinkällaren: the family's wine fridge, reviews and Systembolaget's assortment (deployed on Vercel).

The app (static/) and agent-tools use the same JSON API under /api/v1. The app sends the family code
(X-Familj-Kod), agent-tools its key (X-Api-Key), the nightly catalogue sync Vercel's CRON_SECRET.
"""
import uuid
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from vinkallaren import auth, config, db, etikett, forslag, inkop, kallare, katalog, profil

app = FastAPI(title="Vinkällaren", version="1.0.0")
STATIC = Path(__file__).parent / "static"
MAX_BILD = 6 * 1024 * 1024


@app.get("/health")
async def health():
    return {"ok": True, "databas": db.configured(), "etikettlasning": config.anthropic_configured(),
            "familj_kod": bool(config.familj_kod()), "api_nyckel": bool(config.api_key()), "cron_secret": bool(config.cron_secret())}


# ── Källaren ───────────────────────────────────────────────────────────────
@app.get("/api/v1/status")
async def status(_: str = Depends(auth.kalla)):
    """Antal viner och flaskor i källaren, antal omdömen och när sortimentet senast synkades."""
    viner = await kallare.viner(bara_i_kallaren=False)
    i_kallaren = [v for v in viner if v["antal"] > 0]
    return {"viner_i_kallaren": len(i_kallaren), "flaskor": sum(v["antal"] for v in i_kallaren),
            "viner_totalt": len(viner), "omdomen": sum(v["antal_omdomen"] for v in viner),
            "sortiment": await katalog.senaste_synk(), "etikettlasning": config.anthropic_configured()}


@app.get("/api/v1/kallaren")
async def get_kallaren(typ: str | None = Query(None, description="Rött vin, Vitt vin, Rosévin, Mousserande vin ..."),
                       sok: str | None = Query(None, description="Fritext: namn, producent, region, druva, årgång"),
                       alla: bool = Query(False, description="true = även viner som tagit slut"),
                       bilder: bool = Query(False), _: str = Depends(auth.kalla)):
    """Vinerna i källaren med antal flaskor, druvor, region, smak, Systembolagets 'passar till' och våra betyg."""
    rows = await kallare.viner(bara_i_kallaren=not alla, typ=typ, sok=sok, bilder=bilder)
    return {"antal_viner": len(rows), "flaskor": sum(v["antal"] for v in rows), "viner": rows}


@app.get("/api/v1/viner/{vin_id}")
async def get_vin(vin_id: uuid.UUID, _: str = Depends(auth.kalla)):
    return await kallare.vin(str(vin_id))


@app.post("/api/v1/viner")
async def post_vin(body: dict = Body(...), _: str = Depends(auth.kalla)):
    """Lägg in flaskor: {"artikelnr": "...", "antal": 2, "argang": "2019"} eller {"namn": "...", "producent": ..., "typ": ...}."""
    return await kallare.lagg_till(body)


@app.patch("/api/v1/viner/{vin_id}")
async def patch_vin(vin_id: uuid.UUID, body: dict = Body(...), _: str = Depends(auth.kalla)):
    return await kallare.andra(str(vin_id), body)


@app.post("/api/v1/viner/{vin_id}/drack")
async def post_drack(vin_id: uuid.UUID, body: dict | None = Body(None), kalla: str = Depends(auth.kalla)):
    """En flaska mindre; med betyg sparas också ett omdöme: {"betyg": 4, "kommentar": "...", "mat": "..."}."""
    return await kallare.drack(str(vin_id), body or {}, kalla)


@app.post("/api/v1/viner/{vin_id}/omdome")
async def post_omdome(vin_id: uuid.UUID, body: dict = Body(...), kalla: str = Depends(auth.kalla)):
    await kallare.vin(str(vin_id))
    return await kallare.omdome(str(vin_id), body, kalla)


# For the voice agent: name the wine in words instead of an id.
@app.post("/api/v1/drack")
async def post_drack_fras(body: dict = Body(...), kalla: str = Depends(auth.kalla)):
    """{"vin": "barolon från 2016", "betyg": 4, "kommentar": "...", "mat": "..."} – en flaska mindre (och ett omdöme).
    Med betyg och ett vin som inte finns i källaren läggs vinet till med 0 flaskor (ur sortimentet om det hittas)."""
    fras = str(body.get("vin") or "")
    if body.get("betyg") in (None, ""):
        v = await kallare.hitta(fras)
        return await kallare.drack(v["id"], body, kalla)
    v, nytt = await kallare.hitta_eller_skapa(fras)
    out = await kallare.drack(v["id"], body, kalla)
    if nytt:
        out["nytt_vin"] = f"Fanns inte i källaren; lades till (0 flaskor) från {nytt}"
    return out


@app.post("/api/v1/omdome")
async def post_omdome_fras(body: dict = Body(...), kalla: str = Depends(auth.kalla)):
    """{"vin": "...", "betyg": 1-5, "kommentar": "...", "mat": "..."} – omdöme utan att räkna ner antalet. Ett vin som inte
    finns i källaren (present, restaurang) läggs till med 0 flaskor, ur sortimentet om det hittas, annars med namnet."""
    v, nytt = await kallare.hitta_eller_skapa(str(body.get("vin") or ""))
    saved = await kallare.omdome(v["id"], body, kalla)
    out = {"vin": v["visningsnamn"], "omdome": saved}
    if nytt:
        out["nytt_vin"] = f"Fanns inte i källaren; lades till (0 flaskor) från {nytt}"
    return out


# ── Inköpslistan ───────────────────────────────────────────────────────────
@app.get("/api/v1/inkop")
async def get_inkop(alla: bool = Query(False, description="true = även avbockade"), _: str = Depends(auth.kalla)):
    """Inköpslistan: viner att köpa med antal, pris och uppskattad summa."""
    return await inkop.lista(alla=alla)


@app.post("/api/v1/inkop")
async def post_inkop(body: dict = Body(...), kalla: str = Depends(auth.kalla)):
    """Lägg till: {"artikelnr": "...", "antal": 2} ur sortimentet, {"namn": "..."} för hand, eller {"vin": "ripasso från angelini"}
    som matchas mot sortimentet. Samma vin igen ökar antalet."""
    return await inkop.lagg_till(body, kalla)


@app.patch("/api/v1/inkop/{rad_id}")
async def patch_inkop(rad_id: uuid.UUID, body: dict = Body(...), _: str = Depends(auth.kalla)):
    return await inkop.andra(str(rad_id), body)


@app.delete("/api/v1/inkop/{rad_id}")
async def delete_inkop(rad_id: uuid.UUID, _: str = Depends(auth.kalla)):
    return await inkop.ta_bort(str(rad_id))


@app.post("/api/v1/inkop/{rad_id}/kopt")
async def post_inkop_kopt(rad_id: uuid.UUID, body: dict | None = Body(None), _: str = Depends(auth.kalla)):
    """Bocka av som köpt; flaskorna läggs i vinkylen om inte {"till_kallaren": false}. {"antal": n, "argang": "..."} valfritt."""
    return await inkop.kopt(str(rad_id), body or {})


# For the voice agent: name the wine in words.
@app.post("/api/v1/inkop/kopt")
async def post_inkop_kopt_fras(body: dict = Body(...), _: str = Depends(auth.kalla)):
    """{"vin": "ripasson", "antal": 2, "till_kallaren": true} – bocka av ett vin på listan med ord."""
    r = await inkop.hitta(str(body.get("vin") or ""))
    return await inkop.kopt(r["id"], body)


@app.post("/api/v1/inkop/ta-bort")
async def post_inkop_ta_bort_fras(body: dict = Body(...), _: str = Depends(auth.kalla)):
    """{"vin": "..."} – ta bort ett vin från listan med ord."""
    r = await inkop.hitta(str(body.get("vin") or ""))
    return await inkop.ta_bort(r["id"])


# ── Sortimentet och etiketter ──────────────────────────────────────────────
@app.get("/api/v1/katalog/sok")
async def get_sok(q: str = Query(..., min_length=2), argang: str | None = None, antal: int = Query(8, ge=1, le=30),
                  _: str = Depends(auth.kalla)):
    """Sök i Systembolagets vinsortiment på namn, producent och region."""
    return {"traffar": await katalog.sok(q, argang, antal)}


@app.api_route("/api/v1/katalog/synk", methods=["GET", "POST"])
async def synk(_: str = Depends(auth.schemalaggare_eller_app)):
    """Hämta Systembolagets vinsortiment på nytt (körs nattligen)."""
    return await katalog.synka()


@app.get("/api/v1/katalog/{artikelnr}")
async def get_produkt(artikelnr: str, _: str = Depends(auth.kalla)):
    p = await katalog.produkt(artikelnr)
    if not p:
        raise HTTPException(404, "Finns inte i sortimentet")
    return p


@app.post("/api/v1/skanna")
async def skanna(bild: UploadFile = File(...), _: str = Depends(auth.kalla)):
    """Foto av etiketten → avläst etikett och de bäst matchande vinerna i sortimentet. Bilden sparas för källaren."""
    data = await bild.read()
    if len(data) > MAX_BILD:
        raise HTTPException(413, "Bilden är för stor (max 6 MB)")
    media = bild.content_type or "image/jpeg"
    las = await etikett.las(data, media)
    path = f"{uuid.uuid4()}.{media.split('/')[-1].replace('jpeg', 'jpg')}"
    try:
        await db.upload(path, data, media)
    except HTTPException:
        path = None
    kandidater = await etikett.kandidater(las) if las.get("ar_vin") else []
    return {"etikett": las, "kandidater": kandidater, "etikett_bild": path}


# ── Förslag ────────────────────────────────────────────────────────────────
def _lista(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@app.get("/api/v1/forslag/mat")
async def get_till_mat(mat: str | None = Query(None, description="Vad ni ska äta, med egna ord"),
                       kategorier: str | None = Query(None, description="Kommaseparerat: " + ", ".join(forslag.KATEGORIER)),
                       antal: int = Query(5, ge=1, le=20), _: str = Depends(auth.kalla)):
    """Vilket vin i källaren passar till maten? Rangordnat med skäl (Systembolagets 'passar till', vår smakprofil, våra betyg)."""
    if not mat and not kategorier:
        raise HTTPException(400, "Ange mat eller kategorier")
    return await forslag.till_mat(mat, _lista(kategorier), antal)


@app.get("/api/v1/forslag/kop")
async def get_kop(mat: str | None = None, kategorier: str | None = None, typ: str | None = None,
                  max_pris: float | None = Query(None, gt=0), min_pris: float | None = Query(None, gt=0),
                  bestallningsvaror: bool = Query(False, description="true = även beställningsvaror"),
                  antal: int = Query(8, ge=1, le=20), _: str = Depends(auth.kalla)):
    """Köptips ur Systembolagets sortiment efter vår smak, valfritt till en rätt, en vintyp eller en prisnivå."""
    return await forslag.kopa(mat=mat, kategorier=_lista(kategorier), typ=typ, max_pris=max_pris, min_pris=min_pris,
                              bestallningsvaror=bestallningsvaror, antal=antal)


@app.get("/api/v1/profil")
async def get_profil(_: str = Depends(auth.kalla)):
    """Familjens smakprofil: druvor, länder och regioner vi gillar och inte gillar, smakbild, prisnivå, favoriter."""
    return profil.publik(await profil.bygg())


# ── Appen ──────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/ikon.svg", include_in_schema=False)
async def ikon():
    return FileResponse(STATIC / "ikon.svg", media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})
