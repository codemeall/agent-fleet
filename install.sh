#!/usr/bin/env bash
# Link the fleet skill into every installed harness and put `fleet` on PATH.
# Idempotent. Never overwrites a real directory or a link it did not create.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SKILL="$ROOT/skill"
BIN_DIR="${FLEET_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="$HOME/.config/agent-fleet"

link() { # link <target> <link-path>
  local target="$1" path="$2"
  if [ -L "$path" ]; then
    if [ "$(readlink "$path")" = "$target" ]; then echo "ok       $path"; return; fi
    echo "SKIP     $path links elsewhere ($(readlink "$path"))"; return
  fi
  if [ -e "$path" ]; then echo "SKIP     $path exists and is not a link"; return; fi
  ln -s "$target" "$path"
  echo "linked   $path"
}

for dir in "$HOME/.claude/skills" "$HOME/.claude-co/skills" "$HOME/.codex/skills" "$HOME/.cursor/skills"; do
  if [ -d "$dir" ]; then link "$SKILL" "$dir/fleet"; else echo "absent   $dir (harness not installed)"; fi
done

mkdir -p "$BIN_DIR"
chmod +x "$SKILL/bin/fleet"
link "$SKILL/bin/fleet" "$BIN_DIR/fleet"

mkdir -p "$CONFIG_DIR"
if [ ! -e "$CONFIG_DIR/config.toml" ]; then
  cp "$ROOT/config.example.toml" "$CONFIG_DIR/config.toml"
  echo "created  $CONFIG_DIR/config.toml"
else
  echo "ok       $CONFIG_DIR/config.toml (kept)"
fi

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "NOTE     add $BIN_DIR to PATH";; esac
echo "next: fleet doctor"
