# QF-139: the delivery encoder pins x264's thread count, so a master is byte-identical at every core count

Proposed by the QF-29a build (2026-11-15, golden images): QF-134 made every delivered and judged picture use the
same libx264 arguments on every machine, but not the same bytes. Parent: 18049e0e (QF-35a x QF-29a; the encoders as
QF-134 e49616fc left them). Round 2 after review 1 (production's core count, the review proxy, the scan).

## The problem, measured

x264's output depends on its thread count (frame threads constrain motion search and rate control; the count is
also written into the stream). Unless told, x264 takes 1.5 threads per logical processor the process may run on.
Before this row:

| encode | thread count it got | 32-processor dev PC | production VM (6 processors) | CI runner (4) |
|---|---|---|---|---|
| Reel and documentary master, review proxy | `-threads os.cpu_count()` | 32 | 6 | 4 |
| loop bridge body, micro-patched master, graphic, then-and-now, footage, motion, parallax clips | x264's own | 48 | 9 | 6 |

The production VM's 6 logical processors are read from the read-only production sample: the one libx264 run in it
(rendered on production 2026-11-06, with the code that passed `-threads os.cpu_count()` to the master) carries
`threads=6` in its master and `threads=9` (x264's own on 6 processors) in its two graphic clips. (Round 1 took the
QF-12 build note's unsourced "4 vCPU"; review 1 corrected it.) GitHub's standard Linux runner for a public
repository has 4.

The review proxy has a second problem: it is rate-capped (`-maxrate`/`-bufsize`), and x264's frame-threaded rate
control is not deterministic even at a fixed thread count: the same machine gives a different file run to run.

## The fix

- `src/config.py`: `VIDEO_ENCODE_THREADS = 6` among the locked production specifications (not a setting: a
  machine may not choose it; `.env.example` says so beside `VIDEO_PREVIEW_ENCODER`). 6 is the count production's
  masters were already encoded with, so production's master encoder setting does not change.
- `src/encoding.py`: `X264_THREADS = ["-threads", "6"]`, carried by `delivery_video_encoder()` and
  `delivery_intermediate_encoder()` (so the master manifest's `video_encoder` record names it, and the graphic clip
  cache key changes once); `X264_ONE_THREAD = ["-threads", "1"]` for rate-capped encodes.
- `src/video_composer.py`: the master's `-threads os.cpu_count()` output argument is gone (the encoder carries the
  fixed count); the review proxy encodes with `X264_ONE_THREAD`. Input-side `-threads` (decoders, bit-exact) keep the
  machine's count.
- `src/footage.py`, `src/motion.py`, `src/parallax.py`, `src/then_now.py`: their libx264 encodes, which are cut
  into the master, pass `X264_THREADS`.

x264's other thread-dependent parameters follow from the count (`lookahead_threads=1` at 6 threads, slow and
veryfast presets); sliced threads stay off. CRF, preset, pixel format, tune and every other argument are unchanged.

### What changes on production (release note)

- Masters (Reel, documentary): encoder setting unchanged (6 before and after). The documentary master made from
  the same inputs on 6 processors is the same file before and after (`sha256` in `before.json` / `after.json`). A
  Reel master changes bytes once, only because its loop-bridge body intermediate is now encoded with 6 threads
  instead of 9.
- Graphic, then-and-now, footage, motion and parallax clips, the loop-bridge body and micro-patched masters: 9 -> 6
  threads; they change bytes once (and so do the masters they are cut into).
- Review proxy: 6 threads (non-deterministic) -> 1 thread. Encode time below.
- Cached graphic clips re-encode once (their key names the encoder and now its thread count).

## Acceptance

`measure.py <repo root>` runs every libx264 path that reaches the delivered picture or the review proxy for real
(local ffmpeg, generated inputs) on four simulated machines: 32, 6, 4 and 2 logical processors. A machine is
simulated by both things the code and the encoder read: `os.cpu_count()` returns the count, and the process affinity
(inherited by every ffmpeg) is restricted to that many processors. The streams prove the simulation reached x264
(before: 48 / 9 / 6 / 3 threads where x264 chose). The review proxy is made three times on every machine from a busy
clip whose bitrate is held at the cap (1,699 to 1,705 kbit/s over 6 s against `-maxrate 1500k -bufsize 3000k`), where
frame-threaded rate control can differ. `before.json` (18049e0e), `after.json`:

| | before | after |
|---|---|---|
| surfaces (of 8) whose file differs between machines or runs | 8 | 0 |
| x264 threads written into the stream, 32 / 6 / 4 / 2 processors | 32/6/4/2 or 48/9/6/3 | 6/6/6/6 (proxy 1/1/1/1) |
| review proxy: distinct files in 12 runs (3 per machine) | 12 (3 on every one machine) | 1 |

Surfaces: graphic clip, then-and-now clip, footage segment, motion clip, Reel master ending on the loop bridge,
documentary master, micro-patched master, review proxy (rate-capped).

## Two real development Reels

`real_runs.py <repo root> <work dir> <master> <master>` uses two real Reel masters from the development runs (read
only; 47.7 s and 46.7 s, 1080x1920; `real_runs.json`). Simulated machines as in `measure.py`.

Masters, encoded again with the master's delivery arguments on 32, 6 and 4 processors:

