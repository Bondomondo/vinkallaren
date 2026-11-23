import asyncio
import json

import httpx
import pytest
import respx

from conftest import AGENT, APP, CRON


def test_auth(client):
    assert client.get("/api/v1/kallaren").status_code == 401
    assert client.get("/api/v1/kallaren", headers={"X-Familj-Kod": "fel"}).status_code == 401
    assert client.get("/api/v1/kallaren", headers=APP).status_code == 200
    assert client.get("/api/v1/kallaren", headers=AGENT).status_code == 200
    assert client.get("/api/v1/kallaren", headers={"Authorization": "Bearer agent-test"}).status_code == 200
    assert client.get("/api/v1/kallaren", headers={"Authorization": f"Bearer {CRON}"}).status_code == 401  # cron only for the sync
    h = client.get("/health").json()
    assert h["ok"] and h["databas"] and h["familj_kod"] and h["api_nyckel"] and not h["etikettlasning"]
    assert client.get("/").status_code == 200 and "Vinkällaren" in client.get("/").text
    assert client.get("/manifest.webmanifest").status_code == 200 and client.get("/sw.js").status_code == 200


def test_cellar_flow(client, sortiment):
    # From the assortment; the same wine and vintage again adds to the count
    v = client.post("/api/v1/viner", json={"artikelnr": "1001", "antal": 2, "etikett_bild": "a.jpg"}, headers=APP).json()
    assert v["visningsnamn"] == "Ceretto Barolo Cannubi 2016" and v["antal"] == 2 and v["druvor"] == ["Nebbiolo"] and v["passar_till"] == ["Lamm", "Nöt", "Vilt"]
    assert v["etikett_url"].startswith("https://bilder.test/a.jpg")
    v2 = client.post("/api/v1/viner", json={"artikelnr": "1001", "antal": 1}, headers=APP).json()
    assert v2["id"] == v["id"] and v2["antal"] == 3
    # Another vintage is another wine
    v3 = client.post("/api/v1/viner", json={"artikelnr": "1001", "argang": "2017"}, headers=APP).json()
    assert v3["id"] != v["id"] and v3["argang"] == "2017"
    # By hand (not at Systembolaget)
    hand = client.post("/api/v1/viner", json={"namn": "Gårdens Rosé", "producent": "Vingården i Klagshamn", "typ": "Rosévin", "antal": 4}, headers=APP).json()
    assert hand["visningsnamn"] == "Vingården i Klagshamn Gårdens Rosé" and hand["antal"] == 4 and hand.get("artikelnr") is None
    assert client.post("/api/v1/viner", json={"antal": 1}, headers=APP).status_code == 400
    assert client.post("/api/v1/viner", json={"artikelnr": "9999"}, headers=APP).status_code == 404
    k = client.get("/api/v1/kallaren", headers=APP).json()
    assert k["antal_viner"] == 3 and k["flaskor"] == 8
    assert client.get("/api/v1/kallaren", params={"sok": "nebbiolo 2017"}, headers=APP).json()["antal_viner"] == 1
    assert client.get("/api/v1/kallaren", params={"typ": "Rosé"}, headers=APP).json()["antal_viner"] == 1
    # Drink one with a rating; drink without rating; rate without drinking
    d = client.post(f"/api/v1/viner/{v['id']}/drack", json={"betyg": 5, "kommentar": "Fantastisk", "mat": "lammstek"}, headers=APP).json()
    assert d["antal"] == 2 and d["registrerat"]["flaska_drucken"] and d["registrerat"]["omdome"]["betyg"] == 5 and d["snittbetyg"] == 5
    assert d["omdomen"][0]["kalla"] == "app"
    d = client.post(f"/api/v1/viner/{v['id']}/drack", headers=APP).json()
    assert d["antal"] == 1 and d["registrerat"]["omdome"] is None
    o = client.post(f"/api/v1/viner/{v['id']}/omdome", json={"betyg": 3}, headers=APP).json()
    assert o["betyg"] == 3
    assert client.post(f"/api/v1/viner/{v['id']}/omdome", json={"betyg": 6}, headers=APP).status_code == 400
    assert client.get(f"/api/v1/viner/{v['id']}", headers=APP).json()["snittbetyg"] == 4.0
    # Counts and edits
    assert client.patch(f"/api/v1/viner/{hand['id']}", json={"antal": 0}, headers=APP).json()["antal"] == 0
    assert client.patch(f"/api/v1/viner/{hand['id']}", json={"antal": -1}, headers=APP).status_code == 400
    assert client.get("/api/v1/kallaren", params={"alla": "true"}, headers=APP).json()["antal_viner"] == 3
    assert client.get("/api/v1/kallaren", headers=APP).json()["antal_viner"] == 2
    st = client.get("/api/v1/status", headers=APP).json()
    assert st["viner_i_kallaren"] == 2 and st["flaskor"] == 2 and st["omdomen"] == 2


