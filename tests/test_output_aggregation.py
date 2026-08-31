"""SYNC / SESSIONS must span internal and USB loot dirs.

When the output device is switched, sessions can end up on either the internal
eMMC or the USB stick. Uploads and the session listing read from both so
nothing is stranded on the storage that isn't currently selected.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

from . import conftest_path  # noqa: F401


def _install_pagerctl_stub():
    if "pagerctl" in sys.modules:
        return
    mod = types.ModuleType("pagerctl")

    class Pager:
        width, height = 480, 222
        BTN_A = BTN_B = BTN_UP = BTN_DOWN = 0
        EVENT_PRESS = 1

        def init(self):
            return 0

    mod.Pager = Pager
    sys.modules["pagerctl"] = mod


_install_pagerctl_stub()
import wdgwars as app  # noqa: E402


def _write_session(sessions_dir: Path, name: str, uploaded: bool = False):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    csv = sessions_dir / name
    csv.write_text("WigleWifi-1.6\ncols\nrow\n")
    if uploaded:
        csv.with_suffix(csv.suffix + ".uploaded").write_text("{}")
    return csv


class _GpsStub:
    def start(self):
        pass

    def stop(self):
        pass


def _make_app(internal: Path, active: Path, output_active: str):
    a = app.App.__new__(app.App)
    a.p = None
    a.pal = None
    a.cfg = {}
    a.gps = _GpsStub()
    a.internal_loot = internal
    a.loot_dir = active
    a.output_active = output_active
    return a


class TestOutputAggregation(unittest.TestCase):
    def test_internal_only_when_internal_selected(self):
        with tempfile.TemporaryDirectory() as td:
            internal = Path(td) / "internal"
            _write_session(internal / "sessions", "wd-1-00.csv")
            a = _make_app(internal, internal, "internal")
            # Only one directory is scanned; no duplication.
            self.assertEqual(a._session_dirs(), [internal / "sessions"])
            self.assertEqual(len(a._all_pending()), 1)

    def test_usb_selected_spans_both(self):
        with tempfile.TemporaryDirectory() as td:
            internal = Path(td) / "internal"
            usb = Path(td) / "usb"
            _write_session(internal / "sessions", "wd-old-00.csv")
            _write_session(usb / "sessions", "wd-new-00.csv")
            a = _make_app(internal, usb, "usb")

            dirs = a._session_dirs()
            self.assertIn(usb / "sessions", dirs)
            self.assertIn(internal / "sessions", dirs)

            pending = {p.name for p in a._all_pending()}
            self.assertEqual(pending, {"wd-old-00.csv", "wd-new-00.csv"})

            sessions = {p.name for p, _ in a._all_sessions()}
            self.assertEqual(sessions, {"wd-old-00.csv", "wd-new-00.csv"})

    def test_uploaded_marker_excludes_from_pending_across_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            internal = Path(td) / "internal"
            usb = Path(td) / "usb"
            _write_session(internal / "sessions", "wd-a-00.csv", uploaded=True)
            _write_session(usb / "sessions", "wd-b-00.csv")
            a = _make_app(internal, usb, "usb")

            pending = {p.name for p in a._all_pending()}
            self.assertEqual(pending, {"wd-b-00.csv"})       # a is uploaded
            statuses = {p.name: s for p, s in a._all_sessions()}
            self.assertEqual(statuses["wd-a-00.csv"], "ok")
            self.assertEqual(statuses["wd-b-00.csv"], "pending")


if __name__ == "__main__":
    unittest.main()
