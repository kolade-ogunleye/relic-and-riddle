#!/usr/bin/env bash
# The board runner, one palette per call (freeze cycle 4, lane BOARD; the lander's per-integration copies, made one).
#
#   BOARD_PY=<venv python> BOARD_DEV=<dev checkout> [BOARD_PEERS=<peers dir>] [BOARD_FRIBIDI=<dir>] \
#     run_palette.sh <palette> <tree> <out root> [workers] [extra board.py args...]
#
# BOARD_PREV=<previous out root> (cycle 5, optional): the out root of the previous integration's boards. Its
# <palette>/surfaces.json balances the shards by each job's real seconds (--timings: 19 % off the surfaces phase at
# 4 workers against the per-kind defaults, computed from integration 4's job times). No image is reused: the board
# renders in full.
#
# BOARD_UNSAFE_REUSE_FOR_ITERATION=1 (round 3, iteration only, NEVER evidence; queue/REVIEW2-board-c5.md R1-R5):
# with BOARD_PREV holding DEPS.json and MANIFEST.json, it is also the --reuse base (board.py
# --unsafe-reuse-for-iteration): the board is stamped not for the panel and every copied tile, surface file and
# sheet says REUSED, NOT EVIDENCE.
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
  if [ "${BOARD_UNSAFE_REUSE_FOR_ITERATION:-}" = 1 ] && [ -f "$BOARD_PREV/$palette/DEPS.json" ] \
     && [ -f "$BOARD_PREV/$palette/MANIFEST.json" ]; then
    prev+=(--reuse "$BOARD_PREV/$palette" --unsafe-reuse-for-iteration)   # iteration only: stamped not evidence
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
