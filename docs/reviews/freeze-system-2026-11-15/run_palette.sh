#!/usr/bin/env bash
# The board runner, one palette per call (freeze cycle 4, lane BOARD; the lander's per-integration copies, made one).
#
#   BOARD_PY=<venv python> BOARD_DEV=<dev checkout> [BOARD_PEERS=<peers dir>] [BOARD_FRIBIDI=<dir>] \
#     run_palette.sh <palette> <tree> <out root> [workers] [extra board.py args...]
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
peers=()
[ -n "${BOARD_PEERS:-}" ] && peers=(--peers "$BOARD_PEERS")
brand=()
[ "$palette" != default ] && brand=(--brand "$palette")
here="$tree/docs/reviews/freeze-system-2026-11-15"
cd "$here" || exit 9
mkdir -p "$root"
start=$(date +%s)
echo "start $(date -Iseconds) palette=$palette tree=$tree workers=$workers" > "$root/$palette.log"
timeout 32400 "$PY" board.py "$tree" "$DEV" "$root/$palette" "${brand[@]}" --workers "$workers" "${peers[@]}" "$@" \
  >> "$root/$palette.log" 2>&1
rc=$?
end=$(date +%s)
echo "exit $rc seconds $((end-start)) $(date -Iseconds)" > "$root/$palette.done"
echo "exit $rc seconds $((end-start)) $(date -Iseconds)" >> "$root/$palette.log"
exit $rc
