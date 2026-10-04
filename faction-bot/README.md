# Digimon TCG Faction Bot

Discord bot that lets your server split into factions and earn points based
on how members place in **local tournaments tracked by [DigiLab](https://digilab.cards)**.

## How it works

DigiLab's public API is read-only and has no concept of Discord users or
points — it only knows tournament results by player name. So this bot:

1. Lets each Discord member **link their account** to their DigiLab player
   profile (`/register`).
2. Lets members **join a faction** (`/faction join`).
3. **Polls DigiLab** every `POLL_INTERVAL_MINUTES` (default 15) for new
   results in your configured local scene, matches them to linked members,
   and awards points to their faction based on placement.
4. Posts an announcement embed and tracks a leaderboard.

Every result is keyed by DigiLab's `result_id`, so re-polling (or a bot
restart) never double-counts a result.

## How points actually get in

**As of DigiLab's 2026-07-20 API update, this no longer requires decklists
at all.** They added `GET /api/tournament/{id}` — full standings (every
placement, record, and deck) for a single event, with no decklist
submission required. The bot's automatic sync (`/factionadmin sync` and
the background poller) now uses this directly: it discovers tournaments in
your scene via `/api/tournaments`, then pulls full standings for each one
via `/api/tournament/{id}`. As long as your store logs full results (not
just the winner) on DigiLab, this awards points automatically — no manual
work needed most weeks.

Manual options are still there as fallbacks, for events that aren't on
DigiLab at all or if you want to fix/backfill something:

1. **Automatic DigiLab sync (primary path now)** — `/factionadmin sync`
   and the background poller pull full standings from
   `/api/tournament/{id}` for every tournament in your configured scene.
   No decklist required, official API, nothing fragile.

2. **Fetch one specific tournament on demand** —
   `/factionadmin log-tournament-id tournament:6116` (ID or full URL)
   calls the same official endpoint immediately for a single event,
   useful right after locals wraps instead of waiting for the next poll.

3. **Bulk logging by paste (no DigiLab lookup at all)** —
   `/factionadmin log-tournament` pops up a text box. Paste standings,
   one line per player:
   ```
   1, Bobby Lau
   2, Jefe
   3, Dan Ly
   4, Matt G
   5, Matt K
   ```
   Matches each name (case-insensitive) against whoever's `/register`ed.
   Useful for events that never made it onto DigiLab.

4. **Single-player logging** — `/factionadmin log-result` for one person:
   ```
   /factionadmin log-result user:@Jefe placement:2 player_count:5
   ```

All four write to the same points table — mixing them for different events
is fine. Just avoid running both the automatic sync **and** a manual
command for the *same* tournament, since they don't recognize each other's
entries as duplicates (manual logs use synthetic IDs specifically so they
never collide with DigiLab's real IDs, which means they also won't
dedupe against each other).

## Points scheme

Edit `config.py` to tune this:

```python
SMALL_TOURNAMENT_THRESHOLD = 10  # player_count below this uses the small-event table

PLACEMENT_POINTS_STANDARD = {
    1: 10, 2: 8, 3: 7, 4: 6, 5: 5, 6: 4, 7: 3, 8: 2,
}

PLACEMENT_POINTS_SMALL = {
    1: 7, 2: 5, 3: 4, 4: 3,
}

# Only in-person store locals are tracked — regionals, majors, and online
# events are excluded entirely.
TRACKED_EVENT_TYPES = ["locals"]
```

Tournaments with **10 or more players** use the standard table; anything
smaller automatically drops to the reduced table. Any valid placement
**not** explicitly listed still scores **1 point** — everyone who plays
and places gets something, not just top-8 (or top-4 in small events)
finishers. DigiLab's `player_count` field on each result is what decides
which table applies.

There's no event-type multiplier — a locals win is worth the same
everywhere. Only `locals` results factor into faction points at all;
regionals, majors, and online events are never pulled in.

## Setup

### 0. Get the code onto your Ubuntu host

```bash
git clone https://github.com/LordCringer/Digimon-Faction-Battle.git
cd Digimon-Faction-Battle
```

### 1. Discord bot

- Create an application at https://discord.com/developers/applications
- Bot tab → enable the **Server Members Intent**
- Invite it with the `applications.commands` and `bot` scopes, and at least
  `Send Messages` / `Embed Links` permissions.

### 2. DigiLab API key

Request one in **#api** on the [DigiLab Discord](https://discord.gg/FYHHqbqsxk)
— it's free for non-commercial community tools like this.

