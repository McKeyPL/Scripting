#!/usr/bin/env python3
# =============================================================================
#  yt-live-recorder.py
#  Monitors a YouTube channel and automatically records all live streams
#  in separate threads. After recording finishes it returns to monitoring.
# =============================================================================
#
# ██ PREREQUISITES (Windows Server) ██
#
#  1. Python 3.10+
#     Download: https://www.python.org/downloads/windows/
#     IMPORTANT: select "Add Python to PATH" during installation
#     Verify: python --version
#
#  2. yt-dlp
#     pip install -U yt-dlp
#     -- OR --
#     Download yt-dlp.exe and place it in the script folder:
#     https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
#     Verify: yt-dlp --version
#
#  3. Deno (required to solve YouTube n-challenge since ~2025.11)
#     winget install DenoLand.Deno
#     -- OR --
#     Download deno.exe and place it in the same folder as yt-dlp:
#     https://github.com/denoland/deno/releases/latest
#     Verify: deno --version
#
#  4. ffmpeg (required to merge bestvideo+bestaudio -> mkv/mp4)
#     winget install Gyan.FFmpeg
#     -- OR manual install --
#     Download: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
#     Extract, add the \bin folder to the system PATH:
#       Settings -> System -> Environment variables -> Path -> New
#     Verify: ffmpeg -version
#
# ██ QUICK START ██
#
#  Open PowerShell in the folder with the script and run:
#
#    python yt-live-recorder.py `
#      --channel-url "https://www.youtube.com/@NazwaKanalu" `
#      --cookies-browser firefox `
#      --output-dir "D:\Recordings" `
#      --log-file recorder.log
#
#  Minimal version (all defaults):
#    python yt-live-recorder.py --channel-url "https://www.youtube.com/@NazwaKanalu"
#
# ██ ALL OPTIONS ██
#
#  --channel-url      YouTube channel URL (required)
#                     Example: https://www.youtube.com/@ChannelName
#                     Example: https://www.youtube.com/channel/UCxxxxxx
#
#  --interval         Live check interval [seconds], default: 60
#
#  --output-dir       Recording output directory, default: ./recordings
#
#  --format           yt-dlp format, default: bestvideo+bestaudio/best
#                     Examples:
#                       "bestvideo[height<=1080]+bestaudio/best"  <- max 1080p
#                       "best"                                    <- best single stream
#
#  --merge-format     Output format after video+audio merge
#                     Default: mkv  |  Other options: mp4, ts
#
#  --retries          Number of retry attempts on network errors, default: 5
#
#  --cookies-browser  Get cookies from browser (YT account)
#                     Options: firefox | chrome | edge | chromium
#                     NOTE: the browser must be installed locally
#
#  --cookies-file     Alternatively: path to cookies.txt (Netscape format)
#                     Export with the "Get cookies.txt LOCALLY" extension
#
#  --log-file         Optional log file (default is stdout only)
#
# ██ RUNNING AS A WINDOWS SERVICE (Task Scheduler) ██
#
#  To start the script automatically after server restart:
#
#  1. Open "Task Scheduler" (taskschd.msc)
#  2. Action -> Create Task
#  3. General:
#       Name: yt-live-recorder
#       Check: "Run whether user is logged on or not"
#       Check: "Run with highest privileges"
#  4. Triggers -> New:
#       Begin the task: "At startup"
#  5. Actions -> New:
#       Program:   python
#       Arguments: "C:\path\to\yt-live-recorder.py"
#                  --channel-url "https://www.youtube.com/@Channel"
#                  --cookies-browser firefox
#                  --output-dir "D:\Recordings"
#                  --log-file "D:\Recordings\recorder.log"
#       Start in:  C:\path\to\script
#  6. Settings: check "Restart the task if it fails"
#
# ██ RUNNING AS A SERVICE (NSSM - recommended for servers) ██
#
#  NSSM makes it easy to manage the script as a real Windows service.
#
#  1. Download NSSM: https://nssm.cc/download
#  2. Open PowerShell as Administrator in the folder with nssm.exe:
#
#     .\nssm.exe install yt-live-recorder
#
#  3. In the NSSM window:
#       Path:            C:\Python312\python.exe
#       Startup dir:     C:\path\to\script
#       Arguments:       yt-live-recorder.py
#                        --channel-url "https://www.youtube.com/@Channel"
#                        --cookies-browser firefox
#                        --output-dir "D:\Recordings"
#                        --log-file "D:\Recordings\recorder.log"
#
#  4. "I/O" tab: set stdout and stderr to a log file if desired
#  5. Click "Install service"
#  6. Run: .\nssm.exe start yt-live-recorder
#     Verify: .\nssm.exe status yt-live-recorder
#
# ██ USEFUL DIAGNOSTIC COMMANDS ██
#
#  # Check if the channel has a live stream (manual test)
#  yt-dlp --flat-playlist --match-filter "is_live" --print "%(id)s %(title)s" ^
#    "https://www.youtube.com/@ChannelName"
#
#  # List available formats for a stream
#  yt-dlp --list-formats "https://www.youtube.com/watch?v=VIDEO_ID"
#
#  # Update yt-dlp
#  yt-dlp -U
#  # or via pip:
#  pip install -U yt-dlp
#
# =============================================================================

