"""A reconstruction is granted by its structure, never by its prose (QF-60b, operator decision A after round 3).

The pre-voice verdict names its kind, a cause from a fixed list (lost_or_destroyed, sealed_or_inaccessible,
never_photographed, before_photography, no_longer_operating) that the kind allows, and the words of its own sentence
that name what is reconstructed (placed in that sentence as whole words). The free-text reason is for the reviewer
only. A verdict without that structure, with a cause the kind does not allow, with subject words that are not its
sentence's, or with a tampered field grants nothing in the planner, the generation gate or the nothing-added rule;
every round-3 probe and every prose reason of rounds 1-3 is refused on its prose alone; genuine structured verdicts of
every kind are granted. A VERSION 4 record, or a VERSION 5 record of round 3, holding a reconstruction verdict is
asked again once for the reconstruction alone; a record without one is untouched.

The reason lists below (rounds 1-3) are kept as prose that must never grant on its own."""

import json
from unittest import mock

import pytest
import test_prevoice_lines_2026_10_28 as pv
from test_prevoice_lines_2026_10_28 import world as pv_world  # noqa: F401  (the pre-voice check's fixture)

from src import image_gen, image_quarantine, reconstruction

LINE = "Inside, the lost mechanism once turned to show the planets."
KIND = "mechanism_in_use"

PLACEHOLDERS = [
    "none", "None.", "NONE", "null", "nil", "false", "true", "unknown", "Unknown.", "n/a", "N/A", "n.a.", "na", "NA",
    "-", "--", "?", "tbd", "TBD", "todo", "TODO", "no reason", "No reason given.", "not applicable", "Not applicable.",
    "same as above", "Same as above!", "see above", "ditto", "abc", "reason", "placeholder", "lorem ipsum",
    # Unicode, case and punctuation forms of the same placeholders
    "\uff2e\uff2f\uff2e\uff25", "n\u200b/a", "\ufeffnone", "\u00a0unknown\u00a0", "n\u00f6ne", "\u201cnone\u201d",
    "N / A", "(none)", "- none -",
    # single generic words and reasons that only restate the kind
    "reconstruction", "mechanism", "Original appearance.", "mechanism in use", "Mechanism in use.",
    "An unphotographed interior.", "construction", "The construction.", "No photograph exists of the interior.",
    "No photo is available.", "It is a reconstruction of the original appearance.",
    "gears gears gears", "Lost gears.",
    # QF-60b review 1, blocking 1: hedge and filler forms granted on 585a290
    "I don't know.", "Don't know why.", "Not sure really.", "No clue whatsoever.", "Unclear from context.",
    "Cannot say for certain.", "Model uncertain here.", "Insufficient information available.",
    "To be determined later.", "To be decided later.", "Reason left blank intentionally.",
    "Refer to previous answer.", "As described elsewhere earlier.",
    "Lorem ipsum dolor sit amet consectetur adipiscing",
    # review 1, blocking 2: lookalike letters (Cyrillic o, e, a inside Latin words)
    "n\u043e re\u0430s\u043en given", "n\u043et \u0430pplic\u0430ble here", "N\u043e re\u0430s\u043en g\u0456ven.",
    "The planets dial no l\u043enger turns; its gears are c\u043erroded fragments.",
    # review 1, blocking 3: another language
    "Ninguna razon dada.", "Keine Angabe vorhanden.", "Aucune raison donn\u00e9e.", "Nessun motivo indicato.",
    "Sem motivo informado.", "Geen reden opgegeven.", "Desconocido, sin raz\u00f3n.", "Inconnu, aucune raison.",
    "Unbekannt, kein Grund.", "\u043d\u0435\u0442 \u043f\u0440\u0438\u0447\u0438\u043d\u044b",
    "\u0644\u0627 \u064a\u0648\u062c\u062f \u0633\u0628\u0628", "\u7406\u7531\u306a\u3057",
    # other hedges, uncertainty and deferral, even with a subject and a why
    "I don\u2019t know how the planets dial turned.", "Idk how the planets dial turned.",
    "Perhaps the planets dial no longer turns.", "The planets dial may have turned, but nobody knows.",
    "Not sure whether the planets dial still turns.", "The planets dial probably corroded and stopped.",
    "How the planets dial turned is to be determined.", "We can't tell how the planets dial turned.",
    "Dont know how the planets dial worked.", "How the planets dial moved is unknown.",
    "The planets dial turned somehow; details to follow.", "Refer to the planets note: it no longer turns.",
    # boilerplate and deferral
    "See the facts sheet for why.", "Reason to follow.", "Explained in the script notes.",
    "Same reason as the previous sentence.", "The reason is given elsewhere in the record.",
    "Text goes here, text goes here.", "Placeholder: replace with the real reason.", "Answer withheld for now.",
    # lorem ipsum variants
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor.", "LOREM IPSUM planets turned",
    "Dolor sit amet consectetur adipiscing elit.", "ipsum lorem: the planets dial no longer turns",
    # a subject but no why
    "The planets.", "The planets and the dials of the mechanism.", "It is about the planets in the sentence.",
    "The planets were important to its makers.",
]

# A why but no subject: nothing LINE names (the test's sentence has no gears). Refused on LINE; on a sentence that
# names gears, "The gears corroded long ago." would be a genuine reason, so these are not placeholders everywhere.
SUBJECTLESS = [
    "Only the ancient fragments survive.", "Only ancient fragments survive.", "The gears corroded long ago.",
    "It no longer works and only corroded fragments survive.",
    "The working state cannot be photographed; only the remains survive.",
    "The ancient operating state cannot be photographed; only fragments survive.",
    "The old original appearance is lost.", "Something ancient was destroyed long ago.",
]

