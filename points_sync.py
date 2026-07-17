import logging
from datetime import date, timedelta

import discord

import config
from db import DB
from digilab import DigiLabClient, DigiLabError

log = logging.getLogger("points_sync")


async def sync_points(db: DB, client: DigiLabClient, bot: discord.Client) -> list[dict]:
    """
    Pull recent decklist results for the configured scene, award points to
    any registered+factioned player we haven't already scored, and return
    a list of award dicts for announcement. Idempotent: safe to call as
    often as you like, results are keyed by DigiLab's result_id.
    """
    scene = await db.get_setting("scene_slug")
    if not scene:
        log.info("No scene configured yet, skipping sync")
        return []

    # Look back 60 days by default so a bot restart / late registration
    # still picks up recent results, without paging through full history.
    date_from = (date.today() - timedelta(days=60)).isoformat()
    log.info("Sync starting: scene=%r event_types=%r date_from=%s", scene, config.TRACKED_EVENT_TYPES, date_from)

    awards = []
    total_results_seen = 0
    page = 1
    while True:
        try:
            resp = await client.decklists(
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
            log.info("Page %d: no data returned, stopping", page)
            break

        page_results = resp["data"]
        total_results_seen += len(page_results)
        log.info("Page %d: %d result(s) returned by DigiLab", page, len(page_results))

        for result in page_results:
            result_id = result["result_id"]
            if await db.already_awarded(result_id):
                log.info("result_id=%s (%s) already awarded, skipping", result_id, result.get("player_name"))
                continue

            player_slug = result.get("player_slug")
            if not player_slug:
                continue

            discord_id = await db.get_discord_id_for_slug(player_slug)
            if not discord_id:
                log.info("result_id=%s player_slug=%s has no linked Discord account, skipping",
                         result_id, player_slug)
                continue  # this player hasn't linked their DigiLab account

            faction_name = await db.get_member_faction(discord_id)
            if not faction_name:
                log.info("result_id=%s discord_id=%s is linked but not in a faction, skipping",
                         result_id, discord_id)
                continue  # registered but not in a faction

            points = config.points_for_result(
                result["placement"], result["event_type"], result.get("player_count")
            )
            await db.award_points(
                result_id=result_id,
                discord_id=discord_id,
                faction_name=faction_name,
                points=points,
                placement=result["placement"],
                event_date=result["event_date"],
                event_type=result["event_type"],
                store_name=result.get("store_name"),
                reason="tournament result",
            )
            awards.append({
                "discord_id": discord_id,
                "faction_name": faction_name,
                "points": points,
                "placement": result["placement"],
                "player_name": result.get("player_name"),
                "store_name": result.get("store_name"),
                "event_date": result["event_date"],
                "event_type": result["event_type"],
            })

        pagination = resp.get("pagination", {})
        if page >= pagination.get("total_pages", 1):
            break
        page += 1

    log.info("Sync finished: %d result(s) seen from DigiLab, %d award(s) given", total_results_seen, len(awards))

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
