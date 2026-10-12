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

## Google Veo 3.1 (Gemini API)
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
