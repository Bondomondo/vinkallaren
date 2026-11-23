"""Read a wine label photo with Claude and find the wine in the assortment."""
import base64
import json

import anthropic
from fastapi import HTTPException

from . import config, katalog

MODEL = "claude-opus-5-5"
TYPER = ["Rött vin", "Vitt vin", "Rosévin", "Mousserande vin", "Starkvin", "Annat"]
SCHEMA = {
    "type": "object",
    "properties": {
        "ar_vin": {"type": "boolean", "description": "Om bilden visar en vinetikett eller vinflaska"},
        "producent": {"type": "string", "description": "Producent/vingård/domaine, tom sträng om okänt"},
        "namn": {"type": "string", "description": "Vinets namn eller cuvée, utan producent; tom sträng om okänt"},
        "argang": {"type": "string", "description": "Årgång med fyra siffror, tom sträng om ingen syns (NV)"},
        "land": {"type": "string", "description": "Land på svenska, tom sträng om okänt"},
        "region": {"type": "string", "description": "Region/appellation, tom sträng om okänt"},
        "druvor": {"type": "array", "items": {"type": "string"}, "description": "Druvor om de står eller följer av appellationen"},
        "typ": {"type": "string", "enum": TYPER},
        "sokfras": {"type": "string", "description": "Bästa söksträng för Systembolagets sortiment: producent och namn"},
        "sakerhet": {"type": "string", "enum": ["hög", "medel", "låg"]},
    },
    "required": ["ar_vin", "producent", "namn", "argang", "land", "region", "druvor", "typ", "sokfras", "sakerhet"],
    "additionalProperties": False,
}
PROMPT = ("Det här är ett foto av en vinflaska eller vinetikett som ska läggas in i en hemmavinkällare. "
          "Läs av etiketten: producent, vinets namn, årgång, land, region och druvor. Hitta inte på något som inte står "
          "eller tydligt följer av etiketten (en appellation som Chablis ger Chardonnay). Skriv land på svenska. "
          "Sökfrasen ska vara det som bäst hittar vinet i Systembolagets sortiment, normalt producent följt av vinets namn.")


async def las(bild: bytes, media_type: str) -> dict:
    if not config.anthropic_configured():
        raise HTTPException(503, "Etikettläsning kräver ANTHROPIC_API_KEY")
    if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise HTTPException(415, "Bilden måste vara JPEG, PNG, WebP eller GIF")
    client = anthropic.AsyncAnthropic(max_retries=2, timeout=60)
    try:
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(bild).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
        )
    except anthropic.RateLimitError:
        raise HTTPException(429, "Etikettläsningen är överbelastad, försök igen om en stund")
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Etikettläsningen misslyckades ({exc.status_code})")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Kunde inte nå etikettläsningen")
    if response.stop_reason == "refusal":
        raise HTTPException(422, "Etiketten kunde inte läsas")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise HTTPException(502, "Etikettläsningen gav inget begripligt svar")


async def kandidater(etikett: dict) -> list[dict]:
    """Assortment matches for a read label: the search phrase first, then producer + name, then name alone."""
    seen: dict[str, dict] = {}
    argang = etikett.get("argang") or None
    for q in (etikett.get("sokfras"), f"{etikett.get('producent', '')} {etikett.get('namn', '')}", etikett.get("namn")):
        if not q or len(q.strip()) < 3:
            continue
        for r in await katalog.sok(q, argang, 6):
            if r["artikelnr"] not in seen or r["likhet"] > seen[r["artikelnr"]]["likhet"]:
                seen[r["artikelnr"]] = r
        if any(r["likhet"] >= 0.8 for r in seen.values()):
            break
    rows = sorted(seen.values(), key=lambda r: (-(r["likhet"] + (0.15 if argang and r.get("argang") == argang else 0)), r.get("pris") or 0))
    return rows[:6]
