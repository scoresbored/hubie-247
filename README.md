# Hubie, live 24/7

An AI version of Hubie, the Housebets sloth, that streams to Kick around the clock with nobody touching it.
He plays House originals in **demo mode ($0 bets)**, talks about the moments worth talking about,
chats with viewers by name, moderates chat, and restarts himself when something breaks.

> 18+ only. Demo play only, no real money is ever bet. Gamble responsibly.

## What Hubie does

| Area | How |
| --- | --- |
| On screen | Animated Hubie (14 expression frames: two talking poses, picked per line; smooth lip sync from his live voice with auto-gain and blended mouth shapes; eyes that watch the game and look at viewers when he talks; confetti on big hits and streaks, blinks, breathing and sway, in his box he just chats (no reaction faces; set HUBIE_REACTIONS=full to bring them back), while the full-body pop-out moves carry the big expressions; seasonal sky that follows UTC day and night), the live game, recent results, chat, and a permanent 18+ / demo / gamble-responsibly banner |
| Plays | Rotates every 8-15 min between House originals and strategies: Dice (1.5x / 2x / 10x / 50x), Limbo (2x-100x targets), Wheel (low/medium/high risk), Max Win, Plinko, Coinflip |
| Talks | Comments only on real moments (win streaks, long losing streaks, big hits, droughts ending, milestones), announces and recaps each segment, never leaves long dead air |
| Chat | Reads Kick chat. Hubie speaks to first-time chatters and real questions; a separate 🤖 chat bot handles text replies, moderation notices and polls. Chat picks games with polls (vote 1-3) and `!play <game>` requests. Follows, subs and gifted subs get a bot post and a spoken thank-you. Replies scale with chat speed: a quiet chat gets answered (but not the same person every line); a busy chat gets newcomers, questions and "Hubie" mentions only, capped per minute, so it never looks spammy |
| Music | Original lo-fi background loops generated in code (no third-party tracks), low volume, ducks under Hubie's voice |
| Pop-out moves | Viewers type `!dance`, `!wave`, `!flex`, `!nap`, `!hmm`, `!cheer`, `!snack`, `!lol`, `!dapper` (or `!commands` for the list) and a full-body Hubie springs up over the game to do the move, with a caption naming who asked and its own sound effect (a bloop as he pops up, then e.g. a dance groove, party horn, sad trombone, snores, munching). One at a time, short gap between moves, one per viewer per minute. New poses become new commands by adding an image to `art/hubie/body/` |
| Moderates | Offensive, vulgar or rude messages are **deleted from Kick chat and the user is muted**, before they ever reach the screen, Hubie's brain or his voice. Two layers: instant word rules (slurs, threats, sexual content, swearing, insults, with leetspeak and stretched spellings), then an AI check (Claude) for disguised or rude messages the rules miss. Mutes escalate for repeat offenders (3x, then 24h). Offensive usernames are never shown or spoken. Also removes links, promo/referral spam, caps and repeat spam; answers "I'm under 18" with the 18+ rule |
| Stays safe | Every line passes a filter that blocks win promises, deposit/bonus/code talk and money amounts; every bet is checked to be $0 before the click and again in the site's reply |
| Runs itself | Restarts crashed parts on its own: the stream (ffmpeg), the game browser, the chat reader, the whole app; benches a game for an hour if it keeps failing |

## Personality

Hubie's voice is modelled on how he actually posts as @HousebetsHubie on X: deadpan, all lowercase, unbothered,
laziness as a flex, dry questions that roast gambler logic, honest about RTP and randomness, precise numbers as
punchlines, scammers ignored until they get bored, and the occasional affectionate dig at Porchy, the founder
(never about money, payments or bonuses). Every line is freshly written by the model under hard safety rules.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the agent and tool diagram.

