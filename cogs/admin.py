import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
from digilab import DigiLabError
from points_sync import sync_points


def parse_tournament_id(raw: str) -> int | None:
    """Accepts either a bare numeric ID or a full digilab.cards/tournament/... URL."""
    match = re.search(r"tournament/(\d+)", raw)
    id_str = match.group(1) if match else raw.strip()
    return int(id_str) if id_str.isdigit() else None


class TournamentStandingsModal(discord.ui.Modal, title="Log Tournament Standings"):
    """
    Bulk placement entry — paste standings straight from the tournament
    page (or from memory), one player per line, no DigiLab decklist
    required. Accepts lines like:
        1, Bobby Lau
        2. Jefe
        3 Dan Ly
    Placement is whatever number appears first on the line; everything
    after it (past the first separator) is treated as the player name.
    Names are matched against already-/register'd DigiLab player names.
    """

    standings = discord.ui.TextInput(
        label="Standings — one 'placement, name' per line",
        style=discord.TextStyle.paragraph,
        placeholder="1, Bobby Lau\n2, Jefe\n3, Dan Ly\n4, Matt G\n5, Matt K",
        max_length=1800,
    )

    LINE_RE = re.compile(r"^\s*(\d+)\s*[.,)\-:]?\s*(.+?)\s*$")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        db = interaction.client.db
        lines = [l for l in self.standings.value.splitlines() if l.strip()]
        player_count = len(lines)

        awarded, unregistered, no_faction, unparsed = [], [], [], []

        for line in lines:
            m = self.LINE_RE.match(line)
            if not m:
                unparsed.append(line)
                continue
            placement = int(m.group(1))
            name = m.group(2).strip()

            reg = await db.get_registration_by_player_name(name)
            if not reg:
                unregistered.append(f"{placement}. {name}")
                continue

            faction = await db.get_member_faction(reg["discord_id"])
            if not faction:
                no_faction.append(f"{placement}. {name}")
                continue

            points = config.points_for_result(placement, "locals", player_count)
            await db.manual_award(
                reg["discord_id"], faction, points,
                reason=f"Tournament log: {placement} of {player_count}",
            )
            awarded.append(f"{placement}. {name} → +{points:g} pts ({faction})")

        lines_out = [f"**Logged {len(awarded)} of {player_count} player(s):**"]
        lines_out.extend(awarded) if awarded else lines_out.append("*(none)*")
        if unregistered:
            lines_out.append(f"\n**Not registered ({len(unregistered)}):** " + ", ".join(unregistered))
        if no_faction:
            lines_out.append(f"\n**Registered but no faction ({len(no_faction)}):** " + ", ".join(no_faction))
        if unparsed:
            lines_out.append(f"\n**Couldn't parse ({len(unparsed)}):** " + ", ".join(unparsed))

        embed = discord.Embed(
            title="Tournament standings logged",
            description="\n".join(lines_out)[:4000],
            color=discord.Color.green() if awarded else discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    admin_group = app_commands.Group(
        name="factionadmin",
        description="Faction bot admin commands",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @admin_group.command(name="set-season-start", description="Only count tournaments on/after this date in auto-sync (e.g. faction battle season start)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(date="Start date in YYYY-MM-DD format, e.g. 2026-07-20")
    async def set_season_start(self, interaction: discord.Interaction, date: str):
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                f"`{date}` isn't a valid date — use YYYY-MM-DD format, e.g. `2026-07-20`.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.db.set_setting("season_start_date", date)
        await interaction.followup.send(
            f"Auto-sync will now only count tournaments on or after **{date}**. "
            f"Anything earlier is excluded even if it's within DigiLab's data."
        )

    @admin_group.command(name="season-info", description="Show the current season start date, if any")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def season_info(self, interaction: discord.Interaction):
        await interaction.response.defer()
        start = await self.db.get_setting("season_start_date")
        if start:
            await interaction.followup.send(f"Season start is set to **{start}**. Auto-sync ignores anything before this date.")
        else:
            await interaction.followup.send(
                f"No season start date set — auto-sync defaults to the last "
                f"{config.DEFAULT_LOOKBACK_DAYS} days. Use `/factionadmin set-season-start` to set a fixed cutoff."
            )

    @admin_group.command(name="set-scene", description="Set the DigiLab scene slug tracked for points (e.g. austin-tx)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_scene(self, interaction: discord.Interaction, scene_slug: str):
        await interaction.response.defer()
        scene = await self.bot.digilab.scene(scene_slug)
        if not scene:
            await interaction.followup.send(
                f"DigiLab doesn't recognize scene `{scene_slug}`. Check `/api/scenes` or the site's scene URL slug.",
                ephemeral=True,
            )
            return
        await self.db.set_setting("scene_slug", scene_slug)
        label = scene.get("scope", {}).get("label", scene_slug)
        await interaction.followup.send(f"Tracking tournaments for **{label}** (`{scene_slug}`).")

    @admin_group.command(name="set-channel", description="Set the channel for new-results announcements")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        await self.db.set_setting("announce_channel_id", str(channel.id))
        await interaction.followup.send(f"Results will be announced in {channel.mention}.")

    @admin_group.command(name="sync", description="Manually check DigiLab for new results right now")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer()
        awards = await sync_points(self.db, self.bot.digilab, self.bot)
        if not awards:
            await interaction.followup.send("No new results to award (or scene isn't configured yet).")
        else:
            await interaction.followup.send(f"Awarded points for {len(awards)} new result(s). See announcement above.")

    async def faction_autocomplete(self, interaction: discord.Interaction, current: str):
        factions = await self.db.list_factions()
        return [
            app_commands.Choice(name=f["name"], value=f["name"])
            for f in factions if current.lower() in f["name"].lower()
        ][:25]

    @admin_group.command(name="set-icon", description="Set the emoji used to join a faction via reaction")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=faction_autocomplete)
    @app_commands.describe(name="Faction name", emoji="An emoji (standard or a custom emoji from this server)")
    async def set_icon(self, interaction: discord.Interaction, name: str, emoji: str):
        try:
            parsed = discord.PartialEmoji.from_str(emoji.strip())
        except Exception:
            await interaction.response.send_message(f"`{emoji}` doesn't look like a valid emoji.", ephemeral=True)
            return

        await interaction.response.defer()
        existing = await self.db.get_faction_by_emoji(str(parsed))
        if existing and existing["name"] != name:
            await interaction.followup.send(
                f"{parsed} is already used by **{existing['name']}**. Pick a different emoji.", ephemeral=True
            )
            return

        ok = await self.db.set_faction_emoji(name, str(parsed))
        if not ok:
            await interaction.followup.send(f"No faction named **{name}**.", ephemeral=True)
            return
        await interaction.followup.send(f"**{name}** icon set to {parsed}.")

    @admin_group.command(name="post-signup", description="Post the faction sign-up message (react to join)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Where to post it (defaults to this channel)")
    async def post_signup(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        factions = await self.db.list_factions()
        if not factions:
            await interaction.followup.send("No factions exist yet — use `/faction create` first.", ephemeral=True)
            return

        missing = [f["name"] for f in factions if not f["emoji"]]
        if missing:
            await interaction.followup.send(
                "These factions don't have an icon set yet — use `/factionadmin set-icon` first: "
                + ", ".join(f"**{n}**" for n in missing),
                ephemeral=True,
            )
            return

        lines = [f"{f['emoji']}  **{f['name']}**" for f in factions]
        embed = discord.Embed(
            title="Choose your faction",
            description="React below with the emoji matching the faction you want to join.\n\n" + "\n".join(lines),
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Reacting with a different faction's emoji switches you. Use /faction leave to opt out entirely.")

        await interaction.followup.send(f"Posting sign-up message in {target.mention}...", ephemeral=True)
        message = await target.send(embed=embed)
        for f in factions:
            try:
                await message.add_reaction(discord.PartialEmoji.from_str(f["emoji"]))
            except discord.HTTPException:
                pass

        await self.db.set_setting("signup_message_id", str(message.id))
        await self.db.set_setting("signup_channel_id", str(target.id))

    @admin_group.command(name="exclude-tournament", description="Block a tournament ID from ever awarding points")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(tournament="Tournament ID or full URL", reason="Optional note for your own reference")
    async def exclude_tournament(self, interaction: discord.Interaction, tournament: str, reason: str = ""):
        await interaction.response.defer()
        tournament_id = parse_tournament_id(tournament)
        if tournament_id is None:
            await interaction.followup.send(
                "Couldn't figure out a tournament ID from that — pass the numeric ID or full URL.",
                ephemeral=True,
            )
            return
        await self.db.exclude_tournament(tournament_id, interaction.user.id, reason)
        msg = f"🚫 Tournament `{tournament_id}` is now excluded — it will never award points via auto-sync or `log-tournament-id`."
        if reason:
            msg += f"\nReason: {reason}"
        await interaction.followup.send(msg)

    @admin_group.command(name="include-tournament", description="Remove a tournament ID from the exclusion list")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(tournament="Tournament ID or full URL")
    async def include_tournament(self, interaction: discord.Interaction, tournament: str):
        await interaction.response.defer()
        tournament_id = parse_tournament_id(tournament)
        if tournament_id is None:
            await interaction.followup.send(
                "Couldn't figure out a tournament ID from that — pass the numeric ID or full URL.",
                ephemeral=True,
            )
            return
        was_excluded = await self.db.include_tournament(tournament_id)
        if was_excluded:
            await interaction.followup.send(
                f"Tournament `{tournament_id}` removed from the exclusion list — it's eligible for "
                f"points again and will be picked up on the next auto-sync."
            )
        else:
            await interaction.followup.send(f"Tournament `{tournament_id}` wasn't on the exclusion list.", ephemeral=True)

    @admin_group.command(name="list-excluded", description="List all tournament IDs excluded from awarding points")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_excluded(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = await self.db.list_excluded_tournaments()
        if not rows:
            await interaction.followup.send("No tournaments are currently excluded.", ephemeral=True)
            return
        lines = [
            f"`{r['tournament_id']}`" + (f" — {r['reason']}" if r["reason"] else "") + f" (excluded {r['excluded_at']})"
            for r in rows
        ]
        embed = discord.Embed(title="Excluded tournaments", description="\n".join(lines)[:4000], color=discord.Color.dark_red())
        await interaction.followup.send(embed=embed)

    @admin_group.command(name="clear-leaderboard", description="⚠️ Wipe ALL points for ALL members — cannot be undone")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(confirm="Type CONFIRM (all caps, exact) to proceed")
    async def clear_leaderboard(self, interaction: discord.Interaction, confirm: str):
        if confirm != "CONFIRM":
            await interaction.response.send_message(
                "Not cleared. This wipes **every member's points, in every faction, permanently** — "
                "there's no undo. If you're sure, run this again with `confirm:CONFIRM` (exact, all caps).",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        count = await self.db.clear_all_points()
        await interaction.followup.send(
            f"🗑️ Cleared **{count}** point log entr{'y' if count == 1 else 'ies'} — every member's total is now 0.\n"
            f"-# Already-synced tournaments won't be re-awarded automatically — use `/factionadmin include-tournament` "
            f"on specific ones if you want auto-sync to reprocess them."
        )

    @admin_group.command(name="log-tournament-id", description="Fetch and log full standings for a tournament ID — official API, no decklist required")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(tournament="Tournament ID or full digilab.cards/tournament/... URL")
    async def log_tournament_id(self, interaction: discord.Interaction, tournament: str):
        await interaction.response.defer()

        tournament_id = parse_tournament_id(tournament)
        if tournament_id is None:
            await interaction.followup.send(
                "Couldn't figure out a tournament ID from that — pass either the numeric ID "
                "(e.g. `6116`) or the full URL (e.g. `https://digilab.cards/tournament/6116`).",
                ephemeral=True,
            )
            return

        if await self.db.is_tournament_excluded(tournament_id):
            await interaction.followup.send(
                f"Tournament `{tournament_id}` is on the exclusion list, so it won't be logged. "
                f"Run `/factionadmin include-tournament tournament:{tournament_id}` first if you "
                f"really want to log it.",
                ephemeral=True,
            )
            return

        try:
            detail = await self.bot.digilab.tournament_detail(tournament_id)
        except DigiLabError as e:
            await interaction.followup.send(f"Couldn't reach DigiLab: {e}", ephemeral=True)
            return

        if not detail or not detail.get("tournament"):
            await interaction.followup.send(f"No tournament found with ID `{tournament_id}`.", ephemeral=True)
            return

        info = detail["tournament"]
        player_count = info.get("player_count")
        event_type = info.get("event_type")
        event_date = info.get("date")

        season_start = await self.db.get_setting("season_start_date")
        before_season = bool(season_start and event_date and event_date < season_start)

        db = self.bot.db
        awarded, unregistered, no_faction, anonymous = [], [], [], []
        for standing in detail.get("standings", []):
            player = standing.get("player") or {}
            slug = player.get("slug")
            name = player.get("name") or "Anonymous"
            placement = standing.get("placement")
            if placement is None:
                continue
            if not slug:
                anonymous.append(f"{placement}. {name}")
                continue

            discord_id = await db.get_discord_id_for_slug(slug)
            if not discord_id:
                unregistered.append(f"{placement}. {name}")
                continue
            faction = await db.get_member_faction(discord_id)
            if not faction:
                no_faction.append(f"{placement}. {name}")
                continue

            points = config.points_for_result(placement, event_type, player_count)
            await db.manual_award(
                discord_id, faction, points,
                reason=f"Tournament {tournament_id}: {placement} of {player_count} (auto-fetched)",
            )
            awarded.append(f"{placement}. {name} → +{points:g} pts ({faction})")

        lines_out = [f"**Logged {len(awarded)} of {len(detail.get('standings', []))} placing player(s)** from tournament {tournament_id}:"]
        lines_out.extend(awarded) if awarded else lines_out.append("*(none)*")
        if unregistered:
            lines_out.append(f"\n**Not registered ({len(unregistered)}):** " + ", ".join(unregistered))
        if no_faction:
            lines_out.append(f"\n**Registered but no faction ({len(no_faction)}):** " + ", ".join(no_faction))
        if anonymous:
            lines_out.append(f"\n**Anonymous, can't match ({len(anonymous)}):** " + ", ".join(anonymous))
        if before_season:
            lines_out.append(
                f"\n⚠️ This event ({event_date}) is **before** your configured season start "
                f"(**{season_start}**) — logged anyway since you asked for it by ID directly, "
                f"but the automatic sync would have skipped it."
            )

        embed = discord.Embed(
            title="Tournament standings logged",
            description="\n".join(lines_out)[:4000],
            color=discord.Color.green() if awarded else discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)

    @admin_group.command(name="log-tournament", description="Log a whole tournament's standings at once — no decklist required")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def log_tournament(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TournamentStandingsModal())

    @admin_group.command(name="log-result", description="Log a tournament placement manually — no DigiLab decklist required")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        user="The player who placed",
        placement="Final placement (1 = first place)",
        player_count="Total players in the tournament (determines the points table used)",
        event_date="Event date, e.g. 2026-07-17 (optional, just for the log)",
    )
    async def log_result(self, interaction: discord.Interaction, user: discord.User, placement: int,
                          player_count: int, event_date: str = None):
        if placement < 1 or player_count < 1 or placement > player_count:
            await interaction.response.send_message(
                "Placement has to be between 1 and the player count.", ephemeral=True
            )
            return

        await interaction.response.defer()
        faction = await self.db.get_member_faction(user.id)
        if not faction:
            await interaction.followup.send(f"{user.mention} isn't in a faction yet.", ephemeral=True)
            return

        points = config.points_for_result(placement, "locals", player_count)
        reason = f"Manual locals log: {placement} of {player_count}"
        if event_date:
            reason += f" on {event_date}"
        await self.db.manual_award(user.id, faction, points, reason)

        if points == 0:
            note = " (placement scored 0 under the current points table)"
        else:
            note = ""
        await interaction.followup.send(
            f"Logged **{user.display_name}** — {placement} of {player_count} → "
            f"**{points:g}** points for **{faction}**{note}"
        )

    @admin_group.command(name="award", description="Manually award points to a user")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def award(self, interaction: discord.Interaction, user: discord.User, points: float, reason: str = ""):
        await interaction.response.defer()
        faction = await self.db.get_member_faction(user.id)
        if not faction:
            await interaction.followup.send(f"{user.mention} isn't in a faction yet.", ephemeral=True)
            return
        await self.db.manual_award(user.id, faction, points, reason or "manual award")
        await interaction.followup.send(
            f"Awarded **{points:g}** points to {user.mention} ({faction})" + (f" — {reason}" if reason else "")
        )

    @set_scene.error
    @set_channel.error
    @set_icon.error
    @post_signup.error
    @set_season_start.error
    @season_info.error
    @sync.error
    @exclude_tournament.error
    @include_tournament.error
    @list_excluded.error
    @clear_leaderboard.error
    @log_tournament_id.error
    @log_tournament.error
    @log_result.error
    @award.error
    async def perms_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission for that.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
