import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from . import conftest_path  # noqa: F401
from scanners.handshake import (
    HandshakeCapture, build_filter, classify_frame, pcap_path,
)
from scanners.monitor import DLT_IEEE802_11, DLT_IEEE802_11_RADIOTAP


# 802.11 frame-control first byte: (subtype << 4) | (type << 2) | version.
def _fc0(ftype, subtype):
    return (subtype << 4) | (ftype << 2)


BEACON = bytes([_fc0(0, 8), 0x00]) + b"\x00" * 34    # mgmt/beacon
DATA_EAPOL = bytes([_fc0(2, 0), 0x00]) + b"\x00" * 34  # data frame (EAPOL)
QOS_EAPOL = bytes([_fc0(2, 8), 0x00]) + b"\x00" * 34   # QoS-data frame (EAPOL)
PROBE_REQ = bytes([_fc0(0, 4), 0x00]) + b"\x00" * 34   # mgmt/probe-req


def _pcap(records, linktype=DLT_IEEE802_11):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for data in records:
        out += struct.pack("<IIII", 1700000000, 0, len(data), len(data))
        out += data
    return out


class TestBuildFilter(unittest.TestCase):
    def test_eapol_always_present(self):
        self.assertIn("0x888e", build_filter(include_beacons=False))

    def test_beacons_folded_in_when_asked(self):
        f = build_filter(include_beacons=True)
        self.assertIn("0x888e", f)
        self.assertIn("beacon", f)

    def test_no_beacons_omits_beacon_clause(self):
        self.assertNotIn("beacon", build_filter(include_beacons=False))


class TestPcapPath(unittest.TestCase):
    def test_names_by_session(self):
        p = pcap_path("/mnt/x/handshakes", "20260831-101112")
        self.assertEqual(p, Path("/mnt/x/handshakes/hs-20260831-101112.pcap"))


class TestClassifyFrame(unittest.TestCase):
    def test_beacon(self):
        self.assertEqual(classify_frame(BEACON), "beacon")

    def test_data_is_eapol(self):
        self.assertEqual(classify_frame(DATA_EAPOL), "eapol")
        self.assertEqual(classify_frame(QOS_EAPOL), "eapol")

    def test_other_management_frame(self):
        self.assertEqual(classify_frame(PROBE_REQ), "other")

    def test_empty(self):
        self.assertEqual(classify_frame(b""), "other")


class TestTally(unittest.TestCase):
    def _cap(self):
        return HandshakeCapture("wlan9mon", "/tmp", "sid")

    def test_counts_eapol_and_beacons(self):
        cap = self._cap()
        for frame in (BEACON, DATA_EAPOL, QOS_EAPOL, PROBE_REQ):
            cap._tally(frame, DLT_IEEE802_11)
        self.assertEqual(cap.eapol, 2)
        self.assertEqual(cap.beacons, 1)

    def test_ignores_unknown_linktype(self):
        cap = self._cap()
        cap._tally(DATA_EAPOL, 999)
        self.assertEqual(cap.eapol, 0)


class TestStartGuards(unittest.TestCase):
    def test_missing_iface_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            cap = HandshakeCapture("wlan-does-not-exist", td, "sid")
            cap.start()
            self.assertFalse(cap.available)
            self.assertIsNotNone(cap.last_error)


class TestCountLoop(unittest.TestCase):
    """Drive the follower thread against a pre-written pcap file."""

    def test_tails_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            cap = HandshakeCapture("wlan9mon", td, "sid")
            cap.pcap_file.write_bytes(
                _pcap([BEACON, DATA_EAPOL, QOS_EAPOL, PROBE_REQ]))
            thr = threading.Thread(target=cap._count_loop, daemon=True)
            thr.start()
            deadline = time.monotonic() + 3.0
            while cap.eapol < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            cap._stop.set()
            thr.join(timeout=2)
            self.assertEqual(cap.eapol, 2)
            self.assertEqual(cap.beacons, 1)

    def test_radiotap_linktype_is_decoded(self):
        # Minimal radiotap header (version 0, len 8, empty present bitmap).
        rt = struct.pack("<BBHI", 0, 0, 8, 0)
        with tempfile.TemporaryDirectory() as td:
            cap = HandshakeCapture("wlan9mon", td, "sid2")
            cap.pcap_file.write_bytes(
                _pcap([rt + DATA_EAPOL], linktype=DLT_IEEE802_11_RADIOTAP))
            thr = threading.Thread(target=cap._count_loop, daemon=True)
            thr.start()
            deadline = time.monotonic() + 3.0
            while cap.eapol < 1 and time.monotonic() < deadline:
                time.sleep(0.05)
            cap._stop.set()
            thr.join(timeout=2)
            self.assertEqual(cap.eapol, 1)


if __name__ == "__main__":
    unittest.main()
