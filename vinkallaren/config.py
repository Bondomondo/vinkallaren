"""Settings from the environment (Vercel)."""
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def supabase_url() -> str:
    return env("SUPABASE_URL").rstrip("/")


def supabase_key() -> str:
    """The project's secret (service role) key: the tables have no policies, so only this key reaches them."""
    return env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")


def familj_kod() -> str:
    """Shared code the family types once in the app."""
    return env("FAMILJ_KOD")


def api_key() -> str:
    """Key for agent-tools (and other servers)."""
    return env("VINKALLAREN_API_KEY")


def cron_secret() -> str:
    return env("CRON_SECRET")


def anthropic_configured() -> bool:
    return bool(env("ANTHROPIC_API_KEY"))


# The assortment: Systembolaget no longer publishes product data, so the catalogue comes from community
# mirrors of it. The first that answers is used.
KATALOG_KALLOR = [u for u in env(
    "KATALOG_KALLOR",
    "https://susbolaget.emrik.org/v1/products,"
    "https://raw.githubusercontent.com/AlexGustafsson/systembolaget-api-data/main/data/assortment.json",
).split(",") if u.strip()]
