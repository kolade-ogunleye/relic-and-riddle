"""
src/video_composer.py
=====================
FFmpeg assembly embodying the locked production standards:
1. Anton 92pt running-speech kinetic captions (gold active, white past, gray upcoming),
   zero-overlap ASS events, Meta UI safe zone (MarginV = 540px / y = 1380px).
2. Varied Ken Burns motion (rendered at 2x then downscaled to avoid zoompan jitter)
   with matching forward drift on the opening and closing shots for the loop.
3. Voice cleanup, sidechain ducking of the ambient bed under speech, then measured
   gain + 4x-oversampled brickwall limiting to hit -14 LUFS (EBU R128) with true peak
   below -1.5 dBTP. (Single-filter loudnorm undershoots when the hammer transient makes
   linear mode impossible, and overshoots true peak in its dynamic mode.)
4. H.264 CRF 18 (slow), TV-range yuv420p, AAC 48 kHz, +faststart for streaming.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from PIL import Image, ImageDraw

from .config import (
    AUDIO_SAMPLE_RATE,
    FONTS_DIR,
    FONT_NAME,
    FONT_SIZE,
    FONT_SIZE_ASS,
    CAPTION_MARGIN_V,
    MIN_BURST_WORDS,
    TARGET_BURST_WORDS,
    MAX_BURST_WORDS,
    MAX_BURST_CHARS,
    CAPTION_SMALL_CAPS,
    CAPTION_SC_SCALE,
    CAPTION_PAD_X,
    CAPTION_PAD_Y,
    CAPTION_BORDER_RADIUS,
    COL_ACTIVE,
    COL_EMPHASIS,
    EMPHASIS_POP,
    COL_PAST,
    COL_FUTURE,
    COL_BACK,
    TARGET_LUFS,
    TARGET_TRUE_PEAK,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PRESET,
)
from .typography import is_video

log = logging.getLogger(__name__)

TRANSITIONS = [
    "zoomin", "dissolve", "smoothleft", "fadeblack",
    "zoomin", "dissolve", "smoothright", "fade",
    "zoomin", "dissolve", "smoothleft", "fadeblack",
]
TRANS_DUR = 0.40


class RenderError(RuntimeError):
    pass


def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed (exit {res.returncode}):\n{tail}")
    return res


from .encoding import bounded_encode, get_video_encoder as _get_video_encoder


def _ass_escape(text: str) -> str:
    """Neutralizes ASS override syntax so caption text can't inject styling tags."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


MAX_CAPTION_WIDTH = 650  # px: inside Meta's safe zone, leaving >= 185px clear on both sides


TERMINAL_PUNCT = re.compile(r"[\.\?!…]+[\"\'”’\)]*$")
CLAUSE_PUNCT = re.compile(r"[,;:\-—–]+[\"\'”’\)]*$")


def rounded_rect_path(w: int, h: int, r: int) -> str:
    """Vector path in ASS drawing commands for a rectangle with rounded corners."""
    c = round(r * 0.552285)
    return (
        f"m {r} 0 "
        f"l {w - r} 0 "
        f"b {w - r + c} 0 {w} {r - c} {w} {r} "
        f"l {w} {h - r} "
        f"b {w} {h - r + c} {w - r + c} {h} {w - r} {h} "
        f"l {r} {h} "
        f"b {r - c} {h} 0 {h - r + c} 0 {h - r} "
        f"l 0 {r} "
        f"b 0 {r - c} {r - c} 0 {r} 0"
    )


ROMAN_NUMERALS = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI"}
MEASURE_UNITS = {
    "tons", "ton", "feet", "ft", "meters", "m", "kilometres", "km", "miles", "years", "year",
    "stories", "levels", "level", "kg", "lbs", "pounds", "inches", "centuries", "century",
    "monasteries", "temples", "shafts", "first", "second", "third", "fourth", "fifth"
}
DESIGNATORS = {
    "cave", "caves", "level", "levels", "chamber", "chambers", "room", "rooms", "phase", "phases",
    "shaft", "shafts", "tier", "tiers", "gate", "gates", "tomb", "tombs", "site", "sites",
    "trench", "trenches", "layer", "layers", "step", "steps", "dynasty", "century", "centuries",
    "chapter", "part", "no.", "number"
}
TITLES = {"king", "queen", "emperor", "pope", "saint", "st.", "dr.", "mr.", "mrs.", "lord", "sir"}
DANGLING_TAILS = {
    # Determiners & Articles
    "the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "my", "your", "his", "her",
    # Prepositions
    "of", "at", "to", "in", "for", "on", "with", "by", "from", "into", "over", "under", "about",
    "across", "through", "between", "during", "before", "after", "upon", "within", "without",
    "toward", "towards", "against", "behind", "beneath", "beside",
    # Conjunctions (coordinating & subordinating)
    "and", "or", "but", "nor", "so", "yet", "though", "although", "while", "whereas",
    "because", "since", "until", "unless", "if", "as", "than", "whether",
    # Relative Pronouns
    "which", "who", "whom", "whose", "where", "when",
    # Copulas & Auxiliaries (dangling verb tails before predicates)
    "is", "was", "are", "were", "been", "being",
}

