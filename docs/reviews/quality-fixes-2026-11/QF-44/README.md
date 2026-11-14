# QF-44: SSRF hardening of page_text and image downloads

Row: `docs/reviews/project-quality-review-2026-11-05.md`, section 9.2.

## Metric (before -> after)

| Measure | Before (712c87c) | After |
|---|---|---|
| SSRF attempts refused without the forbidden URL being requested | 0 / 37 | 37 / 37 |
| Internal or off-host URLs actually requested | 37 | 0 |
| Acceptance: mocked 302 to `http://127.0.0.1/` -> `page_text` returns None | false | true |
| Acceptance: `download_jpeg` for an off-host museum URL raises | false | true |
| Museum providers with no host allowlist | met, nasa, smithsonian | none |
| Controls (public page, provider-host image, Commons image, reference) fetched | 4/4 | 4/4 |

Before: `before.txt` / `before.json`; after: `after.txt` / `after.json` (`measurements.json` is the last run).

## Re-run

```
python docs/reviews/quality-fixes-2026-11/QF-44/measure.py <repo root>
```

The network and DNS are fixtures (`requests.get`/`requests.head` answer from a route table and emulate requests'
default redirect following; `socket.getaddrinfo` answers from a table), so nothing leaves the machine and an auditor
without `output/` gets the same numbers. The cases call the real code: `free_evidence.page_text`,
`commons.download_jpeg`, `image_gen._download_reference` and `footage.download`.

Attacks: for each of 15 internal targets (loopback in IPv4, IPv6 and IPv4-mapped spellings, decimal and hex
`127.0.0.1`, cloud metadata `169.254.169.254`, RFC 1918 ranges, `localhost`, a bare intranet name, `.local`, a public
name resolving to `127.0.0.1`, a name resolving to `10.x`), a cited page answering 302 to it, and (for https targets)
the target named directly; plus off-host and redirect-off-host museum image downloads, a Europeana holder host that
resolves to `127.0.0.1` (image and footage download) and off-host / redirect-inside reference downloads.

## What changed

- `src/url_guard.py` (new): `public_host()` refuses empty and local names, bare single-label names, numeric address
  spellings and any address (literal or resolved) that is not global; `checked()` adds HTTPS, default port and no
  credentials; `follow()` makes every request with `allow_redirects=False` and checks each hop before requesting it.
- `src/free_evidence.page_text`: HEAD precheck (a non-HTML content type stops before the body is asked for), then
  GET, both through `url_guard.follow`; a refused hop returns None.
- `src/footage.py`: host lists for `met`, `nasa`, `smithsonian`; `IMAGE_PROVIDERS`; `url_allowed(url, provider)`
  (provider hosts + public host; a wildcard provider's host is also resolved). `footage.download` uses it on every hop.
- `src/commons.download_jpeg(..., provider=None)`: the URL and every hop must pass `footage.url_allowed`; raises
  `url_guard.RefusedURL` (a ValueError) otherwise. `commons._get` takes `allow_redirects`.
- `src/image_gen._download_reference`: same check on the URL and every hop; None when refused.
- Callers: `beat_images` and `photo_orientation` pass the candidate's recorded provider (`beat_images` skips a
  refused candidate); `then_now` skips one.

Regression test: `tests/test_qf44_ssrf_guard.py` (fails on 712c87c, passes on the fix).
