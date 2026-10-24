# Verified sources (policy, licences, pricing)

Checked 2026-10-12 against the official pages below. Re-check before relying on anything
older than about 6 months; `main.py policy` watches Meta's policies weekly.

## Meta: original content guidelines (Facebook)
https://www.facebook.com/business/help/262834734651607 (read in a browser; the page is script-rendered)

- Original: content "filmed or created by the publisher", or that the publisher has the rights to publish first.
- Third-party material can still be recommended when the publisher "meaningfully enhance[s] the content with additional educational, critical, or entertainment value". Their example: "selectively interspersing third-party clips ... showcasing relevant historical examples to add evidence for your narrative, so long as original commentary remains the focus".
- These count as *unoriginal* ("minor editorialization"):
  - borders, logos or watermarks
  - only an on-screen caption or title
  - background music
  - speed changes
  - subtitle transcripts
  - "simply describing (in text, on-screen, via voiceover or in the description) what happens in a photo or video with no other meaningful additions"
  - "basic compilations ... without adding your own narrative or new information"
  - intros or outros
- Penalties: reduced distribution, limited or lost recommendations, limited or lost monetization.

**What this means for us**
- **Our format qualifies only through its narrative.** The researched narration is the original layer: it must add information and context the pictures don't show, not describe them, and it must stay the focus from start to finish.
- **Footage and images are evidence for the narrative,** never a compilation on their own. Captions, music and borders add nothing towards originality.
- **Elements we create ourselves strengthen the case most.** Examples: maps, timelines and scale diagrams, which count as "created by the publisher".

## Pexels
- Licence: https://www.pexels.com/license/
  - Videos are free to use and modify, including on social media; attribution is not required.
  - Not allowed: identifiable people shown in a bad light or offensively; implied endorsement; use in trademarks; redistribution on stock or wallpaper sites.
- API: https://www.pexels.com/api/documentation/
  - "show a prominent link to Pexels" when using the API, and "always credit our photographers when possible".
  - Limits: 200 requests per hour and 20,000 per month; working around them leads to termination.
  - Video search: `GET https://api.pexels.com/v1/videos/search` (`query`, `orientation`, `size`, `per_page` up to 80). `video_files[]` has `width/height/quality/link/fps`; `user.name/url` is the videographer.
- **For us:** credit "Footage: Pexels / <videographer>" in the caption whenever a Pexels clip is used.

## Pixabay
- Licence: https://pixabay.com/service/license-summary/
  - Free use, no attribution required, modification allowed.
  - Not allowed: standalone redistribution; immoral or illegal use; "misleading or deceptive" use; trademarks.
- API: https://pixabay.com/api/docs/
  - Show users where results come from.
  - Cache responses for 24 hours.
  - No permanent hotlinking; download instead.
  - Limit: 100 requests per 60 seconds.
  - Video search: `GET https://pixabay.com/api/videos/`, with renditions `large` (often 3840×2160, empty if unavailable), `medium`, `small` and `tiny`.
- **For us:**
  - The "misleading" clause backs the rule that a clip may only illustrate the specific site it actually shows.
  - Credit "Footage: Pixabay / <user>".

## NASA
https://www.nasa.gov/nasa-brand-center/images-and-media/
- NASA media "generally are not subject to copyright in the United States".
- The NASA insignia and logotype are *not* public domain, and nothing may imply NASA endorsement.
- Third-party material inside NASA media is not covered; clear it with the rights holder.
- **For us:** use only NASA-produced items. Skip any with logos or third-party credits.

## ElevenLabs
https://elevenlabs.io/pricing
- **Free:** 10k credits, Voice Design and music, but **no commercial licence** and no music commercial use.
- **Starter:** 30k credits. Adds the commercial licence, instant voice cloning and music commercial use; Voice Design is included. The FAQ says $6/mo; the card showed a first-month promo.
- **Creator:** 121k credits, adds professional voice cloning. FAQ $22/mo.
- **For us:** narration on the Free plan is not licensed for monetized posting. Starter (or Azure Speech) is required before launch. A *designed* voice avoids any cloning-consent question.

## Google Veo 3.1 (Gemini API): retired, not used
> Removed from the pipeline on 2026-10-20: Google shuts down the Veo 3.1 Preview models on
> 2026-10-22. Kept as a record of what was checked.
https://ai.google.dev/gemini-api/docs/pricing
- Prices per second, with audio by default; there is no free tier:

  | Model | 720p | 1080p | 4K |
  |---|---|---|---|
  | Standard | $0.40 | $0.40 | $0.60 |
  | Fast | $0.10 | $0.12 | $0.30 |
  | Lite | $0.05 | $0.08 | not supported |

- **For us:** the motion A/B compares Lite vs Fast (vs Standard). We strip the generated audio ourselves.

## Upload-Post
- Plans: https://www.upload-post.com/pricing-comparison
  - Free: 10 uploads per month.
  - Basic: $24/mo, or $16/mo billed annually. Unlimited, within platform caps.
- Limits: https://docs.upload-post.com/guides/limit-of-uploads/
  - Instagram: 50 posts per 24 hours; Facebook: 25 per 24 hours. Only successful publishes count.
  - **It flags duplicate or similar content within 48 hours per account and network.**