CLAUSE_STARTERS = {
    "though", "although", "while", "whereas", "because", "since", "until", "unless",
    "if", "when", "where", "however", "yet", "which", "who", "whom", "whose"
}


def _is_atomic_boundary(w1_raw: str, w2_raw: str) -> bool:
    """True if w1 and w2 must NOT be split across burst boundaries."""
    w1 = re.sub(r"[^\w]", "", w1_raw).lower()
    w2 = re.sub(r"[^\w]", "", w2_raw).lower()
    if not w1 or not w2:
        return False
    # Entity Designator + Number/Code/Ordinal (e.g. 'Cave' + '16', 'Level' + '3', 'Dynasty' + '4')
    if w1 in DESIGNATORS and (any(c.isdigit() for c in w2) or w2.upper() in ROMAN_NUMERALS or len(w2) <= 2 or w2 in {"first", "second", "third"}):
        return True
    # Number + Measurement Unit (e.g. '200,000' + 'tons', '100' + 'feet', '2' + 'kilometres')
    if any(c.isdigit() for c in w1) and w2 in MEASURE_UNITS:
        return True
    # Honorific/Title + Name (e.g. 'King' + 'Krishna')
    if w1 in TITLES:
        return True
    # Determiner + Ordinal / Regal (e.g. 'the' + 'first')
    if w1 == "the" and w2 in {"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"}:
        return True
    # Name + Roman Numeral (e.g. 'Krishna' + 'I')
    if w2.upper() in ROMAN_NUMERALS:
        return True
    return False


def partition_words_grammatical(
    words: List[Dict[str, Any]],
    min_words: int = MIN_BURST_WORDS,
    target_words: int = TARGET_BURST_WORDS,
    max_words: int = MAX_BURST_WORDS,
    max_chars: int = MAX_BURST_CHARS,
) -> List[List[Dict[str, Any]]]:
    """
    Groups spoken words into readable phrases respecting grammatical boundaries:
    1. Splits strictly at terminal punctuation (. ? ! …) so every new sentence starts fresh.
    2. Dynamic partition ensures phrases use at least min_words (default 2) where possible.
    3. Heavily penalizes ending phrases on orphan prepositions/determiners/conjunctions ('at', 'of', 'though').
    4. Strongly rewards starting new caption blocks on subordinating conjunctions ('though', 'while', 'because').
    5. Respects natural clause punctuation (, ; : - —) and limits line length to avoid margin overflow.
    6. Enforces atomic boundaries to prevent breaking [Name + Roman Numeral] or [Designator + Number].
    """
    sentences: List[List[Dict[str, Any]]] = []
    curr: List[Dict[str, Any]] = []
    for w in words:
        curr.append(w)
        if TERMINAL_PUNCT.search(w.get("word", "")):
            sentences.append(curr)
            curr = []
    if curr:
        sentences.append(curr)

    def _partition_sentence(sent: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        n = len(sent)
        if n == 0:
            return []
        if n <= min_words:
            return [sent]
        memo: Dict[int, Optional[List[List[Dict[str, Any]]]]] = {}

        def _solve(i: int) -> Optional[List[List[Dict[str, Any]]]]:
            if i == n:
                return []
            if i in memo:
                return memo[i]
            rem = n - i
            rem_clean = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in sent[i:]]
            rem_txt = " ".join(rem_clean)
            if min_words <= rem <= max_words and len(rem_txt) <= max_chars:
                memo[i] = [sent[i:]]
                return memo[i]

            best: Optional[List[List[Dict[str, Any]]]] = None
            best_score = -999999
            candidate_sizes = [target_words, min_words, 4, 5, 6]
            for size in candidate_sizes:
                if i + size <= n:
                    if i + size < n and _is_atomic_boundary(sent[i + size - 1].get("word", ""), sent[i + size].get("word", "")):
                        continue
                    rem_after = n - (i + size)
                    if rem_after > 0 and rem_after < min_words:
                        continue
                    after = _solve(i + size)
                    if after is not None:
                        chunk = sent[i:i + size]
                        clean_chunk = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in chunk]
                        txt = " ".join(clean_chunk)
                        last_w = re.sub(r"[^\w]", "", clean_chunk[-1]).lower()
                        score = 100
                        if len(txt) <= max_chars:
                            score += 50
                        else:
                            score -= (len(txt) - max_chars) * 20
                        if last_w in DANGLING_TAILS:
                            score -= 200  # Strictly penalize dangling prepositions/determiners/conjunctions at block ends
                        if last_w in CLAUSE_STARTERS:
                            score -= 300  # Conjunctions/clause starters must start a new block, never end one
                        if re.search(r"[\,\;]$", chunk[-1].get("word", "")):
                            score += 70   # Reward breaking at clause commas
                        # If next word begins a new subordinate clause, strongly reward cutting right before it!
                        if i + size < n:
                            next_w = re.sub(r"[^\w]", "", sent[i + size].get("word", "")).lower()
                            if next_w in CLAUSE_STARTERS:
                                if re.search(r"[\,\;]$", chunk[-1].get("word", "")):
                                    score += 160  # Large reward for breaking before 'though', 'although', etc. after comma
                                else:
                                    score += 60
                        if 14 <= len(txt) <= 24:
                            score += 40   # Reward optimal glanceable width
                        if score > best_score:
                            best_score = score
                            best = [chunk] + after
            if best is None:
                cut = min(rem, target_words)
                best = [sent[i:i + cut]] + (_solve(i + cut) or [sent[i + cut:]])
            memo[i] = best
            return best

        res = _solve(0)
        return res if res is not None else [sent]

    all_phrases: List[List[Dict[str, Any]]] = []
    for s in sentences:
        for p in _partition_sentence(s):
            if p:
                all_phrases.append(p)
    return all_phrases