# (kind, the verdict's own sentence, a genuine reason: prose alone, refused here)
GENUINE = [
    ("original_appearance", "The Parthenon's marble once blazed with painted colour.",
     "The painted colour on the Parthenon's marble faded and weathered away centuries before photography."),
    ("original_appearance", "The Colossus of Rhodes stood beside the harbour.",
     "The Colossus was destroyed by an earthquake in antiquity, so its appearance is lost."),
    ("original_appearance", "The pillars of G\u00f6bekli Tepe once stood in open rings.",
     "The rings at G\u00f6bekli Tepe were buried in antiquity, so the pillars' original setting is lost."),
    ("original_appearance", "Notre-Dame once carried a timber spire.",
     "Notre-Dame's timber spire burned in 2019; the original is lost."),
    ("unphotographed_interior", "Inside the burial chamber, the sarcophagus lay beneath painted stars.",
     "The burial chamber was sealed in antiquity and no photograph of it exists."),
    ("unphotographed_interior", "Inside the vault, the relics rested on stone shelves.",
     "The vault was bricked up and is inaccessible; it has never been photographed."),
    ("mechanism_in_use", LINE, "The planets dial no longer turns; its gears survive only as corroded fragments."),
    ("mechanism_in_use", "The bronze gears of the mechanism turned inside a wooden case.",
     "How the bronze gears meshed and turned cannot be photographed: only corroded fragments survive."),
    ("mechanism_in_use", "Inside the mill, the great wheel turned, grinding grain.",
     "The mill wheel stopped working a century ago and was dismantled."),
    ("construction", "The pyramid rose block by block on the Giza plateau.",
     "How the pyramid's blocks were quarried, hauled and raised happened millennia before any camera."),
    ("construction", "Workers raised the obelisk with ropes and ramps.",
     "The obelisk was raised by hand on earthen ramps, long before photography."),
]


# QF-60b review 2, blocking 1: reconstruction-eligible sentences in the style of the real runs' generable and unshown
# lines (written for this test; no run's narration), each with its kind.
SENTENCES = {
    "etched": ("construction", ("Builders etched the desert figures by scraping away dark surface pebbles to reveal "
                               "the pale clay beneath.")),
    "stakes": ("construction", "Surveyors set wooden stakes at the ends of the lines to lay them out."),
    "freighter": ("original_appearance", "Bronze gears that predicted eclipses sank with a Roman freighter."),
    "crank": ("mechanism_in_use", "A hand crank once drove the bronze gears and swept the pointers across the dials."),
    "vault": ("unphotographed_interior", "Carved into soft volcanic tuff, the complex hides a barrel-vaulted chamber."),
    "doors": ("unphotographed_interior", ("Behind rolling stone doors, the lowest sections of the underground city "
                                         "held stores of grain.")),
    "hotmix": ("construction", "Roman builders mixed quicklime with volcanic ash in a hot mix."),
    "dome": ("construction", "Builders raised the great central dome in brick and lime mortar, with no iron rings."),
    "cup": ("original_appearance", "The glass cup glowed red and green as its maker intended."),
    "melt": ("construction", ("Roman artisans diluted a larger melt to add tiny amounts of gold and silver to the "
                             "glass.")),
    "wheels": ("mechanism_in_use", "Water wheels lifted the river water into the aqueduct channel."),
    "plaster": ("original_appearance", "Painted plaster covered the temple walls in bright colours."),
    "tomb": ("unphotographed_interior", "Inside the sealed tomb, offerings lay stacked around the coffin."),
    "bellows": ("mechanism_in_use", "Bellows forced air into the furnace as smiths hammered the iron."),
    "ramps": ("construction", "Workers hauled the stone blocks up earthen ramps on wooden sledges."),
    "lighthouse": ("original_appearance", "The lighthouse stood more than a hundred metres above the harbour."),
    "escapement": ("mechanism_in_use", "The clock's escapement ticked as weights descended inside the tower."),
    "chapels": ("construction", "Miners carved chapels and statues out of the salt deep underground."),
    "headquarters": ("unphotographed_interior", ("Deep in the catacombs, partisans kept a hidden headquarters with "
                                                "bunks and a radio.")),
    "sculptures": ("original_appearance", ("The salt sculptures had sharp, crisp edges before humidity reached the "
                                          "mine.")),
}

# 34 reasons written the way the model answers VERSION 4 ("a concrete reconstruction_reason explaining the
# unavailable view"), in the style of its real "reason" field: (sentence, reason, genuine by round 3's reading). Round
# 4 grants none of them on its prose: the structure decides (test_prose_alone_never_grants).
REALISTIC = [
    ("etched", "No photograph can show the builders scraping away the dark pebbles to reveal the clay.", True),
    ("etched", "The figures were etched centuries before photography by people scraping pebbles by hand.", True),
    ("etched", "The geoglyphs were made long before photography existed.", False),
    ("stakes", "No photo displays the wooden stakes being set out at the ends of the lines.", False),
    ("stakes", "The stakes were laid out long before any camera existed.", True),
    ("freighter", "The bronze gears' original, uncorroded appearance can no longer be seen.", True),
    ("freighter", "The freighter and its cargo sank and were lost to the sea two thousand years ago.", True),
    ("crank", "The crank drove the gears and swept the pointers across the dials.", False),
    ("crank", ("The crank and gears survive only as corroded fragments, so their working motion cannot be "
              "photographed."), True),
    ("crank", "The turning gears of the mechanism are not shown in any photo.", False),
    ("vault", "The barrel-vaulted chamber does not appear among the pool's pictures.", False),
    ("vault", "The barrel-vaulted chamber is closed to visitors and has never been photographed.", True),
    ("doors", ("The lowest sections were blocked and backfilled centuries ago, so no photograph exists of them in "
              "use."), True),
    ("hotmix", "No photograph can show Roman builders hot-mixing quicklime with volcanic ash.", True),
    ("hotmix", "The hot mix was made two thousand years ago, long before photography.", True),
    ("hotmix", "Roman builders mixed quicklime with volcanic ash.", False),
    ("dome", "The dome was raised in the sixth century, long before cameras.", True),
    ("dome", "The dome was built from bricks and lime mortar.", False),
    ("cup", "The cup's original glow has faded with age and cannot be seen as its maker intended.", True),
    ("melt", "Roman artisans diluted the melt in antiquity; no photograph can show it.", True),
    ("melt", "The diluted melt was made in Roman workshops that no camera ever recorded.", True),
    ("wheels", ("The water wheels were dismantled long ago, so their lifting of the river water cannot be "
               "photographed."), True),
    ("wheels", "Water wheels lifted water into the channel.", False),
    ("plaster", "The painted plaster has flaked away and the bright colours are lost.", True),
    ("plaster", "Only faint traces of the painted plaster remain on the temple walls.", True),
    ("tomb", "The tomb was resealed after its excavation and was never photographed inside.", True),
    ("bellows", "The bellows and furnace were destroyed, and no working forge of this kind survives.", True),
    ("bellows", "Bellows forcing air into a working furnace is an unphotographed historical process.", True),
    ("ramps", "Hauling the blocks up the ramps happened thousands of years before photography.", True),
    ("lighthouse", "The lighthouse collapsed in earthquakes centuries ago and its stones were reused.", True),
    ("escapement", ("The original escapement was replaced long ago, so its ticking as the weights descended cannot "
                   "be photographed."), True),
    ("chapels", "No photograph can capture centuries of miners carving the chapels by hand.", True),
    ("headquarters", ("The hidden headquarters was abandoned and walled up after the war; no photograph of it in use "
                     "exists."), True),
    ("headquarters", "The partisans' hidden headquarters is not depicted in any available photo.", False),
]