def test_spoken_wine_names(client, sortiment):
    a = client.post("/api/v1/viner", json={"artikelnr": "1001", "antal": 2}, headers=APP).json()
    client.post("/api/v1/viner", json={"artikelnr": "1001", "argang": "2017", "antal": 1}, headers=APP)
    client.post("/api/v1/viner", json={"artikelnr": "1002", "antal": 1}, headers=APP)
    r = client.post("/api/v1/drack", json={"vin": "chablisen", "betyg": 4}, headers=AGENT)
    assert r.status_code == 404  # "chablisen" is not a word in the name
    r = client.post("/api/v1/drack", json={"vin": "Chablis", "betyg": 4, "kommentar": "frisk"}, headers=AGENT).json()
    assert r["antal"] == 0 and r["omdomen"][0]["kalla"] == "agent"
    r = client.post("/api/v1/drack", json={"vin": "barolo"}, headers=AGENT)
    assert r.status_code == 409 and "2016" in r.json()["detail"] and "2017" in r.json()["detail"]
    r = client.post("/api/v1/omdome", json={"vin": "barolo 2016", "betyg": 5}, headers=AGENT).json()
    assert r["vin"] == "Ceretto Barolo Cannubi 2016" and r["omdome"]["betyg"] == 5
    assert client.get(f"/api/v1/viner/{a['id']}", headers=APP).json()["antal"] == 2  # a review does not open a bottle
    assert client.post("/api/v1/omdome", json={"vin": "", "betyg": 5}, headers=AGENT).status_code == 400


def test_pairing_and_profile(client, sortiment):
    ids = {}
    for nr in ("1001", "1002", "1003", "1004"):
        ids[nr] = client.post("/api/v1/viner", json={"artikelnr": nr, "antal": 2}, headers=APP).json()["id"]
    hand = client.post("/api/v1/viner", json={"namn": "Okänd Bordeaux", "typ": "Rött vin", "antal": 1}, headers=APP).json()
    r = client.get("/api/v1/forslag/mat", params={"mat": "lammstek med rosmarin"}, headers=AGENT).json()
    assert r["kategorier"] == ["Lamm"]
    assert r["forslag"][0]["visningsnamn"].startswith("Ceretto Barolo") and "passar det till lamm" in r["forslag"][0]["skal"][0]
    names = [f["visningsnamn"] for f in r["forslag"]]
    assert names.index("Okänd Bordeaux") < names.index("William Fèvre Chablis 2022")  # red by type beats white for lamb
    r = client.get("/api/v1/forslag/mat", params={"mat": "räkor och hummer"}, headers=AGENT).json()
    assert r["kategorier"] == ["Skaldjur"] and {r["forslag"][0]["visningsnamn"], r["forslag"][1]["visningsnamn"]} == {"William Fèvre Chablis 2022", "Pol Roger Champagne Brut"}
    r = client.get("/api/v1/forslag/mat", params={"mat": "något gott", "kategorier": "Vilt"}, headers=AGENT).json()
    assert r["kategorier"] == ["Vilt"] and r["forslag"][0]["visningsnamn"].startswith("Ceretto")
    r = client.get("/api/v1/forslag/mat", params={"mat": "något gott"}, headers=AGENT).json()
    assert r["kategorier"] is None and "ange kategorier" in r["notering"]
    assert client.get("/api/v1/forslag/mat", headers=AGENT).status_code == 400
    # Profile: before reviews from the cellar, then from ratings
    p = client.get("/api/v1/profil", headers=APP).json()
    assert p["underlag"].startswith("källarens innehåll") and "_vikter" not in p
    client.post(f"/api/v1/viner/{ids['1001']}/omdome", json={"betyg": 5}, headers=APP)
    client.post(f"/api/v1/viner/{ids['1003']}/omdome", json={"betyg": 1}, headers=APP)
    p = client.get("/api/v1/profil", headers=APP).json()
    assert p["underlag"] == "omdömen" and p["gillar"]["druvor"] == ["Nebbiolo"] and set(p["gillar_inte"]["druvor"]) == {"Syrah", "Grenache"}
    assert p["favoriter"][0]["vin"].startswith("Ceretto") and p["minst_omtyckta"][0]["vin"] == "Guigal Côtes du Rhône 2020"
    assert p["smak"]["fyllighet"] == 10 and p["prisniva"]["median"] == 449
    # Buying: Nebbiolo from Piemonte not already owned; order-only wines left out unless asked for
    k = client.get("/api/v1/forslag/kop", params={"typ": "Rött vin"}, headers=AGENT).json()
    assert k["forslag"][0]["artikelnr"] == "1005" and any("Nebbiolo" in s for s in k["forslag"][0]["skal"])
    assert "1006" not in [f["artikelnr"] for f in k["forslag"]]
    k = client.get("/api/v1/forslag/kop", params={"typ": "Rött vin", "bestallningsvaror": "true", "mat": "lamm", "max_pris": 300}, headers=AGENT).json()
    assert {f["artikelnr"] for f in k["forslag"]} == {"1005", "1006"} and k["kategorier"] == ["Lamm"]
    assert client.get("/api/v1/forslag/kop", params={"max_pris": 10}, headers=AGENT).status_code == 404