def measure_caption_phrase(phrase: Any, fit_rendered: int, sc_scale: int = CAPTION_SC_SCALE) -> Tuple[int, int]:
    """Measures rendered width and height of a caption phrase in pixels."""
    from .typography import get_font
    if isinstance(phrase, str):
        words_list = [{"word": w} for w in phrase.split()]
    else:
        words_list = phrase
    f_base = get_font("Anton", fit_rendered)
    f_sc = get_font("Anton", max(16, int(round(fit_rendered * (sc_scale / 100.0)))))
    space_w = f_base.getlength(" ")
    total_w = 0.0
    for wi, w in enumerate(words_list):
        if wi > 0:
            total_w += space_w
        raw = w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w))
        if wi == len(words_list) - 1:
            raw = re.sub(r"[\,\;\.]+$", "", raw)
        tokens = re.findall(r"[A-Za-z]+|[^A-Za-z]+", raw)
        for tok in tokens:
            if tok.isalpha():
                total_w += f_base.getlength(tok[0].upper())
                if len(tok) > 1:
                    total_w += f_sc.getlength(tok[1:].upper())
            else:
                total_w += f_base.getlength(tok)
    # 75px accounts for ascenders, full caps, and descenders like commas at base FONT_SIZE 76
    text_h = max(30, int(round(75 * (fit_rendered / FONT_SIZE))))
    return int(round(total_w)), text_h


def _caption_fit_size(phrase: Any, scale: float = 1.0, max_width: int = MAX_CAPTION_WIDTH) -> int:
    """Largest font size <= FONT_SIZE at which `phrase` (shown at up to `scale`) fits max_width."""
    from .typography import get_font
    from PIL import Image, ImageDraw
    if isinstance(phrase, str):
        words = phrase.split()
    else:
        words = [w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w)) for w in phrase]
    if words:
        words[-1] = re.sub(r"[\,\;\.]+$", "", words[-1])
    plain = " ".join(words).upper()
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    width = draw.textlength(plain, font=get_font("Anton", FONT_SIZE)) * scale
    if width <= max_width:
        return FONT_SIZE
    return max(48, int(FONT_SIZE * max_width / width))



