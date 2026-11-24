"""An in-memory stand-in for Supabase (the PostgREST filters the app uses) and test settings."""
import copy
import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

KOD = "familj-test"
NYCKEL = "agent-test"
CRON = "cron-test"


class FakeDB:
    def __init__(self):
        self.t = {"produkter": [], "viner": [], "omdomen": [], "synk": [], "inkopslista": []}
        self.uploads = {}

    @staticmethod
    def _match(row, key, cond):
        v = row.get(key)
        op, _, arg = cond.partition(".")
        if op == "eq":
            if isinstance(v, bool):
                return str(v).lower() == arg
            return str(v) == arg if v is not None else False
        if op == "gt":
            return v is not None and float(v) > float(arg)
        if op == "lte":
            return v is not None and float(v) <= float(arg)
        if op == "gte":
            return v is not None and float(v) >= float(arg)
        if op == "is":
            return v is None
        if op == "in":
            items = [x.strip().strip('"') for x in arg.strip("()").split(",")]
            return str(v) in items
        if op == "ilike":
            pat = "^" + re.escape(arg.lower()).replace(r"\*", ".*") + "$"
            return v is not None and re.match(pat, str(v).lower()) is not None
        if op == "ov":
            items = set(arg.strip("{}").split(","))
            return bool(items & set(v or []))
        raise AssertionError(f"filter {cond}")

    def _filter(self, rows, params):
        out = []
        for r in rows:
            ok = True
            for k, cond in (params or {}).items():
                if k in ("select", "order", "limit", "on_conflict"):
                    continue
                if k == "and":
                    for part in cond.strip("()").split(","):
                        col, _, rest = part.partition(".")
                        ok = ok and self._match(r, col, rest)
                    continue
                ok = ok and self._match(r, k, cond)
            if ok:
                out.append(r)
        return out

    async def select(self, table, params=None):
        rows = self._filter(self.t[table], params)
        order = (params or {}).get("order")
        if order:
            for part in reversed(order.split(",")):
                col, _, direction = part.partition(".")
                rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col) or ""), reverse=direction == "desc")
        if (params or {}).get("limit"):
            rows = rows[: int(params["limit"])]
        return copy.deepcopy(rows)

    async def insert(self, table, rows, *, upsert=False):
        table = table.split("?")[0]
        rows = rows if isinstance(rows, list) else [rows]
        out = []
        for r in rows:
            r = copy.deepcopy(r)
            if table == "produkter" and upsert:
                self.t[table] = [x for x in self.t[table] if x["artikelnr"] != r["artikelnr"]]
            else:
                r.setdefault("id", str(uuid.uuid4()))
            if table == "viner":
                for k, d in (("druvor", []), ("passar_till", []), ("antal", 0), ("plats", "vinkylen"), ("skapad", "2026-11-23T10:00:00+01:00")):
                    r.setdefault(k, d)
            if table == "inkopslista":
                r.setdefault("kopt", False)
                r.setdefault("skapad", f"2026-11-24T10:00:{len(self.t['inkopslista']):02d}+01:00")
            if table == "omdomen":
                r.setdefault("datum", date.today().isoformat())
                r.setdefault("skapad", f"2026-11-23T10:00:{len(self.t['omdomen']):02d}+01:00")
            self.t[table].append(r)
            out.append(copy.deepcopy(r))
        return out

    async def update(self, table, match, values):
        out = []
        for r in self._filter(self.t[table], match):
            r.update(values)
            out.append(copy.deepcopy(r))
        return out

    async def delete(self, table, match):
        doomed = self._filter(self.t[table], match)
        self.t[table] = [r for r in self.t[table] if r not in doomed]

    async def rpc(self, fn, args):
        assert fn == "sok_produkter"
        q = args["q"].lower().split()
        out = []
        for p in self.t["produkter"]:
            hay = " ".join(str(p.get(k) or "") for k in ("namn", "namn2", "producent", "region", "region2", "land")).lower()
            hits = sum(1 for w in q if w in hay)
            if hits:
                out.append({**{k: p.get(k) for k in ("artikelnr", "namn", "namn2", "producent", "typ", "land", "region", "argang", "pris",
                                                      "druvor", "passar_till", "bild_url")}, "likhet": hits / len(q)})
        out.sort(key=lambda r: -r["likhet"])
        return out[: args.get("max_antal", 8)]

    async def upload(self, path, data, content_type):
        self.uploads[path] = (data, content_type)
        return path

    async def signed_urls(self, paths, seconds=3600):
        return {p: f"https://bilder.test/{p}?token=x" for p in paths if p}


