#!/usr/bin/env bash
# Build the release package the in-app updater downloads and verifies:
#   dist/truani-<version>.tar.gz   — source tarball of the committed tree
#   dist/checksums.txt             — sha256 of the tarball (sha256sum format)
#
# Uses the COMMITTED tree (git archive HEAD), not the working tree, so a release
# always reflects exactly what was tagged. Run from the commit you are releasing.
#
# Usage:
#   bash scripts/build-release.sh            # build from HEAD's VERSION
#   TAG=v0.1.5 bash scripts/build-release.sh # also assert VERSION matches the tag
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
NAME="truani-${VERSION}"
OUT="dist"

# Guard: if a TAG is provided (CI), it must match the VERSION file. Catches the
# classic "forgot to bump VERSION" release mistake before assets are published.
if [ -n "${TAG:-}" ] && [ "v${VERSION}" != "${TAG}" ]; then
  echo "ERROR: VERSION file (${VERSION}) does not match tag (${TAG})." >&2
  echo "Bump VERSION to ${TAG#v} and re-tag." >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"

# Wrap in a single top-level dir (truani-<version>/) so the updater's
# _find_app_root() locates app.py one level down — same shape as GitHub's
# auto-generated source archive.
git archive --format=tar.gz --prefix="${NAME}/" -o "${OUT}/${NAME}.tar.gz" HEAD

( cd "$OUT" && sha256sum "${NAME}.tar.gz" > checksums.txt )

echo "Built release assets:"
echo "  ${OUT}/${NAME}.tar.gz"
echo "  ${OUT}/checksums.txt"
echo
cat "${OUT}/checksums.txt"