def format_word_caption(
    word: str,
    colour: str,
    is_key: bool,
    is_spoken_now: bool,
    has_key: bool,
    pop_val: int = EMPHASIS_POP,
    sc_scale: int = CAPTION_SC_SCALE,
    enable_small_caps: bool = CAPTION_SMALL_CAPS,
) -> str:
    """Formats a word for ASS kinetic subtitles.
    When enable_small_caps is True, initial letter of alphabetic segments is full size,
    and remaining letters are small capitals (sc_scale%).
    """
    clean = _ass_escape(word)
    if not enable_small_caps:
        return f"{{\\c{colour}\\fscx100\\fscy100}}{clean.upper()}"

    tokens = re.findall(r"[A-Za-z]+|[^A-Za-z]+", clean)
    out = []
    sc_base = sc_scale
    init_tag = f"\\c{colour}\\fscx100\\fscy100"
    sc_tag = f"\\c{colour}\\fscx{sc_base}\\fscy{sc_base}"

    ACRONYMS = {
        "BC", "AD", "BCE", "CE", "BP", "MYA", "KYA", "US", "USA", "UK", "DNA", "GPS",
        "UNESCO", "CIA", "FBI", "NASA", "USSR", "WWI", "WWII", "AM", "PM", "FT", "KM", "M", "KG"
    }
    ROMAN_NUMERALS = {
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
        "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX"
    }

    for tok in tokens:
        if tok.isalpha():
            upper = tok.upper()
            if upper in ACRONYMS or upper in ROMAN_NUMERALS or (tok.isupper() and 2 <= len(tok) <= 4):
                out.append(f"{{{init_tag}}}{upper}")
            else:
                first = tok[0].upper()
                rest = tok[1:].upper()
                if rest:
                    out.append(f"{{{init_tag}}}{first}{{{sc_tag}}}{rest}")
                else:
                    out.append(f"{{{init_tag}}}{first}")
        else:
            out.append(f"{{{init_tag}}}{tok}")
    return "".join(out)


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def emphasis_indices(words: List[Dict[str, Any]], terms: Optional[List[str]]) -> Set[int]:
    """Word positions to emphasise: the first occurrence of each key term (multi-word terms
    match as a sequence). Without terms, the first two words containing a digit."""
    tokens = [_norm(w["word"]) for w in words]
    picked: Set[int] = set()
    for term in (terms or [])[:3]:
        seq = [t for t in (_norm(x) for x in term.split()) if t]
        for i in range(len(tokens) - len(seq) + 1):
            if seq and tokens[i:i + len(seq)] == seq:
                picked.update(range(i, i + len(seq)))
                break
    if not terms:
        picked = set([i for i, w in enumerate(words) if any(c.isdigit() for c in w["word"])][:2])
    return picked


def _audio_chain(vo_idx: int, bg_idx: int) -> str:
    """Voice cleanup + centered stereo VO + ducked stereo bed + sum at delivery sample rate, before mastering."""
    return (
        f"[{vo_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"highpass=f=85,equalizer=f=3500:width_type=q:w=1.2:g=1.5,equalizer=f=7000:width_type=q:w=2.0:g=-2.0,"
        f"asplit[v_main][v_sc];"
        f"[{bg_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo[bg_rs];"
        f"[bg_rs][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,aformat=channel_layouts=stereo"
    )


def _master_chain(gain_db: float) -> str:
    """Gain + true-peak-safe limiter (limiting at 4x rate catches inter-sample peaks) with dynamic range breathing room."""
    return (
        f",volume={gain_db:.2f}dB,aresample={AUDIO_SAMPLE_RATE * 4},"
        f"alimiter=limit=0.80:attack=2.5:release=80:level=disabled:asc=1,"
        f"aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo"
    )


