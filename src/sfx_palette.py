"""The licensed sound-effect palette: one table read by the sound plan (what the model may choose), the
ambient bed (word cues, gains, the synthesized stand-in each recording replaces, continuous setting layers)
and the pack generator (prompts and lengths), so the three cannot drift apart.

Measured 2026-10-25 before widening it (quality step 4, licensed SFX): the three most recent production
and development episodes (Kailasa, Roman concrete, Richat) each planned five or six moments and chose no
effect at all, because the five effects on offer (hammer, stone door, gear ratchet, water surge, chisel)
describe one kind of story; and the opening and revelation hit, the one effect in every episode, was the
synthesized sub-bass braam because no licensed recording of it existed. The transition whoosh was the only
licensed recording in use.

Every recording is generated once with ElevenLabs Sound Effects on the paid plan (commercial licence), with
rights evidence bound to its bytes (data/sfx/<name>.rights.json); a recording whose evidence does not match
is never mixed, and the synthesized stand-in plays instead. Keywords are whole spoken words naming what the
effect depicts (the fallback when there is no plan); layer words name a setting, under which the recording
runs quietly for the whole episode, as the cavern wind does for a story told underground.
"""
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Effect:
    prompt: str
    seconds: float
    keywords: Tuple[str, ...] = ()       # whole words that name the sound: a one-shot cue at the first of them
    gain: float = 0.38                   # relative to the synthesized stand-in's loudness
    synth: str = "_synth_stone_grind"    # the AudioEngine synthesizer whose loudness (and absence) it stands in for
    loop: bool = False                   # generated as a seamless loop (continuous textures)
    layer_words: Tuple[str, ...] = ()    # a setting the whole episode is told in: the recording runs under it all


