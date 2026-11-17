# Privacy data map

What personal data Relic & Riddle keeps, why, for how long, who processes it and how it is deleted. The
four tables below cover every store that can hold personal data: the Telegram activity log (`audit_log`),
the audience tables (`audience_*`), the face-privacy cache (`cache/privacy`) and the topic photo library
(`assets/**`). Keep the retention column in step with `PipelineDatabase.RETENTION_DAYS` in `src/db.py`.

## Faces in photos

Photos from Wikimedia Commons and museum collections can show living visitors. Before any render, every
photo that is not artwork (`privacy.is_artwork`) goes through `src/privacy.py`:

- **Default, `FACE_VERIFY_CLOUD=false`:** everything runs on this machine. YuNet (OpenCV) proposes faces,
  face geometry trims each box to a head, and a box is blurred when YuNet's score is at least 0.55, the face
  is at least 0.3 % of the photo's shorter side (smaller, it is at most 9 px across in any frame: not
  identifiable), a box over a tenth of the shorter side scores at least 0.70, and it is not a printed letter
  on a light page. Skin tone is not used, and a monochrome photo is judged like a colour one, so archival
  photos are still blurred. The cascade detector stands in only when YuNet cannot run. The check cannot tell
  a carved, painted or stained-glass face from a living one: those are blurred too unless the photo's source
  record marks it as artwork. No photo or face crop leaves the machine, unless the next setting is on.
- **`FACE_VERIFY_CLOUD_AMBIGUOUS=true` (with `FACE_VERIFY_CLOUD=false`; off by default):** in an art-like
  photo (its source record names a relief, carving, statue, stained glass, gilding and the like:
  `privacy.looks_like_art`), each face the local check keeps is one it cannot tell from a carved or painted
  face. Only those face crops (the padded face box, never the whole photo) are sent to Google Gemini with the
  billed `GEMINI_API_KEY`, and only Gemini is asked (no Claude fallback). Turn this on only once the operator has
  confirmed that the key's project is on Google's paid tier; never the free-tier key. When the setting is off, the
  key is missing, or the call fails, the face is blurred and nothing is sent (fail closed). Every request that went
  out is recorded in `llm_usage` (task `privacy:ambiguous-face`, model `<GEMINI_MODEL> (face check)`, tokens and
  cost in USD), and each photo's count and cost are logged with the photo, so a run's photo set adds up to the
  run's cost.
- **`FACE_VERIFY_CLOUD=true`:** the whole photo (up to 3072 px) and each candidate face crop are also sent
  to Google Gemini with the billed `GEMINI_API_KEY` (paid tier, never the free-tier key) and, if Gemini
  cannot answer, to Anthropic Claude. Both are processors under their API terms and data processing
  addenda. Turn this on only once both are accepted for this account.

Google's terms say paid-tier prompts and responses are processed under its Data Processing Addendum for
Products Where Google is a Data Processor. Accepting that addendum (and Anthropic's, for
`FACE_VERIFY_CLOUD=true`) for this account is the operator's responsibility; whether it is in place
[UNVERIFIED: not checkable from the repository].

Every face that is kept is blurred (soft focus, its detail destroyed) before composition; the blur itself
always runs locally. `FACE_PRIVACY_ENABLED=false` turns the whole step off.

## 1. Purpose

| Store | Personal data held | Purpose |
|---|---|---|
| `audit_log` (SQLite) | Telegram user id and username of the operator or anyone who wrote to the bot, the command or button pressed (approvals included), allowed/denied, time | Security: who approved, rejected or tried a command; investigating a denied or disputed action |
| `audience_*` (SQLite: `audience_metrics`, `audience_media_ids`, `audience_sources`) | None about individual viewers: per-post platform totals (views, reach, watch time, shares), the platform's id of our own post, each source's outcome with API messages (credentials redacted) | Measuring how our own videos did; the weekly digest, `/analytics` and the audit findings |
| `cache/privacy` (files) | Blurred copies of photos (faces already de-identified); with `FACE_VERIFY_CLOUD=true`, a face crop exists for seconds while Claude is asked, then is deleted; an ambiguous crop sent with `FACE_VERIFY_CLOUD_AMBIGUOUS=true` is never written to disk | Not re-checking an unchanged photo on every run |
| `assets/**` (files) | Third-party photos that may show identifiable people before blurring; `credits.json` with photographers' names, licences and source pages | The topic's picture pool and the attribution every licence requires |

## 2. Retention

| Store | Kept for | Enforced by |
|---|---|---|
| `audit_log` | 90 days | `PipelineDatabase.prune_history()` (the nightly improvement cycle in `main.py`) |
| `audience_*` | `audience_metrics` and `audience_media_ids` 730 days; `audience_sources` 90 days. A post older than 730 days is not measured again | `PipelineDatabase.prune_history()`; `audience.due_snapshots()` |
| `cache/privacy` | Until the photo, the detector version or the verifier changes (then a new copy is made); stale copies until the cache is cleared. Ambiguous face crops sent to Gemini: held in memory only for the request here; at Google, on the paid tier, "logged for a limited period of time" for abuse monitoring only and not used to improve Google's products (Gemini API Additional Terms, Paid Services, read 2026-11-05; no number of days is stated) | Cache key in `privacy._cache_path`; Google's terms for the crops |
| `assets/**` | As long as the topic is in the library | Operator |

Encrypted backup snapshots (`docs/backup.md`) contain the database and `assets/`; a pruned or deleted row
stays in older snapshots until those snapshots are removed.

## 3. Processors

| Store | Processor | When | What it receives |
|---|---|---|---|
| `audit_log` | Telegram | Always (the bot is how the operator works) | The messages and button presses themselves, before they are logged here |
| `audience_*` | Meta (Instagram/Facebook Graph), Upload-Post, YouTube | When audience collection is configured | Our own post ids and access tokens; they return totals, nothing about viewers |
| `cache/privacy` | Google Gemini (billed key) and Anthropic Claude | Only with `FACE_VERIFY_CLOUD=true` | Whole photos and face crops for face detection and verification |
| `cache/privacy` | Google (Gemini API, paid tier, billed `GEMINI_API_KEY`) | Only with `FACE_VERIFY_CLOUD_AMBIGUOUS=true` and `FACE_VERIFY_CLOUD=false` | The face crops the local check cannot tell from art, in art-like photos; never the whole photo |
| `cache/privacy` | None | Default (`FACE_VERIFY_CLOUD=false`) | Nothing: detection, verification and blur run locally |
| `assets/**` | Wikimedia Commons and museum APIs (sources), the publishing platforms (blurred frames in the published video) | When a topic is fetched or a video is published | Search queries; the published video |

## 4. Deletion

| Store | How to delete | On request from a person |
|---|---|---|
| `audit_log` | Automatic after 90 days; at once: `DELETE FROM audit_log WHERE user_id = ?` | Delete their rows by Telegram user id |
| `audience_*` | Automatic per the retention above; at once: `DELETE FROM audience_metrics WHERE run_id = ?` (and the other two tables) | Not applicable: no viewer data is stored |
| `cache/privacy` | Delete the folder; it is rebuilt from `assets/` on the next run | Delete the folder, then remove the source photo from `assets/` |
| `assets/**` | Remove the photo and its `credits.json` record, or the topic folder | Remove the photo and re-render any unpublished run that used it; a published video is taken down on the platforms |