class VideoComposer:
    def __init__(self, fonts_dir: Path = FONTS_DIR):
        self.fonts_dir = fonts_dir

    @staticmethod
    def _format_ass_time(sec: float) -> str:
        cs_total = int(round(max(0.0, sec) * 100))
        h, rem = divmod(cs_total, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def _format_srt_time(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _format_vtt_time(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def export_subtitles_srt(self, words: List[Dict[str, Any]], srt_path: Path) -> Path:
        """Export standardized SubRip (.srt) sidecar file for YouTube closed caption indexing."""
        phrases = partition_words_grammatical(words)
        lines = []
        for i, phrase in enumerate(phrases, start=1):
            t_start = phrase[0]["start"]
            t_end = phrase[-1]["start"] + phrase[-1].get("dur", 0.3) + 0.1
            text = " ".join(w["word"] for w in phrase).strip()
            lines.append(f"{i}\n{self._format_srt_time(t_start)} --> {self._format_srt_time(t_end)}\n{text}\n")
        srt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported SRT captions (%d cues) -> %s", len(phrases), srt_path.name)
        return srt_path

    def export_subtitles_vtt(self, words: List[Dict[str, Any]], vtt_path: Path) -> Path:
        """Export standardized WebVTT (.vtt) sidecar file for web player closed captions."""
        phrases = partition_words_grammatical(words)
        lines = ["WEBVTT\n"]
        for i, phrase in enumerate(phrases, start=1):
            t_start = phrase[0]["start"]
            t_end = phrase[-1]["start"] + phrase[-1].get("dur", 0.3) + 0.1
            text = " ".join(w["word"] for w in phrase).strip()
            lines.append(f"{i}\n{self._format_vtt_time(t_start)} --> {self._format_vtt_time(t_end)}\n{text}\n")
        vtt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported VTT captions (%d cues) -> %s", len(phrases), vtt_path.name)
        return vtt_path

    def build_subtitles(
        self,
        words: List[Dict[str, Any]],
        ass_path: Path,
        emphasis: Optional[List[str]] = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        margin_v: Optional[int] = None,
    ) -> Path:
        """Dynamic running-speech kinetic captions:
        1. Grammatical phrasing: sensitive to punctuation (. ? ! …) so every new sentence starts fresh.
        2. At least three words per caption burst (wherever feasible) with dynamic resolution protection.
        3. Equal margin translucent pill background with subtle rounded corners (vector Bézier curve).
        4. Word-by-word karaoke highlighting (gold key terms with pop, active white, upcoming grey).
        5. Small Caps typography: initial capital anchors with proportioned small capitals for peak legibility.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries).
        """
        is_widescreen = width > height
        eff_margin_v = margin_v if margin_v is not None else (70 if is_widescreen else CAPTION_MARGIN_V)
        max_caption_width = int(width * 0.75) if is_widescreen else MAX_CAPTION_WIDTH

        if is_widescreen:
            header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,Montserrat,38,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0.5,0,1,1.8,1.2,2,0,0,{eff_margin_v},1
Style: CapBox,Montserrat,38,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        else:
            header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,{FONT_NAME},{FONT_SIZE_ASS},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,0,0,5,0,0,0,1
Style: CapBox,{FONT_NAME},{FONT_SIZE_ASS},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        phrases = partition_words_grammatical(words)
        key = emphasis_indices(words, emphasis)
        raw_events: List[Tuple[int, float, float, str, str]] = []  # (layer, start, end, style, text)

        base = 0

        for pi, phrase in enumerate(phrases):
            if pi < len(phrases) - 1:
                next_start = phrases[pi + 1][0]["start"]
            else:
                next_start = phrase[-1]["start"] + phrase[-1]["dur"] + 0.10

            phrase_start = phrase[0]["start"]
            phrase_end = next_start

            # Fit font size to ensure the caption comfortably clears side margins and buttons
            has_key = any(base + j in key for j in range(len(phrase)))
            fit = _caption_fit_size(phrase, EMPHASIS_POP / 100 if has_key else 1.0, max_width=max_caption_width)
            if is_widescreen:
                from .typography import fit_font
                measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
                caption_text = " ".join(w["word"] for w in phrase)
                fit_wide = fit_font(measure_draw, caption_text, "Montserrat", 38, max_caption_width, 28).size
                size_tag = f"{{\\fs{fit_wide}}}" if fit_wide < 38 else ""
            elif fit < FONT_SIZE:
                fit_ass = int(round(fit / 0.5625))
                size_tag = f"{{\\fs{fit_ass}}}"
            else:
                size_tag = ""

            center_x = width // 2
            center_y = height - eff_margin_v

            if not is_widescreen:
                # Calculate exact bounding dimensions to render perfectly centered pill background
                text_w, text_h = measure_caption_phrase(phrase, fit, CAPTION_SC_SCALE)

                box_w = int(round(text_w + CAPTION_PAD_X * 2))
                box_h = int(round(text_h + CAPTION_PAD_Y * 2))
                box_x = int(round(center_x - box_w / 2))
                # Align vertical center: compensate for Anton font's ascent/baseline visual center shift
                text_visual_center_y = center_y + int(round(7.5 * (fit / FONT_SIZE)))
                box_y = int(round(text_visual_center_y - box_h / 2))
                path = rounded_rect_path(box_w, box_h, CAPTION_BORDER_RADIUS)

                # Layer 0: Background pill box with subtle rounded corners and exact equal margins
                box_text = f"{{\\pos({box_x},{box_y})\\p1}}{path}{{\\p0}}"
                raw_events.append((0, phrase_start, phrase_end, "CapBox", box_text))

            # Layer 1: Text events with word-by-word karaoke progression and no-wrap protection
            for wi, target in enumerate(phrase):
                t_end = phrase[wi + 1]["start"] if wi < len(phrase) - 1 else next_start
                parts = []
                for ji, w in enumerate(phrase):
                    is_key = base + ji in key
                    disp_word = w["word"]
                    if ji == len(phrase) - 1:
                        disp_word = re.sub(r"[\,\;\.]+$", "", disp_word)

                    if is_widescreen:
                        if ji > wi:
                            c_tag = r"{\c&HCCCCCC&}"
                        elif is_key:
                            c_tag = r"{\c&H00D7D7&}"
                        elif ji == wi:
                            c_tag = r"{\c&HFFFFFF&}"
                        else:
                            c_tag = r"{\c&H999999&}"
                        parts.append(f"{c_tag}{disp_word}")
                    else:
                        if ji > wi:
                            colour = COL_FUTURE
                        else:
                            colour = COL_EMPHASIS if is_key else COL_PAST if ji < wi else COL_ACTIVE
                        word_ass = format_word_caption(
                            disp_word,
                            colour=colour,
                            is_key=is_key,
                            is_spoken_now=(ji == wi),
                            has_key=has_key,
                        )
                        parts.append(word_ass)
                text_content = f"{{\\pos({center_x},{center_y})\\q2}}{size_tag}" + " ".join(parts)
                raw_events.append((1, target["start"], t_end, "CapText", text_content))

            base += len(phrase)

        # Sort all events chronologically (layer 0 background pill always appears under layer 1 text)
        raw_events.sort(key=lambda e: (e[1], e[0]))

        lines = [header]
        for layer, ts, te, style, txt in raw_events:
            te_clamped = max(te, ts + 0.02)
            lines.append(
                f"Dialogue: {layer},{self._format_ass_time(ts)},{self._format_ass_time(te_clamped)},{style},,0,0,0,,{txt}"
            )

        ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Wrote %d caption events -> %s", len(raw_events), ass_path.name)
        return ass_path

    @staticmethod
    def _measure(vo_path: Path, bed_path: Path, chain: str) -> Tuple[float, float]:
        """Integrated loudness (LUFS) and true peak (dBTP) of the mixed program."""
        res = _run_ffmpeg(["ffmpeg", "-hide_banner", "-nostats", "-i", str(vo_path), "-i", str(bed_path),
                           "-filter_complex", chain + ",ebur128=peak=true", "-f", "null", "-"])
        summary = res.stderr[res.stderr.rfind("Summary:"):]
        i_match = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
        p_match = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
        if not i_match or not p_match or "inf" in i_match.group(1):
            raise RenderError("Could not measure program loudness (silent audio?).")
        return float(i_match.group(1)), float(p_match.group(1))

    def calibrate_gain(self, vo_path: Path, bed_path: Path, tolerance: float = 0.5, accept: float = 1.0,
                       max_iter: int = 6, target_lufs: float = TARGET_LUFS,
                       target_true_peak: float = TARGET_TRUE_PEAK) -> float:
        """
        Finds the gain that lands the limited mix on target_lufs (within `tolerance` LU,
        the usual delivery tolerance). The limiter absorbs part of each gain step, so steps
        are scaled up. If it never gets that close, the best result within `accept` LU is
        used with a warning; platforms re-normalize, so that is not worth failing a run.
        """
        base = _audio_chain(0, 1)
        integrated, _ = self._measure(vo_path, bed_path, base)
        gain = target_lufs - integrated
        best_gain, best_err = gain, float("inf")
        for _ in range(max_iter):
            integrated, peak = self._measure(vo_path, bed_path, base + _master_chain(gain))
            log.info("Mastering: gain %+.2f dB -> %.1f LUFS, %.1f dBTP", gain, integrated, peak)
            if peak > target_true_peak:
                raise RenderError(f"True peak {peak} dBTP exceeds {target_true_peak} after limiting.")
            err = abs(integrated - target_lufs)
            if err < best_err:
                best_gain, best_err = gain, err
            if err <= tolerance + 1e-9:
                return gain
            gain += (target_lufs - integrated) * 1.5
        if best_err <= accept:
            log.warning("Loudness within %.1f LU of %.1f LUFS (not %.1f); using best gain.", best_err, target_lufs, tolerance)
            return best_gain
        raise RenderError(f"Loudness did not converge to {target_lufs} LUFS (best was {best_err:.1f} LU off).")

    @staticmethod
    def _is_portrait_video(path: Path) -> bool:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
                capture_output=True, text=True, timeout=10
            )
            if out.returncode == 0 and out.stdout.strip():
                parts = out.stdout.strip().split("x")
                if len(parts) == 2:
                    w, h = int(parts[0]), int(parts[1])
                    return (w / h) < 1.3
        except Exception:
            pass
        return False


    @bounded_encode
    def compose_video(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
        durations: Optional[List[float]] = None,
        emphasis: Optional[List[str]] = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        aspect_ratio: str = "9:16",
        delivery_profile=None,
    ) -> Path:
        """
        durations: seconds on screen per scene (e.g. from scene_planner beats). Each cut is
        a short cross-fade centred on the beat boundary. Without durations, scenes share
        the narration equally.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries) with BBC intelligent compositing.
        """
        from .delivery_profiles import get_delivery_profile
        profile = delivery_profile or get_delivery_profile("deep_dive" if width > height else "reel")
        width, height, aspect_ratio = profile.width, profile.height, profile.aspect_ratio
        from .checkpoints import file_inputs, fingerprint
        from .credits import sha256_file
        from .typography import FONT_FILES
        source_inputs = file_inputs([*scene_images, vo_path, bed_path])
        font_inputs = file_inputs([self.fonts_dir / filename for filename in FONT_FILES.values()])
        if not all(source_inputs.values()) or not all(font_inputs.values()):
            raise RenderError("Render sources and bundled fonts must exist")
        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(words, ass_path, emphasis=emphasis, width=width, height=height)

        # Export sidecar SRT and VTT closed captions for YouTube / platform transcript indexing
        srt_path = output_mp4_path.parent / (output_mp4_path.stem + ".srt")
        vtt_path = output_mp4_path.parent / (output_mp4_path.stem + ".vtt")
        self.export_subtitles_srt(words, srt_path)
        self.export_subtitles_vtt(words, vtt_path)

        n = len(scene_images)
        if not durations or len(durations) != n:
            durations = [(vo_dur + TRANS_DUR) / n] * n
        # Transition k starts half a fade before boundary k so the cut lands on the beat.
        boundaries = [sum(durations[:k + 1]) for k in range(n - 1)]
        offsets = [max(0.0, b - TRANS_DUR / 2) for b in boundaries]
        clip_durs = [d + TRANS_DUR for d in durations]
        clip_durs[-1] += TRANS_DUR  # tail headroom; the output is cut to the narration length
        w2, h2 = width * 2, height * 2

        input_args: List[str] = []
        filter_parts: List[str] = []
        for i, item in enumerate(scene_images):
            item = Path(item)
            clip_dur = clip_durs[i]
            frames = int(clip_dur * VIDEO_FPS) + 1
            if is_video(item):
                input_args += ["-t", f"{clip_dur:.3f}", "-i", str(item)]
                if width > height and self._is_portrait_video(item):
                    fg_h = int(round(height * 0.94)) // 2 * 2
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,split[bg_in{i}][fg_in{i}];"
                        f"[bg_in{i}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=25:5,eq=brightness=-0.15[bg{i}];"
                        f"[fg_in{i}]scale=-2:{fg_h}[fg{i}];"
                        f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                    )
                else:
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                    )
                continue

            input_args += ["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", str(item)]
            if i == 0 or i == n - 1:  # matching forward drift for the loop seam
                z, x, y = "min(pzoom+0.0016,1.24)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif i % 2 == 1:
                z, x, y = "max(1.20-0.0014*on,1.0)", "iw/2-(iw/zoom/2)+sin(on/35)*32", "ih/2-(ih/zoom/2)"
            else:
                z, x, y = "min(pzoom+0.0013,1.18)", "iw/2-(iw/zoom/2)-sin(on/35)*28", "ih/2-(ih/zoom/2)+sin(on/45)*16"

            is_portrait_on_widescreen = False
            if width > height:
                try:
                    with Image.open(item) as im:
                        img_w, img_h = im.size
                        if (img_w / img_h) < 1.3:
                            is_portrait_on_widescreen = True
                except Exception:
                    pass

            if is_portrait_on_widescreen:
                # Cinematic vertical tilt/pan: scale width to 1920 so the tall image fills the screen horizontally,
                # then smoothly pan vertically across the full height with a cinematic cosine ease, revealing 100% of the asset.
                tilt_dir = f"(0.5-0.5*cos(PI*t/{clip_dur:.3f}))" if (i % 2 == 0) else f"(0.5+0.5*cos(PI*t/{clip_dur:.3f}))"
                filter_parts.append(
                    f"[{i}:v]scale={width}:-2,"
                    f"crop={width}:{height}:0:'max(0,min(in_h-out_h,(in_h-out_h)*{tilt_dir}))',"
                    f"fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                )
            else:
                filter_parts.append(
                    f"[{i}:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
                    f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={VIDEO_FPS},"
                    f"setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                )

        curr = "[v0]"
        for k in range(n - 1):
            out = f"[x{k}]" if k < n - 2 else "[video_raw]"
            off = round(offsets[k], 3)
            filter_parts.append(f"{curr}[v{k + 1}]xfade=transition={TRANSITIONS[k % len(TRANSITIONS)]}:duration={TRANS_DUR}:offset={off}{out}")
            curr = out
        if n == 1:
            filter_parts.append("[v0]null[video_raw]")

        input_args += ["-i", str(vo_path), "-i", str(bed_path)]
        gain = self.calibrate_gain(vo_path, bed_path, target_lufs=profile.target_lufs,
                                   target_true_peak=profile.target_true_peak)
        filter_parts.append(_audio_chain(n, n + 1) + _master_chain(gain) + "[a_final]")

        ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts_str = str(self.fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")
        vignette_aspect = "16/9" if width > height else "9/16"
        # Studio-grade cinema grade: Kodak 2383 soft-knee roll-off (lifts subterranean blacks above clipping, rolls highlights)
        cinema_curve = "curves=master='0/0.02 0.2/0.19 0.5/0.51 0.8/0.82 1/0.98':red='0/0.01 0.5/0.50 1/0.99':blue='0/0.04 0.5/0.49 1/0.94'"
        filter_parts.append(
            f"[video_raw]eq=contrast=1.07:brightness=0.008:saturation=1.08,{cinema_curve},"
            f"noise=alls=7:allf=t+u,vignette=PI/4.2:aspect={vignette_aspect},"
            f"subtitles='{ass_str}':fontsdir='{fonts_str}',scale=out_range=tv,format=yuv420p[v_final]"
        )

        encoder_args = _get_video_encoder()

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-threads", str(os.cpu_count() or 0),
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[v_final]", "-map", "[a_final]",
            *encoder_args,
            "-threads", str(os.cpu_count() or 0),
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-r", str(VIDEO_FPS),
            # Meta Reels spec: closed GOP of 2-5 s. Fixed 2 s keyframe interval, no scene-cut keyframes.
            "-g", str(VIDEO_FPS * 2), "-keyint_min", str(VIDEO_FPS * 2), "-sc_threshold", "0", "-flags", "+cgop",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
            "-max_muxing_queue_size", "8192",
            "-movflags", "+faststart",
            "-t", f"{vo_dur:.2f}",
            str(output_mp4_path),
        ]
        log.info("Rendering master -> %s", output_mp4_path.name)
        _run_ffmpeg(cmd)
        from .encoded_audio import finalize_encoded_audio
        encoded_audio = finalize_encoded_audio(output_mp4_path, profile.target_lufs, profile.target_true_peak)
        if source_inputs != file_inputs([*scene_images, vo_path, bed_path]):
            raise RenderError("Source files changed while rendering")
        self.export_subtitles_srt(words, output_mp4_path.with_suffix(".srt"))
        self.export_subtitles_vtt(words, output_mp4_path.with_suffix(".vtt"))
        import json
        manifest = {"master_sha256": sha256_file(output_mp4_path),
                    "encoded_audio": encoded_audio,
                    "scenes": file_inputs(scene_images), "voiceover": file_inputs([vo_path]),
                    "ambient_bed": file_inputs([bed_path]), "fonts": font_inputs,
                    "captions": file_inputs([ass_path, output_mp4_path.with_suffix(".srt"), output_mp4_path.with_suffix(".vtt")]), "words_fingerprint": fingerprint(words),
                    "width": width, "height": height, "renderer_sha256": sha256_file(Path(__file__))}
        manifest_path = output_mp4_path.with_suffix(".composition.json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
        log.info("Master rendered: %s (%.2f MB)", output_mp4_path.name, size_mb)
        return output_mp4_path

    def compose_deep_dive(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
        durations: Optional[List[float]] = None,
        emphasis: Optional[List[str]] = None,
    ) -> Path:
        """Compose a 16:9 widescreen broadcast documentary with BBC blurred-backdrop compositing."""
        return self.compose_video(
            scene_images=scene_images,
            vo_path=vo_path,
            bed_path=bed_path,
            words=words,
            vo_dur=vo_dur,
            output_mp4_path=output_mp4_path,
            durations=durations,
            emphasis=emphasis,
            width=1920,
            height=1080,
            aspect_ratio="16:9",
        )

    @staticmethod
    def make_review_proxy(master_mp4: Path, proxy_path: Path) -> Path:
        """Small 720p copy for phone review over Telegram (bot uploads are capped near 50 MB)."""
        _run_ffmpeg([
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-threads", str(os.cpu_count() or 0),
            "-i", str(master_mp4),
            "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "28",
            "-threads", str(os.cpu_count() or 0),
            "-maxrate", "1500k", "-bufsize", "3000k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy_path),
        ])
        log.info("Review proxy: %s (%.1f MB)", proxy_path.name, os.path.getsize(proxy_path) / (1024 * 1024))
        return proxy_path