# The same sentences, one reason each as round 3's VERSION 5 prompt asked: granted by round 3's prose rule, by none
# here without the structure.
REWRITTEN = [
    ("etched", ("The desert figures were etched by hand centuries before photography, so no photograph shows the "
               "pebbles being scraped away.")),
    ("stakes", "The wooden stakes were set out long before photography and only a few rotted stumps survive."),
    ("freighter", "The bronze gears corroded on the seabed, so their original appearance is lost."),
    ("crank", ("The hand crank is lost and the bronze gears survive only as corroded fragments, so they no longer "
              "turn.")),
    ("vault", "The barrel-vaulted chamber is sealed and has never been photographed."),
    ("doors", ("The lowest sections behind the stone doors were backfilled and are inaccessible, so they have never "
              "been photographed.")),
    ("hotmix", "Roman builders hot-mixed the quicklime two thousand years ago, long before photography."),
    ("dome", "The central dome was raised in the sixth century, before photography."),
    ("cup", "The glass cup's original glow has faded and darkened, so its intended colours are lost."),
    ("melt", "The Roman artisans diluted the melt in antiquity, so no photograph can show it."),
    ("wheels", "The water wheels were dismantled centuries ago and no longer lift the river water."),
    ("plaster", "The painted plaster has flaked away, so the temple walls' bright colours are lost."),
    ("tomb", "The tomb was resealed after its excavation and its offerings were never photographed in place."),
    ("bellows", "The bellows and the furnace were destroyed, so the smiths' forge no longer works."),
    ("ramps", ("The earthen ramps were dismantled after the blocks were hauled, thousands of years before "
              "photography.")),
    ("lighthouse", "The lighthouse collapsed in earthquakes centuries ago, so its original appearance is lost."),
    ("escapement", "The clock's original escapement was replaced long ago and no longer ticks."),
    ("chapels", "The miners carved the chapels by hand over centuries, before photography."),
    ("headquarters", "The hidden headquarters in the catacombs was walled up after the war and never photographed."),
    ("sculptures", "Humidity eroded the salt sculptures, so their crisp edges are lost."),
]

# Review 2, blocking 2: a reason that only restates the depicted action, or copies its sentence, states no cause.
GEARS = "Bronze gears turned dials of planets inside the box."
DOME = "Workers built the dome and raised the pillars around it."
RESTATED = [
    ("mechanism_in_use", GEARS, "The gears turned."),
    ("mechanism_in_use", GEARS, "Gears turn. Dials turn. That is all."),
    ("mechanism_in_use", GEARS, "Bronze gears turned dials of planets inside the box."),
    ("mechanism_in_use", GEARS, "The bronze gears worked and moved the planet dials."),
    ("mechanism_in_use", GEARS, "The gears operated, rotated and meshed to drive the dials."),
    ("construction", DOME, "The dome was built."),
    ("construction", DOME, "The pillars were raised."),
    ("construction", DOME, "Workers built the dome and raised the pillars around it."),
    ("construction", DOME, "The dome was assembled, laid and erected by the workers."),
]
# A copy of a sentence that itself names a cause gives no cause the sentence does not.
COPIED = [
    ("unphotographed_interior", "Inhabitants sealed the lowest sections of the city during raids.",
     "Inhabitants sealed the lowest sections of the city during raids."),
    ("original_appearance", "Bronze gears that predicted eclipses sank with a Roman freighter.",
     "Bronze gears predicting eclipses sank with a Roman freighter."),
    ("construction", "The city was abandoned and buried under the dunes.", "The city was abandoned and buried."),
]

