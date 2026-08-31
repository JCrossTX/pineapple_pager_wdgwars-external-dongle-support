#!/bin/sh
# Run once on the pager to fetch pagerctl bindings from the wifman payload
# and ensure runtime dependencies are installed.
#
# If this script fails with "Illegal option -" or ": not foundh", the
# file has CRLF line endings (usual cause: cloned/unpacked on Windows).
# Fix with:  sed -i 's/\r$//' bootstrap.sh payload.sh launch_*.sh
# The repo's .gitattributes forces LF on text files so a fresh `git clone`
# should be immune.

PAYLOAD_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="$PAYLOAD_DIR/lib"
WIFMAN_LIB="/root/payloads/user/reconnaissance/wifman/lib"

mkdir -p "$LIB"

echo "[bootstrap] pagerctl bindings"
if [ -f "$WIFMAN_LIB/pagerctl.py" ] && [ -f "$WIFMAN_LIB/libpagerctl.so" ]; then
    cp "$WIFMAN_LIB/pagerctl.py"    "$LIB/"
    cp "$WIFMAN_LIB/libpagerctl.so" "$LIB/"
    echo "  copied from $WIFMAN_LIB"
else
    WIFMAN_BASE="https://raw.githubusercontent.com/LOCOSP/pineapple_pager_wifman/main/wifman/lib"
    for f in pagerctl.py libpagerctl.so; do
        if [ ! -f "$LIB/$f" ]; then
            wget -q -O "$LIB/$f.tmp" "$WIFMAN_BASE/$f" && mv "$LIB/$f.tmp" "$LIB/$f" \
                && echo "  downloaded $f" \
                || echo "  warn: download of $f failed (pager offline?)"
        fi
    done
fi

echo "[bootstrap] opkg runtime packages (best-effort, needs internet)"
opkg update >/dev/null 2>&1 || true
# Core payload deps, plus:
#   kmod-mt7921u / kmod-mt76-usb / kmod-mt7921-firmware — drive the MediaTek
#     MT7921AU in the Alfa AWUS036AXM external WiFi dongle so it comes up as a
#     usable wlanN (bring it to monitor mode as wlan2mon; WDGoWars auto-picks it).
#   kmod-usb-storage + block-mount + filesystem kmods/tools — let the pager
#     read/write a USB stick on the powered hub, so sessions and handshake
#     pcaps can be saved straight to removable media.
for pkg in iw bluez-utils kmod-usb-acm gpsd gpsd-clients tcpdump-mini \
           kmod-mt7921u kmod-mt76-usb kmod-mt7921-firmware \
           kmod-usb-storage block-mount kmod-fs-vfat kmod-fs-exfat dosfstools; do
    opkg list-installed 2>/dev/null | grep -q "^$pkg " \
        || opkg install "$pkg" >/dev/null 2>&1 \
        || echo "  warn: could not install $pkg"
done

echo "[bootstrap] loading cdc_acm for u-blox"
modprobe cdc_acm 2>/dev/null || true

echo "[bootstrap] loading USB modules for external dongle + storage"
# mt7921u brings up the Alfa AWUS036AXM; usb-storage exposes a hub-attached
# USB stick as /dev/sd*. Both are best-effort — the kmods above may only apply
# after a reboot on some firmware builds.
for mod in mt7921u usb-storage; do
    modprobe "$mod" 2>/dev/null || true
done

mkdir -p /mmc/root/loot/wdgwars/sessions

chmod +x "$PAYLOAD_DIR/payload.sh" 2>/dev/null
chmod +x "$PAYLOAD_DIR"/launch_*.sh 2>/dev/null

# Push the reverse launcher (launch_wdgwars.sh) into every installed peer
# payload so they can JUMP TO WDGoWars. Skips peers that aren't installed.
echo "[bootstrap] installing reverse JUMP TO launcher in peers"
RL="$PAYLOAD_DIR/launchers/launch_wdgwars.sh"
if [ -f "$RL" ]; then
    PEERS="/root/payloads/user/reconnaissance"
    for peer in loki pagergotchi wifman pager_bjorn; do
        if [ -d "$PEERS/$peer" ]; then
            cp "$RL" "$PEERS/$peer/launch_wdgwars.sh" \
                && chmod +x "$PEERS/$peer/launch_wdgwars.sh" \
                && echo "  -> $peer"
        fi
    done
else
    echo "  warn: $RL missing, skipping (outgoing JUMP TO still works)"
fi

echo "[bootstrap] done"
