"""Which wine to open for a dish, and what to buy next.

Systembolaget's own "passar till" symbols (Lamm, Nöt, Fisk ...) carry the pairing; a dish is turned
into those categories with a Swedish keyword list (the agent may also pass the categories itself).
The family's taste profile adds a bonus. Each suggestion comes with short Swedish reasons, so the
sommelier can explain it and still apply its own judgement.
"""
import re

from fastapi import HTTPException

from . import db, kallare, profil

KATEGORIER = ["Lamm", "Nöt", "Fläsk", "Fågel", "Vilt", "Fisk", "Skaldjur", "Grönsaker", "Ost", "Dessert", "Asiatiskt",
              "Kryddstarkt", "Aperitif", "Sällskapsdryck", "Buffémat"]
ORD = {
    "Lamm": ["lamm", "får", "lammstek", "lammracks", "lammkotlett", "moussaka"],
    "Nöt": ["nöt", "biff", "entrecote", "entrecôte", "oxfilé", "ryggbiff", "högrev", "hamburgare", "burgare", "steak", "oxkind",
            "grytbit", "köttgryta", "bourguignon", "rostbiff", "flankstek", "köttfärs", "lasagne", "bolognese", "kalv", "wallenbergare"],
    "Fläsk": ["fläsk", "gris", "kotlett", "karré", "bacon", "skinka", "revben", "korv", "salsiccia", "carbonara", "pulled pork"],
    "Fågel": ["kyckling", "höna", "anka", "ankbröst", "kalkon", "fågel", "gås", "coq au vin"],
    "Vilt": ["vilt", "älg", "rådjur", "hjort", "vildsvin", "ren", "renskav", "fasan", "rapphöna"],
    "Fisk": ["fisk", "lax", "torsk", "gös", "abborre", "sill", "strömming", "rödspätta", "hälleflundra", "tonfisk", "makrill", "röding",
             "sjötunga", "piggvar", "kolja", "fish"],
    "Skaldjur": ["räk", "kräft", "hummer", "mussl", "ostron", "krabba", "havskräft", "pilgrim", "skaldjur", "bläckfisk", "sushi", "toast skagen"],
    "Grönsaker": ["vego", "vegetarisk", "vegansk", "grönsak", "svamp", "kantarell", "risotto", "sallad", "halloumi", "pumpa", "sparris",
                  "tomat", "pasta", "pizza", "rödbet", "linser", "bönor"],
    "Ost": ["ost", "chèvre", "brie", "parmesan", "gorgonzola", "comté", "västerbotten", "fondue"],
    "Dessert": ["dessert", "tårta", "choklad", "kaka", "glass", "paj", "efterrätt", "crème brûlée", "pannacotta"],
    "Asiatiskt": ["thai", "kinesisk", "indisk", "sushi", "wok", "japansk", "vietnam", "asiatisk", "korean", "ramen", "pad thai", "dumplings"],
    "Kryddstarkt": ["kryddstark", "chili", "stark", "tacos", "curry", "mexikansk", "jalapeño", "vindaloo"],
    "Aperitif": ["aperitif", "fördrink", "välkomstdrink", "snacks", "chips", "nötter", "tilltugg"],
    "Sällskapsdryck": ["sällskap", "mingel", "bara dricka", "på altanen", "fredagsmys"],
    "Buffémat": ["buffé", "julbord", "smörgåsbord", "påskbord", "midsommar", "plock", "tapas", "charkbricka", "chark"],
}
# For wines without Systembolaget's symbols (added by hand): a sensible default per wine type.
TYP_STANDARD = {
    "Rött vin": {"Nöt", "Lamm", "Vilt", "Fläsk", "Ost"},
    "Vitt vin": {"Fisk", "Skaldjur", "Fågel", "Grönsaker"},
    "Rosévin": {"Grönsaker", "Fisk", "Fågel", "Sällskapsdryck", "Buffémat"},
    "Mousserande vin": {"Aperitif", "Skaldjur", "Sällskapsdryck", "Buffémat"},
    "Starkvin": {"Ost", "Dessert"},
}


def kategorier_for(mat: str | None, givna: list[str] | None = None) -> list[str]:
    out = [k for k in (givna or []) if k in KATEGORIER]
    text = (mat or "").lower()
    for kat, words in ORD.items():
        if kat not in out and any(re.search(rf"(?<![a-zåäö]){re.escape(w)}", text) for w in words):
            out.append(kat)
    return out


def _profil_bonus(v: dict, p: dict) -> tuple[float, list[str]]:
    w, reasons = p.get("_vikter") or {}, []
    bonus = 0.0
    druv = [d for d in v.get("druvor") or [] if (w.get("druvor") or {}).get(d, 0) > 0]
    if druv:
        bonus += min(1.0, 0.4 * len(druv))
        reasons.append(f"ni brukar gilla {', '.join(druv[:2])}")
    for k, label in (("region", "regionen"), ("land", "landet")):
        if v.get(k) and (w.get(k) or {}).get(v[k], 0) > 0:
            bonus += 0.4
            reasons.append(f"ni tycker om viner från {v[k]}")
            break
    smak = p.get("_smak") or {}
    diffs = [abs(v[k] - smak[k]) for k in profil.KLOCKOR if v.get(k) and k in smak]
    if diffs:
        avvik = sum(diffs) / len(diffs)
        bonus += max(0.0, 1 - avvik / 4)
        if avvik <= 1.5:
            reasons.append("smakprofilen ligger nära det ni brukar uppskatta")
    if any((w.get("druvor") or {}).get(d, 0) < 0 for d in v.get("druvor") or []):
        bonus -= 1.0
        reasons.append("obs: druvan har fått låga betyg tidigare")
    return bonus, reasons


