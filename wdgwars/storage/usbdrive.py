"""External USB storage detection and mounting.

The pager keeps its loot on internal eMMC (``/mmc``, ``/dev/mmcblk*``). Once the
device is running off a powered hub — the external Alfa AWUS036AXM WiFi dongle
and the u-blox GPS on the same hub — a USB stick can share that hub, and it is
the natural place to drop session CSVs and handshake pcaps without filling the
pager's own flash.

A hub-attached mass-storage device shows up as ``/dev/sd*``; internal storage is
always ``/dev/mmcblk*``, so the two are trivially told apart on this hardware.
The firmware does not auto-mount, so this module also mounts a chosen partition
under a fixed mountpoint.

The parsing is split out from the I/O so it can be unit-tested without a real
device: ``parse_proc_partitions`` and ``parse_proc_mounts`` are pure, and
``list_usb_partitions`` takes injectable readers.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Where we mount a USB stick if the firmware left it unmounted. A dedicated
# mountpoint (rather than /mnt/sda1) keeps our own mounts easy to find and tear
# down, and survives the device letter changing between boots.
DEFAULT_MOUNT = "/mnt/wdgwars-usb"

# Subdirectory created under a USB mount so loot from several tools does not
# collide at the drive root. Mirrors the internal "/mmc/root/loot/wdgwars".
LOOT_SUBDIR = "wdgwars"

# A partition on a USB mass-storage disk: sda1, sdb2, ... The whole-disk node
# (sda) is also mountable when the stick has no partition table.
_USB_PART_RE = re.compile(r"^sd[a-z]+([0-9]+)?$")


@dataclass(frozen=True)
class UsbPartition:
    device: str                 # "/dev/sda1"
    disk: str                   # "sda"
    size_bytes: int             # from /proc/partitions blocks * 1024
    mountpoint: str | None      # where it is mounted, or None
    fstype: str | None          # "vfat", "exfat", ... when known

    @property
    def name(self) -> str:
        return self.device.rsplit("/", 1)[-1]

    @property
    def is_mounted(self) -> bool:
        return bool(self.mountpoint)

    @property
    def size_mb(self) -> int:
        return self.size_bytes // (1024 * 1024)


# ── pure parsers ────────────────────────────────────────────────────────────

def parse_proc_partitions(text: str) -> list[tuple[str, int]]:
    """``/proc/partitions`` → ``[(name, size_bytes), ...]`` for USB partitions.

    Whole-disk nodes (``sda``) are kept only when the disk exposes no numbered
    partition of its own, so a stick with a partition table lists ``sda1`` and
    not the raw disk. Internal ``mmcblk*`` is never returned.
    """
    disks: dict[str, int] = {}
    parts: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].isdigit():
            continue          # header line or blank
        name = fields[3]
        if not _USB_PART_RE.match(name):
            continue
        try:
            size = int(fields[2]) * 1024
        except ValueError:
            continue
        if re.search(r"[0-9]$", name):
            parts[name] = size
        else:
            disks[name] = size
    # Drop a whole disk if any of its partitions showed up.
    for disk in list(disks):
        if any(p.startswith(disk) and p != disk for p in parts):
            del disks[disk]
    out = {**disks, **parts}
    return sorted(out.items())


def parse_proc_mounts(text: str) -> dict[str, tuple[str, str]]:
    """``/proc/mounts`` → ``{device: (mountpoint, fstype)}``.

    Only ``/dev/sd*`` devices are kept. Mountpoints are un-escaped from the
    octal form the kernel writes for spaces and other odd characters.
    """
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        dev, mnt, fstype = fields[0], fields[1], fields[2]
        if not dev.startswith("/dev/sd"):
            continue
        out[dev] = (_unescape_mount(mnt), fstype)
    return out


def _unescape_mount(path: str) -> str:
    """Reverse the ``\\040``-style octal escaping in /proc/mounts fields."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def _disk_of(part_name: str) -> str:
    """"sda1" -> "sda"; a bare "sda" is its own disk."""
    m = re.match(r"^(sd[a-z]+)", part_name)
    return m.group(1) if m else part_name