def test_scan(client, sortiment, monkeypatch):
    from vinkallaren import etikett
    assert client.post("/api/v1/skanna", files={"bild": ("e.jpg", b"jpeg", "image/jpeg")}, headers=APP).status_code == 503

    async def las(data, media):
        assert data == b"jpeg" and media == "image/jpeg"
        return {"ar_vin": True, "producent": "Ceretto", "namn": "Barolo Cannubi", "argang": "2016", "land": "Italien", "region": "Piemonte",
                "druvor": ["Nebbiolo"], "typ": "Rött vin", "sokfras": "Ceretto Barolo Cannubi", "sakerhet": "hög"}
    monkeypatch.setattr(etikett, "las", las)
    r = client.post("/api/v1/skanna", files={"bild": ("e.jpg", b"jpeg", "image/jpeg")}, headers=APP).json()
    assert r["kandidater"][0]["artikelnr"] == "1001" and r["etikett"]["argang"] == "2016" and r["etikett_bild"].endswith(".jpg")
    assert client.post("/api/v1/skanna", files={"bild": ("e.jpg", b"x" * (7 * 1024 * 1024), "image/jpeg")}, headers=APP).status_code == 413


def test_label_reading_handles_refusal_and_json(monkeypatch):
    from vinkallaren import etikett
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class Block:
        def __init__(self, text):
            self.type, self.text = "text", text

    class Resp:
        def __init__(self, stop, text):
            self.stop_reason, self.content = stop, [Block(text)]

    calls = []

    class Messages:
        def __init__(self, resp):
            self.resp = resp

        async def create(self, **kw):
            calls.append(kw)
            return self.resp

    class Client:
        resp = None

        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": Messages(Client.resp)})()

    monkeypatch.setattr(etikett.anthropic, "AsyncAnthropic", Client)
    Client.resp = Resp("end_turn", json.dumps({"ar_vin": True, "namn": "X"}))
    assert asyncio.run(etikett.las(b"img", "image/jpeg")) == {"ar_vin": True, "namn": "X"}
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
    Client.resp = Resp("refusal", "")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(etikett.las(b"img", "image/jpeg"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        asyncio.run(etikett.las(b"img", "application/pdf"))
    assert e.value.status_code == 415


MIRROR = [
    {"productNumber": "1001", "productNameBold": "Barolo", "productNameThin": "Cannubi", "producerName": "Ceretto", "categoryLevel1": "Vin",
     "categoryLevel2": "Rött vin", "categoryLevel3": "Fruktigt & Smakrikt", "country": "Italien", "originLevel1": "Piemonte", "grapes": ["Nebbiolo"],
     "vintage": "2016", "price": 449, "volume": 750, "alcoholPercentage": 14, "tasteSymbols": ["Lamm", "Nöt"], "tasteClockBody": 10,
     "tasteClockRoughness": 0, "tasteClocks": [{"key": "TasteClockRoughness", "value": 9}], "assortmentText": "Fast sortiment",
     "images": [{"imageUrl": "https://product-cdn.systembolaget.se/productimages/1/1"}], "productLaunchDate": "2020-01-01T00:00:00"},
    {"productNumber": "2001", "productNameBold": "Pilsner", "categoryLevel1": "Öl", "categoryLevel2": "Ljus lager"},
    {"productNumber": "3001", "productNameBold": "Vinlåda", "categoryLevel1": "Vin", "categoryLevel2": "Vinlåda"},
    {"productNameBold": "Utan nummer", "categoryLevel1": "Vin", "categoryLevel2": "Vitt vin"},
]


@respx.mock
def test_catalogue_sync(client, fake, monkeypatch):
    from vinkallaren import config
    monkeypatch.setattr(config, "KATALOG_KALLOR", ["https://spegel1.test/products", "https://spegel2.test/assortment.json"])
    respx.get("https://spegel1.test/products").respond(503)
    respx.get("https://spegel2.test/assortment.json").respond(json={"products": MIRROR})
    assert client.post("/api/v1/katalog/synk").status_code == 401
    r = client.post("/api/v1/katalog/synk", headers={"Authorization": f"Bearer {CRON}"}).json()
    assert r["kalla"] == "spegel2.test" and r["viner"] == 1 and r["poster"] == 4 and "spegel1.test" in r["fel"][0]
    p = fake.t["produkter"][0]
    assert p["artikelnr"] == "1001" and p["namn2"] == "Cannubi" and p["fyllighet"] == 10 and p["stravhet"] == 9 and p["druvor"] == ["Nebbiolo"]
    assert p["bild_url"].endswith("_400.png") and p["lanserad"] == "2020-01-01" and p["passar_till"] == ["Lamm", "Nöt"]
    assert fake.t["synk"][0]["antal"] == 1
    assert client.get("/api/v1/status", headers=APP).json()["sortiment"]["antal"] == 1
    # A top-level list works too; a source with no wines falls through to an error
    respx.get("https://spegel1.test/products").respond(json=MIRROR[1:2])
    respx.get("https://spegel2.test/assortment.json").respond(json=MIRROR)
    r = client.get("/api/v1/katalog/synk", headers=APP).json()
    assert r["kalla"] == "spegel2.test" and "inga viner" in r["fel"][0]
    respx.get("https://spegel2.test/assortment.json").respond(json=[])
    assert client.post("/api/v1/katalog/synk", headers=APP).status_code == 502
    assert client.get("/api/v1/katalog/sok", params={"q": "ceretto barolo"}, headers=APP).json()["traffar"][0]["artikelnr"] == "1001"
    assert client.get("/api/v1/katalog/1001", headers=APP).json()["producent"] == "Ceretto"
    assert client.get("/api/v1/katalog/9", headers=APP).status_code == 404


def test_pairing_from_serving_advice():
    from vinkallaren.katalog import kategorier_ur_text, normalisera
    assert kategorier_ur_text("Serveras vid 16-18°C till smakrika vegetariska rätter eller till rätter av fläsk- eller lammkött, exempelvis pizza.") == ["Lamm", "Fläsk", "Grönsaker"]
    assert kategorier_ur_text("Serveras vid cirka 16°C till vegetariskt eller till rätter av ljust kött, gärna kallskuret och småplock eller till en buffé.") == ["Fläsk", "Fågel", "Grönsaker", "Buffémat"]
    assert kategorier_ur_text("Serveras vid 8-10°C som aperitif eller till fisk och skaldjur.") == ["Fisk", "Skaldjur", "Aperitif"]
    assert kategorier_ur_text("Serveras till ostron och ost.") == ["Skaldjur", "Ost"]
    assert kategorier_ur_text(None) == [] and kategorier_ur_text("Fruktig smak") == []
    rec = {"productNumber": "1", "productNameBold": "X", "categoryLevel1": "Vin", "categoryLevel2": "Rött vin", "usage": "Serveras till rätter av lammkött."}
    assert normalisera(rec)["passar_till"] == ["Lamm"]
    assert normalisera({**rec, "tasteSymbols": ["Nöt"]})["passar_till"] == ["Nöt"]
