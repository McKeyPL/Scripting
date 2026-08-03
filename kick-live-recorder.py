#!/usr/bin/env python3
"""
kick-live-recorder.py
Monitors Kick channels and records streams + live chat (SRT subtitles).

Dependencies:
    Checked and updated by start-kick-recorder.ps1

Usage:
    python kick-live-recorder.py --channels ctsg,jellysketch,perrydotto,ninuschk --output-dir D:\\Recordings
"""

from __future__ import annotations  # dict|None syntax on Python 3.9

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
import websocket

# ── DEFAULTS ──────────────────────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL = 60
DEFAULT_OUTPUT_DIR = "recordings"
DEFAULT_QUALITY = "best"
DEFAULT_RETRIES = 5
# ─────────────────────────────────────────────────────────────────────────────

# channel_name → {"thread": Thread, "stop": Event}
active_recordings: dict = {}
active_lock = threading.Lock()
stop_event = threading.Event()  # global shutdown signal
log = logging.getLogger("kick-recorder")


# ── LOGGER ────────────────────────────────────────────────────────────────────


def setup_logger(log_file=None):
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("kick-recorder")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # safe to call multiple times

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)  # all INFO+ visible in terminal
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)  # _FileLogFilter does the real narrowing
        fh.setFormatter(fmt)
        fh.addFilter(_FileLogFilter())
        logger.addHandler(fh)

    return logger


