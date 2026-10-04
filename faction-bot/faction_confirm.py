import asyncio
import logging

import discord

log = logging.getLogger("faction_confirm")


async def confirm_faction_switch(bot, discord_id: int, current_faction: str, new_faction: str,
                                  timeout: float = 60.0):
    """
    DMs the user asking them to type YES or NO to confirm a faction switch
    (which resets their accumulated points to 0). Works the same way no
    matter how the switch was triggered — slash command, dropdown, modal,
    or raw reaction — since it only needs a Discord user ID, not an
    Interaction object.

    Returns:
        True  - they typed YES
        False - they typed NO, or the confirmation timed out
        None  - couldn't DM them at all (DMs closed to server members)
    """
    try:
        user = bot.get_user(discord_id) or await bot.fetch_user(discord_id)
        dm_channel = user.dm_channel or await user.create_dm()
        await dm_channel.send(
            f"You're currently in **{current_faction}**. Switching to **{new_faction}** "
            f"will reset your accumulated points to **0** — this can't be undone.\n\n"
            f"Type **YES** to confirm the switch, or **NO** to cancel. "
            f"(You have {int(timeout)} seconds.)"
        )
    except discord.HTTPException:
        return None

    def check(m: discord.Message) -> bool:
        return (
            m.author.id == discord_id
            and m.channel.id == dm_channel.id
            and m.content.strip().upper() in ("YES", "NO")
        )

    try:
        msg = await bot.wait_for("message", check=check, timeout=timeout)
    except asyncio.TimeoutError:
        try:
            await dm_channel.send("Confirmation timed out — switch cancelled. You're still in your current faction.")
        except discord.HTTPException:
            pass
        return False

    confirmed = msg.content.strip().upper() == "YES"
    try:
        if confirmed:
            await dm_channel.send(f"Confirmed — switching you to **{new_faction}**.")
        else:
            await dm_channel.send("Cancelled — you're still in your current faction.")
    except discord.HTTPException:
        pass
    return confirmed
