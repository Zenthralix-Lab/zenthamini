#!/usr/bin/env bash
# Build the ZenthaMini release artifacts in dist/:
#   zenthmini_<version>.tar.gz    portable tarball (source only, no runtime data)
#   zenthmini_<version>_all.deb   Debian/Ubuntu package (needs dpkg-deb)
#   SHA256SUMS                    checksums the updater verifies before staging
#
# Usage: bash dist/build.sh [--tar-only]
set -euo pipefail

dist=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$dist/.." && pwd)
cd "$root"

die() { printf 'build.sh: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

build_deb=1
for arg in "$@"; do
  case "$arg" in
    --tar-only) build_deb=0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

version=$(tr -d ' \t\r\n' < Shared/VERSION)
[ -n "$version" ] || die "Shared/VERSION is empty"
case "$version" in *[!0-9A-Za-z._-]*) die "invalid version in Shared/VERSION: $version" ;; esac
name="zenthmini_$version"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/zenthmini-build.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# Package tracked source only: chats, logs, models, engines, tokens, TLS keys,
# caches and earlier artifacts are never shipped in a release.
if git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$root" -c core.quotePath=false ls-files > "$tmp/files.txt"
else
  find . \( -name .git -o -name dist -o -name __pycache__ -o -name .pytest_cache \
      -o -path ./Shared/chat_data -o -path ./Shared/logs -o -path ./Shared/bin \
      -o -path ./Shared/lib -o -path ./Shared/models -o -path ./Shared/vendor \
      -o -path ./Shared/.engines -o -path ./Shared/.ollama-runtime \
      -o -path ./Shared/python \) -prune -o -type f -print \
    | sed 's|^\./||' > "$tmp/files.txt"
fi
grep -v '^\.github/' "$tmp/files.txt" > "$tmp/ship.txt" || true
[ -s "$tmp/ship.txt" ] || die "nothing to package"

tarball="$dist/$name.tar.gz"
rm -f "$tarball"
tar -czf "$tarball" -C "$root" -T "$tmp/ship.txt"
say "built dist/$name.tar.gz ($(wc -c < "$tarball" | tr -d ' ') bytes)"

if [ "$build_deb" = 1 ]; then
  if command -v dpkg-deb >/dev/null 2>&1; then
    stage="$tmp/deb"
    mkdir -p "$stage/DEBIAN" "$stage/opt/zenthmini" "$stage/var/lib/zenthmini"
    tar -xzf "$tarball" -C "$stage/opt/zenthmini"
    cat > "$stage/DEBIAN/control" <<EOF
Package: zenthmini
Version: $version
Section: utils
Priority: optional
Architecture: all
Installed-Size: $(du -sk "$stage/opt" | cut -f1)
Maintainer: Zenthralix <zenthralixlab@gmail.com>
Homepage: https://zenthralix.vercel.app
Description: ZenthaMini - local AI chat by Zenthralix
 Self-contained local chat environment served on http://localhost:3333.
 Models, engines, chats and logs live outside the package; the updater
 installs source releases only.
EOF
    cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
  mkdir -p /var/lib/zenthmini/chat_data /var/lib/zenthmini/logs
  for d in chat_data logs; do
    if [ ! -e "/opt/zenthmini/Shared/$d" ]; then
      ln -s "/var/lib/zenthmini/$d" "/opt/zenthmini/Shared/$d"
    fi
  done
fi
EOF
    cat > "$stage/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
  for d in chat_data logs; do
    if [ -L "/opt/zenthmini/Shared/$d" ]; then rm -f "/opt/zenthmini/Shared/$d"; fi
  done
fi
EOF
    chmod 0644 "$stage/DEBIAN/control"
    chmod 0755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm"
    deb="$dist/${name}_all.deb"
    rm -f "$deb"
    owner=()
    if dpkg-deb --help 2>/dev/null | grep -q -- '--root-owner-group'; then
      owner=(--root-owner-group)
    fi
    dpkg-deb --build ${owner[@]+"${owner[@]}"} "$stage" "$deb" >/dev/null
    say "built dist/${name}_all.deb"
  else
    say "skipping .deb: dpkg-deb not found"
  fi
fi

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

cd "$dist"
: > SHA256SUMS.tmp
for artifact in zenthmini_*.tar.gz zenthmini_*_all.deb; do
  [ -f "$artifact" ] || continue
  hash_file "$artifact" >> SHA256SUMS.tmp
done
LC_ALL=C sort -k2 SHA256SUMS.tmp > SHA256SUMS
rm -f SHA256SUMS.tmp
say "wrote dist/SHA256SUMS"
