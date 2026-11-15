"""
src/voice_licence.py
====================
The recorded commercial-licence status of each voice provider and tier (QF-127).

A real (non-dry) run is voiced only by a provider and tier listed here as licensed. The status is a record per
provider and tier, never a guess from "a key is configured", and a name this table has never seen is not licensed:

  ElevenLabs   licensed only on the paid plans ElevenLabs itself names (its pricing page puts "Commercial License"
               on Starter and up: Starter, Creator, Pro, Scale, Business, Enterprise; its terms give commercial use
               to paid users only), and only while the subscription's status is "active". The Free plan, an unknown
               plan, any other plan name, and a subscription that is trialing, incomplete, past due or free are not.
               The table lists the API's tier ids (what GET /v1/user/subscription answers as `tier`), not marketing
               names: the pricing page's own "Choose" links name Scale as growing_business and Business as
               scale_2024_08_10. A plan that could not be read (no answer, or an answer naming no plan) is not
               licensed either, and is said as that, never as the Free plan.
  Azure Speech the free F0 tier carries no commercial output rights (the Product Terms give them to "Customers of
               the paid tier TTS Service only", for the output of prebuilt neural voices). A paid tier counts only
               when the operator declares it (AZURE_SPEECH_TIER=S0, checked in the Azure portal); undeclared, the
               resource is treated as F0. S0 covers prebuilt neural voices only (NARRATOR_VOICE is one): a custom
               voice has other terms and is not covered by this entry.
  edge-tts     never: test runs only.
  anything else, or no provider: not licensed.

The sources and the dates they were read are in docs/sources.md ("Voice licences").

A saved record (voice.json, or the rights record beside a voiceover) is read against the same table, with the tier it
was made on. A record made before QF-127 names no tier: an ElevenLabs take marked licensed was already made on a paid
plan (the engine checked the plan before the take), so it stands; an Azure take marked licensed was marked so only
because a key was configured, so it is not licensed. The saved files are never rewritten.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

AZURE_FREE_TIER = "f0"
AZURE_LICENSED_TIERS = frozenset({"s0"})          # Standard (pay-as-you-go): the paid text-to-speech tier
# API tier ids of the paid plans on ElevenLabs' pricing page (docs/sources.md): the six plan names, and the two ids
# the page's own "Choose" links carry for Scale (growing_business) and Business (scale_2024_08_10). Another id is
# added only with its source; an id this set does not hold is not licensed.
ELEVENLABS_LICENSED_TIERS = frozenset({"starter", "creator", "pro", "scale", "business", "enterprise",
                                       "growing_business", "scale_2024_08_10"})
# What is said when the subscription could not be read at all (the call failed, or its answer named no plan)
ELEVENLABS_UNREAD = ("ElevenLabs' plan could not be read (the subscription call failed or named no plan): "
                     "no commercial licence confirmed")
# GET /v1/user/subscription `status` (trialing, active, incomplete, past_due, free, free_disabled): only a
# subscription that is active is a paid one. A trial, an unfinished or failed payment and the free states are not.
ELEVENLABS_PAID_STATUSES = frozenset({"active"})
PROVIDER_NAMES = {"elevenlabs": "ElevenLabs", "azure": "Azure Speech", "edge-tts": "edge-tts"}
NOT_LICENSED = "voice not commercially licensed"
GATE_OFF = "VOICE_REQUIRE_LICENSED=false: the voice licence gate is off"
GATE_OFF_KEY = "voice-gate-off"                   # the Needs-you item a real process files at its start
# The audit events of the one publish gate (publisher.MetaPublisher.voice_gate, .voice_posted)
AUDIT_BLOCKED = "publish_blocked_unlicensed_voice"
AUDIT_GATE_OFF = "publish_unlicensed_voice_gate_off"          # before the upload: allowed, nothing is posted yet
AUDIT_POSTED_GATE_OFF = "published_unlicensed_voice_gate_off"  # after it: the upload returned published


def _tier(tier: Any) -> str:
    """A provider, tier or status name as the table knows it: text, trimmed, lower case ("" for anything else)."""
    return tier.strip().lower() if isinstance(tier, str) else ""


def _shown(name: str) -> str:
    return name[:40]


def azure_tier() -> str:
    """The Azure Speech tier the operator declared (AZURE_SPEECH_TIER); F0 when nothing is declared."""
    from . import config
    return _tier(getattr(config, "AZURE_SPEECH_TIER", "")) or AZURE_FREE_TIER


def licence(provider: Any, tier: Any, status: Any = None) -> tuple[bool, str]:
    """(licensed, basis) for a voice made by `provider` on `tier`. `status` is the ElevenLabs subscription's
    status as its API reported it; None when the caller has none to give (a record saved before it was written),
    and then the plan name alone decides."""
    provider, tier = _tier(provider), _tier(tier)
    if provider == "elevenlabs":
        if not tier:
            return False, "ElevenLabs plan unknown: no commercial licence recorded"
        if tier == "free":
            return False, "ElevenLabs Free plan: no commercial licence"
        if tier not in ELEVENLABS_LICENSED_TIERS:
            return False, f"ElevenLabs plan {_shown(tier)} not recognised: no commercial licence recorded"
        if status is not None and _tier(status) not in ELEVENLABS_PAID_STATUSES:
            return False, (f"ElevenLabs {tier} plan, subscription status {_shown(_tier(status)) or 'not reported'}: "
                           "not an active paid subscription, no commercial licence recorded")
        return True, f"ElevenLabs {tier} plan: paid plan, commercial use"
    if provider == "azure":
        if tier in AZURE_LICENSED_TIERS:
            return True, (f"Azure Speech {tier.upper()}: paid tier declared by the operator (AZURE_SPEECH_TIER), "
                          "prebuilt neural voices")
        return False, (f"Azure Speech {_shown(tier).upper()}: no commercial licence recorded for this tier" if tier
                       else "Azure Speech, tier not recorded: no commercial licence recorded")
    if provider == "edge-tts":
        return False, "edge-tts: test runs only, no commercial licence"
    return False, f"{_shown(provider) or 'unknown voice'}: no commercial licence recorded"


def licensed(provider: Any, tier: Any, status: Any = None) -> bool:
    return licence(provider, tier, status)[0]


def elevenlabs_licence(quota: Mapping[str, Any] | None) -> tuple[bool, str]:
    """(licensed, basis) for the ElevenLabs subscription as AudioEngine.voice_quota read it: the plan name and,
    when the answer carries one, the subscription's status. No answer, or one that names no plan, is not licensed
    and is said as what it is (ELEVENLABS_UNREAD): a call that failed is not the Free plan."""
    quota = quota if isinstance(quota, Mapping) else {}
    if not _tier(quota.get("tier")):
        return False, ELEVENLABS_UNREAD
    return licence("elevenlabs", quota.get("tier"), quota.get("status") if "status" in quota else None)


def record_licence(record: Mapping[str, Any] | None) -> tuple[bool, str]:
    """(licensed, basis) for a saved voice record: voice.json ("licensed") or a voiceover's rights record
    ("commercial_license_recorded"). Licensed only when the record says so AND its provider and tier (and the
    plan's status, when the record has it) are licensed in the table; see the module text for records made before
    the tier was saved."""
    if not isinstance(record, Mapping):
        return False, "no voice record"
    provider = _tier(record.get("provider"))
    claimed = record.get("licensed") is True or record.get("commercial_license_recorded") is True
    if "tier" in record:
        ok, basis = licence(provider, record.get("tier"), record.get("plan_status") if "plan_status" in record else None)
    elif provider == "elevenlabs":
        ok, basis = claimed, ("ElevenLabs paid plan (plan name not recorded)" if claimed
                              else "ElevenLabs: no commercial licence recorded")
    else:
        ok, basis = licence(provider, "")
    return bool(claimed and ok), basis


def record_licensed(record: Mapping[str, Any] | None) -> bool:
    return record_licence(record)[0]


def run_record(run_dir: Path | str) -> tuple[dict[str, Any], str]:
    """(a run's voice.json, "") or ({}, why it cannot be read): missing, unreadable, or not a record."""
    path = Path(run_dir) / "voice.json"
    if not path.is_file():
        return {}, "its voice record (voice.json) is missing"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"its voice record (voice.json) could not be read ({e})"
    if not isinstance(record, dict):
        return {}, "its voice record (voice.json) is not a record"
    return record, ""


def gate_on() -> bool:
    """Whether a real run must be voiced by, and posted with, a licensed voice (VOICE_REQUIRE_LICENSED)."""
    from . import config
    return bool(getattr(config, "VOICE_REQUIRE_LICENSED", True))


def gate_off_note(dry: bool | None = None) -> str:
    """What the operator is told wherever they look while the switch is off on a process that makes real runs:
    "" with the gate on, and "" for a dry run (`dry`; by default the process's own mode, BOT_RUNS_DRY)."""
    from . import config
    if gate_on() or (bool(getattr(config, "BOT_RUNS_DRY", True)) if dry is None else dry):
        return ""
    return (f"{GATE_OFF}. A real run may be voiced, and posted, by a voice with no commercial licence. "
            "Set VOICE_REQUIRE_LICENSED=true in .env and restart.")


def recovery(on_card: bool = False) -> str:
    """How the operator gets a master voiced again, by the buttons' own names: the review card's Regenerate with
    its reason "Music / sound" (improve.REASONS), which keeps the script; from the blocked notice, its Resend
    review card button first. A real run re-voices it only while the gate is on, so with it off that comes first."""
    from .improve import REASONS
    how = f"Regenerate › {REASONS['sound']} voices it again with a licensed voice (the script is kept)"
    first = "" if gate_on() else "Set VOICE_REQUIRE_LICENSED=true and restart. "
    return first + (f"On a real run, {how}." if on_card else f"Tap Resend review card; then, on a real run, {how}.")


def provider_name(provider: Any) -> str:
    return PROVIDER_NAMES.get(_tier(provider), str(provider or "unknown"))


def overclaim_flag(record: Mapping[str, Any] | None, short: bool = False) -> str:
    """The review card's line for a saved voice record that says "licensed" for a voice the table does not license
    (a master voiced by Azure before QF-127), or "". With the gate on, Approve does not post such a master; with
    it off, the line says that a real post would go out with it. `short`: the review panel's note, whose lede
    already says what Approve does and whose room is shared with the notes the panel must show (the sensitivity
    chip): the basis and the way out only."""
    if not isinstance(record, Mapping) or not record.get("provider"):
        return ""
    ok, basis = record_licence(record)
    if ok or record.get("licensed") is not True:
        return ""
    if short:
        from .improve import REASONS
        first = "" if gate_on() else "Set VOICE_REQUIRE_LICENSED=true and restart; then, on "
        return (f"⚠️ Voice: {NOT_LICENSED} ({basis}). {first or 'On '}a real run, "
                f"Regenerate › {REASONS['sound']} re-voices it.")
    then = ("Approve will not post it." if gate_on() else
            "The licence gate is off (VOICE_REQUIRE_LICENSED=false): a real post would go out with it.")
    return f"⚠️ Voice: {NOT_LICENSED} ({basis}). {then} {recovery(on_card=True)}"
