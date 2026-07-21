import logging
from datetime import date, timedelta

import discord

import config
from db import DB
from digilab import DigiLabClient, DigiLabError

log = logging.getLogger("points_sync")


def _synthetic_result_id(tournament_id: int, placement: int) -> int:
    """
    /api/tournament/{id} standings don't carry a unique result_id like
    /api/decklists did, so we build one deterministically. tournament_ids
    are in the low thousands and placements are small two/three-digit
    numbers at most, so this stays far below real decklist result_ids
    (tens of thousands) — no collision risk between the two id spaces.
    """
    return tournament_id * 1000 + placement


async def sync_points(db: DB, client: DigiLabClient, bot: discord.Client) -> list[dict]:
    """
    Discover tournaments in the configured scene via /api/tournaments,
    then pull each one's full standings via /api/tournament/{id} — which
    DigiLab added 2026-07-20 and does NOT require a decklist submission,
    unlike the old /api/decklists-based approach this used to use.
    Idempotent: tournaments already fully processed are tracked and
    skipped outright; awards within a tournament are keyed by a
    deterministic synthetic id so re-running never double-counts.
    """
    scene = await db.get_setting("scene_slug")
    if not scene:
        log.info("No scene configured yet, skipping sync")
        return []

    # A fixed season start date (set via /factionadmin set-season-start)
    # takes priority over the rolling lookback default — once set, it's a
    # firm cutoff and never drifts, so "start of the faction battle" stays
    # "start of the faction battle" no matter how long the bot's been running.
    season_start = await db.get_setting("season_start_date")
    if season_start:
        date_from = season_start
    else:
        date_from = (date.today() - timedelta(days=config.DEFAULT_LOOKBACK_DAYS)).isoformat()
    log.info("Sync starting: scene=%r event_types=%r date_from=%s (season_start=%s)",
             scene, config.TRACKED_EVENT_TYPES, date_from, season_start or "not set, using rolling default")

    awards = []
    total_tournaments_seen = 0
    tournaments_processed = 0
    page = 1
    while True:
        try:
            resp = await client.tournaments(
                scene=scene,
                event_type=config.TRACKED_EVENT_TYPES,
                date_from=date_from,
                page=page,
                per_page=100,
                sort="date",
                sort_dir="asc",
            )
        except DigiLabError as e:
            log.warning("DigiLab fetch failed: %s", e)
            break

        if not resp or not resp.get("data"):
            log.info("Page %d: no tournaments returned, stopping", page)
            break

        page_tournaments = resp["data"]
        total_tournaments_seen += len(page_tournaments)
        log.info("Page %d: %d tournament(s) returned by DigiLab", page, len(page_tournaments))

        for t in page_tournaments:
            tournament_id = t["tournament_id"]

            if await db.tournament_already_synced(tournament_id):
                continue

            try:
                detail = await client.tournament_detail(tournament_id)
            except DigiLabError as e:
                log.warning("tournament_id=%s detail fetch failed: %s", tournament_id, e)
                continue

            if not detail or not detail.get("tournament"):
                log.info("tournament_id=%s: no detail returned, skipping", tournament_id)
                continue

            info = detail["tournament"]
            player_count = info.get("player_count")
            event_type = info.get("event_type")
            event_date = info.get("date")
            store = info.get("store") or {}
            store_name = store.get("name")

            for standing in detail.get("standings", []):
                player = standing.get("player") or {}
                slug = player.get("slug")
                placement = standing.get("placement")
                if not slug or placement is None:
                    continue  # anonymous player or malformed row — skip

                synthetic_id = _synthetic_result_id(tournament_id, placement)
                if await db.already_awarded(synthetic_id):
                    continue

                discord_id = await db.get_discord_id_for_slug(slug)
                if not discord_id:
                    log.info("tournament_id=%s placement=%s slug=%s has no linked Discord account, skipping",
                             tournament_id, placement, slug)
                    continue

                faction_name = await db.get_member_faction(discord_id)
                if not faction_name:
                    log.info("tournament_id=%s placement=%s discord_id=%s is linked but not in a faction, skipping",
                             tournament_id, placement, discord_id)
                    continue

                points = config.points_for_result(placement, event_type, player_count)
                await db.award_points(
                    result_id=synthetic_id,
                    discord_id=discord_id,
                    faction_name=faction_name,
                    points=points,
                    placement=placement,
                    event_date=event_date,
                    event_type=event_type,
                    store_name=store_name,
                    reason="tournament result",
                )
                awards.append({
                    "discord_id": discord_id,
                    "faction_name": faction_name,
                    "points": points,
                    "placement": placement,
                    "player_name": player.get("name"),
                    "store_name": store_name,
                    "event_date": event_date,
                    "event_type": event_type,
                })

            await db.mark_tournament_synced(tournament_id)
            tournaments_processed += 1

        pagination = resp.get("pagination", {})
        if page >= pagination.get("total_pages", 1):
            break
        page += 1

    log.info("Sync finished: %d tournament(s) seen, %d newly processed, %d award(s) given",
              total_tournaments_seen, tournaments_processed, len(awards))

    if awards:
        await announce(bot, db, awards)
    return awards


async def announce(bot: discord.Client, db: DB, awards: list[dict]):
    channel_id = await db.get_setting("announce_channel_id")
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return

    lines = []
    for a in awards:
        member = f"<@{a['discord_id']}>"
        lines.append(
            f"**{a['player_name']}** ({member}) — placed {a['placement']} at "
            f"{a['store_name']} ({a['event_type']}, {a['event_date']}) "
            f"→ +{a['points']:g} pts for **{a['faction_name']}**"
        )

    embed = discord.Embed(
        title="🏆 New tournament results",
        description="\n".join(lines[:20]),
        color=discord.Color.blurple(),
    )
    if len(lines) > 20:
        embed.set_footer(text=f"...and {len(lines) - 20} more")
    await channel.send(embed=embed)
