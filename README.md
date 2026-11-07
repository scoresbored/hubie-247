# Hubie, live 24/7

An AI version of Hubie, the Housebets sloth, that streams to Kick around the clock with nobody touching it.
He plays House originals in **demo mode ($0 bets)**, talks about the moments worth talking about,
chats with viewers by name, moderates chat, and restarts himself when something breaks.

> 18+ only. Demo play only, no real money is ever bet. Gamble responsibly.

## What Hubie does

| Area | How |
| --- | --- |
| On screen | Animated Hubie (10 expression frames: lip sync from his live voice volume, blinks, breathing and sway, reaction faces for big hits, streaks, follows, polls and chat questions; seasonal sky that follows UTC day and night), the live game, recent results, chat, and a permanent 18+ / demo / gamble-responsibly banner |
| Plays | Rotates every 8-15 min between House originals and strategies: Dice (1.5x / 2x / 10x / 50x), Limbo (2x-100x targets), Wheel (low/medium/high risk), Max Win, Plinko, Coinflip |
| Talks | Comments only on real moments (win streaks, long losing streaks, big hits, droughts ending, milestones), announces and recaps each segment, never leaves long dead air |
| Chat | Reads Kick chat. Hubie speaks to first-time chatters and real questions; a separate 🤖 chat bot handles text replies, moderation notices and polls. Chat picks games with polls (vote 1-3) and `!play <game>` requests. Follows, subs and gifted subs get a bot post and a spoken thank-you. Replies scale with chat speed: a quiet chat gets answered (but not the same person every line); a busy chat gets newcomers, questions and "Hubie" mentions only, capped per minute, so it never looks spammy |
| Music | Original lo-fi background loops generated in code (no third-party tracks), low volume, ducks under Hubie's voice |
| Moderates | Times out links, promo/referral spam, slurs, caps and repeat spam; answers "I'm under 18" with the 18+ rule |
| Stays safe | Every line passes a filter that blocks win promises, deposit/bonus/code talk and money amounts; every bet is checked to be $0 before the click and again in the site's reply |
| Runs itself | Restarts crashed parts on its own: the stream (ffmpeg), the game browser, the chat reader, the whole app; benches a game for an hour if it keeps failing |

## Architecture

See [docs/architecture.md](docs/architecture.md) for the agent and tool diagram.

```
game.py   plays a House original at $0, reads each result from the site's own bet reply
games.py  the games + strategies Hubie rotates through
brain.py  Claude: Hubie's personality, moment commentary, chat replies
safety.py filter every spoken/posted line; responsible-gambling lines
voice.py  local text-to-speech + mouth-movement envelope
scene.py  draws every frame (game, Hubie, results, chat, banner)
avatar.py animated Hubie: mood faces, crossfades, breathing/sway, lip sync, blinks
theme.py  seasonal look for the whole overlay: Hubie's sky follows the UTC clock (dawn/day/dusk/night), and each month has its own palette, panel pattern and animated particles (fall leaves, holiday snow and lights, new-year fireworks, hearts...); the red safety banner never changes
stream.py pipes frames + voice into ffmpeg -> Kick (auto-restarts)
audio.py  mixer: exactly one frame of audio per video frame, music bed with ducking, drives the mouth
music.py  generates original lo-fi background loops (numpy)
chatfun.py chat polls + !play requests
kick.py   Kick chat in (pop-out chat feed) and out (official Kick API), timeouts
chatmod.py chat moderation rules
main.py   the director: segments, moments, chat worker, no-dead-air, RG reminders
tgbot.py  Telegram control panel (status, preview, logs; confirmed start/stop; optional /poll /giveaway, off by default)
```

## Setup (one page)

**You need:** a Linux server (tested: Ubuntu 24.04, 4 vCPU / 8 GB, Hetzner Helsinki), a Kick channel with a
stream key and a Kick developer app, an Anthropic API key, and a Housebets account for demo play.

1. **Prepare the server** (as root): `bash deploy/bootstrap.sh`
   Installs Xvfb, PulseAudio, ffmpeg, Python, Playwright Chromium, firewall, swap, and turns off surprise reboots.
