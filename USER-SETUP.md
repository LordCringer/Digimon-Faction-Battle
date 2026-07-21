# Joining the Faction Battle

## Quick start (recommended)

Run:
```
/joinfactionbattle
```

You'll get a dropdown to pick your faction. If this is your first time, right after you pick, a popup will ask for your DigiLab / tournament name — this links your account so your tournament results count toward your faction's score. Once linked, you're in.

If you've already linked your DigiLab account before (via `/register` or a previous `/joinfactionbattle`), it skips straight to joining — no popup.

**Tip:** use the exact name you play under at events — the bot matches you against DigiLab's leaderboard for your local scene, so an unusual nickname or misspelling may not find you.

## Other ways to join

- **React to the sign-up message** (if your server has one posted) — click the emoji for your faction. This joins you but does **not** link DigiLab, so your results won't be tracked until you also run `/register`.
- **`/faction join name:Shambala`** — join directly by typing the faction name, also without linking DigiLab.

## Linking DigiLab separately

If you joined a faction without linking (or want to relink after using a different name):
```
/register name:YourTournamentName
```
If multiple players match that name, you'll get a dropdown to pick the right one.

To unlink:
```
/unregister
```

## Checking your status

```
/profile
```
Shows your faction, your linked DigiLab account, and your total points.

```
/faction list
```
Shows every faction's total points and member count.

```
/faction leaderboard
```
Top players overall, or scoped to one faction:
```
/faction leaderboard name:Shambala
```

```
/tournaments
```
Browse recent tournaments in the local scene — date, store, player count, and winner. Handy for spotting your own tournament's ID or double-checking your placement got recorded.

## Switching factions? Read this first

**Joining a different faction wipes your accumulated points — but you'll
be asked to confirm first.** If you try to switch, the bot sends you a DM:
*"Switching to X will reset your points to 0 — type YES to confirm or NO
to cancel."* You have 60 seconds to reply. Nothing changes unless you
reply **YES** — replying **NO**, letting it time out, or having DMs
closed all just cancel the switch and leave you exactly where you were.

Make sure you can receive DMs from server members, or the confirmation
can't reach you and the switch won't go through.

Re-selecting the faction you're already in doesn't trigger any of this —
no DM, no reset, it's just a no-op.

## How you actually earn points

Play in a **locals** tournament, place well, and make sure your placement gets recorded on DigiLab (your store/organizer usually handles this). No decklist submission is required — full standings alone are enough. Points get added automatically within about 15 minutes of the tournament being posted on DigiLab, or sooner if an admin manually triggers a sync or logs your event directly.

Points scale by tournament size — bigger events are worth more for the same placement. First place always scores the most, decreasing down through the standings.

## If something's not working

- **Points not showing up?** Confirm with `/profile` that you're both linked *and* in a faction — both are required.
- **Can't find your name during `/register`?** Your local store may not have submitted your results to DigiLab yet, or you might be searching a name slightly different from what's on your profile there.
- **Still stuck?** Ask an admin to run `/factionadmin log-tournament-id` or `/factionadmin log-result` to log it manually.
