# Natural Earth disputed areas under ruling Q2

Every feature of `data/geo/ne_10m_admin_0_disputed_areas.geojson` (Natural Earth 1:10m, public domain) and whether
`src/map_layers.is_contested` draws it as neutral ground (operator ruling Q2, 2026-11-11: neutral where NOTE_BRK
records a claim, "Claimed by", "Self admin." or "Between", whatever the TYPE, except a Lease or an Overlay).
99 features: 78 neutral, 21 ordinary.

| # | BRK_NAME | TYPE | NOTE_BRK | neutral |
|---|---|---|---|---|
| 1 | Jammu and Kashmir | Disputed | Admin. by India; Claimed by Pakistan | yes |
| 2 | Aksai Chin | Disputed | Admin. by China; Claimed by India | yes |
| 3 | Israel | Disputed |  | no |
| 4 | Gaza | Disputed |  | no |
| 5 | West Bank | Disputed |  | no |
| 6 | Ilemi Triangle | Disputed | Admin. by Kenya; Claimed by South Sudan | yes |
| 7 | Arunachal Pradesh | Disputed | Admin. by India; Claimed by China | yes |
| 8 | Golan Heights | Indeterminate | Admin. By Israel; Claimed by Syria | yes |
| 9 | UNDOF Zone | Overlay | UN Patrolled Zone in Syria | no |
| 10 | Somaliland | Disputed | Self admin.; Claimed by Somalia | yes |
| 11 | Lawa Headwaters | Disputed | Admin. by France; Claimed by Suriname | yes |
| 12 | Courantyne Headwaters | Disputed | Admin. by Guyana; Claimed by Suriname | yes |
| 13 | Korean Demilitarized Zone (south) | Overlay | Since 1953 a 2 km buffer south of the military demarcation line, patrolled by South Korea | no |
| 14 | Korean Demilitarized Zone (north) | Overlay | Since 1953 a 2 km buffer north of the military demarcation line, patrolled by North Korea | no |
| 15 | W. Sahara | Disputed | Admin. by Morocco; Claimed by Western Sahara | yes |
| 16 | W. Sahara | Indeterminate | Self admin.; Claimed by Morocco | yes |
| 17 | Abkhazia | Breakaway | Self admin.; Claimed by Georgia | yes |
| 18 | South Ossetia | Breakaway | Self admin.; Claimed by Georgia | yes |
| 19 | Kosovo | Disputed |  | no |
| 20 | Ceuta | Geo subunit |  | no |
| 21 | West of Essequibo River | Disputed | Admin. by Guyana; Claimed by Venezuela | yes |
| 22 | Artsakh | Breakaway | Self admin.; Claimed by Azer. | yes |
| 23 | Dragonja River | Disputed |  | no |
| 24 | Shaksam Valley | Disputed | Admin. by China; Ceded to China by Pakistan; Claimed by India | yes |
| 25 | Gilgit-Baltistan | Disputed | Admin. by Pakistan; Claimed by India | yes |
| 26 | Melilla | Geo subunit |  | no |
| 27 | Guantanamo Bay USNB | Lease | Leased to U.S.A. by Cuba; Claimed by Cuba | no |
| 28 | Near Om Parvat | Disputed | Admin. by India; Claimed by Nepal | yes |
| 29 | Brazilian Island | Indeterminate | Claimed by Brazil and Uruguay | yes |
| 30 | Olivenza | Disputed | Admin. by Spain; Claimed by Portugal | yes |
| 31 | Transnistria | Breakaway | Self admin.; Claimed by Moldova | yes |
| 32 | Luhansk People's Republic | Breakaway | Self admin.; Claimed by Ukraine | yes |
| 33 | Donetsk People's Republic | Breakaway | Self admin.; Claimed by Ukraine | yes |
| 34 | Gibraltar | Disputed | Admin. By U.K.; Claimed by Spain | yes |
| 35 | Abyei | Disputed | Admin. by Sudan; Claimed by South Sudan | yes |
| 36 | Demchok | Disputed | Admin. by India; Claimed by China | yes |
| 37 | Samdu Valleys | Disputed | Admin. by India; Claimed by China | yes |
| 38 | Tirpani Valleys | Disputed | Admin. by India; Claimed by China | yes |
| 39 | Bara Hotii Valleys | Disputed | Admin. by India; Claimed by China | yes |
| 40 | N. Cyprus | Disputed | Self admin.; Claimed by Cyprus | yes |
| 41 | Cyprus U.N. Buffer Zone | Indeterminate |  | no |
| 42 | Siachen Glacier | Indeterminate | Claimed by Pakistan and India | yes |
| 43 | Baykonur | Lease | Leased to Russia by Kazakhstan | no |
| 44 | Belize | Disputed |  | no |
| 45 | Southern Patagonian Ice Field | Indeterminate | Claimed by Chile and Argentina; under survey | yes |
| 46 | Bhutan (northwest valleys) | Disputed | Admin. by Bhutan; Claimed by China | yes |
| 47 | Bhutan (Chumbi salient) | Disputed | Admin. by Bhutan; Claimed by China | yes |
| 48 | Crimea | Disputed | Admin. by Russia; Claimed by Ukraine | yes |
| 49 | Shebaa Farms | Disputed | Admin. By Israel; Claimed by Lebanon | yes |
| 50 | Ilemi Triange | Disputed |  | no |
| 51 | Corner of Artigas | Disputed | Admin. by Brazil; Claimed by Uruguay | yes |
| 52 | Bir Tawil | Indeterminate | Between Egypt and Sudan | yes |
| 53 | Halayib Triangle | Disputed | Admin. by Egypt; Claimed by Sudan | yes |
| 54 | Vukovar Island | Disputed |  | no |
| 55 | Šarengrad Island | Disputed |  | no |
| 56 | East Jerusalem | Disputed | Admin. By Israel; Claimed by Palestine | yes |
| 57 | No Man's Land (Fort Latrun) | Disputed | Admin. By Israel; Claimed by Palestine | yes |
| 58 | Azad Kashmir | Disputed | Admin. by Pakistan; Claimed by India | yes |
| 59 | No Man's Land (Jerusalem) | Disputed | Admin. By Israel; Claimed by Palestine | yes |
| 60 | Mount Scopus | Disputed | Admin. By Israel; Claimed by Palestine | yes |
| 61 | Junagadh and Manavadar | Disputed | Admin. by India; Claimed by Pakistan | yes |
| 62 | North Borneo | Breakaway |  | no |
| 63 | Taiwan | Disputed | Self admin.; Claimed by China | yes |
| 64 | Tiran and Sanafir Is. | Disputed | Admin. by Egypt; Claimed by Saudi Arabia | yes |
| 65 | Juan De Nova I. | Geo subunit | Admin. by France; Claimed by Madagascar | yes |
| 66 | Mayotte | Disputed | Admin. by France; Claimed by Comoros | yes |
| 67 | Diego Garcia NSF | Overlay | Leased to U.S.A by U.K.; Claimed by Mauritius and Seychelles | no |
| 68 | Kuril Is. | Disputed | Admin. by Russia; Claimed by Japan | yes |
| 69 | Br. Indian Ocean Ter. | Disputed | Admin. by U.K.; Claimed by Mauritius and Seychelles | yes |
| 70 | S. Sandwich Is. | Geo subunit | Admin. by U.K.; Claimed by Argentina | yes |
| 71 | S. Georgia | Geo subunit | Admin. by U.K.; Claimed by Argentina | yes |
| 72 | Falkland Is. | Disputed | Admin. by U.K.; Claimed by Argentina | yes |
| 73 | Paracel Is. | Geo unit | Admin. by China; Claimed by Vietnam and Taiwan | yes |
| 74 | Europa Island | Geo subunit | Admin. by France; Claimed by Madagascar | yes |
| 75 | Swains Island | Disputed | Admin. by American Samoa for U.S.A.; Claimed by Tokelau | yes |
| 76 | Spratly Is. | Indeterminate | Claimed by China, Taiwan, Malaysia, the Philippines, and Brunei | yes |
| 77 | Bird Island | Disputed | Admin. by Venezuela; Claimed by multiple | yes |
| 78 | Wake Atoll | Geo unit | Admin. by U.S.A.; Claimed by Marshall Islands | yes |
| 79 | Korean islands under UN jurisdiction | Overlay | Since 1953 islands south of maritime Northern Limit Line, patrolled by South Korea including Yeonpyeong, Baengnyeong, and Daecheong Is. | no |
| 80 | Rockall I. | Disputed | Admin by U.K.; Claimed by Ireland, Denmark, and Iceland | yes |
| 81 | Navassa I. | Geo unit | Admin. by U.S.A.; Claimed by Haiti | yes |
| 82 | Bassas da India | Geo subunit | Admin. by France; Claimed by Madagascar | yes |
| 83 | Tromelin I. | Geo subunit | Admin. by France; Claimed by Mauritius and the Seychelles | yes |
| 84 | Glorioso Is. | Geo subunit | Admin. by France; Claimed by Madagascar | yes |
| 85 | Pinnacle Is. | Disputed | Admin. by Japan; Claimed by China and Taiwan | yes |
| 86 | Abu Musa I. | Disputed | Admin. by Iran; Claimed by UAE | yes |
| 87 | Bajo Nuevo Bank (Petrel Is.) | Indeterminate | Claimed by Colombia, Jamaica, Nicaragua and the United States | yes |
| 88 | Serranilla Bank | Indeterminate | Claimed by Colombia, Honduras, Nicaragua, and the United States | yes |
| 89 | Sapodilla Cayes | Disputed | Admin. by Belize; Claimed by Honduras and Guatemala | yes |
| 90 | Hans Island | Disputed | Admin. by Denmark; Claimed by Canada | yes |
| 91 | Dokdo | Geo subunit | Admin. by South Korea; Claimed by Japan | yes |
| 92 | Mbane Island | Disputed | Admin by Gabon; Claimed by Equatorial Guinea | yes |
| 93 | Peñón de Vélez de la Gomera | Disputed | Admin. by Spain; Claimed by Morocco | yes |
| 94 | Penon de Alhucemas | Disputed | Admin. by Spain; Claimed by Morocco | yes |
| 95 | Islas Chafarinas | Disputed | Admin. by Spain; Claimed by Morocco | yes |
| 96 | Isla del Perejil | Disputed | Admin. by Spain; Claimed by Morocco | yes |
| 97 | Matthew and Hunter Is. | Disputed | Admin. by New Caledonia for France; Claimed by Vanuatu | yes |
| 98 | Scarborough Reef | Indeterminate |  | no |
| 99 | Doumera Island | Disputed | Admin. By Eritrea; Claimed by Djibouti | yes |
