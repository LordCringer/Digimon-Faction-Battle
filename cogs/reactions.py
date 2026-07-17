import discord
from discord.ext import commands


class Reactions(commands.Cog):
    """
    Handles the faction sign-up message: reacting with a faction's emoji
    joins that faction. Un-reacting does NOT leave a faction (use
    /faction leave for that) — this keeps the logic simple and avoids
    races with the bot's own reaction cleanup below.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _is_signup_message(self, payload: discord.RawReactionActionEvent) -> bool:
        signup_id = await self.db.get_setting("signup_message_id")
        return bool(signup_id) and payload.message_id == int(signup_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if not await self._is_signup_message(payload):
            return

        channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        member = payload.member
        if member is None:
            guild = self.bot.get_guild(payload.guild_id)
            member = await guild.fetch_member(payload.user_id)

        emoji_str = str(payload.emoji)
        faction = await self.db.get_faction_by_emoji(emoji_str)

        if not faction:
            # Not one of the configured faction emojis — strip it so the
            # message stays clean.
            try:
                await message.remove_reaction(payload.emoji, member)
            except discord.HTTPException:
                pass
            return

        # Enforce one faction at a time: strip the user's reaction from any
        # other faction emoji on this message.
        for reaction in message.reactions:
            if str(reaction.emoji) != emoji_str:
                try:
                    await reaction.remove(member)
                except discord.HTTPException:
                    pass

        await self.db.join_faction(payload.user_id, faction["name"])

        try:
            await member.send(f"{faction['emoji']} You joined **{faction['name']}**!")
        except discord.HTTPException:
            pass  # DMs closed, no big deal


async def setup(bot: commands.Bot):
    await bot.add_cog(Reactions(bot))
