#!/bin/sh
# Fetch the pinned lychee-contract into ./.contract.
#
# Reads ./contract.lock ({"tag": "vX.Y.Z", "commit": "<40-hex sha>"}).
#   LYCHEE_CONTRACT_DIR=<path>  use a local checkout instead of downloading
#                               (development before a tag exists; warns if its
#                               HEAD differs from the pin)
#   LYCHEE_CONTRACT_URL=<base>  override the archive base URL (tests only)
# Needs: curl, tar, python3 (and git for LYCHEE_CONTRACT_DIR).
set -eu

[ -f contract.lock ] || { echo "fetch-contract: contract.lock not found in $(pwd)" >&2; exit 1; }

read_key() {
  python3 -c 'import json,sys;print(json.load(open("contract.lock"))[sys.argv[1]])' "$1"
}
tag=$(read_key tag)
commit=$(read_key commit)
short=$(printf %s "$commit" | cut -c1-7)

if [ -n "${LYCHEE_CONTRACT_DIR:-}" ]; then
  head=$(git -C "$LYCHEE_CONTRACT_DIR" rev-parse HEAD)
  if [ "$head" != "$commit" ]; then
    echo "WARNING: LYCHEE_CONTRACT_DIR HEAD $head differs from pinned commit $commit" >&2
  fi
  rm -rf .contract
  mkdir .contract
  git -C "$LYCHEE_CONTRACT_DIR" archive HEAD | tar -x -C .contract
  printf %s "$head" > .contract/.commit
  echo "contract: $tag ($short) from LYCHEE_CONTRACT_DIR"
  exit 0
fi

if [ -f .contract/.commit ] && [ "$(cat .contract/.commit)" = "$commit" ]; then
  echo "contract: $tag ($short) from cache"
  exit 0
fi

base=${LYCHEE_CONTRACT_URL:-https://codeload.github.com/LycheeHome/lychee-contract/tar.gz}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$base/$commit" -o "$tmp/archive.tgz"
mkdir "$tmp/x"
tar -xzf "$tmp/archive.tgz" -C "$tmp/x"
top=$(ls "$tmp/x")
case "$top" in
  *"
"*) echo "fetch-contract: archive has more than one top-level entry" >&2; exit 1 ;;
esac
got=${top#lychee-contract-}
if [ "$got" != "$commit" ] || [ "$top" = "$got" ]; then
  echo "fetch-contract: archive commit $got does not match pinned commit $commit" >&2
  exit 1
fi
rm -rf .contract
mv "$tmp/x/$top" .contract
printf %s "$commit" > .contract/.commit
echo "contract: $tag ($short) from download"