@pytest.fixture
def fake(monkeypatch):
    from vinkallaren import db
    f = FakeDB()
    for name in ("select", "insert", "update", "delete", "rpc", "upload", "signed_urls"):
        monkeypatch.setattr(db, name, getattr(f, name))
    monkeypatch.setattr(db, "configured", lambda: True)
    return f


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FAMILJ_KOD", KOD)
    monkeypatch.setenv("VINKALLAREN_API_KEY", NYCKEL)
    monkeypatch.setenv("CRON_SECRET", CRON)
    monkeypatch.setenv("SUPABASE_URL", "https://db.test")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def client(fake):
    from main import app
    return TestClient(app)


APP = {"X-Familj-Kod": KOD}
AGENT = {"X-Api-Key": NYCKEL}


def produkt(nr, namn, typ, passar, *, namn2=None, producent=None, druvor=(), land="Frankrike", region=None, pris=199, argang="2020",
            klockor=(None, None, None, None), sortiment="Fast sortiment"):
    f, s, fs, so = klockor
    return {"artikelnr": nr, "namn": namn, "namn2": namn2, "producent": producent, "typ": typ, "land": land, "region": region,
            "druvor": list(druvor), "argang": argang, "pris": pris, "passar_till": list(passar), "fyllighet": f, "stravhet": s,
            "fruktsyra": fs, "sotma": so, "smak": f"Smak av {namn}", "anvandning": "Serveras vid 16°C", "sortiment": sortiment, "bild_url": None}


@pytest.fixture
def sortiment(fake):
    fake.t["produkter"] = [
        produkt("1001", "Barolo", "Rött vin", ["Lamm", "Nöt", "Vilt"], namn2="Cannubi", producent="Ceretto", druvor=["Nebbiolo"], land="Italien",
                region="Piemonte", pris=449, argang="2016", klockor=(10, 10, 9, 1)),
        produkt("1002", "Chablis", "Vitt vin", ["Fisk", "Skaldjur"], producent="William Fèvre", druvor=["Chardonnay"], region="Bourgogne",
                pris=229, argang="2022", klockor=(6, None, 9, 1)),
        produkt("1003", "Côtes du Rhône", "Rött vin", ["Nöt", "Fläsk"], producent="Guigal", druvor=["Syrah", "Grenache"], region="Rhonedalen",
                pris=139, klockor=(8, 7, 7, 2)),
        produkt("1004", "Champagne Brut", "Mousserande vin", ["Aperitif", "Skaldjur"], producent="Pol Roger", druvor=["Pinot noir", "Chardonnay"],
                region="Champagne", pris=499, argang=None, klockor=(7, None, 10, 2)),
        produkt("1005", "Langhe Nebbiolo", "Rött vin", ["Lamm", "Nöt"], producent="Produttori", druvor=["Nebbiolo"], land="Italien",
                region="Piemonte", pris=239, klockor=(9, 9, 9, 1)),
        produkt("1006", "Rioja Reserva", "Rött vin", ["Lamm", "Nöt"], producent="Muga", druvor=["Tempranillo"], land="Spanien",
                region="Rioja", pris=219, klockor=(9, 8, 8, 2), sortiment="Ordervaror"),
    ]
    return fake
