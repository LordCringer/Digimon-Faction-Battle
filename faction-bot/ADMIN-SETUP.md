# Admin Setup — Digimon Faction Battle Bot

Run these once the bot is online in your server. All require **Manage Server** permission.

## 1. Set your scene
```
/factionadmin set-scene scene_slug:metro-vancouver
```
Find your scene slug from the URL at `digilab.cards/leaderboard`, or check `GET /api/scenes`.

## 2. Set a season start date
```
/factionadmin set-season-start date:2026-07-20
```
This is a firm cutoff — auto-sync only counts tournaments on/after this date. **Do this before running your first sync**, or you'll pull in old, pre-season results. Without it, the bot falls back to a rolling last-60-days window.

Check the current setting anytime:
```
/factionadmin season-info
```

## 3. Set the results announcement channel
```
/factionadmin set-channel channel:#tournament-results
```
New auto-synced or manually-logged results get posted here.

## 4. Set faction icons
Factions are pre-created (Shambala, Liberator, Iliad, Glowing Dawn). Give each an emoji before posting the sign-up message:
```
/factionadmin set-icon name:Shambala emoji:🌀
/factionadmin set-icon name:Liberator emoji:⚔️
/factionadmin set-icon name:Iliad emoji:🏛️
/factionadmin set-icon name:"Glowing Dawn" emoji:🌅
```
Any emoji works, including custom server emoji — just keep them unique.

## 5. Post the sign-up message (optional — reaction-based joining)
```
/factionadmin post-signup channel:#faction-signup
```
Posts an embed listing all factions; members can react to join. **This path does not require a DigiLab link** — `/joinfactionbattle` (tell users about this one) is the primary flow that does.

*Requires the bot to have **Manage Messages** permission in that channel, so it can clean up stray/duplicate reactions.*

## 6. Test the pipeline before trusting it
```
/factionadmin log-tournament-id tournament:6116
```
Or any known tournament ID — this fetches standings directly and tells you exactly who it would log and who it can't match (not registered / no faction). Good sanity check before relying on auto-sync.

Then run a real sync and watch the logs:
```bash
docker compose logs -f faction-bot
```
```
/factionadmin sync
```
You should see `Sync starting...` / `Page N: X tournament(s)...` / `Sync finished: ... award(s) given`.

## Ongoing admin commands

| Command | What it does |
|---|---|
| `/factionadmin sync` | Force an immediate check for new results |
| `/factionadmin log-tournament-id tournament:<id>` | Fetch and log one specific tournament on demand |
| `/factionadmin exclude-tournament tournament:<id> reason:"..."` | Block a tournament from ever awarding points (auto-sync or manual) |
| `/factionadmin include-tournament tournament:<id>` | Remove a tournament from the exclusion list — eligible again next sync |
| `/factionadmin list-excluded` | Show every currently-excluded tournament ID |
| `/factionadmin clear-leaderboard confirm:CONFIRM` | ⚠️ **Wipe every member's points, permanently.** Requires typing `CONFIRM` exactly. |
| `/factionadmin log-tournament` | Paste in standings manually (for events not on DigiLab) |
| `/factionadmin log-result user:@x placement:N player_count:N` | Log a single placement manually |
| `/factionadmin award user:@x points:N reason:"..."` | Manually adjust someone's points (positive or negative) |
| `/factionadmin season-info` | Check current season start / lookback setting |
| `/faction create name:"..." emoji:...` | Add a new faction beyond the default four |
| `/faction delete name:"..."` | Remove a faction |

## Notes
- **`/tournaments` is a public command** — any member can run it to browse
  recent tournaments and their IDs (useful for them to double check their
  own results, or for you to grab an ID for `log-tournament-id` /
  `exclude-tournament` without needing to check yourself first).
- **Switching factions resets a member's points to 0** — this happens
  automatically the moment someone joins a *different* faction than the
  one they're in, through any join path (slash command, dropdown,
  reaction), but only after they confirm via a DM prompt (type YES/NO).
- **Excluding a tournament** blocks it permanently from both auto-sync and
  `log-tournament-id` — useful for a mis-scored or disputed event. It's
  remembered forever until you `include-tournament` it back.
- **`clear-leaderboard` does not undo auto-sync's memory** — tournaments
  already processed stay marked as such, so wiping points won't get
  silently re-awarded on the next sync cycle. If you want specific past
  tournaments to actually reprocess after a wipe, `include-tournament`
  them even if they were never excluded — that also clears their synced
  marker as a side effect.
- Auto-sync runs automatically every `POLL_INTERVAL_MINUTES` (currently 15) in the background — no action needed once scene + season start are set.
- Auto-sync and manual logging (`log-result` / `log-tournament`) don't recognize each other's entries — avoid double-logging the same tournament both ways.
- Only `locals` event results count toward points (`TRACKED_EVENT_TYPES` in `config.py`) — regionals/majors/online are excluded by design.