# Review 2, blocking 3: hedges with a subject and a why, a contrary or negated why, scripts and other languages.
HEDGED = [
    "We do not really know whether the gears turned before photography.",
    "Who can say how the gears turned before photography.",
    "There's no telling how the gears turned before photography.",
    "Nobody can be certain the gears ever turned before photography.",
    "It is possible the gears turned before photography, we think.",
    "The gears could have turned before photography, or not.",
    "The gears turned before photography, I suppose.",
    "It is debatable whether the gears turned before photography.",
    "Did the gears corrode before photography?",
    "The gears corroded before photography?",
    "N/A - gears corroded before photography",
    "The gears corroded before photography [citation needed].",
    "The gears corroded before photography, but we cannot confirm anything.",
    "The gears might have corroded before photography.",
    "The gears likely corroded before photography.",
    "The gears are believed to have corroded before photography.",
    "The gears reportedly corroded before photography.",
    "The gears seemingly corroded before photography.",
    "The gears may well have corroded before photography.",
]
CONTRARY = [
    ("mechanism_in_use", GEARS, "A photo of the gears turning does exist."),
    ("mechanism_in_use", GEARS, "The gears still turn today and are photographed often."),
    ("mechanism_in_use", GEARS, "The gears are not corroded and still work perfectly."),
    ("mechanism_in_use", GEARS, "The gears were never lost; photos of them exist."),
    ("mechanism_in_use", GEARS, "The gears survive intact and can be photographed."),
    ("mechanism_in_use", GEARS, "The gears were not destroyed and are photographed in the museum."),
    ("mechanism_in_use", GEARS, "Photographs of the gears exist, although the box was lost."),
    ("original_appearance", "The marble once blazed with painted colour.",
     "The marble was never lost or destroyed; photos of it exist."),
    ("original_appearance", "The marble once blazed with painted colour.",
     "The painted colour of the marble has not faded at all."),
    ("unphotographed_interior", "Inside, the altar stood beneath painted stars.",
     "The altar is not sealed and has been photographed many times."),
    ("unphotographed_interior", "Inside, the altar stood beneath painted stars.",
     "The altar was never sealed and is photographed by visitors."),
    ("construction", "Workers built the dome and raised the pillars around it.",
     "The dome was never buried or rebuilt and is photographed every day."),
]
SCRIPTS = [
    "The gears turned в древности and are lost.",
    "The gears were lost ένα time.",
    "The gears were lost before photography ᴜɴᴋɴᴏᴡɴ.",
    "The gears were lost ɐɹɔ before photography.",
    "The gears were lost before photography, pero no sabemos nada.",
    "Los gears were lost before photography, no se sabe por que.",
    "Gears were lost before photography, inconnu, the end.",
    "The gears were lost before photography, wir wissen es nicht.",
    "The gears were lost before photography, non lo so, che peccato.",
    "The gears were lost before photography, geen idee waarom het is.",
    "The gears were lost before photography, nao sei a razao.",
    "Les gears sont lost before photography.",
]
# Genuine reasons with negations, dates and remains that round 3's prose rule granted (prose alone: refused here).
GRANTED_EDGE = [
    ("mechanism_in_use", GEARS, "None of the bronze gears still turns; they survive only as corroded fragments."),
    ("mechanism_in_use", GEARS, "The gears corroded on the seabed, so no camera ever saw them turn."),
    ("mechanism_in_use", GEARS, "The gears turned only in the past; today they are fragments."),
    ("original_appearance", "The marble once blazed with painted colour.",
     "Nothing of the painted colour on the marble survives."),
    ("original_appearance", "The marble once blazed with painted colour.",
     "The painted colour faded from the marble before it could be photographed."),
    ("original_appearance", "The spire was made of oak timbers.", "The spire burned and was never rebuilt."),
    ("original_appearance", "The Colossus of Rhodes stood beside the harbour.",
     "The Colossus was toppled by an earthquake in 226 BC."),
    ("original_appearance", "The keep rose above the river.", "The keep was demolished in 1820."),
    ("unphotographed_interior", "Inside the vault, the relics rested on stone shelves.",
     "The vault has never been opened, so no photograph of the relics exists."),
    ("construction", "Masons raised the cathedral's nave stone by stone.",
     "The nave was raised in the 13th century, before photography."),
    ("construction", "Masons raised the cathedral's nave stone by stone.",
     "Masons raised the nave in the 1200s, centuries before any camera."),
]


# ------------------------------------------------------------------ the structure (QF-60b round 4, decision A)
# The table this row defines, written here independently of the code (the code's table must equal it): the causes a
# kind allows. Every other (kind, cause) pair is a mismatch and grants nothing. before_photography is true of any
# ancient subject, so it is a reason for construction alone (round 5): 6 allowed pairs, 14 refused.
CAUSES = ["lost_or_destroyed", "sealed_or_inaccessible", "never_photographed", "before_photography",
          "no_longer_operating"]
COMPATIBLE = {
    "original_appearance": {"lost_or_destroyed"},
    "unphotographed_interior": {"sealed_or_inaccessible", "never_photographed"},
    "mechanism_in_use": {"no_longer_operating", "lost_or_destroyed"},
    "construction": {"before_photography"},
}
ALLOWED = [(kind, cause) for kind in sorted(COMPATIBLE) for cause in CAUSES if cause in COMPATIBLE[kind]]
MISMATCHED = [(kind, cause) for kind in sorted(COMPATIBLE) for cause in CAUSES if cause not in COMPATIBLE[kind]]
# One sentence per kind, the subject words copied from it, and a reviewer's reason in the round-3 style (one the
# round-3 prose rule also accepted, so a test of the structure is not decided by the prose).
PER_KIND = {
    "original_appearance": ("The temple's marble shone with painted colour.", "marble",
                            "The painted colour of the temple's marble was lost centuries before photography."),
    "unphotographed_interior": ("Inside the tomb, offerings lay around the coffin.", "offerings lay around the coffin",
                                "The tomb was sealed in antiquity and its offerings were never photographed."),
    "mechanism_in_use": (GEARS, "Bronze gears",
                         "The bronze gears survive only as corroded fragments and no longer turn."),
    "construction": (DOME, "the dome", "The dome was built centuries before photography."),
}
# Genuine structured verdicts, every kind: (kind, sentence, subject words, cause, reviewer's reason).
STRUCTURED = [
    ("original_appearance", "The Parthenon's marble once blazed with painted colour.", "painted colour",
     "lost_or_destroyed", "The painted colour faded and weathered away."),
    ("original_appearance", "The Colossus of Rhodes stood beside the harbour.", "The Colossus of Rhodes",
     "lost_or_destroyed", "An earthquake toppled it in 226 BC."),
    ("original_appearance", "The pillars of G\u00f6bekli Tepe once stood in open rings.", "pillars of G\u00f6bekli Tepe",
     "lost_or_destroyed", "The rings were buried in antiquity."),
    ("unphotographed_interior", "Inside the burial chamber, the sarcophagus lay beneath painted stars.",
     "the burial chamber", "sealed_or_inaccessible", "The chamber was sealed in antiquity."),
    ("unphotographed_interior", "Inside the vault, the relics rested on stone shelves.", "the relics",
     "never_photographed", "The vault was bricked up and never opened to a camera."),
    ("mechanism_in_use", LINE, "the lost mechanism", "lost_or_destroyed", "Only corroded fragments are left."),
    ("mechanism_in_use", "The bronze gears of the mechanism turned inside a wooden case.", "bronze gears",
     "no_longer_operating", "Paint lost; gears seized."),
    ("mechanism_in_use", "Inside the mill, the great wheel turned, grinding grain.", "the great wheel",
     "lost_or_destroyed", "The mill was dismantled in 1790."),
    ("construction", "The pyramid rose block by block on the Giza plateau.", "pyramid", "before_photography",
     "Built about 2560 BC."),
    ("construction", "Workers raised the obelisk with ropes and ramps.", "the obelisk", "before_photography",
     "Raised by hand on earthen ramps."),
]

