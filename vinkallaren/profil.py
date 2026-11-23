"""The family's taste, from the reviews (and, before there are any, from what is in the cellar)."""
from collections import defaultdict

from . import kallare

KLOCKOR = ("fyllighet", "stravhet", "fruktsyra", "sotma")
KLOCK_SV = {"fyllighet": "fyllighet", "stravhet": "strävhet", "fruktsyra": "fruktsyra", "sotma": "sötma"}


def _vikt(betyg: float) -> float:
    return betyg - 3  # 5 → +2, 4 → +1, 3 → 0, 2 → −1, 1 → −2


async def bygg() -> dict:
    alla = {v["id"]: v for v in await kallare.viner(bara_i_kallaren=False)}
    rows = await kallare.omdomen()
    basis = "omdömen"
    points: list[tuple[dict, float]] = [(alla[o["vin_id"]], _vikt(o["betyg"])) for o in rows if o["vin_id"] in alla]
    if not points:  # no reviews yet: what we bought says something
        basis = "källarens innehåll (inga omdömen än)"
        points = [(v, 0.5) for v in alla.values() if v.get("antal", 0) > 0]
    pref: dict[str, dict[str, float]] = {"druvor": defaultdict(float), "land": defaultdict(float), "region": defaultdict(float), "typ": defaultdict(float)}
    klock_sum, klock_w = defaultdict(float), defaultdict(float)
    priser = []
    for v, w in points:
        for d in v.get("druvor") or []:
            pref["druvor"][d] += w
        for k in ("land", "region", "typ"):
            if v.get(k):
                pref[k][v[k]] += w
        if w > 0:
            for k in KLOCKOR:
                if v.get(k):
                    klock_sum[k] += v[k] * w
                    klock_w[k] += w
            if v.get("pris"):
                priser.append(float(v["pris"]))
    def top(d: dict, n: int, positive: bool = True) -> list[str]:
        items = sorted(((k, s) for k, s in d.items() if (s > 0 if positive else s < 0)), key=lambda x: -abs(x[1]))
        return [k for k, _ in items[:n]]
    priser.sort()
    favoriter = sorted((v for v in alla.values() if v.get("snittbetyg")), key=lambda v: -v["snittbetyg"])
    return {
        "underlag": basis, "antal_omdomen": len(rows), "antal_viner": len(alla),
        "gillar": {"druvor": top(pref["druvor"], 6), "land": top(pref["land"], 4), "regioner": top(pref["region"], 5), "typer": top(pref["typ"], 3)},
        "gillar_inte": {"druvor": top(pref["druvor"], 4, False), "regioner": top(pref["region"], 4, False)},
        "smak": {KLOCK_SV[k]: round(klock_sum[k] / klock_w[k], 1) for k in KLOCKOR if klock_w[k]},
        "prisniva": {"median": priser[len(priser) // 2], "min": priser[0], "max": priser[-1]} if priser else None,
        "favoriter": [{"vin": v["visningsnamn"], "snittbetyg": v["snittbetyg"]} for v in favoriter[:5] if v["snittbetyg"] >= 4],
        "minst_omtyckta": [{"vin": v["visningsnamn"], "snittbetyg": v["snittbetyg"]} for v in favoriter[::-1][:3] if v["snittbetyg"] <= 2],
        "_vikter": {k: dict(d) for k, d in pref.items()},
        "_smak": {k: klock_sum[k] / klock_w[k] for k in KLOCKOR if klock_w[k]},
    }


def publik(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}
