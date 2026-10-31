"""
src/voice_prosody.py
====================
Pillar III: Neural TTS Prosody & Directed Narrative Pacing Engine.
Injects dramatic micro-pauses before historical riddles, cadence slowing
for astonishing archaeological measurements, and paragraph boundary resets.
"""

import re
from typing import Optional


# Archaeological units indicating critical measurements or empirical claims
MEASUREMENT_UNITS = (
    r"(?:nanometers?|microns?|millimeters?|centimeters?|meters?|kilometers?|"
    r"miles?|feet|inches|tons?|metric tons?|megatons?|kilograms?|kg|pounds?|lbs|"
    r"years?|centuries|millennia|degrees?|celsius|fahrenheit|gears?|teeth|columns?)"
)

# Historical date markers (e.g. 70 BC, 1513 AD)
DATE_PATTERN = re.compile(r"\b(\d{1,4})\s*(BC|BCE|AD|CE)\b", re.IGNORECASE)

# Measurement pattern: number (with commas or decimals) followed by unit
MEASURE_PATTERN = re.compile(
    rf"\b(\d+(?:,\d+)*(?:\.\d+)?)\s*({MEASUREMENT_UNITS})\b",
    re.IGNORECASE
)

# Riddle and mystery phrase markers
MYSTERY_PHRASES = (
    "could it be",
    "was it truly",
    "who built",
    "why did",
    "how could",
    "no one knows",
    "the answer remains",
    "an impossible puzzle",
    "an inexplicable riddle",
    "remains unanswered",
    "a mystery that persists",
)


class VoiceProsody:
    """Pre-processes narration text into prosody-directed speech strings
    for Azure, ElevenLabs, and edge-tts synthesis engines."""

    @classmethod
    def apply_prosody_azure(cls, text: str) -> str:
        """Injects SSML micro-pauses (<break time="..."/>) and subtle rate slows
        for Azure Speech and edge-tts."""
        out = text

        # 1. Micro-pause before astonishing empirical measurements
        # "measuring only 70 nanometers" -> "measuring only <break time=\"200ms\"/> 70 nanometers"
        def _measure_sub(match: re.Match) -> str:
            val, unit = match.group(1), match.group(2)
            return f'<break time="200ms"/> {val} {unit}'

        out = MEASURE_PATTERN.sub(_measure_sub, out)

        # 2. Dramatic micro-pause before riddle sentences / questions
        # Insert a 350ms pause before a final rhetorical question or mystery statement
        sentences = re.split(r"([.?!]+[\s\n]+)", out)
        processed_sentences = []
        for i in range(0, len(sentences), 2):
            s = sentences[i]
            punct = sentences[i + 1] if i + 1 < len(sentences) else ""

            # Check if sentence is a question or mystery statement
            is_question = "?" in punct or s.strip().endswith("?")
            is_mystery = any(phrase in s.lower() for phrase in MYSTERY_PHRASES)

            if (is_question or is_mystery) and len(s.strip()) > 10:
                s = f'<break time="350ms"/> {s.strip()}'

            processed_sentences.append(s + punct)

        out = "".join(processed_sentences)

        # Clean up any consecutive break tags
        out = re.sub(r'(<break time="\d+ms"/>\s*)+', r'\1', out)
        return out.strip()

    @classmethod
    def apply_prosody_elevenlabs(cls, text: str) -> str:
        """Injects punctuation-based pacing cues (ellipses and em-dashes) for ElevenLabs,
        which natively reads '... ' with a 300-400ms reflective breath pause."""
        out = text

        # 1. Insert cadence ellipsis before astonishing empirical measurements
        def _measure_sub(match: re.Match) -> str:
            val, unit = match.group(1), match.group(2)
            return f"... {val} {unit}"

        out = MEASURE_PATTERN.sub(_measure_sub, out)

        # 2. Dramatic cadence before rhetorical questions / mystery statements
        sentences = re.split(r"([.?!]+[\s\n]+)", out)
        processed_sentences = []
        for i in range(0, len(sentences), 2):
            s = sentences[i]
            punct = sentences[i + 1] if i + 1 < len(sentences) else ""

            is_question = "?" in punct or s.strip().endswith("?")
            is_mystery = any(phrase in s.lower() for phrase in MYSTERY_PHRASES)

            if (is_question or is_mystery) and len(s.strip()) > 10:
                # Prepend ellipsis pause if not already present
                if not s.strip().startswith("..."):
                    s = f"... {s.strip()}"

            processed_sentences.append(s + punct)

        out = "".join(processed_sentences)

        # Clean up multiple consecutive ellipses
        out = re.sub(r'(\.\.\.\s*)+', '... ', out)
        return out.strip()

    @classmethod
    def prepare_speech_text(cls, text: str, provider: str = "azure") -> str:
        """Tailors narration text for the designated TTS engine."""
        prov = (provider or "azure").lower().strip()
        if "elevenlabs" in prov:
            return cls.apply_prosody_elevenlabs(text)
        elif "azure" in prov or "edge" in prov:
            return cls.apply_prosody_azure(text)
        return text

    @staticmethod
    def strip_prosody(text: str) -> str:
        """Removes all SSML tags and pacing ellipses, returning pure grammatical text
        for token alignment and caption generation."""
        # Strip XML/SSML tags
        clean = re.sub(r"<[^>]+>", "", text)
        # Collapse pacing ellipses
        clean = re.sub(r"\s*\.\.\.\s*", " ", clean)
        # Normalize whitespace
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
