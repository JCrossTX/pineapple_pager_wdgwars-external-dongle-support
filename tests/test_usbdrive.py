import tempfile
import unittest
from pathlib import Path

from . import conftest_path  # noqa: F401
from storage import usbdrive
from storage.usbdrive import (
    UsbPartition, list_usb_partitions, loot_dir_for, parse_proc_mounts,
    parse_proc_partitions, prepare_output,
)


PARTITIONS = """major minor  #blocks  name

 179        0    7634944 mmcblk0
 179        1     131072 mmcblk0p1
   8        0   15267840 sda
   8        1   15266816 sda1
   8       16    7812500 sdb
"""

MOUNTS = r"""proc /proc proc rw,relatime 0 0
/dev/mmcblk0p1 /mmc ext4 rw,relatime 0 0
/dev/sda1 /mnt/usb\0401 vfat rw,noatime 0 0
tmpfs /tmp tmpfs rw 0 0
"""


class TestParsePartitions(unittest.TestCase):
    def test_keeps_usb_only(self):
        names = [n for n, _ in parse_proc_partitions(PARTITIONS)]
        self.assertEqual(names, ["sda1", "sdb"])   # no mmcblk, no raw sda

    def test_raw_disk_kept_only_without_partitions(self):
        # sda has sda1, so the raw disk is dropped; sdb has none, so it stays.
        names = [n for n, _ in parse_proc_partitions(PARTITIONS)]
        self.assertIn("sdb", names)
        self.assertNotIn("sda", names)

    def test_size_is_bytes(self):
        sizes = dict(parse_proc_partitions(PARTITIONS))
        self.assertEqual(sizes["sda1"], 15266816 * 1024)

    def test_empty_input(self):
        self.assertEqual(parse_proc_partitions(""), [])


class TestParseMounts(unittest.TestCase):
    def test_only_sd_devices(self):
        mounts = parse_proc_mounts(MOUNTS)
        self.assertIn("/dev/sda1", mounts)
        self.assertNotIn("/dev/mmcblk0p1", mounts)
        self.assertNotIn("proc", mounts)

    def test_octal_escape_is_decoded(self):
        mnt, fstype = parse_proc_mounts(MOUNTS)["/dev/sda1"]
        self.assertEqual(mnt, "/mnt/usb 1")
        self.assertEqual(fstype, "vfat")


class TestListUsbPartitions(unittest.TestCase):
    def setUp(self):
        self.parts = list_usb_partitions(PARTITIONS, MOUNTS)

    def test_resolves_mount_state(self):
        by_name = {p.name: p for p in self.parts}
        self.assertTrue(by_name["sda1"].is_mounted)
        self.assertEqual(by_name["sda1"].mountpoint, "/mnt/usb 1")
        self.assertFalse(by_name["sdb"].is_mounted)

    def test_sorted_largest_first(self):
        self.assertEqual(self.parts[0].name, "sda1")   # ~14.5 GB first

    def test_disk_attribution(self):
        by_name = {p.name: p for p in self.parts}
        self.assertEqual(by_name["sda1"].disk, "sda")
        self.assertEqual(by_name["sdb"].disk, "sdb")


class TestHelpers(unittest.TestCase):
    def test_loot_dir_for(self):
        self.assertEqual(loot_dir_for("/mnt/x"), Path("/mnt/x/wdgwars"))

    def test_is_writable_true_for_temp(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(usbdrive.is_writable(Path(td) / "sub"))

    def test_is_writable_false_for_unwritable(self):
        self.assertFalse(usbdrive.is_writable("/proc/nonexistent/wdgwars"))


class TestPrepareOutput(unittest.TestCase):
    def test_no_usb_detected(self):
        orig = usbdrive.list_usb_partitions
        usbdrive.list_usb_partitions = lambda *a, **k: []
        try:
            loot, msg = prepare_output(None)
        finally:
            usbdrive.list_usb_partitions = orig
        self.assertIsNone(loot)
        self.assertIn("no USB", msg)

    def test_named_device_absent(self):
        orig = usbdrive.list_usb_partitions
        usbdrive.list_usb_partitions = lambda *a, **k: [
            UsbPartition("/dev/sdb", "sdb", 1000, None, None)]
        try:
            loot, msg = prepare_output("/dev/sdz")
        finally:
            usbdrive.list_usb_partitions = orig
        self.assertIsNone(loot)
        self.assertIn("not present", msg)


if __name__ == "__main__":
    unittest.main()
