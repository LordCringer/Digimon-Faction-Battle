import discord
from discord import app_commands
from discord.ext import commands

from digilab import DigiLabError


class FactionNameTransformer(app_commands.Transformer):
    """Lets us autocomplete faction names from the DB."""
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        return value


def _player_option_kwargs(p: dict) -> dict:
    return dict(
        description=(
            f"Rating {p.get('rating', '?')} · {p.get('events_played', '?')} events"
            f"{' · ' + p['scene_name'] if p.get('scene_name') else ''}"
        )[:100]
    )


class LinkAndJoinSelect(discord.ui.Select):
    """Second-step picker when a name lookup returns multiple DigiLab players.
    Selecting one links it AND completes the faction join in one shot."""

    def __init__(self, players: list[dict], faction_name: str):
        options = [
            discord.SelectOption(label=(p.get("display_name") or "Unknown")[:100], value=str(i), **_player_option_kwargs(p))
            for i, p in enumerate(players[:25])
        ]
        super().__init__(placeholder="Which player is you?", min_values=1, max_values=1, options=options)
        self.players = players
        self.faction_name = faction_name

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        p = self.players[idx]
        db = interaction.client.db
        await db.register_player(interaction.user.id, None, p.get("display_name"), p["slug"])
        await db.join_faction(interaction.user.id, self.faction_name)
        faction = await db.get_faction(self.faction_name)
        icon = (faction["emoji"] if faction else "") or ""
        await interaction.response.edit_message(
            content=f"Linked to **{p.get('display_name')}** and {icon} joined **{self.faction_name}**!".strip(),
            view=None,
        )