# Review 3's probes: granted on 1f7de19a by their prose alone. Each is (kind, its sentence, the reason as written).
WATER_CLOCK = "The water clock rang the bells every hour."
POURED = "Workers poured the dome in layers of concrete."
TEMPLE = "The temple walls glowed with painted colour."
MILL_WHEEL = "The mill wheel drove the grinding stones."
GALLEY = "The galley oars swept through the water."
ROSE = "The dome rose above the city."
STATUE = "Inside the museum stands a statue of the emperor."
RAMBLING = ("Well, so, about the water clock, you see, it rang the bells, and people in the town liked it a lot, and "
            "it stood in the square for many years, and the bells were loud, and the hour was rung each day, and "
            "children listened, and so on and so forth, and the water clock was there, and a camera is a thing.")
ROUND3_PROBES = [
    # blocking 1: a vacuous restatement with the prompt's own "no photograph can show it" appended
    ("mechanism_in_use", WATER_CLOCK, "The water clock rang the bells; no photograph can show it."),
    ("construction", POURED, "Workers poured the dome; no photograph can show it."),
    ("original_appearance", TEMPLE, "The temple walls glowed; no photograph can show it."),
    ("mechanism_in_use", MILL_WHEEL, "The mill wheel drove the stones, which no photograph can show."),
    ("mechanism_in_use", GALLEY, "The galley oars swept; it cannot be photographed."),
    ("construction", ROSE, "The dome rose, impossible to photograph."),
    ("mechanism_in_use", WATER_CLOCK, "The water clock rang the hour with no camera present."),
    # the prompt's own photographable example
    ("unphotographed_interior", STATUE, "No photograph can show the statue inside the museum."),
    # blocking 2: a camera word as the cause, with a photograph said to exist
    ("mechanism_in_use", WATER_CLOCK, "A camera recorded the water clock ringing the bells."),
    ("construction", POURED, "Cameras documented the workers pouring the dome."),
    ("mechanism_in_use", MILL_WHEEL, "The mill wheel was filmed turning by a camera crew in 1990."),
    ("mechanism_in_use", WATER_CLOCK, "A daguerreotype shows the water clock with its bronze float."),
    ("original_appearance", TEMPLE, "An early photo predates the repainting of the temple walls."),
    ("mechanism_in_use", WATER_CLOCK, RAMBLING),
]
# Subject words copied from each probe's sentence, for the probes given a structure that is still wrong.
PROBE_SUBJECTS = {WATER_CLOCK: "The water clock", POURED: "the dome", TEMPLE: "The temple walls",
                  MILL_WHEEL: "The mill wheel", GALLEY: "The galley oars", ROSE: "The dome", STATUE: "a statue"}

# Values a model, a cache or a hand edit may leave in the structured fields that are no valid answer.
BAD_CAUSES = ["Lost_or_destroyed", "lost_or_destroyed ", " before_photography", "lost or destroyed", "lost",
              "destroyed", "before photography", "BEFORE_PHOTOGRAPHY", "", None, 1, True, ["before_photography"],
              {"cause": "before_photography"}, "before_photography|lost_or_destroyed", "before_photography.",
              "unknown", "none", "other", "photo_missing_from_pool"]
# Subject words for GEARS ("Bronze gears turned dials of planets inside the box.") that are not its own words.
BAD_SUBJECTS = [
    "", "   ", None, 5, ["Bronze", "gears"], {"words": "Bronze gears"},
    "the", "inside the", "of", "Inside",                       # stop words only
    "gears bronze", "dials planets", "bronze gear", "gear", "Bronze gears turned the dials",   # order, part words
    "brass gears", "the mechanism", "Antikythera", "the dome",           # words the sentence does not have
    "Bronze g\u0435ars", "Bronze ge\u200bars", "\uff22\uff52\uff4f\uff4e\uff5a\uff45 gears",   # lookalike, split
    "Bronze gears turned dials of planets inside the box",                # 9 words: the sentence, not a subject
]


def structured_verdict(kind, line, subject, cause, reason):
    return {"sentence": line, "kind": "generable", "visual": "named_subject", "reconstruction": True,
            "reconstruction_kind": kind, "reconstruction_cause": cause, "reconstruction_subject_words": subject,
            "reconstruction_reason": reason, "placement": {"by": "word_position", "sentence_index": 0, "beat": line}}


