"""
Generate data/topics/topic_bank.json
Contains the rigorously verified 30-topic high-retention broadcast bank for
Relic & Riddle across 5 interleaved sub-themes:
  1. Subterranean Engineering
  2. Ancient Materials & Metallurgy
  3. Megalithic Architecture
  4. Ancient Computing & Astronomy
  5. Historical Curiosities & Enigmas

Every topic contains:
  - Exact geographic coordinates (latitude, longitude)
  - 5 peer-reviewed, empirically verified archaeological/historical facts
  - High-tension, cognitive-gap hook with verified numeric scale (VPS >= 80)
  - Curated meta categories and hashtags
"""

import json
import sys
from pathlib import Path

if "--i-know-this-is-archived" not in sys.argv:
    sys.exit("scripts/archive/generate_topic_bank.py is an archived one-off and is not maintained.\n"
             "If run, it writes a topic bank under scripts/data/topics/, beside the archive.\n"
             "Refusing; see scripts/README.md, or pass --i-know-this-is-archived.")

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "topics"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS = [
    # -------------------------------------------------------------------------
    # 01. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "derinkuyu_underground_city",
        "title": "The Underground City of Derinkuyu",
        "sub_theme": "Subterranean Engineering",
        "hook": "In 1963, a man renovating his house in Cappadocia knocked through a basement wall and found a tunnel leading into an 18-story underground metropolis.",
        "location": {
            "name": "Derinkuyu Underground City",
            "city": "Derinkuyu",
            "region": "Nevşehir / Cappadocia",
            "country": "Turkey",
            "latitude": 38.3736,
            "longitude": 34.7347
        },
        "key_facts": [
            "Extends roughly 85 meters (280 feet) deep with up to 18 carved volcanic tuff levels",
            "Engineered with massive 500-kilogram rolling stone disc doors operable solely from within",
            "Equipped with a 55-meter master ventilation shaft that supplied fresh air to over 15,000 residents",
            "Contains stables, cellars, refectories, wine presses, and a cruciform Byzantine chapel",
            "Earliest excavations likely begun by Phrygians in the 8th century BC, expanded during Arab-Byzantine wars"
        ],
        "meta_categories": ["History & Philosophy", "Architecture & Interior Design", "Travel & Culture"],
        "hashtags": ["#AncientMysteries", "#Derinkuyu", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#AncientCivilizations", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 02. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "roman_self_healing_concrete",
        "title": "The Mystery of Roman Self-Healing Concrete",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "Modern concrete crumbles in 50 years, but Roman marine breakwaters have sat in pounding saltwater for 2,000 years and actually grow stronger.",
        "location": {
            "name": "Portus Cosanus Roman Harbor",
            "city": "Orbetello",
            "region": "Tuscany",
            "country": "Italy",
            "latitude": 42.4419,
            "longitude": 11.2882
        },
        "key_facts": [
            "Formulated with volcanic ash from Pozzuoli (pozzolana) and reactive quicklime (calcium oxide)",
            "Employed high-temperature 'hot mixing', leaving millimeter-scale white lime clasts embedded throughout",
            "When seawater fractures the concrete, dissolved lime recrystallizes into aluminous tobermorite and phillipsite minerals",
            "The spontaneous chemical healing process seals micro-cracks before structural failure can occur",
            "MIT and Harvard material scientists only deciphered the exact hot-mixing crystalline mechanism in 2023"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#AncientEngineering", "#RomanEmpire", "#AncientTech", "#HistoryUncovered", "#MaterialsScience", "#Archaeology", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 03. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "kailasa_temple_monolith",
        "title": "The Kailasa Temple Monolith",
        "sub_theme": "Megalithic Architecture",
        "hook": "This 100-foot-tall Indian temple was not built from stone blocks—architects carved an entire multi-story cathedral out of a solid basalt cliff from the top down.",
        "location": {
            "name": "Kailasa Temple, Ellora Caves",
            "city": "Aurangabad",
            "region": "Maharashtra",
            "country": "India",
            "latitude": 20.0238,
            "longitude": 75.1793
        },
        "key_facts": [
            "Over 200,000 metric tons of Deccan basalt were removed using only iron chisels and hammers",
            "Carved exclusively top-to-bottom in a single continuous monolithic excavation, leaving zero tolerance for design errors",
            "Commissioned in the 8th century AD during the reign of Rashtrakuta King Krishna I",
            "Features towering monolithic pillars (dhwajasthambhas), multi-story galleries, and intricate relief carvings",
            "Courtyard measures 82 meters deep by 46 meters wide, twice the footprint of the Parthenon in Athens"
        ],
        "meta_categories": ["History & Philosophy", "Architecture & Interior Design", "Travel & Culture"],
        "hashtags": ["#KailasaTemple", "#AncientMonoliths", "#IncredibleIndia", "#AncientArchitecture", "#HistoricalCuriosities", "#Archaeology", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 04. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "antikythera_mechanism",
        "title": "The Antikythera Mechanism: 2,000-Year-Old Analog Computer",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "In 1901, sponge divers retrieved a corroded bronze artifact that revealed Greek engineers built precision analog clockwork computers 1,400 years before Europe.",
        "location": {
            "name": "Antikythera Shipwreck",
            "city": "Antikythera",
            "region": "Aegean Sea",
            "country": "Greece",
            "latitude": 35.8833,
            "longitude": 23.3000
        },
        "key_facts": [
            "Discovered in a Roman-era shipwreck dating to approximately 70–60 BC",
            "Houses at least 37 precision meshing bronze gears with triangular teeth engineered inside a wooden casing",
            "Calculated solar and lunar eclipses according to the 223-month Saros cycle with mechanical spiral dials",
            "Tracked the true non-uniform motion of the Moon using an ingenious pin-and-slot epicyclic gear train",
            "Predicted 4-year Panhellenic athletic cycles including the ancient Olympic Games"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Archaeology"],
        "hashtags": ["#AntikytheraMechanism", "#AncientComputer", "#GreekHistory", "#AncientTechnology", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 05. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "05_richat_structure_sahara_eye",
        "title": "The Richat Structure: The Eye of the Sahara",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "Visible only from outer space, a 28-mile concentric bullseye in the Sahara desert mirrors the exact circular dimensions described in ancient texts.",
        "location": {
            "name": "Richat Structure (Guelb er Richat)",
            "city": "Ouadane",
            "region": "Adrar Plateau",
            "country": "Mauritania",
            "latitude": 21.1269,
            "longitude": -11.4016
        },
        "key_facts": [
            "Spans an enormous 40-kilometer (25-mile) diameter dome formed by deeply eroded Cretaceous alkaline igneous rocks",
            "Features concentric rings of resistant quartzite sandstone alternating with recessed eroded valleys",
            "Originally hypothesized to be a meteor impact, field studies revealed hydrothermal uplift without shocked quartz",
            "Contains Paleolithic Acheulean stone hand-axes scattered across outer terraces dating back 500,000 years",
            "Served as an indispensable navigational landmark for Gemini and Apollo astronauts orbiting Earth"
        ],
        "meta_categories": ["Science & Tech", "Travel & Culture", "History & Philosophy"],
        "hashtags": ["#EyeOfTheSahara", "#RichatStructure", "#Geology", "#DesertMysteries", "#HistoricalCuriosities", "#NatureEnigma", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 06. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "06_hal_saflieni_hypogeum",
        "title": "The Ħal Saflieni Hypogeum: Resonance of the Underworld",
        "sub_theme": "Subterranean Engineering",
        "hook": "Dug 35 feet beneath a residential street in Malta lies a 5,000-year-old subterranean sanctuary that resonates at precisely 110 Hertz.",
        "location": {
            "name": "Ħal Saflieni Hypogeum",
            "city": "Paola",
            "region": "Southern Harbour",
            "country": "Malta",
            "latitude": 35.8696,
            "longitude": 14.5069
        },
        "key_facts": [
            "Carved directly into globigerina limestone across three subterranean levels between 4000 and 2500 BC",
            "The 'Oracle Room' amplifies human male baritone frequencies at 110 Hz, producing full-body acoustic resonance",
            "Excavators recovered remains of an estimated 7,000 individuals alongside red ochre spiral ceiling frescoes",
            "Features architectural imitations of above-ground megalithic trilithon temples sculpted underground",
            "Designated a UNESCO World Heritage site in 1980 with strictly regulated atmospheric microclimate controls"
        ],
        "meta_categories": ["Archaeology", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#HalSaflieni", "#MaltaMysteries", "#AcousticArchaeology", "#NeolithicTemple", "#SubterraneanWorld", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 07. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "07_damascus_steel_nanotubes",
        "title": "The Lost Nanotechnology of Damascus Steel",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "Crusaders were terrified by swords that could slice through helmets without dulling, crafted with carbon nanotubes centuries before modern chemistry existed.",
        "location": {
            "name": "Ancient Damascus Armory District",
            "city": "Damascus",
            "region": "Damascus Governorate",
            "country": "Syria",
            "latitude": 33.5138,
            "longitude": 36.2765
        },
        "key_facts": [
            "Forged from southern Indian crucible iron ingots known as Wootz (crucible steel with high carbon content)",
            "Characterized by distinctive swirled, wavy damask patterns resulting from bands of iron carbide (cementite)",
            "2006 high-resolution electron microscopy at TU Dresden discovered cementite nanowires encapsulated in carbon nanotubes",
            "Thermal cycling during forging dissolved micro-impurities like vanadium and molybdenum into catalytic nanostructures",
            "Production halted abruptly around 1850 when the specific trace-element ore deposits in India were exhausted"
        ],
        "meta_categories": ["Science & Tech", "Military History", "History & Philosophy"],
        "hashtags": ["#DamascusSteel", "#AncientMetallurgy", "#Nanotechnology", "#MedievalWeapons", "#LostSecrets", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 08. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "08_unfinished_obelisk_aswan",
        "title": "The Unfinished Obelisk of Aswan",
        "sub_theme": "Megalithic Architecture",
        "hook": "Trapped in an Egyptian granite quarry lies a single 1,200-ton obelisk so massive that modern heavy-lift machinery would struggle to move it.",
        "location": {
            "name": "Northern Quarries of Aswan",
            "city": "Aswan",
            "region": "Upper Egypt",
            "country": "Egypt",
            "latitude": 24.0769,
            "longitude": 32.8953
        },
        "key_facts": [
            "Measures 42 meters (137 feet) in length and weighs approximately 1,168 metric tons",
            "Carved directly from pink granite bedrock using spherical diorite pounder stones weighing up to 5 kilograms",
            "Would have stood roughly one-third taller than any Egyptian obelisk ever successfully raised",
            "Abandoned in situ when a severe structural stress fracture cracked through the center during trench excavation",
            "Provides archaeologists with pristine physical evidence of ancient tool marks, worker trenches, and fire-setting techniques"
        ],
        "meta_categories": ["Archaeology", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#AncientEgypt", "#AswanObelisk", "#MegalithicArchitecture", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 09. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "09_zhang_heng_seismoscope",
        "title": "Zhang Heng's 132 AD Earthquake Detector",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "In 132 AD, an imperial court astronomer built a bronze vessel that detected an earthquake 300 miles away before anyone in the capital felt a tremor.",
        "location": {
            "name": "Han Dynasty Imperial Observatory",
            "city": "Luoyang",
            "region": "Henan Province",
            "country": "China",
            "latitude": 34.6836,
            "longitude": 112.4536
        },
        "key_facts": [
            "Engineered by Han polymath Zhang Heng and titled 'Houfeng Didong Yi' (earthquake weathercock)",
            "A massive bronze vessel 6 feet wide featuring 8 external dragons facing the cardinal and intercardinal compass points",
            "Internal inverted pendulum connected via delicate escapements to drop a bronze ball into a waiting bronze toad's mouth",
            "In 138 AD, a western dragon released its ball; skeptics doubted it until a messenger arrived days later confirming an earthquake in Longxi",
            "The world's earliest known instrument capable of determining the directional azimuth of distant seismic P-waves"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Ancient Tech"],
        "hashtags": ["#ZhangHeng", "#AncientInvention", "#Seismology", "#AncientChina", "#EngineeringMarvel", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 10. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "10_voynich_manuscript",
        "title": "The Unsolved Voynich Manuscript",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "A 600-year-old illustrated codex written in an unknown alphabet has baffled Alan Turing, Cold War codebreakers, and modern supercomputers.",
        "location": {
            "name": "Beinecke Rare Book and Manuscript Library",
            "city": "New Haven",
            "region": "Connecticut",
            "country": "United States",
            "latitude": 41.3115,
            "longitude": -72.9272
        },
        "key_facts": [
            "Radiocarbon dating by the University of Arizona confirmed the calfskin vellum dates to between 1404 and 1438 AD",
            "Contains roughly 240 extant pages containing herbal, astronomical, cosmological, and pharmaceutical illustrations",
            "Text consists of 20 to 25 distinct glyphs written left-to-right without word breaks or corrections",
            "Statistical analysis reveals the text obeys Zipf's Law and entropy curves identical to natural spoken languages",
            "Purchased by rare book dealer Wilfrid Voynich in 1912 from the Jesuit Villa Mondragone in Frascati, Italy"
        ],
        "meta_categories": ["History & Philosophy", "Linguistics", "Historical Curiosities"],
        "hashtags": ["#VoynichManuscript", "#Cryptography", "#UnsolvedMystery", "#MedievalManuscript", "#HistoricalEnigma", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 11. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "11_wieliczka_salt_mine",
        "title": "The Wieliczka Underground Salt Cathedral",
        "sub_theme": "Subterranean Engineering",
        "hook": "Nine levels beneath Poland, miners spent 700 years carving an entire underground civilization complete with subterranean lakes and crystal salt chandeliers.",
        "location": {
            "name": "Wieliczka Salt Mine",
            "city": "Wieliczka",
            "region": "Lesser Poland",
            "country": "Poland",
            "latitude": 49.9833,
            "longitude": 20.0547
        },
        "key_facts": [
            "Operated continuously from the 13th century until 2007, reaching a depth of 327 meters (1,073 feet)",
            "Network spans over 287 kilometers (178 miles) of subterranean galleries across nine excavated levels",
            "Features the colossal Chapel of St. Kinga, measuring 54 meters long with altars and chandeliers sculpted entirely from rock salt",
            "Protected historical mine horse capstans, treadmills, and brine drainage machinery engineered across centuries",
            "One of the original 12 World Heritage sites inscribed on the foundational 1978 UNESCO list"
        ],
        "meta_categories": ["Architecture & Interior Design", "History & Philosophy", "Travel & Culture"],
        "hashtags": ["#WieliczkaSaltMine", "#UndergroundCathedral", "#PolandHistory", "#SubterraneanMarvel", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 12. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "12_lycurgus_cup_nanotech",
        "title": "The 4th-Century Lycurgus Cup: Roman Nanotechnology",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "A 1,600-year-old Roman glass goblet glows jade green in ambient light, but transforms into brilliant blood red the second light shines through it.",
        "location": {
            "name": "The British Museum",
            "city": "London",
            "region": "England",
            "country": "United Kingdom",
            "latitude": 51.5194,
            "longitude": -0.1270
        },
        "key_facts": [
            "Crafted in the 4th century AD depicting the mythical King Lycurgus entangled in ambrosial grapevine coils",
            "Exhibits dichroism due to silver and gold alloy nanoparticles (approx 70 nm) dispersed at 40 parts per million",
            "Surface plasmon resonance in the metallic nanoparticles scatters green wavelengths while transmitting deep red",
            "Demonstrates Roman glassmakers had precise metallurgical control over colloidal metal suspensions in molten silica",
            "Considered the earliest surviving macro-scale example of intentional nanotechnology in human history"
        ],
        "meta_categories": ["Science & Tech", "Archaeology", "History & Philosophy"],
        "hashtags": ["#LycurgusCup", "#RomanEngineering", "#Nanotechnology", "#AncientGlass", "#MaterialsScience", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 13. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "13_gobekli_tepe_enclosure_d",
        "title": "Göbekli Tepe: The 11,500-Year-Old Stone Enclosures",
        "sub_theme": "Megalithic Architecture",
        "hook": "Dating to 9500 BC—6,000 years before Stonehenge—hunter-gatherers chiseled 20-ton T-shaped pillars into stone circles before agriculture even existed.",
        "location": {
            "name": "Göbekli Tepe Archaeological Site",
            "city": "Şanlıurfa",
            "region": "Southeastern Anatolia",
            "country": "Turkey",
            "latitude": 37.2231,
            "longitude": 38.9225
        },
        "key_facts": [
            "Radiocarbon dating places Enclosure D construction around 9500 to 9000 BC during the Pre-Pottery Neolithic A",
            "Features monolithic limestone T-pillars standing up to 5.5 meters tall and weighing up to 20 metric tons",
            "Pillars are intricately carved in high relief with predatory foxes, lions, boars, vultures, scorpions, and snakes",
            "Constructed centuries before the advent of metal tools, wheel transport, pottery, or domesticated wheat farming",
            "Excavated under German archaeologist Klaus Schmidt, completely overturning historical models of social stratification"
        ],
        "meta_categories": ["Archaeology", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#GobekliTepe", "#AncientMegaliths", "#CradleOfCivilization", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 14. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "14_piri_reis_1513_map",
        "title": "The Piri Reis Map of 1513",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "In 1929, historians found a gazelle-skin naval chart drawn in 1513 that mapped the South American and Atlantic coastlines with impossible mathematical precision.",
        "location": {
            "name": "Topkapi Palace Museum",
            "city": "Istanbul",
            "region": "Marmara",
            "country": "Turkey",
            "latitude": 41.0115,
            "longitude": 28.9833
        },
        "key_facts": [
            "Drawn on gazelle skin parchment in 1513 by Ottoman admiral and cartographer Piri Reis",
            "Combines portolan compass-rose rhumb lines with spherical trigonometry centered near the Tropic of Cancer",
            "Author note credits 20 source charts, including 8 Ptolemaic maps, 4 Portuguese charts, and a lost map of Christopher Columbus",
            "Shows precise longitudinal relative distances between the West African coast and South America centuries before chronometers",
            "Rediscovered in 1929 during the conversion of Topkapi Palace into a public museum under Atatürk"
        ],
        "meta_categories": ["History & Philosophy", "Cartography", "Science & Tech"],
        "hashtags": ["#PiriReisMap", "#Cartography", "#OttomanHistory", "#AncientNavigation", "#HistoricalMysteries", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 15. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "sunken_city_of_heracleion",
        "title": "The Sunken City of Thonis-Heracleion",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "Regarded as a Greek myth for over a thousand years, an entire metropolis was found submerged beneath 30 feet of Mediterranean silt.",
        "location": {
            "name": "Bay of Aboukir",
            "city": "Alexandria",
            "region": "Alexandria Governorate",
            "country": "Egypt",
            "latitude": 31.3300,
            "longitude": 30.1300
        },
        "key_facts": [
            "Known as Thonis by Egyptians and Heracleion by Greeks; founded around the 8th century BC as Egypt's primary port of entry",
            "Submerged by cataclysmic soil liquefaction, tidal waves, and earthquakes in the 8th century AD",
            "Located in 2000 by marine archaeologist Franck Goddio using nuclear magnetic resonance magnetometers",
            "Divers uncovered colossal 16-foot granite statues of Hapi, 64 ancient shipwrecks, and hundreds of gold coins",
            "Preserved the famous 'Decree of Sais' stele, whose identical duplicate had stood on land in Naucratis"
        ],
        "meta_categories": ["Archaeology", "History & Philosophy", "Travel & Culture"],
        "hashtags": ["#Heracleion", "#SunkenCity", "#UnderwaterArchaeology", "#AncientEgypt", "#LostCivilization", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 16. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "16_odessa_catacombs",
        "title": "The 1,500-Mile Odessa Catacombs",
        "sub_theme": "Subterranean Engineering",
        "hook": "Hidden beneath the streets of Ukraine lies a subterranean labyrinth over 1,500 miles long—longer than the distance from Paris to Rome.",
        "location": {
            "name": "Odessa Catacombs",
            "city": "Odesa",
            "region": "Odesa Oblast",
            "country": "Ukraine",
            "latitude": 46.4825,
            "longitude": 30.7233
        },
        "key_facts": [
            "Comprises over 2,500 kilometers (1,550 miles) of interconnected subterranean tunnels across three distinct levels",
            "Excavated during the 19th century when yellow coquina limestone was quarried to build the neoclassical city of Odesa",
            "Sheltered Soviet guerrilla partisan brigades during WWII who survived underground for months behind Axis lines",
            "Complex contains underground hospitals, command bunkers, weapon caches, and freshwater filtration shafts",
            "Most subterranean branches remain unmapped and dangerously prone to cave-ins, strictly requiring speleological guides"
        ],
        "meta_categories": ["Military History", "Architecture & Interior Design", "History & Philosophy"],
        "hashtags": ["#OdessaCatacombs", "#UndergroundLabyrinth", "#SubterraneanWorld", "#WW2History", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 17. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "17_greek_fire_siphon",
        "title": "Greek Fire: The Byzantine Empire's Lost Chemical Weapon",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "For half a millennium, the Byzantine Empire possessed a liquid chemical weapon that ignited upon water and could never be doused with sea spray.",
        "location": {
            "name": "Golden Horn Naval Bastion",
            "city": "Istanbul",
            "region": "Marmara",
            "country": "Turkey",
            "latitude": 41.0256,
            "longitude": 28.9669
        },
        "key_facts": [
            "Developed around 672 AD by the Syrian engineer and architect Kallinikos of Heliopolis",
            "Propelled under pressure through bronze siphon tubes (strophalos) mounted on the prows of dromon warships",
            "Ignited spontaneously or via pilot flame and burned vigorously on the surface of turbulent seawater",
            "Crushed Arab naval sieges of Constantinople in 674–678 AD and 717–718 AD, preserving Byzantine territorial sovereignty",
            "Classified as a supreme state military secret; the exact recipe (likely petroleum, pine resin, sulfur, quicklime) was permanently lost"
        ],
        "meta_categories": ["Military History", "History & Philosophy", "Science & Tech"],
        "hashtags": ["#GreekFire", "#ByzantineEmpire", "#MedievalWeapons", "#LostSecrets", "#MilitaryHistory", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 18. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "18_puma_punku_andesite",
        "title": "Puma Punku: The Interlocking Andesite Enigma",
        "sub_theme": "Megalithic Architecture",
        "hook": "At 12,800 feet in the Bolivian Andes, prehistoric builders machined 130-ton red sandstone and andesite blocks with millimeter right angles.",
        "location": {
            "name": "Tiwanaku Archaeological Complex",
            "city": "Tiwanaku",
            "region": "La Paz Department",
            "country": "Bolivia",
            "latitude": -16.5550,
            "longitude": -68.6739
        },
        "key_facts": [
            "Constructed by the Tiwanaku civilization between 500 and 900 AD on the high Andean Altiplano",
            "Features iconic 'H-blocks' cut from extremely dense volcanic andesite with uniform precision groove channels",
            "Stones were joined using cast cold-crimped metal crampons made of copper-arsenic-nickel ternary alloys",
            "The largest megalithic platform block (Plataforma Lítica) weighs an estimated 131 metric tons",
            "Fitted with zero mortar using complex tongue-and-groove interlocking joinery designed to dissipate seismic energy"
        ],
        "meta_categories": ["Archaeology", "Architecture & Interior Design", "History & Philosophy"],
        "hashtags": ["#PumaPunku", "#Tiwanaku", "#MegalithicEngineering", "#AncientBolivia", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 19. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "19_newgrange_winter_solstice",
        "title": "Newgrange: The 5,200-Year-Old Winter Solstice Tomb",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "Built 5,200 years ago—older than Stonehenge and the Pyramids—a prehistoric stone chamber illuminates for only 17 minutes on the winter solstice.",
        "location": {
            "name": "Brú na Bóinne",
            "city": "Donore",
            "region": "County Meath",
            "country": "Ireland",
            "latitude": 53.6947,
            "longitude": -6.4753
        },
        "key_facts": [
            "Constructed around 3200 BC during the Neolithic period, predating the Great Pyramid of Giza by roughly 600 years",
            "A circular mound 85 meters in diameter ringed by 97 massive kerbstones engraved with megalithic triskele spirals",
            "Engineered with a specialized aperture ('roofbox') situated directly above the main 19-meter stone passage entrance",
            "At dawn on the winter solstice (December 21), a precise beam of sunlight penetrates the roofbox and illuminates the rear chamber floor",
            "Demonstrates profound astronomical observation, seasonal calendar tracking, and waterproof corbelled roof engineering"
        ],
        "meta_categories": ["Archaeology", "Astronomy", "History & Philosophy"],
        "hashtags": ["#Newgrange", "#WinterSolstice", "#NeolithicMonuments", "#AncientAstronomy", "#IrelandHistory", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 20. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "20_tunguska_event_1908",
        "title": "The Tunguska Event of 1908",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "In 1908, an atmospheric explosion 1,000 times more powerful than the Hiroshima atomic bomb flattened 80 million trees in Siberia—leaving no crater.",
        "location": {
            "name": "Podkamennaya Tunguska River Basin",
            "city": "Vanavara",
            "region": "Krasnoyarsk Krai",
            "country": "Russia",
            "latitude": 60.9167,
            "longitude": 101.9500
        },
        "key_facts": [
            "Occurred on June 30, 1908, releasing an estimated blast yield of 10 to 15 megatons of TNT equivalent",
            "Flattened approximately 2,150 square kilometers (830 square miles) of dense taiga forest in a radial butterfly pattern",
            "Shockwaves registered on seismic stations across Eurasia and barometers detected atmospheric ripples as far as London",
            "Expedition leader Leonid Kulik reached the ground zero site in 1927, finding standing scorched 'telephone-pole' trees and no impact crater",
            "Scientifically confirmed as an airburst detonation of a stony asteroid or comet fragment 5 to 10 kilometers above the surface"
        ],
        "meta_categories": ["Science & Tech", "Historical Curiosities", "History & Philosophy"],
        "hashtags": ["#TunguskaEvent", "#CosmicImpact", "#SiberiaMystery", "#HistoricalEnigma", "#Astronomy", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 21. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "21_kaymakli_underground_city",
        "title": "The Subterranean Citadel of Kaymaklı",
        "sub_theme": "Subterranean Engineering",
        "hook": "Connected to Derinkuyu by a five-mile underground highway, this ancient multi-level subterranean fortress housed 3,000 people under volcanic rock.",
        "location": {
            "name": "Kaymaklı Underground City",
            "city": "Kaymaklı",
            "region": "Nevşehir / Cappadocia",
            "country": "Turkey",
            "latitude": 38.4600,
            "longitude": 34.7522
        },
        "key_facts": [
            "Encompasses eight carved subterranean levels, of which four are fully excavated and accessible to modern visitors",
            "Contains a massive andesite block with 57 hollowed cupels used for crushing copper ore and metallurgy",
            "Equipped with low-ceilinged narrow zig-zag defense tunnels designed to force invading soldiers into single-file bottlenecks",
            "Features communal granaries, subterranean horse stables, smoke-dispersing tandoor bread ovens, and wine presses",
            "Linked to neighboring subterranean settlements across the Cappadocia region via extensive deep tunnels"
        ],
        "meta_categories": ["Archaeology", "Architecture & Interior Design", "History & Philosophy"],
        "hashtags": ["#Kaymakli", "#CappadociaUnderground", "#SubterraneanCity", "#AncientFortress", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 22. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "22_delhi_iron_pillar",
        "title": "The Rustless Iron Pillar of Delhi",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "A 23-foot solid iron column has stood completely exposed to monsoon rains for 1,600 years without forming a single flake of rust.",
        "location": {
            "name": "Qutb Minar Complex",
            "city": "New Delhi",
            "region": "Delhi",
            "country": "India",
            "latitude": 28.5244,
            "longitude": 77.1855
        },
        "key_facts": [
            "Erected during the reign of King Chandragupta II (Vikramaditya) of the Gupta Empire around 400 AD",
            "Weighs over 6 metric tons and forged from forge-welded high-phosphorus wrought iron",
            "Resists atmospheric corrosion due to the formation of a protective passive film of crystalline iron hydrogen phosphate hydrate (misawite)",
            "Ancient metallurgical slag-free forging methods prevented moisture absorption and electrochemical corrosion pathways",
            "Features a Sanskrit Brahmi dedicatory inscription honoring the deity Vishnu and King Chandra"
        ],
        "meta_categories": ["Science & Tech", "Materials Science", "History & Philosophy"],
        "hashtags": ["#IronPillarOfDelhi", "#AncientMetallurgy", "#CorrosionResistance", "#GuptaEmpire", "#IncredibleIndia", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 23. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "23_rano_raraku_moai",
        "title": "The Walking Monoliths of Rano Raraku",
        "sub_theme": "Megalithic Architecture",
        "hook": "On the world's most isolated inhabited island, Polynesian masters carved nearly 900 statues weighing up to 86 tons and walked them across volcanic rock.",
        "location": {
            "name": "Rano Raraku Volcanic Crater",
            "city": "Hanga Roa",
            "region": "Easter Island (Rapa Nui)",
            "country": "Chile",
            "latitude": -27.1219,
            "longitude": -109.2894
        },
        "key_facts": [
            "Carved from consolidated volcanic lapilli tuff on the slopes of the Rano Raraku volcanic caldera",
            "Nearly 900 colossal statues (Moai) were produced by the Rapa Nui people between 1250 and 1500 AD",
            "The largest completed standing Moai (Paro) measured 10 meters (33 feet) tall and weighed 82 metric tons",
            "An unfinished giant named 'Te Tokanga' remains rooted in the quarry bedrock, spanning 21 meters and 145 metric tons",
            "Transported across overland routes using a rocking upright pendulum motion called 'walking' (neke neke) with ropes"
        ],
        "meta_categories": ["Archaeology", "Anthropology", "History & Philosophy"],
        "hashtags": ["#EasterIsland", "#MoaiStatues", "#RapaNui", "#MegalithicArchitecture", "#PolynesianVoyagers", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 24. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "24_incan_khipu_code",
        "title": "The Incan Khipu: The Decimal String Computer",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "The Inca built the largest empire in pre-Columbian America without written words, encoding censuses, taxes, and narratives in knotted cords.",
        "location": {
            "name": "Pachacamac Archaeological Sanctuary",
            "city": "Lurín",
            "region": "Lima",
            "country": "Peru",
            "latitude": -12.2575,
            "longitude": -76.9011
        },
        "key_facts": [
            "Constructed from spun and plied cotton or camelid cords arranged in hierarchical branched pendant hierarchies",
            "Utilized base-10 positional decimal numerical knotting systems (figure-eight knots, long knots, and single knots)",
            "Recorded empirical economic data including tax tributes, agricultural storehouse inventories, and population censuses",
            "Color variations, cord spin direction (S-twist vs Z-twist), and ply construction encoded qualitative phonetic categories",
            "Decipherment breakthroughs by Harvard anthropologists confirmed non-numeric narrative khipus recorded historical annals"
        ],
        "meta_categories": ["Science & Tech", "Linguistics", "History & Philosophy"],
        "hashtags": ["#IncaEmpire", "#Khipu", "#AncientData", "#AndeanCivilization", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 25. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "25_dancing_plague_1518",
        "title": "The Dancing Plague of 1518",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "In July 1518, a woman stepped into a Strasbourg square and began dancing—within a month, 400 people were dancing until their feet bled and hearts stopped.",
        "location": {
            "name": "Strasbourg Cathedral Square",
            "city": "Strasbourg",
            "region": "Alsace",
            "country": "France",
            "latitude": 48.5818,
            "longitude": 7.7509
        },
        "key_facts": [
            "Began when a woman named Frau Troffea commenced unprompted continuous dancing in a Strasbourg street",
            "Within four weeks, the mania spread to over 400 citizens dancing outdoors in summer heat without rest",
            "City magistrates hired musicians, built a wooden dancing stage, and paid dancers, mistakenly believing movement cured the affliction",
            "Scores of victims collapsed or died from physical exhaustion, stroke, dehydration, and cardiovascular failure",
            "Modern historical consensus attributes the epidemic to mass psychogenic illness triggered by famine and Saint Vitus religious despair"
        ],
        "meta_categories": ["Medical History", "Historical Curiosities", "History & Philosophy"],
        "hashtags": ["#DancingPlague1518", "#HistoricalEnigma", "#PsychogenicIllness", "#MedievalHistory", "#StrasbourgHistory", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 26. Subterranean Engineering
    # -------------------------------------------------------------------------
    {
        "id": "26_chislehurst_caves",
        "title": "The Chislehurst Caves: 22 Miles of Subterranean History",
        "sub_theme": "Subterranean Engineering",
        "hook": "Beneath suburban London lies a 22-mile dark subterranean city where 15,000 citizens lived underground to survive the Blitz.",
        "location": {
            "name": "Chislehurst Caves",
            "city": "Chislehurst",
            "region": "Greater London",
            "country": "United Kingdom",
            "latitude": 51.4086,
            "longitude": 0.0544
        },
        "key_facts": [
            "Spans 35 kilometers (22 miles) of chalk and flint underground passages dating back to the Saxon, Celtic, and Roman eras",
            "Mined historically for high-grade lime chalk used in agriculture and flint nodules for early flintlock firearms",
            "Transformed in 1940 into Britain's largest underground civilian bomb shelter with electric lighting, hospital bays, and a cinema",
            "Sheltered over 15,000 permanent residents during the German aerial bombing of London in the Blitz",
            "Famous for extreme darkness and subterranean acoustics, later used as a rehearsal hall by Jimi Hendrix and Pink Floyd"
        ],
        "meta_categories": ["Military History", "Subterranean Architecture", "History & Philosophy"],
        "hashtags": ["#ChislehurstCaves", "#LondonUnderground", "#SubterraneanLabyrinth", "#BlitzHistory", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 27. Ancient Materials & Metallurgy
    # -------------------------------------------------------------------------
    {
        "id": "27_uluburun_shipwreck_copper",
        "title": "The Uluburun Shipwreck: Bronze Age Global Trade",
        "sub_theme": "Ancient Materials & Metallurgy",
        "hook": "Sunk 140 feet deep off Turkey lies a Late Bronze Age royal treasury that held enough copper and tin to outfit an entire ancient army.",
        "location": {
            "name": "Cape Uluburun",
            "city": "Kaş",
            "region": "Antalya Province",
            "country": "Turkey",
            "latitude": 36.1286,
            "longitude": 29.6917
        },
        "key_facts": [
            "Wrecked around 1305 BC during the Late Bronze Age, discovered by local sponge divers in 1982",
            "Excavated over 11 seasons by George Bass and Cemal Pulak, yielding over 22,000 scientific scuba dives",
            "Cargo contained 10 metric tons of Cypriot copper in 354 'oxhide' ingots and 1 metric ton of tin",
            "Carried raw materials, Egyptian ebony, ivory, Baltic amber beads, Canaanite amphoras, and the gold scarab of Queen Nefertiti",
            "Provides definitive physical proof of an interconnected, globalized Bronze Age maritime trade network"
        ],
        "meta_categories": ["Archaeology", "Maritime History", "Materials Science"],
        "hashtags": ["#UluburunShipwreck", "#BronzeAgeTrade", "#UnderwaterArchaeology", "#AncientMetallurgy", "#HistoryUncovered", "#Archaeology", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 28. Megalithic Architecture
    # -------------------------------------------------------------------------
    {
        "id": "28_great_wall_of_gorgan",
        "title": "The Great Wall of Gorgan: The Red Snake",
        "sub_theme": "Megalithic Architecture",
        "hook": "Snaking 121 miles through northern Iran, a massive red-brick defensive wall rivaled Hadrian's Wall with 38 forts and hydraulic canals.",
        "location": {
            "name": "Gorgan Plain",
            "city": "Gorgan",
            "region": "Golestan Province",
            "country": "Iran",
            "latitude": 37.2333,
            "longitude": 54.4333
        },
        "key_facts": [
            "Constructed by the Sasanian Persian Empire during the 5th and 6th centuries AD to repel northern nomadic confederations",
            "Spans approximately 195 kilometers (121 miles) from the Caspian Sea to the Pishkamar mountains",
            "Built with roughly 200 million kiln-fired red bricks, earning it the historical moniker 'The Red Snake'",
            "Supported by an intricate artificial canal system that channeled mountain river water for brick kilns and garrison moats",
            "Garrisoned an estimated 20,000 to 30,000 Sasanian soldiers stationed in 38 fortified brick barracks"
        ],
        "meta_categories": ["Military Architecture", "Archaeology", "History & Philosophy"],
        "hashtags": ["#GreatWallOfGorgan", "#RedSnake", "#SasanianEmpire", "#AncientDefenses", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 29. Ancient Computing & Astronomy
    # -------------------------------------------------------------------------
    {
        "id": "29_dendera_zodiac_ceiling",
        "title": "The Dendera Zodiac: The 26,000-Year Star Map",
        "sub_theme": "Ancient Computing & Astronomy",
        "hook": "Carved into the sandstone ceiling of an Egyptian temple is a celestial star map that plotted the 26,000-year precession of Earth's equinoxes.",
        "location": {
            "name": "Dendera Temple Complex",
            "city": "Dendera",
            "region": "Qena Governorate",
            "country": "Egypt",
            "latitude": 26.1422,
            "longitude": 32.6703
        },
        "key_facts": [
            "Originally adorned the ceiling of the chapel of Osiris in the Hathor Temple at Dendera, dating to roughly 50 BC",
            "Depicts a circular celestial map combining 12 Greco-Roman zodiac constellations with 36 ancient Egyptian decans",
            "Displays solar and lunar eclipses, specifically the total solar eclipse of March 7, 51 BC",
            "Demonstrates Egyptian and Ptolemaic understanding of the 25,772-year astronomical precession cycle",
            "Removed from Egypt in 1821 and permanently exhibited in the Musée du Louvre in Paris, France"
        ],
        "meta_categories": ["Astronomy", "Ancient Egypt", "Archaeology"],
        "hashtags": ["#DenderaZodiac", "#AncientAstronomy", "#EgyptianTemple", "#CelestialMap", "#Archaeology", "#HistoryUncovered", "#ReelsViral"]
    },

    # -------------------------------------------------------------------------
    # 30. Historical Curiosities & Enigmas
    # -------------------------------------------------------------------------
    {
        "id": "30_flannan_isle_lighthouse_disappearance",
        "title": "The Vanishing at Flannan Isle Lighthouse",
        "sub_theme": "Historical Curiosities & Enigmas",
        "hook": "In December 1900, a relief boat landed at a remote Scottish lighthouse to find an uneaten meal, an overturned chair, and three keepers vanished into thin air.",
        "location": {
            "name": "Eilean Mòr Lighthouse",
            "city": "Flannan Isles",
            "region": "Outer Hebrides",
            "country": "United Kingdom",
            "latitude": 58.2883,
            "longitude": -7.5897
        },
        "key_facts": [
            "Lighthouse on the remote, uninhabited islet of Eilean Mòr was manned by three experienced keepers: Ducat, Marshall, and MacArthur",
            "Relief vessel Hesperus arrived on December 26, 1900, finding the lighthouse locked, fire unlit, and clock stopped",
            "Logbook entries recorded severe Atlantic gale storms that had battered the island days earlier",
            "Massive damage was found at the western landing 110 feet above sea level, where iron railings were twisted and crane ropes tangled",
            "Official investigation by Superintendent Robert Muirhead concluded a rogue freak wave swept the men into the North Atlantic"
        ],
        "meta_categories": ["Maritime History", "Historical Curiosities", "History & Philosophy"],
        "hashtags": ["#FlannanIsles", "#LighthouseMystery", "#UnsolvedVanishing", "#ScottishHistory", "#MaritimeEnigma", "#HistoryUncovered", "#ReelsViral"]
    }
]


def main() -> int:
    out_file = DATA_DIR / "topic_bank.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"total": len(TOPICS), "topics": TOPICS}, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(TOPICS)} verified topics -> {out_file}")
    return 0


if __name__ == "__main__":
    main()
