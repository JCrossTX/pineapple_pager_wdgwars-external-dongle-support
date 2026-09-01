import unittest

from . import conftest_path  # noqa: F401
from ui import statusbar
from scanners.iface import IfaceInfo


def ifc(name, phy):
    return IfaceInfo(name, "monitor", phy, True)


class TestExternalAdapter(unittest.TestCase):
    def test_none(self):
        self.assertFalse(statusbar.external_adapter_present([]))

    def test_builtin_radios_only(self):
        self.assertFalse(statusbar.external_adapter_present(
            [ifc("wlan0", 0), ifc("wlan1mon", 1)]))

    def test_external_on_phy2(self):
        self.assertTrue(statusbar.external_adapter_present(
            [ifc("wlan0", 0), ifc("wlan2mon", 2)]))


class TestStateLeds(unittest.TestCase):
    def test_all_off(self):
        self.assertEqual(
            statusbar.status_states(False, [], [], False),
            {"GPS": False, "EXT": False, "USB": False, "PCAP": False})

    def test_all_on(self):
        s = statusbar.status_states(True, [ifc("wlan2mon", 2)], ["sda1"], True)
        self.assertTrue(all(s.values()))


class TestBatteryAsset(unittest.TestCase):
    def test_on_battery_is_no_bolt_text(self):
        # No external power -> plain battery, no bolt, at any level.
        self.assertEqual(statusbar.battery_asset(5), "batt_text")
        self.assertEqual(statusbar.battery_asset(95), "batt_text")
        self.assertEqual(statusbar.battery_asset(None), "batt_text")

    def test_full_shows_bolt(self):
        self.assertEqual(statusbar.battery_asset(100, full=True), "batt_full")

    def test_charging_levels_have_bolt(self):
        self.assertEqual(statusbar.battery_asset(5, charging=True), "batt_25")
        self.assertEqual(statusbar.battery_asset(45, charging=True), "batt_50")
        self.assertEqual(statusbar.battery_asset(70, charging=True), "batt_75")
        self.assertEqual(statusbar.battery_asset(95, charging=True), "batt_100")


class TestBrightnessAsset(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(statusbar.brightness_asset(10), "bri_2")
        self.assertEqual(statusbar.brightness_asset(70), "bri_7")
        self.assertEqual(statusbar.brightness_asset(100), "bri_8")


class TestGhzAsset(unittest.TestCase):
    def test_all(self):
        self.assertEqual(statusbar.ghz_asset(["all"]), "ghz_256")

    def test_tri_band_plan(self):
        self.assertEqual(
            statusbar.ghz_asset(["2g", "5g_fast", "5g_dfs", "6g_psc"]), "ghz_256")

    def test_2_and_5(self):
        self.assertEqual(statusbar.ghz_asset(["2g", "5g_fast", "5g_dfs"]), "ghz_25")

    def test_6_only(self):
        self.assertEqual(statusbar.ghz_asset(["6g_psc"]), "ghz_6")

    def test_2_only(self):
        self.assertEqual(statusbar.ghz_asset(["2g"]), "ghz_2")

    def test_empty(self):
        self.assertEqual(statusbar.ghz_asset([]), "ghz_off")


class TestSoundAsset(unittest.TestCase):
    def test_muted(self):
        self.assertEqual(statusbar.sound_asset(True), "mute")

    def test_levels(self):
        self.assertEqual(statusbar.sound_asset(False), "vol_high")
        self.assertEqual(statusbar.sound_asset(False, "low"), "vol_low")
        self.assertEqual(statusbar.sound_asset(False, "medium"), "vol_med")


class TestOrder(unittest.TestCase):
    def test_order(self):
        self.assertEqual(statusbar.ORDER,
                         ("GPS", "EXT", "USB", "PCAP", "sound", "bri", "ghz", "batt"))


class TestAssetsPresent(unittest.TestCase):
    """Every asset name the mappers can return must exist in assets/icons/."""
    def test_all_referenced_icons_exist(self):
        import os
        names = {"gps_on", "gps_off", "pcap_on", "pcap_off",
                 "batt_25", "batt_50", "batt_75", "batt_100", "batt_full", "batt_text",
                 "bri_2", "bri_3", "bri_5", "bri_7", "bri_8",
                 "ghz_2", "ghz_5", "ghz_6", "ghz_25", "ghz_26", "ghz_56",
                 "ghz_256", "ghz_off",
                 "vol_high", "vol_med", "vol_low", "mute"}
        for n in names:
            self.assertTrue(os.path.isfile(os.path.join(statusbar._ICON_DIR, n + ".png")),
                            f"missing icon asset: {n}.png")


if __name__ == "__main__":
    unittest.main()
