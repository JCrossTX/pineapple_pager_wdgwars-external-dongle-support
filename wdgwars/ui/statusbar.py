"""Top status bar — a reproduction of the Pager firmware toolbar.

Layout mirrors the firmware bar: screen title on the left, a row of indicators
on the right, clock last. It uses the firmware's own icon bitmaps (shipped in
``assets/icons/``). The only deliberate changes from the stock bar are the two
additions this fork needs, rendered as coloured letters:

    GPS   EXT   USB   PCAP   SOUND   BRIGHT   GHZ   BATTERY   09:10 PM
    (img) (txt) (txt) (img)  (img)   (img)    (img) (img)     (txt)

Indicator kinds:
  * State LEDs — GPS, EXT, USB, PCAP — gray when not connected/ready, cyan when
    ready. GPS/PCAP blit an on/off bitmap; EXT/USB are coloured text.
  * Value icons — SOUND, BRIGHT(ness), GHZ (band), BATTERY — always shown, the
    bitmap chosen from the live value.

Everything reflects real state; the caller supplies the values. The mapping
helpers are pure and unit-tested; only the draw path touches the LCD.
"""

from __future__ import annotations

import os
import time

# Left-to-right order. Value keys carry an asset name; LED keys carry a bool.
ORDER = ("GPS", "EXT", "USB", "PCAP", "sound", "bri", "ghz", "batt")
_LED_ICONS = {"GPS": "gps", "PCAP": "pcap"}   # on/off bitmaps
_LED_TEXT = ("EXT", "USB")                    # coloured letters
_VALUE_KEYS = ("sound", "bri", "ghz", "batt")  # dict value is the asset name

_ICON_DIR = os.path.join(
    os.environ.get("WDGWARS_PAYLOAD_DIR") or
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "icons",
)
_icon_cache: dict = {}     # name -> (handle, w, h)
_icon_missing: set = set()


# ── pure state / value mapping ───────────────────────────────────────────────

def external_adapter_present(ifaces) -> bool:
    """External WiFi adapter attached — a radio on phy >= 2 (the Pager's own are
    phy0/phy1; a staged AWUS036AXM enumerates as phy2+)."""
    return any(getattr(i, "phy", 0) >= 2 for i in ifaces)


def battery_asset(pct, charging: bool = False, full: bool = False) -> str:
    """Battery bitmap name.

    The lightning-bolt frames are used **only on external power**: ``batt_full``
    when fully charged, and the ``batt_25/50/75/100`` bolt frames while charging.
    On battery (no external power) there is **no bolt** — it is always the plain
    ``batt_text`` battery, with the level drawn on it as a number.
    """
    if full:
        return "batt_full"
    if not charging:
        return "batt_text"          # on battery — plain level icon, no bolt
    if pct is None or pct > 80:
        return "batt_100"
    if pct <= 30:
        return "batt_25"
    if pct <= 55:
        return "batt_50"
    return "batt_75"


def brightness_asset(pct) -> str:
    """Brightness bitmap name from a 0-100 level (firmware levels 2/3/5/7/8)."""
    if pct is None:
        return "bri_5"
    for lvl, name in ((20, "bri_2"), (40, "bri_3"), (60, "bri_5"), (80, "bri_7")):
        if pct <= lvl:
            return name
    return "bri_8"


def ghz_asset(band_plan) -> str:
    """GHz-band bitmap name from the scan band plan (the `scan.band_plan` keys)."""
    keys = set(band_plan or [])
    if "all" in keys:
        return "ghz_256"
    has2 = "2g" in keys
    has5 = bool(keys & {"5g_fast", "5g_dfs", "5g"})
    has6 = bool(keys & {"6g_psc", "6g"})
    combo = (has2, has5, has6)
    return {
        (True, True, True): "ghz_256",
        (True, True, False): "ghz_25",
        (True, False, True): "ghz_26",
        (False, True, True): "ghz_56",
        (True, False, False): "ghz_2",
        (False, True, False): "ghz_5",
        (False, False, True): "ghz_6",
    }.get(combo, "ghz_off")


def sound_asset(muted: bool, level: str = "high") -> str:
    """Sound bitmap name — mute when muted, else the volume level."""
    if muted:
        return "mute"
    return {"low": "vol_low", "medium": "vol_med", "med": "vol_med"}.get(
        level, "vol_high")


def status_states(fix_3d: bool, ifaces, usb_parts, handshake_enabled: bool
                  ) -> dict:
    """The four state-LED booleans (GPS/EXT/USB/PCAP)."""
    return {
        "GPS": bool(fix_3d),
        "EXT": external_adapter_present(ifaces),
        "USB": bool(usb_parts),
        "PCAP": bool(handshake_enabled),
    }