- **For us:** a topic's carousel must not reuse the Reel's caption, and must go out at least 48 hours after that Reel.

## Map and graphics data
- Natural Earth (https://www.naturalearthdata.com/about/terms-of-use/): "All versions of Natural Earth
  raster + vector map data ... are in the public domain"; commercial use invited; "Crediting the
  authors is unnecessary". We use 1:50m admin-0 countries v5.1.2 (`scripts/fetch_geodata.py`).
- Wikidata (https://www.wikidata.org/wiki/Wikidata:Licensing): structured data is CC0. Coordinates
  (P625) are accepted only inside the topic's country (src/geo.py). API etiquette
  (https://www.mediawiki.org/wiki/API:Etiquette): identifying User-Agent, serial requests, maxlag, cache.
- Scale-graphic references (src/graphics.py REFERENCES): Statue of Liberty 305 ft 1 in ground to torch
  (U.S. National Park Service); Elizabeth Tower ("Big Ben") 96 m / 316 ft (UK Parliament); Great
  Pyramid of Giza about 138.5 m / 454 ft today.

## AI motion APIs (src/motion.py)
- Veo 3.1 (retired 2026-10-20, see above) (https://ai.google.dev/gemini-api/docs/veo): `POST .../v1beta/models/<model>:predictLongRunning`
  with `x-goog-api-key`; poll `GET .../v1beta/<operation>` until `done`; video at
  `response.generateVideoResponse.generatedSamples[0].video.uri`. Models `veo-3.1-generate-preview`,
  `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview`. `9:16` allowed; 1080p requires 8 s;
  image-to-video `personGeneration` only `allow_adult`; audio always on (we strip it).
  Not confirmed from the REST page: the exact type of `durationSeconds` (number vs string). Check on first live call.
- Kling 3.0 (https://kling.ai/document-api/api/video/3-0-omni/image-to-video, read in a browser):
  `POST https://api-singapore.klingai.com/image-to-video/kling-3.0`, `Authorization: Bearer <key>`,
  `contents` [prompt, first_frame (URL or Base64)], `settings` {resolution 720p/1080p/4k, duration 3-15,
  audio native/off, multi_shot}, `options.watermark_info.enabled`; poll `GET /tasks?task_ids=<id>`;
  video at `data[0].outputs[type=video].url` (cleared after 30 days).
- Runway (https://docs.dev.runwayml.com): `POST https://api.dev.runwayml.com/v1/image_to_video`,
  `X-Runway-Version: 2024-11-06`, model `gen4.5`. Vertical ratio values and the task response for
  video weren't shown in the docs read on 2026-10-12, so Runway isn't wired yet (no guessed APIs).
- Local Wan 2.2 TI2V-5B (Apache-2.0): not installed; needs roughly 25-30 GB of model downloads plus
  PyTorch. Waiting for the user's OK.

## Gemini API (checked 2026-10-12 with the project's key)
- https://ai.google.dev/gemini-api/docs/pricing: Gemini 3.8 Flash input/output are free on the free tier, but
  "Grounding with Google Search" is "Not available" there. Paid tier: 5,000 free grounded search requests per
  month (shared across Gemini 3+ models), then $14 per 1,000. Veo has no free tier.
- Gemini 2.5 Flash (the last model with free-tier grounding) returns "no longer available to new users" for this key.
- So the Gemini fact-check fallback and Veo both need billing enabled on the key's Google Cloud project.

## Captions, hashtags and titles (checked 2026-10-12)
- Instagram caps hashtags at 5 per post and Reel (rolling out; reported by Social Media Today and others
  quoting Instagram's @creators announcement; Instagram says generic tags like #reels/#explore don't help
  and may hurt). We use 3-5 specific tags and none in the first comment.
- Instagram caption: 2,200 characters; about the first 125 show before "more" (we keep the first line within 125).
- Upload-Post (https://docs.upload-post.com/api/upload-video/): Instagram caption = `instagram_title`
  (falls back to `title`); Facebook Reel title = `facebook_title` (falls back to `title`; max 255 chars per
  https://docs.upload-post.com/resources/character-limits/), text = `facebook_description`. AI labels:
  `is_ai_generated` (Instagram) and `facebook_is_ai_generated` (Facebook).
- Carousels: 1080x1350 (4:5) — the tallest ratio Meta's content-publishing API has accepted for feed images
  (4:5 to 1.91:1); Instagram's app reportedly also accepts 3:4, not relied on for API posting.

## Using AI subscriptions instead of APIs (checked 2026-10-12)
- Anthropic Consumer Terms (https://www.anthropic.com/legal/consumer-terms), section 3: users may not
  "access the Services through automated or non-human means, whether through a bot, script, or otherwise",
  "except when you are accessing our Services via an Anthropic API Key or where we otherwise explicitly
  permit it". Claude Code's docs show `claude -p` for scripts and recommend an API key for scripted/CI use
  (https://code.claude.com/docs/en/headless); Pro/Max limits are 5-hour and weekly, shared with interactive use.
- Gemini CLI: secondary sources report Google ended the consumer (AI Pro) Gemini CLI backend in June 2026.
- OpenAI's terms page could not be fetched automatically (403); not verified.
- Decision: the unattended pipeline uses API keys; subscriptions remain for interactive development.
