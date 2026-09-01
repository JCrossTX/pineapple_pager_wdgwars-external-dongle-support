# Changelog

## Unreleased — external dongle support (fork)

Adds support for an external WiFi dongle and USB storage staged on a powered
hub, plus opt-in passive handshake capture.

### Added

- **Alfa AWUS036AXM (MT7921AU) support.** `bootstrap.sh` now runs `opkg update`
  and installs `kmod-mt7921u`, `kmod-mt76-usb` and `kmod-mt7921-firmware` so the
  dongle enumerates as a usable `wlanN`. Brought up in monitor mode (e.g.
  `wlan2mon`) it is auto-selected by `scan.wifi_iface = "auto"`, and its
  tri-band radio sees 6 GHz networks the pager's own `phy0` cannot.
- **USB storage output** (`storage/usbdrive.py`, **CONFIG → OUTPUT DEVICE**).
  Detects hub-attached USB partitions (`/dev/sd*`, distinct from internal
  `/dev/mmcblk*`), mounts a chosen one under `/mnt/wdgwars-usb`, and writes
  sessions + handshake pcaps to `<mount>/wdgwars/`. Falls back to internal
  storage if the stick can't be mounted/written, so a scan is never blocked.
  **SYNC NOW** and **SESSIONS** always look for a USB source — mounting a stick
  if needed — and span both internal and USB, so switching the output device
  never strands earlier sessions on the other storage.
  `bootstrap.sh` installs `kmod-usb-storage`, `block-mount`, `kmod-fs-vfat`,
  `kmod-fs-exfat` and `dosfstools`.
- **Firmware-style top status bar** (`ui/statusbar.py`) on the main menu and the
  live-scan screen: screen title on the left, then indicators + clock on the
  right, using the Pager's own icon bitmaps (`wdgwars/assets/icons/`). GPS /
  EXT / USB / PCAP are gray when not ready and cyan (`#00FFE5`) when
  connected/ready; sound, brightness and GHz-band show the live value; battery
  shows the charge bitmap (bolt only on external power — on battery it draws the
  exact % in green). All driven by real hardware/config state (3 s cache for the
  heavier probes). The wardrive 2×2 stays fully visible under the bar.
- **ERASE SYNCED** (main menu, right after SYNC NOW). Deletes the session CSVs
  that were successfully uploaded (carry a `.uploaded` marker) across internal
  and USB, after a confirmation; badge shows the count. Pending/errored sessions
  and the handshake pcaps (separate `handshakes/` dir) are never touched.
- **More band-plan presets** — `2.4 + 6`, `2.4 + 5 + 6`, `5 + 6` and `6 only`,
  alongside the existing `rotate` / `2.4 only` / `2.4 + 5` / `full sweep`, for
  every useful combination of the 2.4 / 5 / 6 GHz bands.
- **BAND PLAN now steers the monitor hopper too** — the same `scan.band_plan`
  keys drive both the `iw scan` rotation and the monitor-mode channel hopper,
  so 6 GHz (6E) PSC channels are hopped whenever the plan includes them (the
  default plan does), and a restrictive plan like `2.4 only` or `6 only` limits
  monitor mode as well. Still intersected with the radio's supported/enabled
  frequencies.
- **Passive handshake capture** (`scanners/handshake.py`, **CONFIG → SCAN SETUP
  → HANDSHAKE CAP**). Off by default. Records WPA EAPOL 4-way-handshake frames
  (plus optional beacons for SSID context) to a tool-compatible pcap at
  `<loot>/handshakes/hs-<session>.pcap` via `tcpdump`. Strictly passive — no
  deauth, injection, or association. Requires a monitor interface (EAPOL is
  invisible to `iw scan`) and rides its channel hopper. The live HUD shows an
  `hs:N` EAPOL counter and the end-of-session dialog names the pcap.

### Config

- `storage.output` (`"internal"` / `"usb"`), `storage.usb_mount`,
  `storage.usb_device`.
- `handshake.enabled`, `handshake.include_beacons`, `handshake.snaplen`.

### Tests

245, up from 218. Adds pure-parser coverage for the USB partition/mount
detection and for the handshake pcap classifier + follower thread.

## 1.1 — 2026-07-26