# ── drawing ──────────────────────────────────────────────────────────────────

def _load(p, name: str):
    """Return (handle, w, h) for an icon, or None. Cached.

    Note: the firmware's pagerctl PNG decoder only handles 8-bit truecolour
    RGB (PNG colour type 2, like ``assets/background.png``). Palette (type 3)
    or RGBA (type 6) icons decode to scrambled pixels on-device, so every file
    in ``assets/icons/`` is stored as RGB with any transparency flattened onto
    the bar background (``bg_dim``). Keep new icons in that format.
    """
    if name in _icon_cache:
        return _icon_cache[name]
    if name in _icon_missing:
        return None
    path = os.path.join(_ICON_DIR, name + ".png")
    if os.path.isfile(path):
        try:
            import struct
            with open(path, "rb") as fh:
                w, h = struct.unpack(">II", fh.read(24)[16:24])
            entry = (p.load_image(path), int(w), int(h))
            _icon_cache[name] = entry
            return entry
        except Exception:
            pass
    _icon_missing.add(name)
    return None


def draw_status_bar(p, pal, title: str, states: dict,
                    clock: str | None = None) -> None:
    """Render the firmware-style top bar: screen title (left), indicators +
    clock (right-aligned).

    ``states`` carries the LED booleans (``GPS``/``EXT``/``USB``/``PCAP``) and
    the value-icon asset names (``sound``/``bri``/``ghz``/``batt``); a missing or
    None value skips that indicator.
    """
    from .theme import HEADER_H, FONT_TITLE, FONT_BODY, CHAR_W

    p.fill_rect(0, 0, p.width, HEADER_H, pal.bg_dim)
    p.hline(0, HEADER_H, p.width, pal.cyan)

    if clock is None:
        try:
            clock = time.strftime("%I:%M %p")
        except Exception:
            clock = ""

    ty = 7
    x = p.width - 6

    if clock:
        cw = p.text_width(clock, FONT_BODY)
        x -= cw
        p.draw_text(x, ty, clock, pal.cyan, FONT_BODY)
        x -= 10

    def blit(name: str) -> None:
        nonlocal x
        ent = _load(p, name)
        if not ent:
            return
        _h, w, h = ent
        x -= w
        try:
            p.draw_image_scaled(x, (HEADER_H - h) // 2, w, h, ent[0])
        except Exception:
            pass
        x -= 6

    def text(label: str, lit: bool) -> None:
        nonlocal x
        w = p.text_width(label, FONT_BODY)
        x -= w
        p.draw_text(x, ty, label, pal.cyan if lit else pal.fg_dim, FONT_BODY)
        x -= 8

    from .theme import CHAR_H

    def blit_battery(name: str, pct) -> None:
        """Battery icon; on the no-bolt `batt_text` the level is drawn on it,
        at the clock font size (FONT_BODY) so it nearly fills the battery — as
        the stock firmware does."""
        nonlocal x
        ent = _load(p, name)
        if not ent:
            return
        _h, w, h = ent
        x -= w
        bx, by = x, (HEADER_H - h) // 2
        try:
            p.draw_image_scaled(bx, by, w, h, ent[0])
        except Exception:
            pass
        if name == "batt_text" and pct is not None:
            # 100 shows as "100"; one/two-digit levels get a trailing "%".
            v = int(pct)
            s = "100" if v >= 100 else f"{v}%"
            tw = p.text_width(s, FONT_BODY)
            tx = bx + max(0, (w - tw) // 2)
            ty2 = by + (h - CHAR_H * FONT_BODY) // 2
            p.draw_text(tx, ty2, s, pal.green, FONT_BODY)
        x -= 6

    def put(key: str) -> None:
        if key in _LED_ICONS:
            lit = bool(states.get(key))
            ent = _load(p, f"{_LED_ICONS[key]}_{'on' if lit else 'off'}")
            if ent:
                blit(f"{_LED_ICONS[key]}_{'on' if lit else 'off'}")
            else:
                text(key, lit)
        elif key in _LED_TEXT:
            text(key, bool(states.get(key)))
        elif key == "batt":
            name = states.get("batt")
            if name:
                blit_battery(name, states.get("batt_pct"))
        else:  # value icon
            name = states.get(key)
            if name:
                blit(name)

    for key in reversed(ORDER):
        put(key)

    # Title last, clipped to whatever space remains left of the indicators so a
    # long screen title (e.g. "TEST CONNECTION") never overlaps the icons.
    if title:
        avail = (x - 6) - 6
        max_chars = max(0, avail // (CHAR_W * FONT_TITLE))
        shown = title[:max_chars]
        if shown:
            p.draw_text(6, 7, shown, pal.fg, FONT_TITLE)
