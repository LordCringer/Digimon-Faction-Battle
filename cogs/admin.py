import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
from digilab import DigiLabError
from points_sync import sync_points


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
        await interaction.response.send_message(embed=embed)


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
        await self.db.set_setting("season_start_date", date)
        await interaction.response.send_message(
            f"Auto-sync will now only count tournaments on or after **{date}**. "
            f"Anything earlier is excluded even if it's within DigiLab's data."
        )

    @admin_group.command(name="season-info", description="Show the current season start date, if any")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def season_info(self, interaction: discord.Interaction):
        start = await self.db.get_setting("season_start_date")
        if start:
            await interaction.response.send_message(f"Season start is set to **{start}**. Auto-sync ignores anything before this date.")
        else:
            await interaction.response.send_message(
                f"No season start date set — auto-sync defaults to the last "
                f"{config.DEFAULT_LOOKBACK_DAYS} days. Use `/factionadmin set-season-start` to set a fixed cutoff."
            )

    @admin_group.command(name="set-scene", description="Set the DigiLab scene slug tracked for points (e.g. austin-tx)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_scene(self, interaction: discord.Interaction, scene_slug: str):
        scene = await self.bot.digilab.scene(scene_slug)
        if not scene:
            await interaction.response.send_message(
                f"DigiLab doesn't recognize scene `{scene_slug}`. Check `/api/scenes` or the site's scene URL slug.",
                ephemeral=True,
            )
            return
        await self.db.set_setting("scene_slug", scene_slug)
        label = scene.get("scope", {}).get("label", scene_slug)
        await interaction.response.send_message(f"Tracking tournaments for **{label}** (`{scene_slug}`).")

    @admin_group.command(name="set-channel", description="Set the channel for new-results announcements")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.db.set_setting("announce_channel_id", str(channel.id))
        await interaction.response.send_message(f"Results will be announced in {channel.mention}.")

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

        existing = await self.db.get_faction_by_emoji(str(parsed))
        if existing and existing["name"] != name:
            await interaction.response.send_message(
                f"{parsed} is already used by **{existing['name']}**. Pick a different emoji.", ephemeral=True
            )
            return

        ok = await self.db.set_faction_emoji(name, str(parsed))
        if not ok:
            await interaction.response.send_message(f"No faction named **{name}**.", ephemeral=True)
            return
        await interaction.response.send_message(f"**{name}** icon set to {parsed}.")

    @admin_group.command(name="post-signup", description="Post the faction sign-up message (react to join)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Where to post it (defaults to this channel)")
    async def post_signup(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        factions = await self.db.list_factions()
        if not factions:
            await interaction.response.send_message("No factions exist yet — use `/faction create` first.", ephemeral=True)
            return

        missing = [f["name"] for f in factions if not f["emoji"]]
        if missing:
            await interaction.response.send_message(
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

        await interaction.response.send_message(f"Posting sign-up message in {target.mention}...", ephemeral=True)
        message = await target.send(embed=embed)
        for f in factions:
            try:
                await message.add_reaction(discord.PartialEmoji.from_str(f["emoji"]))
            except discord.HTTPException:
                pass

        await self.db.set_setting("signup_message_id", str(message.id))
        await self.db.set_setting("signup_channel_id", str(target.id))

    @admin_group.command(name="log-tournament-id", description="Fetch and log full standings for a tournament ID — official API, no decklist required")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(tournament="Tournament ID or full digilab.cards/tournament/... URL")
    async def log_tournament_id(self, interaction: discord.Interaction, tournament: str):
        await interaction.response.defer()

        # Accept either a bare ID ("6116") or a full URL.
        match = re.search(r"tournament/(\d+)", tournament)
        tournament_id_str = match.group(1) if match else tournament.strip()
        if not tournament_id_str.isdigit():
            await interaction.followup.send(
                "Couldn't figure out a tournament ID from that — pass either the numeric ID "
                "(e.g. `6116`) or the full URL (e.g. `https://digilab.cards/tournament/6116`).",
                ephemeral=True,
            )
            return
        tournament_id = int(tournament_id_str)

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

        faction = await self.db.get_member_faction(user.id)
        if not faction:
            await interaction.response.send_message(f"{user.mention} isn't in a faction yet.", ephemeral=True)
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
        await interaction.response.send_message(
            f"Logged **{user.display_name}** — {placement} of {player_count} → "
            f"**{points:g}** points for **{faction}**{note}"
        )

    @admin_group.command(name="award", description="Manually award points to a user")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def award(self, interaction: discord.Interaction, user: discord.User, points: float, reason: str = ""):
        faction = await self.db.get_member_faction(user.id)
        if not faction:
            await interaction.response.send_message(f"{user.mention} isn't in a faction yet.", ephemeral=True)
            return
        await self.db.manual_award(user.id, faction, points, reason or "manual award")
        await interaction.response.send_message(
            f"Awarded **{points:g}** points to {user.mention} ({faction})" + (f" — {reason}" if reason else "")
        )

    @set_scene.error
    @set_channel.error
    @set_icon.error
    @post_signup.error
    @set_season_start.error
    @season_info.error
    @sync.error
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