2. **Install Hubie**: `sudo -u hubie git clone https://github.com/scoresbored/hubie-247.git /opt/hubie/app`,
   then `bash /opt/hubie/app/setup.sh`. Later updates: `./update.sh` (git pull + restart) or `/update` in Telegram
   (Python packages + the voice model).
3. **Secrets**: copy `.env.example` to `/opt/hubie/.env`, fill it in, `chmod 600`. Never commit it.
   Get `KICK_CHATROOM_ID` from `https://kick.com/api/v2/channels/<your-channel>` (`chatroom.id`).
4. **Log the game browser in once** (demo play needs a logged-in account): on the server run
   `python tools/spike.py login` on a virtual screen you can view over an SSH-tunnelled VNC, log in, press Enter.
5. **Connect Kick chat once**: `ssh -L 8765:localhost:8765 hubie@<server>` then
   `cd /opt/hubie/app && /opt/hubie/venv/bin/python -m hubie.kick_auth`, open the link, approve.
   Kick app redirect URL: `http://localhost:8765/callback`.
6. **Go live**: `./start.sh` (background, tmux) or install `deploy/hubie.service` for systemd.
   Press Go Live on Kick once. Stop with `./stop.sh`. Logs: `/opt/hubie/logs/hubie.jsonl`.
   Test without streaming: `./run.sh --offline` (writes `/opt/hubie/logs/preview.png`).
7. **Optional Telegram control panel**: add `TG_BOT_TOKEN` and `TG_ADMIN_IDS` to `.env`, run `./bot.sh`.
   Read-only commands (`/status`, `/preview`, `/logs`) are safe during a run; `/start` `/stop` `/restart` ask to confirm
   and warn that they reset the contest run.

## Stack

- **Model:** Claude Haiku 5.5 (Anthropic API) for personality, commentary and chat replies
- **Voice:** Piper TTS (local, offline), voice `en_US-ryan-high`
- **Animation:** Pillow frame renderer; Hubie expression set generated with Google Gemini (Nano Banana) from the official Hubie picture, cut out of green screen; lip sync, blinks, breathing, sway and reactions animated in code
- **Game control:** Playwright + Chromium on Xvfb; results from the site's own bet API reply
- **Streaming:** ffmpeg (libx264 + AAC) over RTMPS to Kick; voice mixed in-process, one audio chunk per video frame (no A/V drift)
- **Chat:** Kick pop-out chat feed (read) + official Kick public API (send, timeouts), httpx
- **Hosting:** Hetzner Cloud CPX32, Ubuntu 24.04
- No templates or starter kits were used.

## Future feature: community controls (built, off by default)

Streamers and mods could run things from Telegram: `/poll` starts a chat poll for Hubie's next game right now,
and `/giveaway 5 Shoutout from Hubie` runs a 5-minute giveaway where viewers type `!enter` and Hubie draws a
random winner live, with his reactions and a spoken announcement.

It is **switched off for the contest** (`HUBIE_HUMAN_CONTROLS=0`), because a live command is a human action and
the contest is about running with no humans. With it off, Hubie still runs polls himself every other segment.
When switched on, every use is logged as a `human_touch` and the Telegram bot warns that it resets the run.
Giveaway prizes are checked by the same safety filter and must be non-monetary: shoutouts, picking the next
game, a gifted Kick sub. Anything mentioning cash, crypto, bonuses, codes or credits is refused.

## Safety rules (hard-coded)

- Bets are **always $0**. The bet box is checked before every click; if the site ever reports a non-zero bet, Hubie stops betting.
- Hubie never promises or predicts wins, never mentions deposits, bonuses, codes, cashing out or money amounts.
- 18+ / demo / gamble-responsibly banner is always on screen; spoken reminders every ~10 minutes.
- Chat instructions are treated as chat, never as commands.
- Giveaways (optional, off) only allow non-monetary prizes; nothing to buy, free to enter, 18+.

## Repo layout

```
hubie/      the app
art/hubie/  Hubie expression frames (idle, 2 talking mouths, 7 moods, blink overlay)
deploy/     bootstrap.sh (server prep), hubie.service (optional systemd)
tools/      spike.py (game recon / endurance test / one-time login), find_pack.py
docs/       architecture diagram
run.sh start.sh stop.sh setup.sh update.sh bot.sh
```
