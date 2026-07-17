import discord
from discord import app_commands
from discord.ext import commands

from points_sync import sync_points


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
    @sync.error
    @log_result.error
    @award.error
    async def perms_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission for that.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
