# Agents and tools

```mermaid
flowchart LR
    subgraph Server["Hetzner server (Ubuntu 24.04)"]
        direction LR
        DIR["Director<br/>main.py<br/>segments, moments,<br/>no dead air, RG reminders"]
        GAME["Game agent<br/>game.py + games.py<br/>Playwright + Chromium"]
        BRAIN["Brain<br/>brain.py<br/>Claude Haiku 5.5"]
        SAFE["Safety filter<br/>safety.py"]
        VOICE["Voice<br/>voice.py<br/>Piper TTS"]
        SCENE["Scene renderer<br/>scene.py + avatar.py<br/>Pillow, animated Hubie"]
        STREAM["Streamer<br/>stream.py<br/>ffmpeg"]
        CHAT["Chat agent<br/>kick.py + chatmod.py"]
        SUP["Supervisor<br/>run.sh restart loop<br/>+ per-part auto-restart"]
    end
    HB["housebets.com<br/>House originals<br/>(demo, $0 bets)"]
    ANTH["Anthropic API"]
    KICK["Kick<br/>RTMPS ingest + chat + API"]

    GAME -- "clicks Bet ($0 checked)" --> HB
    HB -- "bet reply JSON (result)" --> GAME
    HB -- "screencast frames" --> GAME
    GAME -- "round events" --> DIR
    GAME -- "game video" --> SCENE
    DIR -- "moment / segment prompts" --> BRAIN
    BRAIN <--> ANTH
    BRAIN -- "line" --> SAFE
    SAFE -- "approved line" --> VOICE
    VOICE -- "audio + mouth envelope" --> SCENE
    VOICE -- "audio" --> STREAM
    SCENE -- "720p30 frames" --> STREAM
    STREAM -- "RTMPS" --> KICK
    KICK -- "chat messages" --> CHAT
    CHAT -- "timeouts / replies" --> KICK
    CHAT -- "questions, newcomers" --> BRAIN
    SUP -. "restarts" .-> STREAM
    SUP -. "restarts" .-> GAME
    SUP -. "restarts" .-> CHAT
```

**Round flow:** the game agent checks the bet is $0, clicks Bet, and reads the result from the site's own reply.
The director decides if the round is a *moment* (streak, big hit, drought, milestone). Only then does the brain
write a line, which must pass the safety filter before Hubie says it.

**Chat flow:** messages are moderated first (links, promo spam, slurs, spam), then answered in text. Only
first-time chatters and real questions get a spoken reply, rate-limited so the game stays the focus.

**Recovery:** ffmpeg, the game browser and the chat reader each restart on failure; a game that fails 3 times in
a row is benched for an hour; `run.sh` restarts the whole app if it exits.
