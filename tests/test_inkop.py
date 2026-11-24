from conftest import AGENT, APP


def test_shopping_list(client, sortiment):
    assert client.get("/api/v1/inkop").status_code == 401
    # From the assortment, by phrase (clear hit), by hand; the same wine again adds to the count
    a = client.post("/api/v1/inkop", json={"artikelnr": "1005", "antal": 2, "anteckning": "till lammet"}, headers=APP).json()
    assert a["visningsnamn"] == "Produttori Langhe Nebbiolo 2020" and a["antal"] == 2 and a["pris"] == 239 and a["kalla"] == "app"
    b = client.post("/api/v1/inkop", json={"vin": "Muga Rioja Reserva"}, headers=AGENT).json()
    assert b["artikelnr"] == "1006" and b["matchning"] == "Systembolaget" and b["kalla"] == "agent"
    c = client.post("/api/v1/inkop", json={"vin": "Grannens äppelvin"}, headers=AGENT).json()
    assert c.get("artikelnr") is None and c["namn"] == "Grannens äppelvin" and c["matchning"].startswith("eget namn")
    again = client.post("/api/v1/inkop", json={"artikelnr": "1005"}, headers=APP).json()
    assert again["id"] == a["id"] and again["antal"] == 3 and again["fanns_redan"]
    for bad in ({}, {"antal": 1}, {"artikelnr": "9999"}, {"namn": "X", "antal": 0}):
        assert client.post("/api/v1/inkop", json=bad, headers=APP).status_code in (400, 404), bad
    lst = client.get("/api/v1/inkop", headers=APP).json()
    assert lst["antal_viner"] == 3 and lst["flaskor"] == 5 and lst["uppskattad_summa_kr"] == 3 * 239 + 219 and lst["utan_pris"] == 1

    # Edit and remove
    assert client.patch(f"/api/v1/inkop/{b['id']}", json={"antal": 6}, headers=APP).json()["antal"] == 6
    assert client.patch(f"/api/v1/inkop/{b['id']}", json={"antal": 0}, headers=APP).status_code == 400
    assert client.post("/api/v1/inkop/ta-bort", json={"vin": "äppelvin"}, headers=AGENT).json() == {"borttagen": "Grannens äppelvin"}
    assert client.post("/api/v1/inkop/ta-bort", json={"vin": "champagne"}, headers=AGENT).status_code == 404

    # Bought: the bottles go into the cellar; a second tick is refused; without the cellar only the list changes
    k = client.post("/api/v1/inkop/kopt", json={"vin": "nebbiolo"}, headers=AGENT).json()
    assert k["kopt"] is True and k["i_kallaren"] == {"vin": "Produttori Langhe Nebbiolo 2020", "antal_nu": 3} and k["vin_id"]
    assert client.post(f"/api/v1/inkop/{a['id']}/kopt", headers=APP).status_code == 409
    k = client.post(f"/api/v1/inkop/{b['id']}/kopt", json={"till_kallaren": False}, headers=APP).json()
    assert k["kopt"] is True and "i_kallaren" not in k
    cellar = client.get("/api/v1/kallaren", headers=APP).json()
    assert cellar["flaskor"] == 3 and cellar["viner"][0]["artikelnr"] == "1005"
    lst = client.get("/api/v1/inkop", headers=APP).json()
    assert lst["antal_viner"] == 0 and lst["uppskattad_summa_kr"] == 0
    assert len(client.get("/api/v1/inkop", params={"alla": "true"}, headers=APP).json()["viner"]) == 2


def test_shopping_list_ambiguous_and_manual_to_cellar(client, sortiment):
    client.post("/api/v1/inkop", json={"artikelnr": "1001"}, headers=APP)
    client.post("/api/v1/inkop", json={"artikelnr": "1005"}, headers=APP)
    r = client.post("/api/v1/inkop/kopt", json={"vin": "piemonte"}, headers=AGENT)
    assert r.status_code == 409 and "Barolo" in r.json()["detail"] and "Langhe" in r.json()["detail"]
    m = client.post("/api/v1/inkop", json={"namn": "Gårdens Rosé", "producent": "Vingården", "typ": "Rosévin", "antal": 2}, headers=APP).json()
    k = client.post(f"/api/v1/inkop/{m['id']}/kopt", json={"antal": 3, "argang": "2024"}, headers=APP).json()
    assert k["i_kallaren"] == {"vin": "Vingården Gårdens Rosé 2024", "antal_nu": 3}
