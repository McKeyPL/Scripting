# Scripting

A personal collection of automation scripts for sysadmin, networking, and stream recording tasks.

---

## 🔴 twitch-live-recorder.py

Monitors one or more Twitch channels and automatically records streams to `.mkv` files using [streamlink](https://streamlink.github.io/). Simultaneously captures live IRC chat and saves it as an `.srt` subtitle file synced to the recording. Runs in parallel threads — one per channel — with graceful shutdown on Ctrl+C.

**Dependencies:** `pip install streamlink`

```bash
python twitch-live-recorder.py --channels xqc,forsen --output-dir D:\Recordings
```

---

## ▶️ start-twitch-recorder.ps1

PowerShell launcher for `twitch-live-recorder.py` on Windows (or Linux with pwsh). Checks for required tools (Python, streamlink, ffmpeg), auto-updates streamlink, and starts the recorder with a pre-configured set of channels and options. Edit the `CONFIGURATION` block at the top before running.

---

## 📺 yt-live-recorder.py

Monitors a YouTube channel and automatically records all active live streams using [yt-dlp](https://github.com/yt-dlp/yt-dlp). Polls the channel at a configurable interval and spawns a separate recording thread per stream. Supports browser cookie injection, format selection, and optional Windows service deployment via Task Scheduler or NSSM.

**Dependencies:** `pip install -U yt-dlp` + ffmpeg + Deno (for n-challenge since ~2025.11)

```bash
python yt-live-recorder.py --channel-url "https://www.youtube.com/@ChannelName" --cookies-browser firefox --output-dir D:\Recordings
```

---

## 💾 CloneVM.ps1

Clones a list of VMware ESXi VMs (read from `vm_list.csv`) to a dedicated backup host using [VMware.PowerCLI](https://developer.vmware.com/powercli). Each clone gets a date-stamped name prefix and is stored in a specified datastore/folder, then powered off after cloning. Can be used as a lightweight offline VM backup solution.

**Dependencies:** `VMware.PowerCLI` PowerShell module

---

## 🔀 Copy-vSS-to-vDS.ps1

Copies all vSS (Standard Switch) port groups — including VLAN IDs — from every ESXi host in a vCenter Datacenter to a target vDS (Distributed Switch). Detects and skips conflicts where the same port group name has different VLAN IDs across hosts, and skips port groups that already exist on the vDS.

**Usage:**
```powershell
.\Copy-vSS-to-vDS.ps1 -vCenter "vcsa01.company.local" -DatacenterName "Production DC 1" -VdsName "Prod vDS 01"
```

---

## 🌐 vlan_add_unifi.sh

Bash script that creates VLANs on a UniFi controller via the Network Integration API. Fetches existing networks first to skip duplicates, supports `DRY_RUN=1` for testing, and logs a summary of created/skipped/failed entries. Edit `BASE_URL`, `SITE_ID`, and `API_KEY` at the top, then add `create_vlan` lines at the bottom.

```bash
DRY_RUN=1 ./vlan_add_unifi.sh
```

---

## License

MIT