class LinkAndJoinPickerView(discord.ui.View):
    def __init__(self, players: list[dict], faction_name: str, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(LinkAndJoinSelect(players, faction_name))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This isn't your prompt.", ephemeral=True)
            return False
        return True


class DigiLabLinkModal(discord.ui.Modal, title="Link your DigiLab account"):
    """Shown after picking a faction, before the join is finalized — linking
    is required so tournament results can be tracked for the faction battle."""

    player_name = discord.ui.TextInput(
        label="Your DigiLab / tournament name",
        placeholder="Name you use at events, e.g. JohnDoe",
        max_length=100,
    )

    def __init__(self, faction_name: str):
        super().__init__()
        self.faction_name = faction_name

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        db = bot.db
        await interaction.response.defer(ephemeral=True)

        scene = await db.get_setting("scene_slug")
        try:
            # DigiLab removed /api/search (2026-07-20); lookup now goes
            # through the leaderboard, scoped to the configured scene.
            players = await bot.digilab.find_players_by_name(self.player_name.value, scene=scene)
        except DigiLabError as e:
            await interaction.followup.send(f"Couldn't reach DigiLab right now: {e}", ephemeral=True)
            return

        if not players:
            await interaction.followup.send(
                f"No DigiLab players found matching **{self.player_name.value}**. "
                f"Run `/joinfactionbattle` again and try a different name — "
                f"you won't be placed in a faction until this succeeds.",
                ephemeral=True,
            )
            return

        if len(players) == 1:
            p = players[0]
            await db.register_player(interaction.user.id, None, p.get("display_name"), p["slug"])
            await db.join_faction(interaction.user.id, self.faction_name)
            faction = await db.get_faction(self.faction_name)
            icon = (faction["emoji"] if faction else "") or ""
            await interaction.followup.send(
                f"Linked to **{p.get('display_name')}** and {icon} joined **{self.faction_name}**!".strip(),
                ephemeral=True,
            )
            return

        view = LinkAndJoinPickerView(players, self.faction_name, interaction.user.id)
        await interaction.followup.send(
            f"Found {len(players)} players matching **{self.player_name.value}** — pick yours "
            f"to finish joining **{self.faction_name}**:",
            view=view,
            ephemeral=True,
        )


class FactionSelect(discord.ui.Select):
    def __init__(self, factions):
        options = []
        for f in factions:
            kwargs = dict(label=f["name"][:100], value=f["name"], description="Join this faction")
            if f["emoji"]:
                try:
                    kwargs["emoji"] = discord.PartialEmoji.from_str(f["emoji"])
                except Exception:
                    pass
            options.append(discord.SelectOption(**kwargs))
        super().__init__(placeholder="Choose your faction...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        db = interaction.client.db
        faction = await db.get_faction(chosen)
        if not faction:
            await interaction.response.edit_message(content=f"**{chosen}** no longer exists.", view=None)
            return

        reg = await db.get_registration(interaction.user.id)
        if reg:
            # Already linked from a previous session — no need to ask again.
            await db.join_faction(interaction.user.id, chosen)
            icon = faction["emoji"] or ""
            await interaction.response.edit_message(
                content=f"{icon} You joined **{chosen}**! (tracking results for your linked player **{reg['player_name']}**)".strip(),
                view=None,
            )
            return

        # Not linked yet: this is required before the join completes.
        # (A modal must be the sole response to this interaction, so the
        # select message itself is left as-is; the modal explains the rest.)
        await interaction.response.send_modal(DigiLabLinkModal(faction_name=chosen))


class FactionBattleView(discord.ui.View):
    def __init__(self, factions, requester_id: int):
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(FactionSelect(factions))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This isn't your faction picker — run `/joinfactionbattle` yourself.", ephemeral=True
            )
            return False
        return True


class Factions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def faction_autocomplete(self, interaction: discord.Interaction, current: str):
        factions = await self.db.list_factions()
        return [
            app_commands.Choice(name=f["name"], value=f["name"])
            for f in factions if current.lower() in f["name"].lower()
        ][:25]

    faction_group = app_commands.Group(name="faction", description="Faction commands")

    @faction_group.command(name="create", description="Create a new faction (admin only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(name="Faction name", emoji="An emoji to represent the faction")
    async def create(self, interaction: discord.Interaction, name: str, emoji: str = ""):
        ok = await self.db.create_faction(name, emoji, interaction.user.id)
        if not ok:
            await interaction.response.send_message(f"A faction named **{name}** already exists.", ephemeral=True)
            return
        await interaction.response.send_message(f"{emoji} Faction **{name}** created.")

    @faction_group.command(name="delete", description="Delete a faction (admin only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=faction_autocomplete)
    async def delete(self, interaction: discord.Interaction, name: str):
        ok = await self.db.delete_faction(name)
        msg = f"Deleted faction **{name}**." if ok else f"No faction named **{name}**."
        await interaction.response.send_message(msg, ephemeral=not ok)

    @faction_group.command(name="join", description="Join a faction")
    @app_commands.autocomplete(name=faction_autocomplete)
    async def join(self, interaction: discord.Interaction, name: str):
        faction = await self.db.get_faction(name)
        if not faction:
            await interaction.response.send_message(
                f"No faction named **{name}**. Use `/faction list` to see options.", ephemeral=True
            )
            return
        await self.db.join_faction(interaction.user.id, name)
        await interaction.response.send_message(f"{faction['emoji']} You joined **{name}**!")

    @faction_group.command(name="leave", description="Leave your current faction")
    async def leave(self, interaction: discord.Interaction):
        ok = await self.db.leave_faction(interaction.user.id)
        msg = "You left your faction." if ok else "You're not in a faction."
        await interaction.response.send_message(msg, ephemeral=True)

    @faction_group.command(name="list", description="List all factions and their standings")
    async def list_factions(self, interaction: discord.Interaction):
        await interaction.response.defer()
        factions = await self.db.list_factions()
        if not factions:
            await interaction.followup.send("No factions have been created yet.")
            return
        totals = await self.db.faction_totals()
        rows = []
        for f in sorted(factions, key=lambda f: totals.get(f["name"], 0), reverse=True):
            members = await self.db.faction_members(f["name"])
            icon = f["emoji"] or "❔"
            rows.append(f"{icon} **{f['name']}** — {totals.get(f['name'], 0):g} pts ({len(members)} members)")
        embed = discord.Embed(title="Factions", description="\n".join(rows), color=discord.Color.gold())
        await interaction.followup.send(embed=embed)

    @faction_group.command(name="leaderboard", description="Top players, overall or within a faction")
    @app_commands.autocomplete(name=faction_autocomplete)
    async def leaderboard(self, interaction: discord.Interaction, name: str = None):
        await interaction.response.defer()
        rows = await self.db.member_totals(faction_name=name, limit=10)
        if not rows:
            await interaction.followup.send("No points have been awarded yet.")
            return
        lines = []
        for i, r in enumerate(rows, start=1):
            lines.append(f"**{i}.** <@{r['discord_id']}> — {r['total']:g} pts ({r['faction_name']})")
        title = f"Leaderboard — {name}" if name else "Leaderboard — All Factions"
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="joinfactionbattle", description="Pick your faction with a dropdown menu")
    async def join_faction_battle(self, interaction: discord.Interaction):
        factions = await self.db.list_factions()
        if not factions:
            await interaction.response.send_message("No factions exist yet — ask an admin to create some.", ephemeral=True)
            return
        view = FactionBattleView(factions, interaction.user.id)
        await interaction.response.send_message(
            "⚔️ Choose your faction below. If this is your first time, you'll be asked to "
            "link your DigiLab account right after — this is required so your tournament "
            "results count toward your faction's score.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="profile", description="Show your faction, DigiLab link, and points")
    async def profile(self, interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.defer()
        target = user or interaction.user
        faction = await self.db.get_member_faction(target.id)
        reg = await self.db.get_registration(target.id)
        total = await self.db.user_total(target.id)

        embed = discord.Embed(title=f"{target.display_name}'s profile", color=discord.Color.blurple())
        embed.add_field(name="Faction", value=faction or "*none*", inline=True)
        embed.add_field(name="Points", value=f"{total:g}", inline=True)
        embed.add_field(
            name="DigiLab account",
            value=f"[{reg['player_name']}](https://digilab.cards/player/{reg['player_slug']})" if reg else "*not linked — use `/register`*",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @create.error
    @delete.error
    async def perms_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission for that.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Factions(bot))