Reworked WiFi capture, GPS geo-tagging and deduplication. Started from a
report that the CSV kept growing for minutes after a wardrive stopped, and
from [#3](https://github.com/LOCOSP/pineapple_pager_wdgwars/issues/3).

### Upgrading — read this first

**`scan.dedup_ttl_s` is gone; it is now `scan.refresh_ttl_s`.**

The key was renamed because its *meaning* changed, not just its default. It
used to be the only deduplication rule — "re-log every AP this often". It is
now a slow backstop OR'd with a movement filter. Carrying an old value of
`60` forward would keep re-logging every visible AP once a minute while
parked, which is exactly the behaviour the movement filter exists to stop.

Nothing breaks if you do nothing: the old key is ignored and the new default
(`300`) applies. But **if you had deliberately tuned `dedup_ttl_s`, that
setting is now silently inactive** — re-tune `scan.refresh_ttl_s` and
`scan.min_move_m` instead.

**`tcpdump-mini` is a new optional dependency.**

It backs the monitor-mode capture backend. `bootstrap.sh` installs it and
`payload.sh` warns if it is missing. Without it nothing crashes — the payload
falls back to `iw scan` — but you get materially worse coverage without an
obvious reason why, so re-run `sh bootstrap.sh` after updating:

```sh
opkg update && opkg install tcpdump-mini
```

### Added

- **Monitor-mode capture backend.** Passive beacon capture through
  `tcpdump -w -`, decoded in-process (radiotap → 802.11 management frames →
  RSN/WPA/WPS elements), with its own channel hopper. Measured on a Pager,
  same spot and minute: **18 unique BSSIDs against 5** for `iw scan`, and 6 of
  those were 5 GHz networks the old path could not see at all. Costs about 7%
  of one 580 MHz core.
- **Selectable capture interface** (`scan.wifi_iface`, and
  **CONFIG → SCAN SETUP → WIFI SOURCE** on the device). `auto` prefers a live
  monitor interface, else the best managed one that is not the pager's own
  management radio. An externally staged adapter such as an AWUS036ACM on
  `wlan2mon` is picked up with no configuration. Closes #3.
- **UPLOAD LOG** screen reading `GET /api/upload-history`, so the server's own
  `imported` / `captured` / `no_gps` / `bad_rows` counts are visible on the
  pager.
- **Async upload path.** Files ≥ 20 MB go to `POST /api/v2/upload-csv`,
  gzipped, with job polling. A sync upload that hits a gateway timeout
  escalates to the queue on retry.
- **SCAN SETUP** menu: WiFi source, band plan, monitor hop, movement filter,
  refresh TTL, require-GPS-fix.

### Changed

- **Deduplication is movement-aware.** A row is written for a new BSSID, after
  moving `scan.min_move_m` (30 m), when RSSI is `scan.rssi_delta_db` (6 dB)
  stronger, or after `scan.refresh_ttl_s` (300 s). Parked, the file stops
  growing; driving, an AP yields several rows from different places, which is
  what trilateration needs.
- **Observations are geo-tagged with the position that was true when they were
  heard**, from a rolling GPS history, rather than when they were written.
- **`iw scan` rotates short per-band passes** instead of one tri-band sweep, so
  a position sample lands every ~2 s instead of every ~15 s. Supported
  frequencies are probed per wiphy; bands the radio rejects are retired.
- **The HUD leads with rows written and rows/min.** Raw sightings moved to
  small type, labelled "seen". The two numbers differ by roughly 5× and were
  being read as a stuck writer.
- **CSV writes are buffered**, size tracked in a counter, flushed on a timer
  with a periodic `fsync` — instead of `flush()` plus `stat()` per row and no
  `fsync` at all. An SSH `wc -l` still shows near-live progress.
- HUD renders at 2 Hz and only when something changed.
- Upload bodies stream from a temp file beside the CSV instead of being built
  in memory, which peaked at twice the file size.
- Scanner queues are bounded; BLE and monitor observations are aggregated into
  per-MAC emit windows.

### Fixed

- **Rows were written without a GPS fix.** Position is retained for the HUD
  after a dropout but no longer written, which had been pinning every AP in a
  tunnel to the coordinate where the fix died.
- **Stale kernel BSS cache entries were logged as fresh.** `iw scan` returns
  the cfg80211 cache, so sightings arrive up to ~30 s old — one of five
  entries in a real capture was 20 s stale, a few hundred metres at road
  speed. `last seen` is now parsed, `first_seen` back-dated, stale entries
  dropped, and the cache flushed before each pass.
- **WEP networks were classified as open.** iw 6.9 prints
  `capability: ESS (0x0431)` and omits the `Privacy` word even though bit 4 is
  set; the hex value is now authoritative.
- **`Channel` and `Frequency` could disagree.** Channel came from the AP's
  element, frequency from radiotap — where *we* were listening. Overlapping
  2.4 GHz channels produced rows like "channel 8, 2442 MHz".
- **APs without a DS Parameter Set got a channel guessed from the receive
  frequency**, different on every pass. HT Operation is now read as a
  fallback.
- **Enterprise networks were reported as PSK.** AuthMode gained 802.1X, OWE,
  WPS and IBSS; WPA3 transition mode reports both SAE and PSK. Both capture
  backends share one builder, so an AP looks identical whichever saw it.
- `AccuracyMeters` is never written as `0` — WiGLE downweights it.
- Non-ASCII SSIDs arrived as literal `\xNN` escapes; they are now decoded.
- Channels 32 and 173 were reported as channel 0.
- The scan timeout was 12 s, which silently discarded whole tri-band sweeps.
- Queues grew unbounded while paused, then flushed to the CSV at the *resume*
  position.
- `iw phy` dumps every radio, so a scan on the 2.4 GHz-only `phy0` believed it
  supported 5 and 6 GHz. Probing is scoped to the interface's own wiphy.

### Known gaps

**The GPS fix gate and position interpolation have unit tests but have not run
against a live receiver** — no GPS was available during hardware testing, so
the CSV path was exercised with a synthetic track. Worth a short drive before
relying on it. The LCD UI and a live upload were also not exercised on-device.

### Tests

218, up from 21. Includes a real iw 6.9 capture from a Pager (BSSIDs and SSIDs
rewritten to synthetic values — a real capture names the networks around
whoever recorded it, and a BSSID is geolocatable) and a synthetic pcap driven
through the actual monitor-mode read loop.

## 1.0

Initial release. WiFi capture via `iw dev wlan0 scan`, BLE via `bluetoothctl`
under a pty, GPS over gpsd, WigleWifi-1.6 CSV output, manual sync to
wdgwars.pl, app handoff to Loki / PagerGotchi / WiFMan / Bjorn.
