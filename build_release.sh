#!/bin/sh
# Build a release archive of the WDGoWars payload, ready to unpack and scp
# onto the pager. This is the "compiled file for releases": a clean, LF-only
# bundle of the wdgwars/ payload tree plus README/CHANGELOG, with every Python
# module byte-compiled first as a release gate.
#
# Usage:
#   sh build_release.sh [VERSION]
#
# VERSION defaults to the `# Version:` line in wdgwars/payload.sh with the
# current git short SHA appended (e.g. 1.1-g1a2b3c4), so an untagged build is
# still uniquely identifiable. Pass an explicit version for a tagged release:
#   sh build_release.sh 1.2.0
#
# Outputs into dist/ (gitignored):
#   wdgwars-<version>.tar.gz          the release archive
#   wdgwars-<version>.zip             same, for Windows users (if `zip` present)
#   wdgwars-<version>.tar.gz.sha256   checksum

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# ── version ──────────────────────────────────────────────────────────────────
BASE_VER="$(sed -n 's/^# Version:[[:space:]]*//p' wdgwars/payload.sh | head -1)"
[ -n "$BASE_VER" ] || BASE_VER="0.0"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
VERSION="${1:-${BASE_VER}-g${SHA}}"
NAME="wdgwars-${VERSION}"

echo "[release] building $NAME"

# ── stage ────────────────────────────────────────────────────────────────────
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/$NAME"
mkdir -p "$ROOT"

# The payload tree is the deliverable; README/CHANGELOG ride along for context.
cp -r wdgwars "$ROOT/wdgwars"
cp README.md CHANGELOG.md "$ROOT/" 2>/dev/null || true

# Strip anything that is dev scratch, runtime state, or a secret — the release
# ships a template-only config.json; the real API key is injected on-device.
find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$ROOT" -name '*.pyc' -delete 2>/dev/null || true
rm -rf "$ROOT/wdgwars/data" "$ROOT/wdgwars/lib"
find "$ROOT/wdgwars" -name '*.key' -delete 2>/dev/null || true
find "$ROOT/wdgwars" -name 'config.local.json' -delete 2>/dev/null || true

# Normalise shell scripts to LF — the pager's shell chokes on CRLF (see README).
find "$ROOT" -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# ── release gate: every module must byte-compile ─────────────────────────────
echo "[release] byte-compiling payload (compile gate)"
python3 -m compileall -q "$ROOT/wdgwars"
# The .pyc are only a build-time check; the pager compiles on first run.
find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# ── package ──────────────────────────────────────────────────────────────────
mkdir -p dist
TARBALL="dist/${NAME}.tar.gz"
tar -C "$STAGE" -czf "$TARBALL" "$NAME"
echo "[release] wrote $TARBALL"

if command -v zip >/dev/null 2>&1; then
    ZIP="dist/${NAME}.zip"
    rm -f "$ZIP"
    ( cd "$STAGE" && zip -qr "$REPO_DIR/$ZIP" "$NAME" )
    echo "[release] wrote $ZIP"
else
    echo "[release] note: \`zip\` not found — skipped .zip (tar.gz still built)"
fi

# ── checksum + manifest ──────────────────────────────────────────────────────
if command -v sha256sum >/dev/null 2>&1; then
    ( cd dist && sha256sum "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" )
    echo "[release] wrote dist/${NAME}.tar.gz.sha256"
fi

echo "[release] contents:"
tar -tzf "$TARBALL" | sed 's/^/    /'

SIZE="$(du -h "$TARBALL" | cut -f1)"
echo "[release] done — $TARBALL ($SIZE)"
echo
echo "Deploy:  tar xzf ${NAME}.tar.gz"
echo "         scp -r ${NAME}/wdgwars root@172.16.52.1:/mmc/root/payloads/user/reconnaissance/wdgwars"
echo "         ssh root@172.16.52.1 'cd /mmc/root/payloads/user/reconnaissance/wdgwars && sh bootstrap.sh'"
