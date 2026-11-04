#!/bin/sh
# AGU10 verification 9.6 / AGENT_UPDATE_11 item 3/9: three sanitized headless
# runs of test2 (Fast mode), one per retention level, from the packaged exe.
# Each run gets its own sanitized APPDATA/LOCALAPPDATA under
# <build root>/tmp/ (never .freebuff), a shared HF cache and shared model dir
# (no re-download), and a settings file with the level to measure.
# AGENT_UPDATE_11 item 3: the sanitized HOME of a run is REMOVED when the run
# ends (the log stays in <build root>/logs/), and the run output directory is
# recreated fresh so a re-run never hits `output_exists`.
set -u
BUILD_ROOT="D:/Projects/Vibe/Project_K_build"
EXE="$BUILD_ROOT/dist/DubTool/DubTool.exe"
SRC="D:/Projects/Vibe/Project_K"
cd "$SRC" || exit 2

set -a
. ./.env 2>/dev/null || true
set +a

for LEVEL in none light full; do
  echo "=== keep_level=$LEVEL ==="
  HOME_DIR="$BUILD_ROOT/tmp/rt_$LEVEL/home"
  rm -rf "$BUILD_ROOT/tmp/rt_$LEVEL"      # fresh sanitized home (item 3)
  rm -rf "$BUILD_ROOT/tmp/rout_$LEVEL"    # fresh output dir: no output_exists on re-runs
  mkdir -p "$HOME_DIR" "$BUILD_ROOT/tmp/rout_$LEVEL" "$BUILD_ROOT/logs"
  APPD="$HOME_DIR/AppData/Roaming/DubTool"
  mkdir -p "$APPD"
  printf '{\n  "keep_level": "%s",\n  "privacy_acknowledged": true\n}\n' "$LEVEL" > "$APPD/settings.json"
  APPDATA="$HOME_DIR/AppData/Roaming" \
  LOCALAPPDATA="$HOME_DIR/AppData/Local" \
  HF_HOME="$BUILD_ROOT/hf-cache" \
  HF_HUB_CACHE="$BUILD_ROOT/hf-cache" \
  DUBTOOL_MODELS_DIR="$BUILD_ROOT/models-cache" \
  "$EXE" --headless-run "D:/Projects/Vibe/Project_K/sample/input/test2.mp4" \
      "$BUILD_ROOT/tmp/rout_$LEVEL" --mode fast > "$BUILD_ROOT/logs/rt_$LEVEL.log" 2>&1
  echo "run exit: $?"
  tail -3 "$BUILD_ROOT/logs/rt_$LEVEL.log"
  rm -rf "$BUILD_ROOT/tmp/rt_$LEVEL"      # sanitized homes are removed when the run ends (item 3)
done
ls "$BUILD_ROOT/tmp" 2>/dev/null | grep -c "rt_" | sed 's/^/leftover rt_ dirs: /'