| master | distinct files, parent (`-threads os.cpu_count()`) | this row |
|---|---|---|
| Reel 1 | 3 (threads 32 / 6 / 4) | 1 (threads 6; SHA-256 `4bae7036...`) |
| Reel 2 | 3 (threads 32 / 6 / 4) | 1 (threads 6; SHA-256 `ed0355da...`) |

On 6 processors (the production VM) the parent's file and this row's are the same file for both Reels: the
production master command does not change.

Review proxy, from the real master (about 10 Mbit/s, far above the proxy's 1.5 Mbit/s cap):

| Reel | parent: 3 runs on 32 processors + 1 on 6 | this row (1 thread): 3 on 32 + 1 on 6 + 1 on 4 |
|---|---|---|
| 1 | 4 distinct files | 1 file (`d21e0b17...`) |
| 2 | 4 distinct files | 1 file (`9e66bb88...`, the file review 1 measured with one thread) |

### Encode cost

All times on this PC (wall time depends on what else ran; CPU time much less):

| | Reel 1 | Reel 2 |
|---|---|---|
| master on 6 processors, before and after (same command) | 136.3 / 121.5 s | 109.4 / 116.1 s |
| clips on 6 processors (indicative: this master at x264's own 9 threads vs 6) | 106.9 -> 121.5 s wall, 533.5 -> 481.1 s CPU | 112.7 -> 116.1 s wall, 526.4 -> 442.1 s CPU |
| master on 32 processors (dev PC): parent 32 threads vs 6 | 57.0 -> 113.9 s wall, 932.9 -> 485.4 s CPU | 51.0 -> 92.7 s wall, 763.4 -> 382.1 s CPU |
| review proxy on 6 processors: parent 6 threads vs 1 | 16.8 -> 30.9 s | 25.6 -> 42.4 s |
| review proxy on 32 processors: parent 32 threads vs 1 | 8.4-8.7 -> 26.6-28.6 s | 11.2-12.4 -> 47.7-53.2 s |

On production the master encode is unchanged; the clips take 3% and 14% more wall time and 10% and 16% less CPU
time on this indicative measure (graphic clips are a few seconds long, so seconds per run). The proxy, which the
operator judges in review, takes 14 to 17 s more on 6 processors. On the development PC a master encode takes 1.8 to 2
times as long (it no longer spreads over 32 processors) for half the CPU time: about a minute more per Reel, the
price of judging the bytes production makes.

## Linux with CI's ffmpeg build

`linux_leg.sh` encoded the two Reels with the same fixed master arguments in Ubuntu 24.04 (WSL on this PC) with CI's
pinned ffmpeg (QF-116: BtbN n8.1-7-ga3475e2554), on 2 and 1 logical processors (`taskset`), and the hashes were
compared with this PC's encode (`linux_ci_build.json`, measured in round 1 with the same arguments):

| | Linux 2 vs 1 processors | Linux vs Windows (dev/prod build) |
|---|---|---|
| whole file | identical | different |
| decoded picture (every frame) | identical | **identical** |
| H.264 stream without its SEI (x264's version/options text) | identical | **identical** |
| AAC audio stream and its decoded samples | identical | different |

So with the thread count fixed, x264 makes the same picture bitstream on Windows and Linux on this CPU. The files
still differ for two reasons outside this row, both build differences, not core counts: CI's x264 writes `core 165`
without its revision (dev/prod: `core 165 r3223 0480cb0`) into the stream's SEI text, and ffmpeg's native AAC
encoder gives different bits in the two builds (the decoded input audio is identical on both).

## Known limits

- Proven across core counts with the same ffmpeg build on the same CPU instruction set. x264's output can also
  depend on which code path it runs (review 1: `-x264-params asm=0` gives a different file), so a production CPU
  with other SIMD extensions than this PC's could give other bytes; QF-29a's video check compares the decoded
  pictures for that reason. Byte identity of the whole file with CI also needs the same ffmpeg build (above).
- Encode time on the development PC rises (6 threads instead of 32 or 48 on 32 processors); see the cost table.
- Parallax clips need the depth model, so `measure.py` does not run them; the regression test checks their command.
- A new encode path: `test_every_libx264_encode_in_the_code_carries_the_fixed_count` parses `src/` and `main.py`
  and fails on (1) `libx264` in code (not a docstring or a comparison) outside an argument list that carries
  `*X264_THREADS` or `*X264_ONE_THREAD`, (2) a rate-capped argument list (`-maxrate`/`-bufsize`) without
  `*X264_ONE_THREAD`, (3) an output-side `"-threads"` (no input after it in the same list) other than those two,
  which would override them (ffmpeg takes the last). Exempt: the hardware probe's preview fallback
  (`encoding.get_video_encoder`), the two definitions, and the still camera's lossless FFV1 intermediates.
  Mutations: removing the pin from footage.py or from `delivery_video_encoder`, putting the original
  `*encoder_args, "-threads", str(os.cpu_count() or 0)` back into the master's arguments, or giving the proxy
  `X264_THREADS` each make it fail, naming file and line. A runtime codec chosen from a variable is not visible to it.

Files: `measure.py`, `before.json`, `after.json`, `real_runs.py`, `real_runs.json`, `linux_leg.sh`,
`linux_ci_build.json`.