# ── enumeration ─────────────────────────────────────────────────────────────

def list_usb_partitions(
    partitions_text: str | None = None,
    mounts_text: str | None = None,
) -> list[UsbPartition]:
    """Enumerate mountable USB partitions, mount state resolved.

    Reads ``/proc/partitions`` and ``/proc/mounts`` by default; both can be
    passed in as text for testing. Result is sorted largest-first so the menu
    puts the roomiest stick on top.
    """
    if partitions_text is None:
        partitions_text = _read("/proc/partitions")
    if mounts_text is None:
        mounts_text = _read("/proc/mounts")

    mounts = parse_proc_mounts(mounts_text)
    out: list[UsbPartition] = []
    for name, size in parse_proc_partitions(partitions_text):
        dev = f"/dev/{name}"
        mnt, fstype = mounts.get(dev, (None, None))
        out.append(UsbPartition(device=dev, disk=_disk_of(name),
                                size_bytes=size, mountpoint=mnt, fstype=fstype))
    out.sort(key=lambda p: p.size_bytes, reverse=True)
    return out


def _read(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


# ── mounting ────────────────────────────────────────────────────────────────

def ensure_mounted(device: str, mountpoint: str = DEFAULT_MOUNT,
                   ) -> tuple[str | None, str]:
    """Make *device* available, returning ``(mountpoint, message)``.

    If the device is already mounted somewhere, that existing mountpoint is
    used as-is (never moved). Otherwise it is mounted at *mountpoint*. On
    failure the first element is ``None`` and the message says why.
    """
    for part in list_usb_partitions():
        if part.device == device and part.is_mounted:
            return part.mountpoint, f"already mounted at {part.mountpoint}"

    try:
        os.makedirs(mountpoint, exist_ok=True)
    except OSError as e:
        return None, f"mkdir {mountpoint}: {e}"

    # -o rw only; let mount auto-detect the filesystem (vfat/exfat/ext4). A
    # bare `mount` on a device the firmware can't handle fails cleanly here
    # rather than corrupting anything.
    try:
        proc = subprocess.run(["mount", device, mountpoint],
                              capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return None, "`mount` not available"
    except Exception as e:                       # noqa: BLE001 - report to UI
        return None, f"mount: {e}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, err[-1][:80] if err else f"mount exit {proc.returncode}"
    return mountpoint, f"mounted {device}"


def loot_dir_for(mountpoint: str) -> Path:
    """The directory under a USB mount where sessions/pcaps are written."""
    return Path(mountpoint) / LOOT_SUBDIR


def is_writable(path: str | Path) -> bool:
    """True if we can create files under *path* (creating it if needed)."""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = p / ".wdgwars-write-test"
    try:
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def prepare_output(device: str | None,
                   mountpoint: str = DEFAULT_MOUNT,
                   ) -> tuple[Path | None, str]:
    """Resolve a ready-to-write USB loot directory, mounting as needed.

    *device* is an explicit ``/dev/sdX`` to use, or ``None`` to auto-pick the
    largest detected USB partition (preferring one already mounted). Returns
    ``(loot_dir, message)``; ``loot_dir`` is ``None`` when no usable USB
    storage could be brought online.
    """
    parts = list_usb_partitions()
    if not parts:
        return None, "no USB storage detected"

    chosen: UsbPartition | None = None
    if device:
        chosen = next((p for p in parts if p.device == device), None)
        if chosen is None:
            return None, f"{device} not present"
    else:
        chosen = next((p for p in parts if p.is_mounted), parts[0])

    mnt, msg = ensure_mounted(chosen.device, mountpoint)
    if mnt is None:
        return None, msg
    loot = loot_dir_for(mnt)
    if not is_writable(loot):
        return None, f"{loot} not writable"
    return loot, msg
