#!/usr/bin/env python3
"""
twitch-live-recorder.py
Monitors Twitch channels and records streams + live chat (SRT subtitles).

Dependencies:
    pip install streamlink

Usage:
    python twitch-live-recorder.py --channels xqc,forsen --output-dir D:\\Recordings
"""
from __future__ import annotations   # dict|None syntax on Python 3.9

import argparse
import json
import logging
import random
import re
import select
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── DEFAULTS ──────────────────────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL = 60
DEFAULT_OUTPUT_DIR     = "recordings"
DEFAULT_QUALITY        = "best"
DEFAULT_RETRIES        = 5
# ─────────────────────────────────────────────────────────────────────────────

# channel_name → {"thread": Thread, "stop": Event}
active_recordings: dict = {}
active_lock  = threading.Lock()
stop_event   = threading.Event()      # global shutdown signal
log          = logging.getLogger("twitch-recorder")


# ── LOGGER ────────────────────────────────────────────────────────────────────

def setup_logger(log_file=None):
    fmt       = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    logger    = logging.getLogger("twitch-recorder")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()          # safe to call multiple times

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)   # all INFO+ visible in terminal
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)    # _FileLogFilter does the real narrowing
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
        "[START]", "[DONE ]", "[STOP ]", "[CLEAN]", "[NEW  ]",
        "[CHAT ] Saved", "[WARN ]",
        "Ctrl+C", "stopping", "All threads", "All recordings",
        "twitch-live-recorder",   # startup banner lines
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
    Uses: streamlink --json https://www.twitch.tv/{channel}

    Returns dict with keys: channel, title, game
    """
    url = f"https://www.twitch.tv/{channel}"
    cmd = ["streamlink", "--json", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data.get("streams"):
            return None
        meta  = data.get("metadata") or {}
        title = meta.get("title") or channel
        game  = meta.get("category") or ""
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
    Thread-safe SRT subtitle file writer.
    Timecodes are relative to stream_start, so they sync with the video file.
    Display duration per message: 5 seconds (capped at next message if sooner).
    """

    def __init__(self, path: Path, stream_start: datetime):
        self._path   = path
        self._start  = stream_start
        self._index  = 1
        self._lock   = threading.Lock()
        self._fh     = open(path, "w", encoding="utf-8")

    @staticmethod
    def _fmt(td: timedelta) -> str:
        total = max(0, int(td.total_seconds()))
        h, rem = divmod(total, 3600)
        m, s   = divmod(rem, 60)
        ms     = int(td.microseconds / 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def write(self, author: str, message: str, ts: datetime):
        t_start = ts - self._start
        if t_start.total_seconds() < 0:
            t_start = timedelta(0)
        t_end = t_start + timedelta(seconds=5)

        block = (
            f"{self._index}\n"
            f"{self._fmt(t_start)} --> {self._fmt(t_end)}\n"
            f"{author}: {message}\n\n"
        )
        with self._lock:
            self._fh.write(block)
            self._fh.flush()
            self._index += 1

    def close(self):
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass


# ── CHAT CAPTURE (Twitch IRC, anonymous) ─────────────────────────────────────

def capture_chat(channel: str, srt: SrtWriter, stop_ev: threading.Event):
    """
    Connects to Twitch IRC anonymously (no OAuth needed for public channels).
    Captures PRIVMSG messages and writes them to the SrtWriter in real time.
    Automatically reconnects on disconnect until stop_ev is set.
    """
    server    = ("irc.chat.twitch.tv", 6667)
    irc_chan  = f"#{channel.lower()}"

    while not stop_ev.is_set():
        nick = f"McBot_{random.randint(10000, 99999)}"
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15)
            sock.connect(server)
            sock.send(f"NICK {nick}\r\n".encode())
            sock.send(f"USER {nick} 8 * :{nick}\r\n".encode())
            sock.send(f"JOIN {irc_chan}\r\n".encode())
            sock.setblocking(False)   # non-blocking — we poll via select()

            log.info("[CHAT ] IRC connected: %s", irc_chan)
            last_recv = time.time()
            buf = ""

            while not stop_ev.is_set():
                try:
                    # 1-second select — unblocks quickly when stop_ev is set
                    ready, _, _ = select.select([sock], [], [], 1.0)
                    if not ready:
                        # No data this second — send keepalive every 60s of silence
                        if time.time() - last_recv > 60:
                            sock.send(b"PING :tmi.twitch.tv\r\n")
                            last_recv = time.time()
                        continue

                    chunk = sock.recv(4096).decode("utf-8", errors="replace")
                    if not chunk:
                        break
                    last_recv = time.time()
                    buf += chunk

                    while "\r\n" in buf:
                        line, buf = buf.split("\r\n", 1)

                        # Keepalive
                        if line.startswith("PING"):
                            sock.send(b"PONG :tmi.twitch.tv\r\n")
                            continue

                        # Strip IRCv3 tags (@key=val;... prefix) if Twitch sends them
                        # without CAP REQ — older clients may still receive them
                        if line.startswith("@"):
                            line = re.sub(r"^@\S+ ", "", line)

                        # :nick!user@host.tmi.twitch.tv PRIVMSG #channel :message
                        m = re.match(
                            r":(\w+)!\w+@\S+\.tmi\.twitch\.tv PRIVMSG #\S+ :(.+)",
                            line
                        )
                        if m:
                            srt.write(m.group(1), m.group(2).rstrip(), datetime.now())

                except Exception as exc:
                    log.debug("[CHAT ] Recv error %s: %s", channel, exc)
                    break

        except Exception as exc:
            log.warning("[CHAT ] Connection error %s: %s", channel, exc)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        if not stop_ev.is_set():
            log.info("[CHAT ] Reconnecting IRC %s in 5s...", irc_chan)
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
    Runs in a dedicated thread. Records one Twitch stream:
      - streamlink → {timestamp}_{channel}_{title}.mkv
      - Twitch IRC  → {timestamp}_{channel}_{title}_chat.srt

    Cleans itself out of active_recordings when done.
    """
    stream_start  = datetime.now()
    timestamp     = stream_start.strftime("%Y%m%d_%H%M%S")
    safe_channel  = sanitize_filename(channel)
    safe_title    = sanitize_filename(title)

    out_dir = Path(output_dir) / safe_channel
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name  = f"{timestamp}_{safe_channel}_{safe_title}"
    video_path = out_dir / f"{base_name}.mkv"
    chat_path  = out_dir / f"{base_name}_chat.srt"
    meta_path  = out_dir / f"{base_name}_meta.txt"

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

    # ── Chat capture (parallel thread) ───────────────────────────────────────
    srt_writer  = SrtWriter(chat_path, stream_start)
    chat_stop   = threading.Event()
    chat_thread = threading.Thread(
        target=capture_chat,
        args=(channel, srt_writer, chat_stop),
        daemon=True,
        name=f"chat-{channel}",
    )
    chat_thread.start()

    # ── streamlink command ───────────────────────────────────────────────────
    cmd = ["streamlink"]
    if disable_ads:
        cmd += ["--twitch-disable-ads"]
    cmd += [
        f"https://www.twitch.tv/{channel}",
        quality,
        "--output",      str(video_path),
        "--force",                          # overwrite if file exists
        "--retry-open",  str(retries),      # retry stream open on transient errors
        # NOTE: no --retry-streams here — if the stream ends, streamlink should
        # exit so our monitor loop can detect it and start a fresh recording.
    ]

    proc = None
    try:
        log.info("[PROC ] streamlink: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd)

        # Poll until streamlink exits or we get a stop signal
        while proc.poll() is None:
            if stop_ev.wait(2):   # wakes immediately when stop_ev is set
                log.info("[STOP ] Terminating streamlink for %s", channel)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()   # must wait after kill() to reap the process
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

        log.info("[CHAT ] Saved: %s", chat_path)

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
    log.info("  twitch-live-recorder")
    log.info("  Channels : %s", ", ".join(channels))
    log.info("  Output   : %s", output_dir)
    log.info("  Interval : %ds  Quality: %s", check_interval, quality)
    log.info("  Ads skip : %s", disable_ads)
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
        description="Monitors Twitch channels and records streams + chat (SRT).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--channels", required=True,
        help="Comma-separated Twitch channel names, e.g. xqc,forsen,streamer3",
    )
    parser.add_argument("--output-dir",  default=DEFAULT_OUTPUT_DIR,
                        help="Root recording directory")
    parser.add_argument("--quality",     default=DEFAULT_QUALITY,
                        help="streamlink quality: best | 1080p60 | 720p60 | worst")
    parser.add_argument("--interval",    type=int, default=DEFAULT_CHECK_INTERVAL,
                        help="Check interval between polls [seconds]")
    parser.add_argument("--retries",     type=int, default=DEFAULT_RETRIES,
                        help="Stream open retry attempts on transient errors")
    parser.add_argument("--disable-ads", action="store_true",
                        help="Pass --twitch-disable-ads to streamlink")
    parser.add_argument("--log-file",    default=None,
                        help="Optional log file path")
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