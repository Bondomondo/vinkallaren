# Vinkällaren

Familjens vinkyl som app: skanna etiketten på en flaska, håll koll på hur många som finns kvar, ge
omdömen om det ni druckit, och få förslag på vin till maten och tips på vad ni ska köpa. Sommelieren
Jean-Pierre (ElevenLabs) använder samma data via agent-tools.

## Delar

| Del | Var | Vad |
|---|---|---|
| Appen | `static/index.html` | Installerbar webbapp (lägg till på hemskärmen). Källaren, Lägg till (skanna/sök), Inköp, Tips |
| API | `main.py`, `vinkallaren/` | FastAPI på Vercel, `/api/v1/...` |
| Databas | Supabase-projektet `vinkallaren` | `produkter` (Systembolagets vinsortiment), `viner` (våra viner med antal), `omdomen`, `inkopslista`, `synk`; bucket `etiketter` |
| Etikettläsning | `vinkallaren/etikett.py` | Claude läser fotot (producent, namn, årgång, druvor ...) och vinet matchas mot sortimentet |
| Förslag | `vinkallaren/forslag.py`, `profil.py` | Systembolagets "passar till" + familjens smakprofil ur betygen |

Systembolaget publicerar inte längre produktdata i sitt öppna API. Sortimentet hämtas därför
nattligen (Vercel cron 03:17) från en communityspegling (`KATALOG_KALLOR`, den första som svarar).

## API

Alla anrop kräver `X-Familj-Kod` (appen) eller `X-Api-Key` (agent-tools). Synken tar också Vercels `CRON_SECRET`.

| Anrop | Vad |
|---|---|
| `GET /api/v1/status` | Antal viner, flaskor och omdömen, senaste synk |
| `GET /api/v1/kallaren?typ=&sok=&alla=&bilder=` | Vinerna i källaren med betyg |
| `GET /api/v1/viner/{id}` | Ett vin med omdömen |
| `POST /api/v1/viner` | Lägg in flaskor: `{"artikelnr", "antal", "argang"}` eller `{"namn", "producent", "typ", ...}` |
| `PATCH /api/v1/viner/{id}` | Ändra antal, plats, anteckning |
| `POST /api/v1/viner/{id}/drack` | En flaska mindre, med valfritt betyg |
| `POST /api/v1/viner/{id}/omdome` | Omdöme 1–5 |
| `POST /api/v1/drack`, `POST /api/v1/omdome` | Samma sak med vinet i ord (`{"vin": "barolon från 2016", ...}`), för röstagenten |
| `POST /api/v1/skanna` | Foto av etiketten (multipart `bild`) → avläsning och kandidater i sortimentet |
| `GET /api/v1/inkop?alla=` | Inköpslistan med antal, pris och uppskattad summa |
| `POST /api/v1/inkop` | Lägg på listan: `{"artikelnr", "antal"}`, `{"namn", ...}` eller `{"vin": "fras"}` (matchas mot sortimentet). Samma vin igen ökar antalet |
| `PATCH /api/v1/inkop/{id}`, `DELETE /api/v1/inkop/{id}` | Ändra antal/anteckning, ta bort |
| `POST /api/v1/inkop/{id}/kopt` | Bocka av som köpt; flaskorna läggs i vinkylen om inte `{"till_kallaren": false}` |
| `POST /api/v1/inkop/kopt`, `POST /api/v1/inkop/ta-bort` | Samma sak med vinet i ord (`{"vin": "ripasson"}`), för röstagenten |
| `GET /api/v1/katalog/sok?q=&argang=` | Sök i sortimentet |
| `GET /api/v1/forslag/mat?mat=&kategorier=` | Vin ur källaren till en rätt, med skäl |
| `GET /api/v1/forslag/kop?mat=&typ=&max_pris=&bestallningsvaror=` | Köptips ur sortimentet efter vår smak |
| `GET /api/v1/profil` | Familjens smakprofil |
| `POST /api/v1/katalog/synk` | Hämta sortimentet på nytt |

## Inställningar (Vercel)

| Variabel | Vad |
|---|---|
| `SUPABASE_URL` | `https://angwlofbrzyliwfysgsw.supabase.co` |
| `SUPABASE_SECRET_KEY` | Projektets hemliga nyckel (Supabase → Project Settings → API Keys) |
| `ANTHROPIC_API_KEY` | För etikettläsningen |
| `FAMILJ_KOD` | Koden familjen skriver in i appen en gång |
| `VINKALLAREN_API_KEY` | Nyckel för agent-tools (samma värde som `VINKALLAREN_API_KEY` i agent-tools) |
| `CRON_SECRET` | Vercel skickar den till den nattliga synken |
| `KATALOG_KALLOR` | Valfritt: kommaseparerade URL:er till sortimentsspeglingar |

## Utveckling

```
pip install -r requirements-dev.txt
python -m pytest -q
```
