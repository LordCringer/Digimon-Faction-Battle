import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DIGILAB_API_KEY = os.getenv("DIGILAB_API_KEY")

# GUILD_ID is optional. Handle it being unset, blank, or whitespace — all of
# these should mean "no guild ID configured", not crash on int("").
_guild_id_raw = (os.getenv("GUILD_ID") or "").strip()
GUILD_ID = int(_guild_id_raw) if _guild_id_raw else None

DB_PATH = os.getenv("DB_PATH", "faction_bot.db")
_poll_raw = (os.getenv("POLL_INTERVAL_MINUTES") or "").strip()
POLL_INTERVAL_MINUTES = int(_poll_raw) if _poll_raw else 15

DIGILAB_BASE_URL = "https://api.digilab.cards"

# Factions created automatically on first startup (admins still need to set
# an icon for each with /factionadmin set-icon before posting the sign-up
# message). Renaming/deleting factions is still possible via /faction
# create|delete for anything added later.
DEFAULT_FACTIONS = ["Shambala", "Liberator", "Iliad", "Glowing Dawn"]

# Fallback lookback window for auto-sync when no /factionadmin
# set-season-start date has been configured. Once a season start is set,
# that takes over completely and this is ignored.
DEFAULT_LOOKBACK_DAYS = 60

# ---- Points scheme -----------------------------------------------------
# Points awarded per result, based on placement. Two tables: tournaments
# with fewer than SMALL_TOURNAMENT_THRESHOLD players use the reduced scale.
SMALL_TOURNAMENT_THRESHOLD = 10  # player_count below this uses the small-event table

PLACEMENT_POINTS_STANDARD = {
    1: 10,
    2: 7,
    3: 6,
    4: 5,
    5: 4,
    6: 3,
    7: 2,
    8: 1,
}

PLACEMENT_POINTS_SMALL = {
    1: 5,
    2: 3,
    3: 2,
    4: 1,
}

# Only in-person store locals are tracked for faction points — regionals,
# majors, and online events are excluded entirely.
TRACKED_EVENT_TYPES = ["locals"]


def points_for_result(placement: int, event_type: str, player_count: int = None) -> float:
    small_event = player_count is not None and player_count < SMALL_TOURNAMENT_THRESHOLD
    table = PLACEMENT_POINTS_SMALL if small_event else PLACEMENT_POINTS_STANDARD
    return table.get(placement, 0)  # placements outside the table score 0
