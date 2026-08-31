"""Passive WPA handshake capture to pcap.

The wardriving backends only ever *log* broadcasts. This is the one place the
payload keeps raw frames, and it is still strictly passive: no deauth, no
injection, no association. It rides the same monitor interface the capture
backend already brought up — while the channel hopper visits a channel, any
EAPOL 4-way-handshake frames a nearby client happens to exchange are written to
a standard pcap that aircrack-ng / hashcat can read.

``tcpdump`` does the writing (``-w <file>``), so the on-disk file is a real,
tool-compatible pcap even if we crash. A read-only follower thread tails the
same file only to keep a live count for the HUD — it classifies each record by
802.11 frame type (beacon for context vs. EAPOL data for the handshake itself),
which is cheap because the BPF filter already admits nothing else.

Requires a monitor-mode interface, exactly like ``MonitorScanner`` — EAPOL
frames are invisible to ``iw scan``. When no monitor interface is available the
capture simply reports why and stays disabled.
"""

from __future__ import annotations

import os
import queue  # noqa: F401  (kept for parity with sibling scanners' style)
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

from .monitor import (
    DLT_IEEE802_11, DLT_IEEE802_11_RADIOTAP, _PCAP_MAGICS, parse_radiotap,
)

# EAPOL is EtherType 0x888E; on 802.11 it arrives as a data frame carrying a
# 802.1X LLC/SNAP payload. tcpdump understands `ether proto 0x888e` on a
# radiotap link type, which is the whole handshake we care about. Beacons are
# folded in so the resulting pcap names the networks the handshakes belong to.
_EAPOL_FILTER = "ether proto 0x888e"
_BEACON_FILTER = "type mgt subtype beacon"

# 802.11 frame types (the two low bits of the frame-control byte).
_FT_MGMT = 0
_FT_DATA = 2
_SUBTYPE_BEACON = 8


def build_filter(include_beacons: bool = True) -> str:
    """BPF for a passive handshake capture.

    Always captures EAPOL; optionally also beacons so the pcap carries the
    SSID/BSSID context that offline crackers use to label a handshake.
    """
    if include_beacons:
        return f"({_EAPOL_FILTER}) or ({_BEACON_FILTER})"
    return _EAPOL_FILTER


def pcap_path(directory: Path | str, session_id: str) -> Path:
    """Deterministic pcap filename for a session: ``hs-<session_id>.pcap``."""
    return Path(directory) / f"hs-{session_id}.pcap"


def classify_frame(frame: bytes) -> str:
    """"beacon", "eapol", or "other" from an 802.11 frame's control byte."""
    if not frame:
        return "other"
    fc0 = frame[0]
    ftype = (fc0 >> 2) & 0x3
    if ftype == _FT_DATA:
        return "eapol"           # filter admits only EAPOL data frames
    if ftype == _FT_MGMT and (fc0 >> 4) & 0xF == _SUBTYPE_BEACON:
        return "beacon"
    return "other"


class HandshakeCapture:
    """Passive EAPOL/beacon pcap capture off a monitor interface."""

    def __init__(self, iface: str, out_dir: Path | str, session_id: str,
                 include_beacons: bool = True, snaplen: int = 0) -> None:
        self.iface = iface
        self.out_dir = Path(out_dir)
        self.session_id = session_id
        self.include_beacons = include_beacons
        self.snaplen = snaplen           # 0 = whole frame; EAPOL needs it all
        self.pcap_file = pcap_path(self.out_dir, session_id)

        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._count_thr: threading.Thread | None = None

        self.available = False
        self.last_error: str | None = None
        self.eapol: int = 0              # handshake frames written
        self.beacons: int = 0           # context frames written

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._proc is not None:
            return
        if not shutil.which("tcpdump"):
            self.last_error = "`tcpdump` not installed (opkg install tcpdump-mini)"
            return
        if not os.path.exists(f"/sys/class/net/{self.iface}"):
            self.last_error = f"{self.iface} not present"
            return
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.last_error = f"mkdir {self.out_dir}: {e}"
            return

        cmd = [
            "tcpdump", "-i", self.iface, "-w", str(self.pcap_file),
            "-U", "-n", "-s", str(self.snaplen),
            build_filter(self.include_beacons),
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as e:                   # noqa: BLE001 - surface to UI
            self.last_error = f"spawn: {e}"
            self._proc = None
            return

        self.available = True
        self._stop.clear()
        threading.Thread(target=self._stderr_loop, name="hs-err",
                         daemon=True).start()
        self._count_thr = threading.Thread(target=self._count_loop,
                                           name="hs-count", daemon=True)
        self._count_thr.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._count_thr:
            self._count_thr.join(timeout=2)
            self._count_thr = None
        self.available = False

    # ── HUD helpers ─────────────────────────────────────────────────────────

    def summary(self) -> str:
        return f"{self.eapol} eapol / {self.beacons} bcn"

    # ── stderr (tcpdump chatter) ─────────────────────────────────────────────

    _TCPDUMP_NOISE = re.compile(
        r"^(tcpdump: (listening|verbose|data link))"
        r"|^\d+\s+packets? (captured|received by filter|dropped by (kernel|interface))"
        r"|^\d+\s+packets? dropped by",
        re.IGNORECASE,
    )

    def _stderr_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        try:
            for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if line and not self._TCPDUMP_NOISE.match(line):
                    self.last_error = line[:120]
        except Exception:
            pass

    # ── follower: tail the pcap tcpdump is writing, only to count ────────────

    def _count_loop(self) -> None:
        """Follow the growing pcap read-only and tally beacon/EAPOL records.

        tcpdump owns the file; we never write it. Counting is best-effort — a
        wrong count only mis-labels the HUD, it can't corrupt the capture.
        """
        # Wait for tcpdump to create the file and write the global header.
        path = self.pcap_file
        deadline = time.monotonic() + 5.0
        while not self._stop.is_set() and not path.exists():
            if time.monotonic() > deadline:
                return
            self._stop.wait(0.2)
        try:
            fh = path.open("rb")
        except OSError:
            return

        endian: str | None = None
        ts_div = 1_000_000
        linktype = DLT_IEEE802_11_RADIOTAP
        buf = bytearray()
        try:
            while not self._stop.is_set():
                chunk = fh.read(65536)
                if not chunk:
                    self._stop.wait(0.3)          # tail -f: wait for more
                    continue
                buf += chunk

                if endian is None:
                    if len(buf) < 24:
                        continue
                    magic = struct.unpack_from("<I", buf, 0)[0]
                    fmt = _PCAP_MAGICS.get(magic)
                    if fmt is None:
                        return
                    endian, ts_div = fmt
                    linktype = struct.unpack_from(endian + "I", buf, 20)[0]
                    del buf[:24]

                rec_fmt = endian + "IIII"
                while len(buf) >= 16:
                    _s, _f, incl, _orig = struct.unpack_from(rec_fmt, buf, 0)
                    if incl > 262144:
                        return
                    if len(buf) < 16 + incl:
                        break
                    data = bytes(buf[16:16 + incl])
                    del buf[:16 + incl]
                    self._tally(data, linktype)
        finally:
            try:
                fh.close()
            except Exception:
                pass

    def _tally(self, data: bytes, linktype: int) -> None:
        if linktype == DLT_IEEE802_11_RADIOTAP:
            rt = parse_radiotap(data)
            if rt is None:
                return
            hlen = rt[0]
            frame = data[hlen:]
        elif linktype == DLT_IEEE802_11:
            frame = data
        else:
            return
        kind = classify_frame(frame)
        if kind == "eapol":
            self.eapol += 1
        elif kind == "beacon":
            self.beacons += 1
