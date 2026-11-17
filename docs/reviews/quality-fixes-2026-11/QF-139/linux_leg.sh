#!/bin/bash
# QF-139 on Linux with CI's pinned ffmpeg (QF-116: BtbN n8.1-7-ga3475e2554, .github/workflows/tests.yml).
# Usage: bash linux_leg.sh <ffmpeg> <out dir> <master.mp4> [<master.mp4> ...]
# Encodes each master with the fixed delivery arguments (as real_runs.py, -threads 6) on 2 and 1 logical processors
# (taskset) and prints the file's SHA-256. Compare with the same encode made on Windows (round 1 kept real_runs.py's): whole file,
# decoded picture (ffmpeg -map 0:v -f hash), video stream without SEI (-c copy -bsf:v filter_units=remove_types=6
# -f hash) and audio stream (-map 0:a -c copy -f hash).
set -eu
FF=$1
OUT=$2
shift 2
mkdir -p "$OUT"
TAIL="-pix_fmt yuv420p -color_range tv -color_primaries bt709 -color_trc bt709 -colorspace bt709 -r 30 -g 60 -keyint_min 60 -sc_threshold 0 -flags +cgop -c:a aac -b:a 192k -ar 48000 -ac 2 -max_muxing_queue_size 8192 -movflags +faststart"
n=0
for m in "$@"; do
  n=$((n+1))
  for cpus in 2 1; do
    o=$OUT/m${n}_linux_${cpus}.mp4
    # shellcheck disable=SC2086
    taskset $(( (1 << cpus) - 1 )) "$FF" -y -hide_banner -nostats -v error -threads $cpus -i "$m" -map 0:v:0 \
      -map 0:a:0 -c:v libx264 -preset slow -crf 18 -threads 6 $TAIL "$o"
    echo "master $n linux processors=$cpus file=$(sha256sum "$o" | cut -c1-64)"
  done
done
