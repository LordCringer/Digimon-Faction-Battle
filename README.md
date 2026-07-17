# Digimon TCG Faction Bot

Discord bot that lets your server split into factions and earn points based
on how members place in **local tournaments tracked by [DigiLab](https://digilab.cards)**.

## How it works

DigiLab's public API is read-only and has no concept of Discord users or
points — it only knows tournament results by player name. So this bot:

1. Lets each Discord member **link their account** to their DigiLab player
   profile (`/register`).
2. Lets members **join a faction** (`/faction join`).
3. **Polls DigiLab** every `POLL_INTERVAL_MINUTES` (default 30) for new
   results in your configured local scene, matches them to linked members,
   and awards points to their faction based on placement.
4. Posts an announcement embed and tracks a leaderboard.

Every result is keyed by DigiLab's `result_id`, so re-polling (or a bot
restart) never double-counts a result.

## Points scheme

Edit `config.py` to tune this:

```python
SMALL_TOURNAMENT_THRESHOLD = 10  # player_count below this uses the small-event table

PLACEMENT_POINTS_STANDARD = {
    1: 10, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1,
}

PLACEMENT_POINTS_SMALL = {
    1: 5, 2: 3, 3: 2, 4: 1,
}

# Only in-person store locals are tracked — regionals, majors, and online
# events are excluded entirely.
TRACKED_EVENT_TYPES = ["locals"]
```

Tournaments with **10 or more players** use the standard table; anything
smaller automatically drops to the reduced table. Placements outside a
table (9th+ in a big event, 5th+ in a small one) score **0** — DigiLab's
`player_count` field on each result is what decides which table applies.

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
3. Set an icon for each faction:
   ```
   /factionadmin set-icon name:Shambala emoji:🌀
   /factionadmin set-icon name:Liberator emoji:⚔️
   /factionadmin set-icon name:Iliad emoji:🏛️
   /factionadmin set-icon name:"Glowing Dawn" emoji:🌅
   ```
   (any emoji works, including custom server emoji — just pick unique ones)
4. `/factionadmin post-signup channel:#faction-signup` — posts an embed
   listing all factions and reacts to it with each faction's emoji. Members
   can join this way too, but it does **not** require a DigiLab link (see
   note below).
5. Point members at **`/joinfactionbattle`** — this is the primary flow:
   they pick a faction from a dropdown, and if they haven't linked DigiLab
   yet, a popup immediately asks for their tournament name before the join
   is finalized. If they're already linked (e.g. via `/register` earlier),
   it skips straight to joining.
6. `/factionadmin sync` to pull in any recent results immediately instead of
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
| `/profile [user]` | anyone | Your (or someone's) faction, points, DigiLab link |
| `/faction create <name> [emoji]` | Manage Server | Create a faction |
| `/faction delete <name>` | Manage Server | Delete a faction |
| `/factionadmin set-icon <name> <emoji>` | Manage Server | Set the emoji used to join a faction by reacting |
| `/factionadmin post-signup [#channel]` | Manage Server | Post the reaction-based faction sign-up message |
| `/factionadmin set-scene <slug>` | Manage Server | Set which DigiLab scene to track |
| `/factionadmin set-channel <#channel>` | Manage Server | Set results announcement channel |
| `/factionadmin sync` | Manage Server | Force an immediate DigiLab check |
| `/factionadmin award <user> <points> [reason]` | Manage Server | Manually adjust a member's points |

## Notes / limitations

- Matching is by DigiLab player **name search** at registration time — if
  someone plays under a different name at events, `/register` again with
  the right name (it re-links, doesn't duplicate).
- DigiLab's API only exposes **decklist submissions** (which include
  placement), not a bare "attendance" list — so a player only scores if
  they (or the store) submitted a decklist for that event.
- Only `locals` event results count toward faction points
  (`TRACKED_EVENT_TYPES` in `config.py`) — regionals, majors, and online
  events are intentionally excluded to keep this strictly an in-person
  local-scene competition.
- Per DigiLab's terms, this bot's public output should credit DigiLab —
  the announcement embeds link back implicitly via player names; consider
  adding "Data provided by DigiLab (digilab.cards)" somewhere visible if
  you make standings public outside Discord.