def s_spec(v):
    return {"line": v["sentence"], "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **reconstruction.bind(v)}


def s_places(v):
    """(planner, generation gate, relaxed nothing-added rule) for a placed verdict on its own sentence's beat."""
    line = v["sentence"]
    planner_ok = image_gen.illustration_reason(line, verdict=v) == image_gen.RECONSTRUCTION_REASON
    with mock.patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(line, s_spec(v), None, reason=image_gen.RECONSTRUCTION_REASON)
    return planner_ok, info["reason"] == "generation off or no key", (
        "may show more" in image_quarantine._nothing_added_rule(s_spec(v)))


def kind_verdict(kind, cause=None, subject=None, reason=None):
    line, own_subject, own_reason = PER_KIND[kind]
    return structured_verdict(kind, line, own_subject if subject is None else subject,
                              cause if cause is not None else min(COMPATIBLE[kind]),
                              own_reason if reason is None else reason)


def test_the_code_uses_the_compatibility_table_and_cause_list_of_this_row():
    assert {k: set(v) for k, v in getattr(reconstruction, "COMPATIBLE", {}).items()} == COMPATIBLE
    assert sorted(getattr(reconstruction, "CAUSES", {})) == sorted(CAUSES)
    assert (len(ALLOWED), len(MISMATCHED)) == (6, 14)


# Review 4's probes: an honest model may call any ancient subject "before photography", even when its look survives
# unchanged or its mechanism still works today, so that cause grants nothing but construction.
SURVIVING = [
    ("original_appearance", "The Pantheon's granite columns stand at the portico today.", "granite columns",
     "The columns were raised in the second century and stand unchanged."),
    ("mechanism_in_use", "The great water wheel at the abbey turns the millstones.", "great water wheel",
     "The wheel was built in the 1600s and still turns for visitors."),
]


@pytest.mark.parametrize("kind, line, subject, reason", SURVIVING)
def test_before_photography_grants_no_surviving_look_or_working_mechanism(kind, line, subject, reason):
    assert s_places(structured_verdict(kind, line, subject, "before_photography", reason)) == (False, False, False)


def test_before_photography_is_a_reason_for_construction_only():
    assert [kind for kind in sorted(COMPATIBLE) if "before_photography" in COMPATIBLE[kind]] == ["construction"]
    granted = [kind for kind in sorted(COMPATIBLE) if s_places(kind_verdict(kind, "before_photography"))[0]]
    assert granted == ["construction"]
    from src import prevoice_lines
    system = " ".join(prevoice_lines.SYSTEM.split())
    assert "before_photography (the building or making happened before photography began in 1826" in system
    assert "construction only, never a thing that still stands or works" in system


@pytest.mark.parametrize("kind, line, subject, cause, reason", STRUCTURED)
def test_a_genuine_structured_verdict_is_granted_in_all_three_places(kind, line, subject, cause, reason):
    assert s_places(structured_verdict(kind, line, subject, cause, reason)) == (True, True, True)


@pytest.mark.parametrize("kind, cause", ALLOWED)
def test_every_cause_a_kind_allows_is_granted(kind, cause):
    assert s_places(kind_verdict(kind, cause)) == (True, True, True)


@pytest.mark.parametrize("kind, cause", MISMATCHED)
def test_a_cause_the_kind_does_not_allow_grants_nothing(kind, cause):
    assert s_places(kind_verdict(kind, cause)) == (False, False, False)


@pytest.mark.parametrize("kind, line, reason", ROUND3_PROBES)
def test_a_round_3_probe_as_written_has_no_structure_and_grants_nothing(kind, line, reason):
    v = structured_verdict(kind, line, None, None, reason)
    for key in ("reconstruction_cause", "reconstruction_subject_words"):
        v.pop(key)                                            # as the round-3 prompt answered: prose only
    assert s_places(v) == (False, False, False)


@pytest.mark.parametrize("kind, line, reason", ROUND3_PROBES)
def test_a_round_3_probe_with_its_subject_but_no_cause_grants_nothing(kind, line, reason):
    assert s_places(structured_verdict(kind, line, PROBE_SUBJECTS[line], None, reason)) == (False, False, False)


@pytest.mark.parametrize("kind, line, reason", ROUND3_PROBES)
def test_a_round_3_probe_with_a_cause_its_kind_does_not_allow_grants_nothing(kind, line, reason):
    for cause in CAUSES:
        if cause not in COMPATIBLE[kind]:
            v = structured_verdict(kind, line, PROBE_SUBJECTS[line], cause, reason)
            assert s_places(v) == (False, False, False), cause


def test_the_prompts_photographable_statue_example_is_refused_without_an_interior_cause():
    for cause in [*CAUSES, None]:
        v = structured_verdict("unphotographed_interior", STATUE, "a statue", cause,
                               "No photograph can show the statue inside the museum.")
        granted = cause in COMPATIBLE["unphotographed_interior"]
        assert s_places(v) == (granted, granted, granted), cause
    # "inside" alone (the prompt's own warning word) names nothing
    v = structured_verdict("unphotographed_interior", STATUE, "Inside", "sealed_or_inaccessible", "Sealed.")
    assert s_places(v) == (False, False, False)


@pytest.mark.parametrize("prose", [
    *PLACEHOLDERS, *SUBJECTLESS, *(g[2] for g in GENUINE), *(r[1] for r in REALISTIC), *(r[1] for r in REWRITTEN),
    *(r[2] for r in RESTATED + COPIED + CONTRARY + GRANTED_EDGE), *HEDGED, *SCRIPTS])
def test_prose_alone_never_grants(prose):
    """Every reason of rounds 1-3, genuine or not, written as the only answer (no cause, no subject words)."""
    v = structured_verdict("mechanism_in_use", GEARS, "", None, prose)
    assert s_places(v) == (False, False, False)


@pytest.mark.parametrize("kind, sentence, reason", [(SENTENCES[k][0], SENTENCES[k][1], r) for k, r in REWRITTEN])
def test_a_genuine_round_3_reason_without_the_structure_grants_nothing(kind, sentence, reason):
    v = structured_verdict(kind, sentence, None, None, reason)
    v.pop("reconstruction_cause")
    v.pop("reconstruction_subject_words")
    assert s_places(v) == (False, False, False)


@pytest.mark.parametrize("cause", BAD_CAUSES, ids=repr)
def test_a_cause_outside_the_list_or_mistyped_grants_nothing(cause):
    v = kind_verdict("mechanism_in_use")
    v["reconstruction_cause"] = cause
    assert s_places(v) == (False, False, False)


@pytest.mark.parametrize("subject", BAD_SUBJECTS, ids=repr)
def test_subject_words_that_are_not_words_of_the_sentence_grant_nothing(subject):
    v = kind_verdict("mechanism_in_use")
    v["reconstruction_subject_words"] = subject
    assert s_places(v) == (False, False, False)


def test_subject_words_of_another_sentence_grant_nothing():
    for kind in COMPATIBLE:
        for other_kind, (other_line, other_subject, _) in PER_KIND.items():
            if other_kind != kind:
                assert s_places(kind_verdict(kind, subject=other_subject)) == (False, False, False), other_line


def test_subject_words_are_matched_as_whole_words_case_and_punctuation_insensitively():
    for subject in ("bronze gears", "BRONZE GEARS", "Bronze gears,", "  Bronze   gears ", "planets inside the box",
                    "gears turned dials of planets"):
        assert s_places(kind_verdict("mechanism_in_use", subject=subject)) == (True, True, True), subject
    assert s_places(kind_verdict("mechanism_in_use", subject="Bronze gears turned dials of planets inside")) == (
        True, True, True)                                                  # 7 words: still a phrase of it
    for subject in ("ronze gears", "bronze gearsturned", "gear"):
        assert s_places(kind_verdict("mechanism_in_use", subject=subject)) == (False, False, False), subject


@pytest.mark.parametrize("field, value", [
    ("reconstruction", "true"), ("reconstruction", 1), ("reconstruction", None), ("reconstruction_kind", None),
    ("reconstruction_kind", "Mechanism_in_use"), ("reconstruction_kind", "mechanism"), ("reconstruction_kind", []),
    ("kind", "shown"), ("visual", "abstract"), ("sentence", "Another sentence."),
    ("reconstruction_reason", ""), ("reconstruction_reason", None), ("reconstruction_reason", "none"),
    ("reconstruction_reason", "N/A"), ("reconstruction_reason", "TBD"), ("reconstruction_reason", "No reason given."),
    ("reconstruction_reason", "x" * 401),
])
def test_a_tampered_or_missing_field_grants_nothing(field, value):
    v = kind_verdict("mechanism_in_use")
    assert s_places(v) == (True, True, True)
    assert s_places({**v, field: value}) == (False, False, False)


@pytest.mark.parametrize("field, value", [
    ("reconstruction_cause", "never_photographed"), ("reconstruction_cause", "lost"),
    ("reconstruction_subject_words", "the dome"), ("reconstruction_subject_words", ""),
    ("reconstruction_kind", "unphotographed_interior"), ("sentence", DOME),
])
def test_a_verdict_changed_after_it_was_bound_keeps_the_gate_and_the_nothing_added_rule_strict(field, value):
    v = kind_verdict("mechanism_in_use")
    spec = s_spec(v)
    spec["reconstruction_verdict"][field] = value
    with mock.patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(GEARS, spec, None, reason=image_gen.RECONSTRUCTION_REASON)
    assert info["reason"] != "generation off or no key"
    assert "may show more" not in image_quarantine._nothing_added_rule(spec)


def test_the_reviewer_reason_is_shown_but_its_wording_does_not_decide():
    """The structure decides; the reason must be readable (present, not a bare placeholder). Two wordings the round-3
    prose rule refused, terse and genuine, are granted with a valid structure."""
    for reason in ("Paint lost.", "Demolished in 1820.", "Seized; fragments only."):
        assert s_places(kind_verdict("mechanism_in_use", reason=reason)) == (True, True, True), reason


# ------------------------------------------------------------------ the pre-voice answer carries the structure
def test_the_answer_schema_offers_the_cause_list_and_keeps_a_bad_cause_from_failing_the_answer():
    from src import prevoice_lines
    schema = json.dumps(prevoice_lines.LineReport.model_json_schema())
    assert all(cause in schema for cause in CAUSES) and "reconstruction_subject_words" in schema
    report = prevoice_lines.LineReport.model_validate({"lines": [
        {"index": 1, "kind": "generable", "reconstruction": True, "reconstruction_kind": "construction",
         "reconstruction_cause": "built long ago", "reconstruction_subject_words": ["the", "dome"]},
        {"index": 2, "kind": "shown", "photo": 1}]})
    first = report.lines[0]
    assert getattr(first, "reconstruction_cause", "missing") is None
    assert getattr(first, "reconstruction_subject_words", "missing") == ""


def test_the_judge_keeps_the_structured_answer_verbatim_and_it_grants(monkeypatch):
    from src import free_llm, prevoice_lines
    subject = "Bronze gears"

    def answer(system, prompt, schema, **kwargs):
        return schema(lines=[{"index": 1, "kind": "generable", "visual": "named_subject", "reconstruction": True,
                              "reconstruction_kind": "mechanism_in_use", "reconstruction_cause": "no_longer_operating",
                              "reconstruction_subject_words": subject, "reconstruction_reason": "Seized fragments."}])
    monkeypatch.setattr(free_llm, "call", answer)
    row = prevoice_lines.judge(GEARS, [{"file": "fragments.png", "text": "The surviving fragments"}], "Mechanism")[0]
    assert row.get("reconstruction_cause") == "no_longer_operating"
    assert row.get("reconstruction_subject_words") == subject
    placed = {**row, "placement": {"by": "word_position", "sentence_index": 0, "beat": GEARS}}
    assert image_gen.illustration_reason(GEARS, verdict=placed) == image_gen.RECONSTRUCTION_REASON


# The pre-voice prompt asks for the structure (VERSION 5): the cause list and the table come from the code, and the
# echoable stock phrases of round 3 are gone.
def test_the_pre_voice_prompt_asks_for_the_cause_and_the_subject_words_and_is_version_5():
    from src import prevoice_lines
    system = " ".join(prevoice_lines.SYSTEM.split())
    assert prevoice_lines.VERSION == 5 and prevoice_lines.keys("A line.", [])["v"] == 5
    assert "reconstruction_cause: exactly one of:" in system
    for cause in CAUSES:
        assert cause in system
    for kind, allowed in COMPATIBLE.items():                              # the table, causes in list order
        assert f"{kind}: {' or '.join(c for c in CAUSES if c in allowed)}" in system, kind
    assert "reconstruction_subject_words: the words of the sentence that name the thing reconstructed, copied " \
           "exactly as they stand in the sentence, at most 8 words." in system
    assert "reconstruction_reason: one short sentence for the human reviewer." in system


@pytest.mark.parametrize("phrase", ["no photograph can show", "no photo can show", "cannot be photographed",
                                    "no camera", "the concrete cause", "can show it", "impossible to photograph"])
def test_the_pre_voice_prompt_has_no_echoable_stock_phrase(phrase):
    from src import prevoice_lines
    assert phrase not in " ".join(prevoice_lines.SYSTEM.lower().split())


# ------------------------------------------------------------------ re-asking: once, the reconstruction alone
FINE = f"{pv.HOOK} {pv.PANTHEON} {pv.HARBOUR} {pv.QUESTION} {pv.CLOSE}"
STRUCTURE_FIELDS = ("reconstruction_cause", "reconstruction_subject_words")
RECONSTRUCTION_FIELDS = {"reconstruction", "reconstruction_kind", "reconstruction_reason", *STRUCTURE_FIELDS}


def _judged_run(world, monkeypatch, version, reconstruct=None, structured=False, cause="before_photography"):
    """A run judged under `version` (record, stamp and checkpoint) as that version's prompt answered: without the
    structured fields (VERSION 4, and VERSION 5 of round 3), or with them. Optionally one sentence's verdict is a
    reconstruction in the record (checkpointed again). Then the current version: the stamped payload."""
    from src import prevoice_lines as pl
    from src.checkpoints import Checkpoints
    current = pl.VERSION
    monkeypatch.setattr(pl, "VERSION", version)
    out = world.check(pv._payload(FINE))
    record = world.run_dir / pl.RECORD_FILE
    saved = json.loads(record.read_text(encoding="utf-8"))
    for v in saved["verdicts"]:
        if not structured:
            for key in STRUCTURE_FIELDS:
                v.pop(key, None)
        if v["sentence"] == reconstruct:
            v.update(reconstruction=True, reconstruction_kind="construction",
                     reconstruction_reason="The dome was built.")
            if structured:
                v.update(reconstruction_cause=cause, reconstruction_subject_words="concrete dome")
    record.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    Checkpoints(world.run_dir).complete(pl.STAGE, {k: saved[k] for k in ("v", "script", "pool")}, [record])
    monkeypatch.setattr(pl, "VERSION", current)
    return out


def _not_asked(world, first):
    from src import prevoice_lines as pl
    calls, record = len(world.judge.calls), (world.run_dir / pl.RECORD_FILE).read_bytes()
    assert world.check(first) is first                                    # the stamp
    bare = {k: v for k, v in first.items() if k != pl.STAGE}
    assert world.check(bare) is bare                                      # the checkpoint alone
    assert len(world.judge.calls) == calls and world.revisions == []
    assert (world.run_dir / pl.RECORD_FILE).read_bytes() == record        # nothing written again


def test_a_version_4_record_with_no_reconstruction_is_not_asked_again(request, monkeypatch):
    world = request.getfixturevalue("pv_world")      # the pre-voice check's world
    _not_asked(world, _judged_run(world, monkeypatch, 4))


def test_a_structured_version_5_record_is_not_asked_again(request, monkeypatch):
    world = request.getfixturevalue("pv_world")
    _not_asked(world, _judged_run(world, monkeypatch, 5, reconstruct=pv.PANTHEON, structured=True))


def test_a_version_5_record_whose_answer_gave_no_valid_cause_is_not_asked_again(request, monkeypatch):
    """The fields were asked for and the answer left the cause empty: refused at the grant, never asked in a loop."""
    world = request.getfixturevalue("pv_world")
    _not_asked(world, _judged_run(world, monkeypatch, 5, reconstruct=pv.PANTHEON, structured=True, cause=None))


@pytest.mark.parametrize("version", [4, 5], ids=["version 4", "version 5 of round 3"])
def test_a_record_with_an_unstructured_reconstruction_is_asked_once_for_that_answer_alone(request, monkeypatch,
                                                                                        version):
    world = request.getfixturevalue("pv_world")      # the pre-voice check's world
    from src import prevoice_lines as pl
    first = _judged_run(world, monkeypatch, version, reconstruct=pv.PANTHEON)
    before = json.loads((world.run_dir / pl.RECORD_FILE).read_text(encoding="utf-8"))["verdicts"]
    calls = len(world.judge.calls)
    answer = {"reconstruction": True, "reconstruction_kind": "construction",
              "reconstruction_cause": "before_photography", "reconstruction_subject_words": "unreinforced concrete dome",
              "reconstruction_reason": "Raised in the second century."}

    def judge_v5(system, prompt, schema, **kw):     # the new answer: the structure, and a changed kind elsewhere
        assert "reconstruction_cause: exactly one of:" in " ".join(system.split())
        lines = []
        for v in world.judge(system, prompt, schema, **kw).lines:
            v = v.model_dump()
            if v["index"] == 2:
                v.update(answer)
            if v["index"] == 3:
                v.update(kind="unshown", photo=None)
            lines.append(v)
        return schema.model_validate({"lines": lines})
    monkeypatch.setattr("src.free_llm.call", judge_v5)
    out = world.check(first)
    assert len(world.judge.calls) == calls + 1 and world.revisions == []  # one light call, no rewording
    assert out["narration_script"] == FINE and out[pl.STAGE]["v"] == 5
    after = json.loads((world.run_dir / pl.RECORD_FILE).read_text(encoding="utf-8"))["verdicts"]
    assert ([{k: v for k, v in a.items() if k not in RECONSTRUCTION_FIELDS} for a in after]
            == [{k: v for k, v in b.items() if k not in RECONSTRUCTION_FIELDS} for b in before])  # nothing else
    assert [{k: a.get(k) for k in answer} for a in after][1] == answer
    assert [a.get("reconstruction") for a in after] == [False, True, False, False, False]
    assert all(a.get("reconstruction_cause", "missing") is None for i, a in enumerate(after) if i != 1)
    assert world.check(out) is out and len(world.judge.calls) == calls + 1      # current now: asked once
    assert getattr(reconstruction, "structured", lambda *a: False)(after[1], pv.PANTHEON) is True   # the grant test