class _FileLogFilter(logging.Filter):
    """
    File log: WARNING and above always pass.
    INFO: only lifecycle events worth keeping long-term.
    Routine poll noise (Checking / Offline / Already recording / Active recordings)
    is suppressed in the file but still visible in the console.
    """

    _KEEP = (
        "[START]",
        "[DONE ]",
        "[STOP ]",
        "[CLEAN]",
        "[NEW  ]",
        "[CHAT ] Saved",
        "[WARN ]",
        "Ctrl+C",
        "stopping",
        "All threads",
        "All recordings",
        "kick-live-recorder",  # startup banner lines
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        return any(k in msg for k in self._KEEP)


# ── HELPERS ───────────────────────────────────────────────────────────────────


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in Windows/Linux filenames."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:150].strip("_. ")


# ── STREAM CHECK ──────────────────────────────────────────────────────────────


def check_channel_live(channel: str) -> dict | None:
    """
    Returns stream metadata dict if the channel is currently live, else None.
    Uses: streamlink --json https://kick.com/{channel}

    Returns dict with keys: channel, title, game
    """
    url = f"https://kick.com/{channel}"
    cmd = ["streamlink", "--json", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data.get("streams"):
            return None
        meta = data.get("metadata") or {}
        title = meta.get("title") or channel
        game = meta.get("category") or ""
        return {"channel": channel, "title": title, "game": game}
    except subprocess.TimeoutExpired:
        log.warning("[CHECK] Timeout checking channel: %s", channel)
    except json.JSONDecodeError:
        log.debug("[CHECK] JSON decode error for %s", channel)
    except Exception as exc:
        log.debug("[CHECK] Error for %s: %s", channel, exc)
    return None


# ── SRT WRITER ────────────────────────────────────────────────────────────────


class SrtWriter:
    """
    Thread-safe dual SRT subtitle writer.
    Timecodes and indexes are identical in the plain and colored files.
    Timecodes are relative to stream_start, so they sync with the video file.
    Display duration per message: 5 seconds.
    """

    BADGE_LABELS = {
        "moderator": "[MOD]",
        "vip": "[VIP]",
        "og": "[OG]",
        "subscriber": "[SUB]",
        "founder": "[FND]",
        "broadcaster": "[BC]",
    }

    def __init__(self, plain_path: Path, colored_path: Path, stream_start: datetime):
        self._plain_path = plain_path
        self._colored_path = colored_path
        self._start = stream_start
        self._index = 1
        self._lock = threading.Lock()
        self._fh_plain = open(plain_path, "w", encoding="utf-8")
        self._fh_colored = open(colored_path, "w", encoding="utf-8")

    @staticmethod
    def _fmt(td: timedelta) -> str:
        total = max(0, int(td.total_seconds()))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        ms = int(td.microseconds / 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def write(
        self,
        author: str,
        message: str,
        ts: datetime,
        color: str | None = None,
        badges: list | None = None,
    ):
        t_start = ts - self._start
        if t_start.total_seconds() < 0:
            t_start = timedelta(0)
        t_end = t_start + timedelta(seconds=5)

        safe_color = (
            color if color and re.fullmatch(r"#[0-9A-Fa-f]{6}", color) else "#FFFFFF"
        )
        badge_prefix = "".join(
            self.BADGE_LABELS.get(str(badge).lower(), "") for badge in (badges or [])
        )
        if badge_prefix:
            badge_prefix += " "

        plain_text = f"{author}: {message}"
        colored_text = (
            f'{badge_prefix}<font color="{safe_color}">{author}</font>: {message}'
        )

        with self._lock:
            header = f"{self._index}\n" f"{self._fmt(t_start)} --> {self._fmt(t_end)}\n"
            self._fh_plain.write(f"{header}{plain_text}\n\n")
            self._fh_colored.write(f"{header}{colored_text}\n\n")
            self._fh_plain.flush()
            self._fh_colored.flush()
            self._index += 1

    def close(self):
        with self._lock:
            for fh in (self._fh_plain, self._fh_colored):
                try:
                    fh.close()
                except Exception:
                    pass


# ── CHAT CAPTURE (Kick Pusher WebSocket, anonymous) ───────────────────────────


class KickChatClient:
    """Small anonymous client for Kick's public Pusher chat connection."""

    CHATROOM_URL = "https://kick.com/api/v2/channels/{channel}/chatroom"
    PUSHER_URL = (
        "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
        "?protocol=7&client=js&version=8.4.0-rc2&flash=false"
    )
    CHANNEL_PATTERN = "chatrooms.{chatroom_id}.v2"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )

    def __init__(self, channel: str, stop_ev: threading.Event):
        self.channel = channel
        self.stop_ev = stop_ev
        self.chatroom_id: int | None = None
        self.ws = None

    def resolve_chatroom_id(self) -> int | None:
        """Retry until Kick returns a chatroom ID or shutdown is requested."""
        url = self.CHATROOM_URL.format(channel=self.channel)
        while not self.stop_ev.is_set():
            try:
                response = requests.get(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": self.USER_AGENT,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                chatroom_id = response.json().get("id")
                if chatroom_id is None:
                    raise ValueError("response does not contain chatroom id")
                self.chatroom_id = int(chatroom_id)
                return self.chatroom_id
            except (
                requests.RequestException,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                log.warning(
                    "[CHAT ] Could not resolve chatroom for %s: %s",
                    self.channel,
                    exc,
                )
                if self.stop_ev.wait(5):
                    break
        return None

    def connect(self):
        if self.chatroom_id is None:
            raise RuntimeError("chatroom_id has not been resolved")

        self.ws = websocket.create_connection(
            self.PUSHER_URL,
            timeout=15,
            origin="https://kick.com",
        )
        self.ws.settimeout(1)
        subscription = {
            "event": "pusher:subscribe",
            "data": {
                "auth": "",
                "channel": self.CHANNEL_PATTERN.format(chatroom_id=self.chatroom_id),
            },
        }
        self.ws.send(json.dumps(subscription))
        return self.ws

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


def _parse_kick_chat_message(payload: dict) -> dict | None:
    """Map a Kick payload to the recorder's platform-neutral chat structure."""
    sender = payload.get("sender") or {}
    identity = sender.get("identity") or {}
    author = sender.get("username")
    message = payload.get("content")

    if not author or message is None:
        return None

    badges = []
    for badge in identity.get("badges") or []:
        if isinstance(badge, dict) and badge.get("type"):
            badges.append(str(badge["type"]).lower())

    return {
        "author": str(author),
        "message": str(message),
        "color": identity.get("color"),
        "badges": badges,
        "timestamp": datetime.now(),
    }


def capture_chat_kick(channel: str, srt: SrtWriter, stop_ev: threading.Event):
    """
    Captures public Kick chat via Pusher and writes synchronized plain/colored SRT.
    Automatically reconnects on disconnect until stop_ev is set.
    """
    client = KickChatClient(channel, stop_ev)
    if client.resolve_chatroom_id() is None:
        log.info("[CHAT ] Thread stopped: %s", channel)
        return

    pusher_channel = client.CHANNEL_PATTERN.format(chatroom_id=client.chatroom_id)

    while not stop_ev.is_set():
        try:
            ws = client.connect()
            log.info("[CHAT ] WS connected: %s", pusher_channel)

            while not stop_ev.is_set():
                try:
                    raw = ws.recv()
                    if not raw:
                        break

                    event = json.loads(raw)
                    event_name = event.get("event")

                    if event_name == "pusher:ping":
                        ws.send(
                            json.dumps(
                                {"event": "pusher:pong", "data": event.get("data", {})}
                            )
                        )
                        continue

                    if event_name != r"App\Events\ChatMessageEvent":
                        continue

                    payload = event.get("data")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if not isinstance(payload, dict):
                        continue

                    chat_message = _parse_kick_chat_message(payload)
                    if chat_message:
                        srt.write(
                            chat_message["author"],
                            chat_message["message"],
                            chat_message["timestamp"],
                            color=chat_message["color"],
                            badges=chat_message["badges"],
                        )

                except websocket.WebSocketTimeoutException:
                    continue
                except (json.JSONDecodeError, TypeError) as exc:
                    log.debug("[CHAT ] Invalid event %s: %s", channel, exc)
                except Exception as exc:
                    log.debug("[CHAT ] Recv error %s: %s", channel, exc)
                    break

        except Exception as exc:
            log.warning("[CHAT ] Connection error %s: %s", channel, exc)
        finally:
            client.close()

        if not stop_ev.is_set():
            log.info("[CHAT ] Reconnecting WS %s in 5s...", pusher_channel)
            stop_ev.wait(5)

    log.info("[CHAT ] Thread stopped: %s", channel)


# ── STREAM RECORDER ───────────────────────────────────────────────────────────


def record_stream(
    channel: str,
    title: str,
    game: str,
    output_dir: str,
    quality: str,
    disable_ads: bool,
    retries: int,
    stop_ev: threading.Event,
):
    """
    Runs in a dedicated thread. Records one Kick stream:
      - streamlink → {timestamp}_{channel}_{title}.mkv
      - Kick chat  → plain and colored {timestamp}_{channel}_{title}_chat*.srt

    Cleans itself out of active_recordings when done.
    """
    stream_start = datetime.now()
    timestamp = stream_start.strftime("%Y%m%d_%H%M%S")
    safe_channel = sanitize_filename(channel)
    safe_title = sanitize_filename(title)

    out_dir = Path(output_dir) / safe_channel
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{timestamp}_{safe_channel}_{safe_title}"
    video_path = out_dir / f"{base_name}.mkv"
    chat_path = out_dir / f"{base_name}_chat.srt"
    chat_path_colored = out_dir / f"{base_name}_chat_colored.srt"
    meta_path = out_dir / f"{base_name}_meta.txt"

    # Save stream metadata to a sidecar text file
    with open(meta_path, "w", encoding="utf-8") as mf:
        mf.write(f"Channel   : {channel}\n")
        mf.write(f"Title     : {title}\n")
        mf.write(f"Game      : {game}\n")
        mf.write(f"Started   : {stream_start.isoformat()}\n")
        mf.write(f"Quality   : {quality}\n")

    log.info("[START] %s", channel)
    log.info("        Title : %s", title)
    log.info("        Game  : %s", game)
    log.info("        Video : %s", video_path)
    log.info("        Chat  : %s", chat_path)
    log.info("        Color : %s", chat_path_colored)

    # ── Chat capture (parallel thread) ───────────────────────────────────────
    srt_writer = SrtWriter(chat_path, chat_path_colored, stream_start)
    chat_stop = threading.Event()
    chat_thread = threading.Thread(
        target=capture_chat_kick,
        args=(channel, srt_writer, chat_stop),
        daemon=True,
        name=f"chat-{channel}",
    )
    chat_thread.start()

    # ── streamlink command ───────────────────────────────────────────────────
    cmd = ["streamlink"]
    if disable_ads:
        log.warning("[WARN ] --disable-ads is ignored for Kick.")
    cmd += [
        f"https://kick.com/{channel}",
        quality,
        "--output",
        str(video_path),
        "--force",  # overwrite if file exists
        "--retry-open",
        str(retries),  # retry stream open on transient errors
        # NOTE: no --retry-streams here — if the stream ends, streamlink should
        # exit so our monitor loop can detect it and start a fresh recording.
    ]

    proc = None
    try:
        log.info("[PROC ] streamlink: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)

        # Poll until streamlink exits or we get a stop signal
        while proc.poll() is None:
            if stop_ev.wait(2):  # wakes immediately when stop_ev is set
                log.info("[STOP ] Terminating streamlink for %s", channel)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()  # must wait after kill() to reap the process
                break

        rc = proc.returncode if proc else -1
        if stop_ev.is_set():
            log.info("[STOP ] Recording stopped: %s | rc=%s", channel, rc)
        elif rc == 0:
            log.info("[DONE ] %s | stream ended normally", channel)
        else:
            log.warning("[WARN ] %s | streamlink exit code=%d", channel, rc)

    except Exception as exc:
        log.error("[ERR  ] %s: %s", channel, exc)
    finally:
        # Always stop chat capture and close SRT writer
        chat_stop.set()
        chat_thread.join(timeout=10)
        srt_writer.close()

        # Write end time to meta file
        try:
            with open(meta_path, "a", encoding="utf-8") as mf:
                mf.write(f"Ended     : {datetime.now().isoformat()}\n")
        except Exception:
            pass

        log.info("[CHAT ] Saved plain: %s", chat_path)
        log.info("[CHAT ] Saved colored: %s", chat_path_colored)

        with active_lock:
            active_recordings.pop(channel, None)

        log.info("[CLEAN] Recording cleaned up: %s", channel)


# ── MONITOR LOOP ──────────────────────────────────────────────────────────────


def monitor_loop(
    channels: list,
    check_interval: int,
    output_dir: str,
    quality: str,
    disable_ads: bool,
    retries: int,
):
    log.info("=" * 60)
    log.info("  kick-live-recorder")
    log.info("  Channels : %s", ", ".join(channels))
    log.info("  Output   : %s", output_dir)
    log.info("  Interval : %ds  Quality: %s", check_interval, quality)
    log.info("  Ads skip : unsupported on Kick (argument=%s)", disable_ads)
    log.info("  Press Ctrl+C to stop.")
    log.info("=" * 60)

    while not stop_event.is_set():
        log.info("Checking channels: %s", ", ".join(channels))

        for channel in channels:
            with active_lock:
                already = channel in active_recordings

            if already:
                log.info("[CONT ] Already recording: %s", channel)
                continue

            meta = check_channel_live(channel)

            if meta:
                per_stop = threading.Event()
                t = threading.Thread(
                    target=record_stream,
                    args=(
                        channel,
                        meta["title"],
                        meta["game"],
                        output_dir,
                        quality,
                        disable_ads,
                        retries,
                        per_stop,
                    ),
                    daemon=True,
                    name=f"rec-{channel}",
                )
                with active_lock:
                    active_recordings[channel] = {"thread": t, "stop": per_stop}
                t.start()
                log.info("[NEW  ] %s — %s [%s]", channel, meta["title"], meta["game"])
            else:
                log.info("[OFF  ] Offline: %s", channel)

        with active_lock:
            n = len(active_recordings)
        if n:
            log.info("Active recordings: %d", n)

        stop_event.wait(check_interval)

    # ── Shutdown: stop all recordings gracefully ──────────────────────────────
    log.info("Stopping all active recordings...")
    with active_lock:
        items = list(active_recordings.values())
    for item in items:
        item["stop"].set()
    for item in items:
        item["thread"].join(timeout=30)
    log.info("All recordings finished.")


# ── MAIN ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Monitors Kick channels and records streams + chat "
            "(plain and colored SRT)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--channels",
        required=True,
        help="Comma-separated Kick channel names, e.g. xqc,adinross,streamer3",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help="Root recording directory"
    )
    parser.add_argument(
        "--quality",
        default=DEFAULT_QUALITY,
        help="streamlink quality: best | 1080p60 | 720p60 | worst",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_CHECK_INTERVAL,
        help="Check interval between polls [seconds]",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="Stream open retry attempts on transient errors",
    )
    parser.add_argument(
        "--disable-ads",
        action="store_true",
        help="Deprecated compatibility option; ignored for Kick",
    )
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    args = parser.parse_args()

    global log
    log = setup_logger(args.log_file)

    channels = [c.strip().lower() for c in args.channels.split(",") if c.strip()]
    if not channels:
        log.error("No channels specified. Use --channels ch1,ch2")
        sys.exit(1)

    try:
        monitor_loop(
            channels=channels,
            check_interval=args.interval,
            output_dir=args.output_dir,
            quality=args.quality,
            disable_ads=args.disable_ads,
            retries=args.retries,
        )
    except KeyboardInterrupt:
        log.info("Ctrl+C — stopping...")
        stop_event.set()
        with active_lock:
            items = list(active_recordings.values())
        for item in items:
            item["stop"].set()
        for item in items:
            item["thread"].join(timeout=30)
        log.info("All threads finished. Bye.")


if __name__ == "__main__":
    main()