import argparse
import logging
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_CHECK_INTERVAL = 60
DEFAULT_OUTPUT_DIR = "recordings"
DEFAULT_FORMAT = "bestvideo+bestaudio/best"
DEFAULT_MERGE_FORMAT = "mkv"
DEFAULT_RETRIES = 5

active_recordings: dict = {}
active_lock = threading.Lock()
stop_event = threading.Event()
log = logging.getLogger("yt-recorder")


def setup_logger(log_file=None):
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    return logging.getLogger("yt-recorder")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:200].strip()


def ytdlp_base_cmd(cookies_browser=None, cookies_file=None) -> list:
    cmd = ["yt-dlp"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    elif cookies_file:
        cmd += ["--cookies", cookies_file]
    return cmd


def fetch_live_streams(channel_url, cookies_browser, cookies_file) -> list:
    """
    Fetches the list of videos from the channel, then checks each one
    individually to see if it is currently live (live_status=is_live).
    """
    # Step 1: fetch only IDs from the playlist (fast, without metadata)
    cmd_list = ytdlp_base_cmd(cookies_browser, cookies_file) + [
        "--flat-playlist",
        "--print",
        "%(id)s\t%(title)s",
        "--no-warnings",
        "--playlist-end",
        "5",  # check at most the 5 newest
        channel_url,
    ]
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        log.warning("Timeout while fetching video list.")
        return []
    except Exception as exc:
        log.error("Error fetching list: %s", exc)
        return []

    candidates = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "\t" in line:
            vid_id, title = line.split("\t", 1)
            candidates.append({"id": vid_id.strip(), "title": title.strip()})

    if not candidates:
        return []

    # Step 2: check live_status for each video individually
    streams = []
    for c in candidates:
        url = f"https://www.youtube.com/watch?v={c['id']}"
        cmd_check = ytdlp_base_cmd(cookies_browser, cookies_file) + [
            "--no-download",
            "--print",
            "%(live_status)s",
            "--no-warnings",
            url,
        ]
        try:
            r = subprocess.run(cmd_check, capture_output=True, text=True, timeout=30)
            status = r.stdout.strip()
            log.debug("[%s] live_status=%s  %s", c["id"], status, c["title"])
            if status == "is_live":
                streams.append(c)
        except subprocess.TimeoutExpired:
            log.warning("Timeout checking [%s]", c["id"])
        except Exception as exc:
            log.error("Error checking [%s]: %s", c["id"], exc)

    return streams


def record_stream(
    video_id, title, output_dir, fmt, merge_fmt, retries, cookies_browser, cookies_file
):
    """Runs in a separate thread and records one live stream to a file."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    safe_title = sanitize_filename(title)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_tpl = str(out_dir / f"{timestamp}_{safe_title}_%(id)s.%(ext)s")

    cmd = ytdlp_base_cmd(cookies_browser, cookies_file) + [
        "-f",
        fmt,
        "--merge-output-format",
        merge_fmt,
        "--retries",
        str(retries),
        "--fragment-retries",
        str(retries),
        "--retry-sleep",
        "5",
        "--no-warnings",
        "--live-from-start",
        "-o",
        output_tpl,
        url,
    ]

    log.info("[START] [%s] %s", video_id, title)
    try:
        # Try 1: with --live-from-start (full VOD from the beginning)
        proc = subprocess.run(cmd, timeout=None)

        if proc.returncode != 0 and not stop_event.is_set():
            # Try 2: without --live-from-start (record from now)
            log.warning("[RETRY] Retrying without --live-from-start: [%s]", video_id)
            cmd_fallback = [c for c in cmd if c != "--live-from-start"]
            proc = subprocess.run(cmd_fallback, timeout=None)

        if proc.returncode == 0:
            log.info("[DONE ] [%s] %s", video_id, title)
        elif stop_event.is_set():
            log.info("[STOP ] Recording stopped: [%s] %s", video_id, title)
        else:
            log.warning(
                "[WARN ] yt-dlp exit code %d for [%s]", proc.returncode, video_id
            )
    except Exception as exc:
        log.error("[ERR  ] [%s]: %s", video_id, exc)
    finally:
        with active_lock:
            active_recordings.pop(video_id, None)


def monitor_loop(
    channel_url,
    check_interval,
    output_dir,
    fmt,
    merge_fmt,
    retries,
    cookies_browser,
    cookies_file,
):
    log.info("=" * 60)
    log.info("  yt-live-recorder")
    log.info("  Channel : %s", channel_url)
    log.info("  Output  : %s", output_dir)
    log.info("  Interval: %ds  Format: %s -> .%s", check_interval, fmt, merge_fmt)
    log.info("  Press Ctrl+C to stop.")
    log.info("=" * 60)

    while not stop_event.is_set():
        log.info("Checking live streams...")
        streams = fetch_live_streams(channel_url, cookies_browser, cookies_file)

        with active_lock:
            currently = set(active_recordings.keys())

        for stream in streams:
            vid = stream["id"]
            if vid not in currently:
                t = threading.Thread(
                    target=record_stream,
                    args=(
                        vid,
                        stream["title"],
                        output_dir,
                        fmt,
                        merge_fmt,
                        retries,
                        cookies_browser,
                        cookies_file,
                    ),
                    daemon=True,
                    name=f"rec-{vid}",
                )
                with active_lock:
                    active_recordings[vid] = t
                t.start()
                log.info("[NEW  ] Thread started for [%s] %s", vid, stream["title"])
            else:
                log.info("[CONT ] Already recording: [%s] %s", vid, stream["title"])

        if not streams:
            log.info("No active streams.")

        with active_lock:
            n = len(active_recordings)
        if n:
            log.info("Active recordings: %d", n)

        stop_event.wait(check_interval)

    log.info("Stopped. Waiting for all threads to finish...")


def main():
    parser = argparse.ArgumentParser(
        description="Monitors a YouTube channel and records live streams in multiple threads.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--channel-url", required=True, help="YouTube channel URL")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_CHECK_INTERVAL,
        help="Check interval [s]",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help="Recording output directory"
    )
    parser.add_argument("--format", default=DEFAULT_FORMAT, help="yt-dlp format")
    parser.add_argument(
        "--merge-format",
        default=DEFAULT_MERGE_FORMAT,
        help="Output format: mkv | mp4 | ts",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="Number of retry attempts for network errors",
    )
    parser.add_argument(
        "--cookies-browser", default=None, help="firefox | chrome | edge | chromium"
    )
    parser.add_argument(
        "--cookies-file", default=None, help="Path to cookies.txt (Netscape format)"
    )
    parser.add_argument("--log-file", default=None, help="Log file (optional)")
    args = parser.parse_args()

    global log
    log = setup_logger(args.log_file)

    try:
        monitor_loop(
            channel_url=args.channel_url,
            check_interval=args.interval,
            output_dir=args.output_dir,
            fmt=args.format,
            merge_fmt=args.merge_format,
            retries=args.retries,
            cookies_browser=args.cookies_browser,
            cookies_file=args.cookies_file,
        )
    except KeyboardInterrupt:
        log.info("Ctrl+C - stopping...")
        stop_event.set()
        with active_lock:
            threads = list(active_recordings.values())
        for t in threads:
            t.join()
        log.info("All threads finished.")


if __name__ == "__main__":
    main()