def _matchning(v: dict, kat: list[str]) -> tuple[float, list[str]]:
    symbols = set(v.get("passar_till") or [])
    source = "Systembolaget"
    if not symbols:
        symbols, source = TYP_STANDARD.get(v.get("typ") or "", set()), "vintypen"
    hits = [k for k in kat if k in symbols]
    if not kat:
        return 0.0, []
    if hits:
        score = 3.0 + (0.5 if len(hits) > 1 else 0) - (1.0 if source == "vintypen" else 0)
        return score, [f"enligt {source} passar det till {', '.join(h.lower() for h in hits)}"]
    return -1.0, []


async def till_mat(mat: str | None, kategorier: list[str] | None = None, antal: int = 5) -> dict:
    kat = kategorier_for(mat, kategorier)
    viner = await kallare.viner(bara_i_kallaren=True)
    if not viner:
        return {"mat": mat, "kategorier": kat, "forslag": [], "meddelande": "Källaren är tom"}
    p = await profil.bygg()
    ranked = []
    for v in viner:
        m_score, m_reasons = _matchning(v, kat)
        p_score, p_reasons = _profil_bonus(v, p)
        betyg = v.get("snittbetyg")
        b_score = (betyg - 3) * 0.6 if betyg else 0
        reasons = m_reasons + p_reasons + ([f"ni har gett den {betyg} i snitt"] if betyg else [])
        ranked.append((m_score + p_score + b_score, v, reasons))
    ranked.sort(key=lambda x: -x[0])
    keep = ("id", "visningsnamn", "typ", "land", "region", "druvor", "argang", "antal", "passar_till", "fyllighet", "stravhet",
            "fruktsyra", "sotma", "smak", "anvandning", "snittbetyg", "senaste")
    forslag = [{**{k: v.get(k) for k in keep if v.get(k) not in (None, [], "")}, "poang": round(s, 1), "skal": r} for s, v, r in ranked[:antal]]
    return {"mat": mat, "kategorier": kat or None,
            "notering": None if kat else "Kunde inte avgöra vad rätten är för typ av mat; ange kategorier (" + ", ".join(KATEGORIER) + ")",
            "forslag": forslag,
            "ovriga_i_kallaren": [v["visningsnamn"] for _, v, _ in ranked[antal:]][:30]}


async def kopa(*, mat: str | None = None, kategorier: list[str] | None = None, typ: str | None = None, max_pris: float | None = None,
               min_pris: float | None = None, bestallningsvaror: bool = False, antal: int = 8) -> dict:
    """Wines from Systembolaget's assortment that fit the family's taste (and a dish, if given)."""
    kat = kategorier_for(mat, kategorier)
    params = {"select": "artikelnr,namn,namn2,producent,typ,land,region,druvor,argang,pris,passar_till,fyllighet,stravhet,fruktsyra,sotma,smak,sortiment,bild_url",
              "limit": "2000", "order": "pris.asc"}
    if typ:
        params["typ"] = f"ilike.*{typ}*"
    rng = []
    if max_pris:
        rng.append(f"pris.lte.{float(max_pris)}")
    if min_pris:
        rng.append(f"pris.gte.{float(min_pris)}")
    if rng:
        params["and"] = f"({','.join(rng)})"
    if not bestallningsvaror:
        params["sortiment"] = "in.(Fast sortiment,Tillfälligt sortiment,Säsong,Lokalt & Småskaligt)"
    if kat:
        params["passar_till"] = f"ov.{{{','.join(kat)}}}"
    rows = await db.select("produkter", params)
    if not rows:
        raise HTTPException(404, "Inga viner i sortimentet matchar – prova utan prisgräns eller typ")
    p = await profil.bygg()
    har = {v.get("artikelnr") for v in await kallare.viner(bara_i_kallaren=False) if v.get("artikelnr")}
    median = (p.get("prisniva") or {}).get("median")
    ranked = []
    for r in rows:
        score, reasons = _profil_bonus(r, p)
        if kat:
            hits = [k for k in kat if k in (r.get("passar_till") or [])]
            score += 1.0 + 0.3 * len(hits)
            reasons.insert(0, f"Systembolaget anger att det passar till {', '.join(h.lower() for h in hits)}")
        if r["artikelnr"] in har:
            score -= 2.0
            reasons.append("finns redan i källaren eller har druckits")
        if median and r.get("pris"):
            score -= min(1.0, abs(float(r["pris"]) - median) / max(median, 100))
        if r.get("fyllighet") is None and r.get("fruktsyra") is None:
            score -= 0.3  # no taste data: harder to judge
        ranked.append((score, r, reasons))
    ranked.sort(key=lambda x: -x[0])
    keep = ("artikelnr", "namn", "namn2", "producent", "typ", "land", "region", "druvor", "argang", "pris", "passar_till", "smak", "sortiment")
    return {"mat": mat, "kategorier": kat or None, "underlag": p["underlag"], "antal_kandidater": len(rows),
            "forslag": [{**{k: r.get(k) for k in keep if r.get(k) not in (None, [], "")}, "poang": round(s, 1), "skal": why}
                        for s, r, why in ranked[:antal]]}
