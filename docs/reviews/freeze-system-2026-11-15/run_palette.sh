#!/usr/bin/env bash
# The board runner, one palette per call (freeze cycle 4, lane BOARD; the lander's per-integration copies, made one).
#
#   BOARD_PY=<venv python> BOARD_DEV=<dev checkout> [BOARD_PEERS=<peers dir>] [BOARD_FRIBIDI=<dir>] \
#     run_palette.sh <palette> <tree> <out root> [workers] [extra board.py args...]
#
# BOARD_PREV=<previous out root> (cycle 5, optional): the out root of the previous integration's boards. Its
# <palette>/surfaces.json balances the shards by each job's real seconds (--timings: 19 % off the surfaces phase at
# 4 workers against the per-kind defaults, computed from integration 4's job times), and when that board holds the
# code record (DEPS.json) and a MANIFEST.json it is the --reuse base: only the jobs whose inputs changed are
# rendered (board_reuse.py).
#
# BOARD_TMP=<folder> (cycle 5 fix 1, optional): the temp folder of this board's processes (frames cut from the
# runs, the builders' store). An empty one, with a tree that has no cache/, makes the board observe every on-disk
# cache being built, so no job falls back to the whole-code key (board_deps.resolve). Keep it for the next board.
#
# <palette>: default | second | second-colours (board.py PALETTES). Writes <out root>/<palette>/ (the board),
# <out root>/<palette>.log and <out root>/<palette>.done ("exit <rc> seconds <s> <time>"). No path is written into
# this file: the interpreter, the dev checkout and the peers come from the environment. Hold a keep-awake around it
# (a full board runs for hours).
set -u
palette="${1:?palette: default | second | second-colours}"
tree="${2:?tree: the clean worktree under review}"
root="${3:?out root}"
workers="${4:-4}"
shift $(( $# < 4 ? $# : 4 ))
case "$palette" in default|second|second-colours) ;; *) echo "unknown palette $palette" >&2; exit 2 ;; esac
PY="${BOARD_PY:?set BOARD_PY to the venv python}"
DEV="${BOARD_DEV:?set BOARD_DEV to the dev checkout (output/run_*, assets/)}"
[ -n "${BOARD_FRIBIDI:-}" ] && export PATH="$PATH:$BOARD_FRIBIDI"
if [ -n "${BOARD_TMP:-}" ]; then mkdir -p "$BOARD_TMP" && export TMP="$BOARD_TMP" TEMP="$BOARD_TMP" TMPDIR="$BOARD_TMP"; fi
peers=()
[ -n "${BOARD_PEERS:-}" ] && peers=(--peers "$BOARD_PEERS")
brand=()
[ "$palette" != default ] && brand=(--brand "$palette")
prev=()
if [ -n "${BOARD_PREV:-}" ] && [ -f "$BOARD_PREV/$palette/surfaces.json" ]; then
  prev=(--timings "$BOARD_PREV/$palette/surfaces.json")
  if [ -f "$BOARD_PREV/$palette/DEPS.json" ] && [ -f "$BOARD_PREV/$palette/MANIFEST.json" ]; then
    prev+=(--reuse "$BOARD_PREV/$palette")
  fi
fi
here="$tree/docs/reviews/freeze-system-2026-11-15"
cd "$here" || exit 9
mkdir -p "$root"
start=$(date +%s)
echo "start $(date -Iseconds) palette=$palette tree=$tree workers=$workers" > "$root/$palette.log"
timeout 32400 "$PY" board.py "$tree" "$DEV" "$root/$palette" "${brand[@]}" --workers "$workers" "${peers[@]}" "${prev[@]}" "$@" \
  >> "$root/$palette.log" 2>&1
rc=$?
end=$(date +%s)
echo "exit $rc seconds $((end-start)) $(date -Iseconds)" > "$root/$palette.done"
echo "exit $rc seconds $((end-start)) $(date -Iseconds)" >> "$root/$palette.log"
exit $rc