### 3. Configure

```bash
cp .env.example .env
# fill in DISCORD_TOKEN, DIGILAB_API_KEY, and GUILD_ID (for instant slash-command sync during setup)
```

### 4. Run

**Docker (matches your usual deployment pattern):**

```bash
docker compose up -d --build
```

**Bare Python:**

```bash
pip install -r requirements.txt
python bot.py
```

The four factions — **Shambala**, **Liberator**, **Iliad**, and **Glowing
Dawn** — are created automatically the first time the bot starts. You still
need to give each one an icon before members can join by reacting.

## First-time server setup (in Discord)

1. `/factionadmin set-scene austin-tx` — find your scene slug from
   `https://digilab.cards/leaderboard` (it's in the URL) or `GET /api/scenes`.
2. `/factionadmin set-channel #tournament-results` — where new results get announced.
3. `/factionadmin set-season-start date:2026-07-20` — sets a firm cutoff so
   auto-sync only counts tournaments on/after this date, ignoring anything
   from before the faction battle started. Without this, auto-sync falls
   back to a rolling last-60-days window (`DEFAULT_LOOKBACK_DAYS` in
   `config.py`), which will pull in old, pre-season results. Check the
   current setting anytime with `/factionadmin season-info`.
4. Set an icon for each faction:
   ```
   /factionadmin set-icon name:Shambala emoji:🌀
   /factionadmin set-icon name:Liberator emoji:⚔️
   /factionadmin set-icon name:Iliad emoji:🏛️
   /factionadmin set-icon name:"Glowing Dawn" emoji:🌅
   ```
   (any emoji works, including custom server emoji — just pick unique ones)
5. `/factionadmin post-signup channel:#faction-signup` — posts an embed
   listing all factions and reacts to it with each faction's emoji. Members
   can join this way too, but it does **not** require a DigiLab link (see
   note below).
6. Point members at **`/joinfactionbattle`** — this is the primary flow:
   they pick a faction from a dropdown, and if they haven't linked DigiLab
   yet, a popup immediately asks for their tournament name before the join
   is finalized. If they're already linked (e.g. via `/register` earlier),
   it skips straight to joining.
7. `/factionadmin sync` to pull in any recent results immediately instead of
   waiting for the next poll.

**Heads up:** the reaction sign-up message and `/faction join` are still
available as quick manual paths and do **not** enforce linking a DigiLab
account — only `/joinfactionbattle` does. If you want linking to be
mandatory no matter how someone joins, let me know and I can lock those
down too (e.g. have the reaction handler also require a link, or remove
`/faction join` from public use).

**How reaction sign-up works:** reacting with a faction's emoji on the
sign-up message joins that faction immediately (a DM confirms it, if the
member allows DMs). Reacting with a *different* faction's emoji switches
them — the bot automatically removes their old reaction. Un-reacting does
**not** leave a faction (to avoid a race between the bot's own cleanup
reactions and real un-reacts); use `/faction leave` for that. `/faction
join` still works too as a manual alternative to reacting.

The bot needs **Manage Messages** permission in the sign-up channel so it
can strip stray/duplicate reactions.

**Switching factions requires confirmation and resets your points.**
Whenever someone tries to join a *different* faction than the one they're
currently in — via `/joinfactionbattle`, `/faction join`, or reacting on
the sign-up message — the bot DMs them: *"Switching to X will reset your
points to 0 — type YES to confirm or NO to cancel"* (60 second window).
Only on **YES** does the switch and reset actually happen; **NO**, a
timeout, or DMs being closed all cancel it and leave them in their current
faction untouched (for reaction-based attempts, the reaction itself gets
reverted too, so it doesn't visually misrepresent their faction). Joining
fresh (no current faction) or re-picking the same faction skips
confirmation entirely, since there's nothing to reset. `/faction leave`
(with no faction rejoined afterward) does **not** trigger this — the
reset only happens at the moment of joining a different faction.

## Running alongside another Discord bot on the same host

If this Ubuntu host is already running another bot (e.g. a separate TCG
lookup bot), keep them fully independent — different folder, different
container, different Discord bot token.

```bash
cd ~
git clone https://github.com/LordCringer/Digimon-Faction-Battle.git
cd Digimon-Faction-Battle
cp .env.example .env
nano .env   # paste in THIS bot's own token — never reuse another bot's token
docker compose up -d --build
```

Each bot needs its own Discord application/token (Discord will disconnect
whichever one connects second if two containers share a token), its own
container name (already set to `digimon-faction-bot` in
`docker-compose.yml`), and its own data volume (`./data:/data`, local to
this repo's folder, so it won't collide with another bot's SQLite files).

```bash
docker ps                                   # confirm both containers are up
docker compose logs -f faction-bot          # logs for just this bot
docker stats                                # sanity-check host isn't overloaded
```

To manage both from a single `docker-compose.yml`, merge them at a parent
directory level, keeping each service's own `env_file` and volume path:

```yaml
services:
  digimon-tcg-bot:
    build: ./digimon-tcg-bot
    restart: unless-stopped
    env_file: ./digimon-tcg-bot/.env

  faction-bot:
    build: ./Digimon-Faction-Battle
    restart: unless-stopped
    env_file: ./Digimon-Faction-Battle/.env
    volumes:
      - ./Digimon-Faction-Battle/data:/data
```

Then `docker compose up -d` from that parent directory brings up both. No
port conflicts either way — Discord bots only make outbound connections.

## Commands

| Command | Who | Description |
|---|---|---|
| `/register <name>` | anyone | Link your DigiLab player without going through faction selection |
| `/unregister` | anyone | Remove the link |
| `/joinfactionbattle` | anyone | **The main flow:** pick a faction from a dropdown, then link your DigiLab account (required, first time only) — completes the join in one go |
| `/faction join <name>` | anyone | Join a faction directly by name, no dropdown (does **not** require a DigiLab link) |
| `/faction leave` | anyone | Leave your faction |
| `/faction list` | anyone | Show all factions and their point totals |
| `/faction leaderboard [name]` | anyone | Top players, overall or per-faction |
| `/faction members <name>` | anyone | List every member of a specific faction |
| `/profile [user]` | anyone | Your (or someone's) faction, points, DigiLab link |
| `/tournaments [limit]` | anyone | Browse recent tournaments in the configured scene, with their IDs |
| `/faction create <name> [emoji]` | Manage Server | Create a faction |
| `/faction delete <name>` | Manage Server | Delete a faction |
| `/factionadmin set-icon <name> <emoji>` | Manage Server | Set the emoji used to join a faction by reacting |
| `/factionadmin post-signup [#channel]` | Manage Server | Post the reaction-based faction sign-up message |
| `/factionadmin log-tournament-id <tournament>` | Manage Server | Fetch and log one tournament's full standings on demand — official API, no decklist needed |
| `/factionadmin log-tournament` | Manage Server | Bulk-log standings by pasting them — for events not on DigiLab at all |
| `/factionadmin log-result <user> <placement> <player_count>` | Manage Server | Manually log a single placement |
| `/factionadmin set-scene <slug>` | Manage Server | Set which DigiLab scene to track (only matters if you use auto-sync) |
| `/factionadmin set-season-start <date>` | Manage Server | Set a firm cutoff date (YYYY-MM-DD) — auto-sync ignores tournaments before it |
| `/factionadmin season-info` | Manage Server | Show the current season start date, or the fallback lookback window if unset |
| `/factionadmin set-channel <#channel>` | Manage Server | Set results announcement channel |
| `/factionadmin sync` | Manage Server | Force an immediate DigiLab check |
| `/factionadmin award <user> <points> [reason]` | Manage Server | Manually adjust a member's points |

## Notes / limitations

- Matching is by DigiLab player **name lookup against the leaderboard** at
  registration time (scoped to your configured scene) — if someone plays
  under a different name at events, `/register` again with the right name
  (it re-links, doesn't duplicate). The leaderboard only includes players
  with at least one recorded result, so brand-new players may not show up
  until they've played their first tracked event.
- As of DigiLab's 2026-07-20 update, **no decklist submission is required**
  for points — `/api/tournament/{id}` gives full standings regardless.
  A player only needs to actually be recorded in the tournament's results.
- **Anonymous players** (DigiLab profile set to private) appear in
  standings with no slug, so they can never be matched to a Discord
  account — this is a DigiLab-side privacy setting, not something the bot
  can work around.
- Only `locals` event results count toward faction points
  (`TRACKED_EVENT_TYPES` in `config.py`) — regionals, majors, and online
  events are intentionally excluded to keep this strictly an in-person
  local-scene competition.
- Per DigiLab's terms, this bot's public output should credit DigiLab —
  the announcement embeds link back implicitly via player names; consider
  adding "Data provided by DigiLab (digilab.cards)" somewhere visible if
  you make standings public outside Discord.