```
game.py   plays a House original at $0, reads each result from the site's own bet reply
games.py  the games + strategies Hubie rotates through
brain.py  Claude: Hubie's personality, moment commentary, chat replies
safety.py filter every spoken/posted line; responsible-gambling lines
voice.py  local text-to-speech + mouth-movement envelope
scene.py  draws every frame (game, Hubie, results, chat, banner)
sfx.py    original synthesized sound effects (pop-out moves, confetti, rain), mixed over voice + music
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
- **Voice:** Piper TTS (local, offline), voice `en_GB-northern_english_male-medium`, slowed to a relaxed sloth pace (`HUBIE_VOICE_SPEED`, optional `HUBIE_VOICE_PITCH`)
- **Animation:** Pillow frame renderer; Hubie expression set generated with Google Gemini (Nano Banana) from the official Hubie picture, cut out of green screen; lip sync, blinks, breathing, sway and reactions animated in code
- **Game control:** Playwright + Chromium on Xvfb; results from the site's own bet API reply
- **Streaming:** ffmpeg (libx264 + AAC) over RTMPS to Kick; voice mixed in-process, one audio chunk per video frame (no A/V drift)
- **Chat:** Kick pop-out chat feed (read) + official Kick public API (send, timeouts), httpx
- **Pose and animation generation:** xAI Grok Imagine (image editing with reference images, image-to-video)
- **Hosting:** Hetzner Cloud CPX32, Ubuntu 24.04
- No templates or starter kits were used.

## Making new moves with Grok (tools/grok_pose.py)

New pop-out moves are generated with xAI's Grok Imagine API, using Hubie's existing poses as the character
reference, then cut out of the green screen and sized automatically:

    python tools/grok_pose.py new moonwalk "smooth moonwalk, one foot sliding back"   # 3 versions + preview sheet
    python tools/grok_pose.py install moonwalk 2 "{u} asked for a moonwalk"         # becomes !moonwalk
    python tools/grok_pose.py animate dance "bouncy dance, stepping side to side"    # still pose -> animated frames

`animate` turns a still move into a short Grok video, cuts every frame out and the pop-out plays it as a looping
animation. Needs `XAI_API_KEY` in `.env`; a pose costs a few cents.

## Future upgrades: better animation and voice

Hubie's look and voice are built to be swapped without touching the rest of the system, so the
stream can grow into a fully official Hubie:

**Animation**
- **Official art pack:** every expression and pose is a drop-in image (`art/hubie/` for the talking
  frames and moods, `art/hubie/body/` for full-body moves). Swap in official Housebets artwork and
  the cutout tool (`tools/cutout_body.py`) prepares it; nothing else changes.
- **Rigged, fully animated Hubie:** replace the frame-based avatar with a rigged model (Live2D,
  Spine or Rive) for smooth head turns, real phoneme lip shapes and in-between motion. The avatar
  is one module (`hubie/avatar.py`) with a simple interface, so the renderer, moods and lip-sync
  signal stay the same.
- **More moves and moods:** each new full-body pose image becomes a new chat command
  automatically; more talking poses and phoneme mouth shapes (A/E/O/M/F) would sharpen the lip sync.
- **Seasonal outfits:** the monthly theme system can also switch Hubie's outfit (winter scarf in
  December, party hat in January) using the same image drop-in.

**Voice**
- **Hubie's official voice:** the voice is one setting (`HUBIE_VOICE_MODEL`). A custom Piper voice
  trained on an official Hubie voice actor, or a premium neural voice service (e.g. ElevenLabs),
  plugs straight in; lip sync already follows whatever audio comes out.
- **Expressive delivery:** happy lines already open into his big grin; the same "happy" flag can
  drive a livelier speaking style (faster, brighter) for wins and a slower, sleepier one for late
  nights.

**Interaction**
- Community controls for mods (`/poll`, `/giveaway`), already built and switched off for the
  contest (see below).

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
- Chat instructions are treated as chat, never as commands. Rude, vulgar or hateful messages are deleted and never read out.
- Giveaways (optional, off) only allow non-monetary prizes; nothing to buy, free to enter, 18+.

## Repo layout

```
hubie/      the app
art/hubie/  Hubie expression frames (2 talking poses with 3 and 4 mouth shapes, 7 moods, blink overlays)
deploy/     bootstrap.sh (server prep), hubie.service (optional systemd)
tools/      spike.py (game recon / endurance test / one-time login), find_pack.py
docs/       architecture diagram
run.sh start.sh stop.sh setup.sh update.sh bot.sh
```
