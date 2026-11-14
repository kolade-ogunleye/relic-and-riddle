# Light engines: Gemini vs Groq

## "Carved from the Top Down: The Cave 16 Monolith 🪨" (Kailasa Temple, Ellora Caves) · 20 Oct 13:27

- Sound plan: usable gemini=True groq=True, agreement 17%

**Q: Why did the release gate hold or pass this run?**
- gemini: According to release_report.json, the release checks did not fully pass because the "asset_rights" check was flagged as "needs_review" for the file "output/run_kailasa_temple_monolith_1792524470/motion/motion_01.mp4". All other checks in release_report.json—including caption_length, facts, dimensions, duration, audio specifications, sync_drift, provenance, voiceover_rights, font_rights, and ambient_bed_rights—passed.
- groq: The run cleared the release gate because every required quality check succeeded: the automated video check reported no issues, the second fact‑check passed with 100 % confidence, and the self‑healing action fixed the visual repeat problem. In the release_report all items are "pass" except "asset_rights" which is only marked "needs_review" (a non‑blocking flag). With all critical checks passing, the gate allowed the run to proceed.

**Q: Was AI motion used, and why or why not?**
- gemini: Yes, AI motion was used. According to "review card notes" (motion_note), kling-3.0 was used to animate 1 real photo, and the resulting Reel carries the AI label. Additionally, "motion files" records the generated files "motion_01.mp4" and "motion_01_anchor.jpg".
- groq: Yes, AI motion was used – the review card notes state “AI motion (kling-3.0) animates 1 real photo(s); the Reel carries the AI label.”

**Q: Which voice and music were used?**
- gemini: According to voice.json and review card notes, the voiceover used Azure (commercially licensed). According to music_selection.json and release_report.json, the music used was a library track titled "Mysterious Ambient" by creator dlolbful from Freesound (licensed CC0 1.0, file: mysterious/mysterious_ambient_ab278746.mp3). Additionally, release_report.json lists two ElevenLabs sound effects: sfx_cavern_wind.wav and sfx_whoosh.wav.
- groq: Voice: Azure (commercially licensed) – as noted in the review card notes and voice.json. Music: free‑library track “Mysterious Ambient” (mysterious mood, cc0 license) – as listed in the review card notes and music_selection.json.