PALETTE: Dict[str, Effect] = {
    # The original pack (2026-10-23).
    "sfx_hammer.wav": Effect("A heavy sledgehammer strikes an old stone wall once, a dull crack and crumbling plaster "
                             "debris, dry close recording, no music", 2.0,
                             ("knock", "knocked", "knocking", "hammer", "hammered", "hammering", "smash", "smashed", "pounded"),
                             0.40, "_synth_chisel_strike"),
    "sfx_stone_door.wav": Effect("A massive round millstone door rolls slowly along a carved rock groove, deep grinding "
                                 "rumble with stone-on-stone friction, small underground tunnel acoustics", 3.5,
                                 ("door", "doors", "doorway", "gate", "gates"), 0.45, "_synth_stone_grind"),
    "sfx_gear_ratchet.wav": Effect("Small ancient bronze gears turn and click, a delicate mechanical ratchet, "
                                   "close-up and intimate, quiet room", 2.0,
                                   ("gear", "gears", "clockwork", "ratchet", "cogwheel", "cogwheels", "mechanism", "dial", "dials"),
                                   0.35, "_synth_gear_ratchet"),
    "sfx_water_surge.wav": Effect("A deep underwater swell rolls past, muffled ocean current heard from below the "
                                  "surface, low and calm", 3.0,
                                  ("submerged", "shipwreck", "wreck", "diver", "divers", "underwater", "seabed", "sank", "sunk", "sinking"),
                                  0.38, "_synth_water_surge"),
    "sfx_chisel.wav": Effect("An iron chisel struck by a wooden mallet carves soft stone, three steady strikes with "
                             "small chips falling, dry close recording", 2.0,
                             ("chisel", "chiseled", "chiselled", "carve", "carved", "carving", "carvers", "hewn", "quarried", "quarry", "sculpted"),
                             0.36, "_synth_chisel_strike"),
    "sfx_cavern_wind.wav": Effect("A low hollow wind draft moving through deep underground stone tunnels, subtle and "
                                  "constant, no voices, no music", 10.0, (), 0.0, "_synth_cavern_draft", loop=True,
                                  layer_words=("underground", "subterranean", "cave", "caves", "cavern", "caverns", "tunnel", "tunnels",
                                               "shaft", "shafts", "hypogeum", "catacomb", "catacombs", "mine", "mines", "crypt")),
    "sfx_whoosh.wav": Effect("A soft airy cinematic transition whoosh, subtle and smooth, no impact", 1.0, (), 0.0,
                             "_synth_transition_whoosh"),
    # The hit on the opening and on the first revelation: in every episode, synthesized until now (two earlier
    # generations came back clipped and were rejected; the generator still rejects a clipped take).
    "sfx_impact_braam.wav": Effect("A deep cinematic sub-bass impact hit with a soft attack and a long smooth decay, clean "
                                   "and undistorted, moderate level with plenty of headroom, no music", 2.0, (), 0.0,
                                   "_synth_sub_bass_braam"),
    # Added 2026-10-25: the settings and actions the topic bank actually narrates.
    "sfx_desert_wind.wav": Effect("Dry desert wind blowing steadily over sand dunes, fine sand hiss, open space, seamless "
                                  "loop, no voices, no music", 4.0, ("sandstorm", "sandstorms", "gust", "gusts"),
                                  0.36, "_synth_water_surge", loop=True,
                                  layer_words=("desert", "deserts", "dune", "dunes", "sands", "arid", "oasis", "wasteland")),
    "sfx_sea_waves.wav": Effect("Ocean waves rolling onto a rocky shore at mid distance, steady surf, no seagulls, seamless "
                                "loop, no music", 4.0, ("waves", "surf", "breakers"), 0.38, "_synth_water_surge", loop=True,
                                layer_words=("sea", "seas", "ocean", "coast", "coastal", "coastline", "shore", "shores", "island",
                                             "isle", "islands", "harbour", "harbor", "tide", "tides", "lighthouse", "bay")),
    "sfx_mountain_wind.wav": Effect("Cold steady wind over a high mountain ridge, airy and hollow, seamless loop, no voices, "
                                    "no music", 4.0, ("wind", "winds", "howling", "blizzard"), 0.34, "_synth_water_surge", loop=True,
                                    layer_words=("mountain", "mountains", "peak", "peaks", "summit", "plateau", "highlands", "ridge",
                                                 "cliff", "cliffs", "alpine", "hilltop")),
    "sfx_fire_crackle.wav": Effect("A wood fire burning and crackling steadily, close recording, no wind, no music", 4.0,
                                   ("fire", "fires", "flame", "flames", "burning", "burned", "burnt", "blaze", "torch", "torches",
                                    "kiln", "kilns", "furnace", "hearth", "bonfire"), 0.36, "_synth_water_surge", loop=True),
    "sfx_thunder.wav": Effect("Distant rolling thunder, one long rumble fading away, light rain, no music", 4.0,
                              ("storm", "storms", "thunder", "lightning", "tempest", "gale", "gales"), 0.40, "_synth_water_surge"),
    "sfx_footsteps_stone.wav": Effect("Four slow footsteps on a stone floor in a large echoing hall, leather soles, no voices", 3.0,
                                      ("footsteps", "footstep", "walked", "walking", "stairs", "staircase", "descended", "descending",
                                       "climbed", "climbing", "corridor", "corridors"), 0.32, "_synth_chisel_strike"),
    "sfx_anvil_forge.wav": Effect("A blacksmith's hammer strikes a red-hot blade on an iron anvil three times, bright metallic "
                                  "ring, workshop acoustics", 2.5,
                                  ("forge", "forged", "forging", "anvil", "blacksmith", "blacksmiths", "smith", "smiths", "steel",
                                   "iron", "blade", "blades"), 0.40, "_synth_chisel_strike"),
    "sfx_parchment.wav": Effect("Old parchment paper unrolled and handled, dry crisp rustle, quiet room, no voices", 2.5,
                                ("parchment", "manuscript", "manuscripts", "scroll", "scrolls", "papyrus", "letter", "letters",
                                 "document", "documents", "archive", "archives", "ledger", "pages"), 0.35, "_synth_gear_ratchet"),
    "sfx_crowd_murmur.wav": Effect("A distant crowd murmuring in a large stone hall, indistinct, no clear words, no music", 4.0,
                                   ("crowd", "crowds", "gathered", "procession", "pilgrims", "marketplace", "market", "assembly",
                                    "worshippers"), 0.34, "_synth_water_surge", loop=True),
    "sfx_horse_hooves.wav": Effect("Several horses galloping past on hard dry ground, hoofbeats approaching and receding, no voices",
                                   3.0, ("horse", "horses", "cavalry", "hooves", "hoofbeats", "riders", "chariot", "chariots",
                                         "galloped", "galloping"), 0.36, "_synth_chisel_strike"),
    "sfx_bell_toll.wav": Effect("A single large bronze bell tolls once with a long resonant decay, outdoors, no music", 4.0,
                                ("bell", "bells", "tolled", "tolling"), 0.40, "_synth_chisel_strike"),
    "sfx_water_drip.wav": Effect("Water drops dripping slowly into a still pool inside a cave, echoing, no music", 4.0,
                                 ("drip", "dripping", "dripped", "cistern", "cisterns", "wells", "pool", "pools", "seep", "seeping"),
                                 0.35, "_synth_gear_ratchet", loop=True),
    "sfx_rockfall.wav": Effect("Rocks and rubble collapsing and tumbling down a slope, dust settling afterwards, no music", 3.0,
                               ("collapse", "collapsed", "collapsing", "rubble", "landslide", "landslides", "earthquake", "earthquakes",
                                "crumbled", "crumbling", "buried", "avalanche"), 0.42, "_synth_chisel_strike"),
    "sfx_sailing_ship.wav": Effect("A wooden sailing ship at sea, creaking timbers, ropes straining and canvas flapping in the wind, "
                                   "no voices", 4.0, ("ship", "ships", "sail", "sailed", "sailing", "sails", "fleet", "fleets", "vessel",
                                                      "vessels", "voyage", "voyages", "oars", "galley", "galleys", "trireme", "triremes"),
                                   0.36, "_synth_water_surge", loop=True),
    "sfx_sword_clash.wav": Effect("Two iron swords clash three times, sharp metallic rings, no voices, no music", 2.5,
                                  ("battle", "battles", "siege", "sieges", "war", "wars", "army", "armies", "soldiers", "warriors",
                                   "invaders", "invasion", "attack", "attacked", "sword", "swords", "raid", "raiders"),
                                  0.38, "_synth_chisel_strike"),
}

CREDITS_PER_SECOND = 40  # ElevenLabs Sound Effects with a set duration


def cues() -> Dict[str, Tuple[Tuple[str, ...], float, str]]:
    """{name: (keywords, gain, synth)} for every effect a spoken word or the plan can place as a one-shot."""
    return {name: (e.keywords, e.gain, e.synth) for name, e in PALETTE.items() if e.keywords}


def layers() -> Dict[str, Tuple[str, ...]]:
    """{name: layer_words} for the continuous settings, in precedence order (underground first)."""
    return {name: e.layer_words for name, e in PALETTE.items() if e.layer_words}


def credit_estimate(names=None) -> int:
    return sum(int(PALETTE[n].seconds * CREDITS_PER_SECOND) for n in (names or PALETTE))
