#!/usr/bin/env bash
# Version and release notes of this checkout, for the CI and Release workflows.
#
#   release-info.sh version   the version; server, agent and integration must agree
#   release-info.sh check     a version that has no tag on origin yet needs its notes
#   release-info.sh title     release title: first line of the notes, without "# "
#   release-info.sh body      release notes: the rest of the file
#   release-info.sh released  exit 0 if origin has the tag, 3 if not, 1 on errors
#
# Notes live in release-notes/v<version>.md. The first line is "# <title>".
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

die() {
  echo "::error::$*" >&2
  exit 1
}

version_of() { sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$1"; }

version() {
  local server agent manifest
  server=$(version_of server/src/haushalttickets_server/__init__.py)
  agent=$(version_of agent/src/haushalttickets_agent/__init__.py)
  manifest=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
    custom_components/haushalttickets/manifest.json)
  [[ -n $server ]] || die "No __version__ in the server package"
  [[ $agent == "$server" ]] || die "Agent version $agent differs from server version $server"
  [[ $manifest == "$server" ]] || die "Integration version $manifest differs from server version $server"
  echo "$server"
}

notes() {
  local file
  file="release-notes/v$(version).md"
  [[ -f $file ]] || die "$file is missing (first line: # <release title>, then the notes)"
  echo "$file"
}

title() {
  local file line
  file=$(notes)
  line=$(head -n 1 "$file")
  [[ $line == "# "* ]] || die "$file must start with '# <release title>'"
  line=${line#"# "}
  [[ -n ${line//[[:space:]]/} ]] || die "$file has an empty title"
  echo "$line"
}

body() {
  local file text
  file=$(notes)
  # Everything after the title, without leading blank lines.
  text=$(tail -n +2 "$file" | sed '/[^[:space:]]/,$!d')
  [[ -n $text ]] || die "$file has no notes below the title"
  echo "$text"
}

released() {
  # 0: tag exists on origin, 1: it does not. Anything else is an error.
  local status=0
  git ls-remote --exit-code --tags origin "refs/tags/v$1" >/dev/null || status=$?
  case $status in
    0) return 0 ;;
    2) return 1 ;;
    *) die "Could not ask origin for tag v$1 (git ls-remote exit $status)" ;;
  esac
}

check() {
  local v
  v=$(version)
  if released "$v"; then
    echo "v$v is already released; bump the version to release again."
    return
  fi
  title >/dev/null
  body >/dev/null
  echo "v$v will be released when this reaches main: $(title)"
}

case ${1:-} in
  version | check | title | body) "$1" ;;
  released) if released "$(version)"; then exit 0; else exit 3; fi ;;
  *) die "usage: $0 version|check|title|body|released" ;;
esac
