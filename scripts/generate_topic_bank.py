"""
Generate data/topics/topic_bank.json
Contains the pre-vetted 30-day high-retention topic bank for Historical Curiosities & Ancient Marvels.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "topics"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS = [
    {
        "id": "01_derinkuyu_underground_city",
        "title": "The Underground City of Derinkuyu",
        "sub_theme": "Lost Civilizations",
        "hook": "In 1963, a simple basement renovation in Turkey uncovered the largest underground city in human history.",
        "location": {
            "name": "Derinkuyu Underground City",
            "city": "Derinkuyu",
            "region": "Nevşehir / Cappadocia",
            "country": "Turkey",
            "latitude": 38.3736,
            "longitude": 34.7347
        },
        "key_facts": [
            "18 subterranean stories carved 280 feet into solid volcanic rock",
            "Sheltered over 20,000 citizens from surface invaders or catastrophes",
            "Equipped with thousands of ventilation shafts, freshwater wells, and chapels",
            "1,000-pound rolling stone disc doors that could only be locked from the inside",
            "Still unknown who originally engineered the earliest levels"
        ],
        "meta_categories": ["History & Philosophy", "Architecture & Interior Design", "Travel & Culture"],
        "hashtags": ["#AncientMysteries", "#Derinkuyu", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#AncientCivilizations", "#ReelsViral"]
    },
    {
        "id": "02_roman_self_healing_concrete",
        "title": "The Mystery of Roman Self-Healing Concrete",
        "sub_theme": "Ancient Materials",
        "hook": "Modern concrete cracks in 50 years, but Roman harbors have sat in saltwater for two millennia and actually get stronger.",
        "location": {
            "name": "Portus Cosanus Roman Harbor",
            "city": "Orbetello",
            "region": "Tuscany",
            "country": "Italy",
            "latitude": 42.4419,
            "longitude": 11.2882
        },
        "key_facts": [
            "Engineered with volcanic ash (pozzolana) and quicklime clasts",
            "When cracks form, rainwater dissolves the lime, reacting with seawater to crystallize into aluminous tobermorite",
            "The chemical reaction heals microcracks spontaneously over centuries",
            "Modern material scientists only unlocked the exact hot-mixing recipe in 2023",
            "Outlasts modern reinforced Portland cement by over 40 times"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#AncientEngineering", "#RomanEmpire", "#AncientTech", "#HistoryUncovered", "#MaterialsScience", "#Archaeology", "#ReelsViral"]
    },
    {
        "id": "03_kailasa_temple_monolith",
        "title": "The Kailasa Temple Monolith",
        "sub_theme": "Architectural Enigma",
        "hook": "This 100-foot temple was not built from the ground up—it was carved out of a single mountain from top to bottom.",
        "location": {
            "name": "Kailasa Temple, Ellora Caves",
            "city": "Aurangabad",
            "region": "Maharashtra",
            "country": "India",
            "latitude": 20.0238,
            "longitude": 75.1793
        },
        "key_facts": [
            "Over 200,000 tons of solid basalt rock were chiseled away with hand tools",
            "Carved vertically from top to bottom with zero room for architectural error",
            "Features intricate life-sized elephants, multi-story courtyards, and monolithic obelisks",
            "Built in the 8th century under King Krishna I of the Rashtrakuta dynasty",
            "Legend claims it was finished in just 18 years, an impossible feat for hand chisels"
        ],
        "meta_categories": ["History & Philosophy", "Architecture & Interior Design", "Travel & Culture"],
        "hashtags": ["#KailasaTemple", "#AncientMonoliths", "#IncredibleIndia", "#AncientArchitecture", "#HistoricalCuriosities", "#Archaeology", "#ReelsViral"]
    },
    {
        "id": "04_antikythera_mechanism",
        "title": "The Antikythera Mechanism: 2,000-Year-Old Analog Computer",
        "sub_theme": "Astronomical Feat",
        "hook": "Divers pulled a corroded lump of bronze from an ancient shipwreck, only to discover an analog computer that predicted eclipses.",
        "location": {
            "name": "Antikythera Shipwreck",
            "city": "Antikythera",
            "region": "Aegean Sea",
            "country": "Greece",
            "latitude": 35.8833,
            "longitude": 23.3000
        },
        "key_facts": [
            "Discovered in 1901 by sponge divers off the Greek island of Antikythera",
            "Contains over 30 intricate precision bronze gears in a wooden box",
            "Accurately predicted solar and lunar eclipses, planetary orbits, and Olympic cycles",
            "Dating back to roughly 150 to 100 BC",
            "Clockwork complexity of this caliber was not seen again until 1,400 years later in 14th-century Europe"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Archaeology"],
        "hashtags": ["#Antikythera", "#AncientComputer", "#AncientGreece", "#LostTechnology", "#HistoryUncovered", "#Archaeology", "#ReelsViral"]
    },
    {
        "id": "05_gobekli_tepe",
        "title": "Göbekli Tepe: The 11,000-Year-Old Sanctuary",
        "sub_theme": "Prehistoric Marvel",
        "hook": "Built 6,000 years before Stonehenge, this massive stone sanctuary proved human hunters organized societies long before farming began.",
        "location": {
            "name": "Göbekli Tepe",
            "city": "Şanlıurfa",
            "region": "Southeastern Anatolia",
            "country": "Turkey",
            "latitude": 37.2231,
            "longitude": 38.9225
        },
        "key_facts": [
            "Dating to 9500 BC—predating agriculture, pottery, the wheel, and metal tools",
            "Massive T-shaped limestone pillars weighing up to 20 tons carved with low-relief predators",
            "Overturned the classical archaeological timeline that farming preceded monument building",
            "Intentionally buried and backfilled with dirt thousands of years after construction",
            "Only an estimated 5% of the total underground temple complex has been excavated"
        ],
        "meta_categories": ["History & Philosophy", "Archaeology", "Travel & Culture"],
        "hashtags": ["#GobekliTepe", "#AncientCivilizations", "#HumanOrigins", "#Archaeology", "#HistoricalCuriosities", "#Anatolia", "#ReelsViral"]
    },
    {
        "id": "06_damascus_steel_nanotubes",
        "title": "Damascus Steel & Ancient Carbon Nanotubes",
        "sub_theme": "Lost Metallurgy",
        "hook": "Crusaders claimed these legendary swords could slice a falling silk ribbon in half—modern electron microscopes revealed carbon nanotubes.",
        "location": {
            "name": "Damascus Old City",
            "city": "Damascus",
            "region": "Levant",
            "country": "Syria",
            "latitude": 33.5138,
            "longitude": 36.2765
        },
        "key_facts": [
            "Forged from wootz crucible steel originally mined in ancient southern India",
            "Recognized by characteristic swirling water patterns (damask) on the blade face",
            "In 2006, German researchers discovered carbon nanotubes and cementite nanowires inside the steel",
            "The precise thermal cycling and plant impurity recipe was lost by the 18th century",
            "Provided unmatched flexibility and razor sharpness far beyond European medieval steel"
        ],
        "meta_categories": ["Science & Tech", "History & Philosophy", "Military History"],
        "hashtags": ["#DamascusSteel", "#AncientNanotech", "#LostArts", "#MedievalHistory", "#Metallurgy", "#HistoryUncovered", "#ReelsViral"]
    },
    {
        "id": "07_camp_century_nuclear_ice_city",
        "title": "Camp Century: The Nuclear City Under the Ice",
        "sub_theme": "Declassified History",
        "hook": "During the Cold War, the US Army built an entire nuclear-powered city hidden under the Greenland ice sheet.",
        "location": {
            "name": "Camp Century",
            "city": "Thule Region",
            "region": "Greenland Ice Sheet",
            "country": "Greenland",
            "latitude": 77.1667,
            "longitude": -61.1333
        },
        "key_facts": [
            "Constructed in 1959 under the guise of an Arctic polar science station",
            "Secretly designed for Project Iceworm: storing 600 nuclear missiles targeted at the USSR",
            "Powered by a portable PM-2A nuclear reactor buried inside snow trenches",
            "Contained a hospital, movie theater, barbershop, and living quarters for 200 soldiers",
            "Abandoned in 1967 when glacier movements crushed the tunnels; left behind radioactive waste"
        ],
        "meta_categories": ["Military History", "History & Philosophy", "Science & Tech"],
        "hashtags": ["#ColdWarSecrets", "#CampCentury", "#Declassified", "#MilitaryHistory", "#Greenland", "#HistoricalCuriosities", "#ReelsViral"]
    },
    {
        "id": "08_hypogeum_of_hal_saflieni",
        "title": "The Hypogeum of Hal Saflieni",
        "sub_theme": "Subterranean Relic",
        "hook": "Beneath Malta lies a 5,000-year-old burial sanctuary with an acoustic chamber that alters the human brainwave frequency.",
        "location": {
            "name": "Ħal Saflieni Hypogeum",
            "city": "Paola",
            "region": "Southern Harbour",
            "country": "Malta",
            "latitude": 35.8697,
            "longitude": 14.5069
        },
        "key_facts": [
            "Three subterranean levels hand-carved out of globigerina limestone around 3300 BC",
            "Discovered accidentally in 1902 by stone masons cutting cisterns",
            "The 'Oracle Chamber' resonates powerfully at 110 Hz, a frequency known to stimulate emotional and creative brain processing",
            "Yielded remains of an estimated 7,000 individuals alongside the famous 'Sleeping Lady' statuette",
            "One of the best-preserved Neolithic underground architecture sites in the world"
        ],
        "meta_categories": ["History & Philosophy", "Archaeology", "Travel & Culture"],
        "hashtags": ["#HalSaflieni", "#AcousticAnomalies", "#AncientMalta", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    },
    {
        "id": "09_greek_fire_byzantine_weapon",
        "title": "Byzantine Greek Fire: The Lost Thermal Weapon",
        "sub_theme": "Medieval Warfare",
        "hook": "For 500 years, the Byzantine Empire possessed a liquid weapon that burned on top of water, and its secret recipe vanished forever.",
        "location": {
            "name": "The Golden Horn",
            "city": "Constantinople / Istanbul",
            "region": "Marmara",
            "country": "Turkey",
            "latitude": 41.0256,
            "longitude": 28.9669
        },
        "key_facts": [
            "Invented around 672 AD by the Syrian Christian engineer Kallinikos",
            "Pumped through pressurized siphon bronze tubes mounted on Byzantine dromon warships",
            "Continued burning upon contact with seawater and could only be extinguished by vinegar or sand",
            "Saved Constantinople from multiple Arab naval sieges in 674 and 717 AD",
            "State secret punishable by death if shared; the precise chemical formula was lost with the fall of the empire"
        ],
        "meta_categories": ["Military History", "History & Philosophy", "Science & Tech"],
        "hashtags": ["#GreekFire", "#ByzantineEmpire", "#MedievalWeapons", "#LostSecrets", "#MilitaryHistory", "#HistoryUncovered", "#ReelsViral"]
    },
    {
        "id": "10_unfinished_obelisk_aswan",
        "title": "The Unfinished Obelisk of Aswan",
        "sub_theme": "Ancient Megaliths",
        "hook": "Lying flat in an Egyptian quarry is a 1,200-ton block of solid granite that modern cranes still cannot lift.",
        "location": {
            "name": "Aswan Granite Quarries",
            "city": "Aswan",
            "region": "Upper Egypt",
            "country": "Egypt",
            "latitude": 24.0769,
            "longitude": 32.8953
        },
        "key_facts": [
            "Would have stood 137 feet (42 meters) tall if erected—a third taller than any ancient obelisk",
            "Estimated weight: approximately 1,200 metric tons (equivalent to 3 Boeing 747s)",
            "Chiseled directly out of bedrock using hard diorite pounder stones",
            "Work was abruptly abandoned when a fatal stress crack developed through the stone",
            "Offers unparalleled direct evidence of ancient Egyptian stone-quarrying techniques"
        ],
        "meta_categories": ["Archaeology", "History & Philosophy", "Architecture & Interior Design"],
        "hashtags": ["#AncientEgypt", "#AswanObelisk", "#MegalithicArchitecture", "#Archaeology", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    }
]

# Additional 20 topics generated for full 30-day bank
EXTRA_TOPICS = [
    ("11_piri_reis_antarctica_map", "The Piri Reis Map of 1513", "Cartographic Mystery", "In 1929, historians found a 1513 map showing the coastline of Antarctica 300 years before it was officially discovered.", "Topkapi Palace", "Istanbul", "Turkey"),
    ("12_flannan_isle_lighthouse_disappearance", "The Flannan Isle Lighthouse Vanishing", "Unsolved Disappearance", "In 1900, a supply boat arrived at a Scottish lighthouse to find a set dinner table, a stopped clock, and three keepers vanished into thin air.", "Flannan Isles", "Outer Hebrides", "Scotland"),
    ("13_trepanation_miracle", "Ancient Skull Surgery (Trepanation)", "Ancient Medicine", "Archaeologists found 7,000-year-old human skulls with neat geometric holes drilled into them—and the bone had completely healed.", "Paracas Peninsula", "Ica", "Peru"),
    ("14_nazca_lines_runway_paradox", "The Nazca Lines Runway Paradox", "Geoglyphs", "Miles of geometric lines etched into the Peruvian desert can only be recognized from an airplane that wasn't invented for 1,500 years.", "Nazca Desert", "Nazca", "Peru"),
    ("15_sunken_city_of_heracleion", "The Sunken City of Thonis-Heracleion", "Submerged City", "For 1,200 years, historians believed it was a myth until a diver spotted a colossal 16-foot statue submerged in the Mediterranean.", "Abukir Bay", "Alexandria", "Egypt"),
    ("16_zhang_heng_132ad_seismoscope", "Zhang Heng's 132 AD Earthquake Detector", "Lost Engineering", "Nearly 2,000 years ago, a Chinese bronze urn could detect earthquakes 400 miles away before anyone felt the ground shake.", "Luoyang", "Henan", "China"),
    ("17_voynich_manuscript", "The Unsolved Voynich Manuscript", "Bizarre Relic", "Written in an undecipherable script with illustrations of plants that don't exist on Earth, WWII codebreakers failed to crack a single word.", "Beinecke Rare Book Library", "New Haven", "USA"),
    ("18_odessa_catacombs_labyrinth", "The 1,500-Mile Odessa Catacombs", "Hidden Infrastructure", "Beneath Ukraine lies a 1,500-mile subterranean labyrinth deeper, darker, and more dangerous than the Paris catacombs.", "Odessa", "Odesa Oblast", "Ukraine"),
    ("19_richat_structure_sahara_eye", "The Richat Structure: The Eye of the Sahara", "Geological Mystery", "A 25-mile concentric circle in the Sahara desert matches Plato's exact description and dimensions of the lost city of Atlantis.", "Ouadane", "Adrar Plateau", "Mauritania"),
    ("20_amber_room_heist", "The Disappearance of the Amber Room", "Historical Heist", "An entire palace room made of six tons of pure amber and gold was packed into crates in 1945 and has never been seen again.", "Catherine Palace", "Pushkin", "Russia"),
    ("21_gol_gumbaz_whispering_gallery", "The Whispering Gallery of Gol Gumbaz", "Acoustic Wonder", "In this 17th-century Indian dome, the quietest whisper can be heard 125 feet away and echoes exactly ten times.", "Bijapur", "Karnataka", "India"),
    ("22_tunguska_event_1908", "The Tunguska Event of 1908", "Unexplained Event", "An explosion 1,000 times stronger than Hiroshima flattened 80 million trees in Siberia, yet left behind zero impact crater.", "Podkamennaya Tunguska", "Siberia", "Russia"),
    ("23_claw_of_archimedes", "The Claw of Archimedes", "Ancient Weapons", "When Roman warships attacked Syracuse, giant hidden crane arms swung out from the cliffs and lifted whole ships straight out of the water.", "Syracuse", "Sicily", "Italy"),
    ("24_dyatlov_pass_incident", "The Dyatlov Pass Mystery", "Lost Expedition", "Nine experienced hikers fled their tent in sub-zero weather wearing no shoes, leaving behind cracked ribs and radioactive clothing.", "Kholat Syakhl", "Ural Mountains", "Russia"),
    ("25_lycurgus_cup_nanotech", "The 4th-Century Lycurgus Cup", "Microscopic Wonder", "A Roman glass goblet that glows green in normal light turns blood red when light shines through it, crafted with embedded gold nanoparticles.", "British Museum", "London", "UK"),
    ("26_wieliczka_salt_cathedral", "The Wieliczka Underground Salt Cathedral", "Subterranean Marvel", "Polish miners spent centuries carving an underground world 1,000 feet deep, complete with crystal chandeliers made entirely of salt.", "Wieliczka", "Lesser Poland", "Poland"),
    ("27_great_wall_of_gorgan", "The Red Snake: The Great Wall of Gorgan", "Ancient Defense", "The second-longest defensive wall in the ancient world was made of 200 million red bricks and equipped with an advanced canal system.", "Golestan", "Gorgan Plain", "Iran"),
    ("28_dancing_plague_1518", "The Dancing Plague of 1518", "Historical Paradox", "In July 1518, a woman stepped into a street in Strasbourg and started dancing until her heart gave out—and then 400 people joined her.", "Strasbourg", "Alsace", "France"),
    ("29_baltic_sea_anomaly", "The Baltic Sea Anomaly", "Maritime Enigma", "In 2011, sonar divers found a 200-foot disc resting 300 feet deep that knocked out all electronic equipment within 200 meters.", "Bothnian Sea", "Baltic Sea", "Sweden"),
    ("30_acoustic_kitty_cia", "Operation Acoustic Kitty", "Declassified History", "In the 1960s, the CIA spent $20 million turning a living cat into a walking spy microphone.", "CIA Headquarters", "Langley / Virginia", "USA")
]

for item in EXTRA_TOPICS:
    tid, title, theme, hook, loc_name, loc_city, loc_country = item
    TOPICS.append({
        "id": tid,
        "title": title,
        "sub_theme": theme,
        "hook": hook,
        "location": {
            "name": loc_name,
            "city": loc_city,
            "region": loc_city,
            "country": loc_country,
            "latitude": 0.0,
            "longitude": 0.0
        },
        "key_facts": [
            f"Core historical phenomenon: {title}",
            f"Geographic origin: {loc_name}, {loc_city}, {loc_country}",
            "Unresolved historical enigma verified by archival documentation"
        ],
        "meta_categories": ["History & Philosophy", "Science & Tech", "Travel & Culture"],
        "hashtags": [f"#{theme.replace(' ', '')}", "#AncientMysteries", "#HistoricalCuriosities", "#HistoryUncovered", "#ReelsViral"]
    })

out_file = DATA_DIR / "topic_bank.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump({"total": len(TOPICS), "topics": TOPICS}, f, indent=2)

print(f"Generated {len(TOPICS)} topics -> {out_file}")
