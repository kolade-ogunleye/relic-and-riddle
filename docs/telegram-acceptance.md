# Telegram operator fixes: development retest

These changes are on `fix/telegram-operator-panels`, based on the runtime candidate.
Production remains unchanged. Dev configuration and provider caps are preserved.
An automated check is not operator acceptance; keep this register open until retest.

| Finding | Change | Operator retest |
| --- | --- | --- |
| False Facebook/Instagram disconnection warning | HTTP/response validation; old cache invalidation; explicit account refresh; server-selected Facebook Page recognized | Dashboard → Check accounts; verify both connected |
| Done closes screens instead of returning | Command panels use Dashboard navigation | Queue → Dashboard; status/help/ask → Dashboard |
| Waiting ideas creates another card | Waiting proposal view returns an editable panel | Topic ideas → Waiting ideas → Topic ideas → Dashboard |
| Covers/music open another message | Callback sends use the current panel; track navigation reuses its audio panel | Dashboard → Covers → A/B/C → Dashboard; Music → Next/Keep/Cancel |
| Stale progress after worker cancellation | PID plus process creation identity; dead/reused/unknown workers distinguished; old run buttons cannot show a different run | `/progress`; actual cancellation test still needs a disposable run |
| Image instructions require shell access | Topic picker, `/fetchimages N`, `/image N`, bounded image upload with rights evidence | Images → Choose topic; fetch or upload; review set |
| Swipe replies can pick an unrelated latest run | Run reference taken from card buttons; general replies use quoted context and current local records | Reply to run card and to Topic ideas; verify appropriate context |
| Make #1 label disagrees with next topic | Actual topic serial displayed; stale queue start rejected | Dashboard/Queue numbers agree; changed queue needs refreshed action |

Photo/audio panels retain their media when displaying navigation captions. Telegram
caption limits still apply; slash `/status` supplies a full text screen. Approval
and destructive review actions remain separate from navigation. Explicit old
`done:` buttons continue to close historical cards.

Uploads require source URL, eligible licence, licence URL and author. They never
approve themselves or widen licence policy. Fetching preserves prior rejections,
uses existing curated searches and makes new content subject to review. Image
changes are refused while the topic has a producing run. A missing curated search
is reported rather than inventing a search or approving unrelated results.

Research results update the originating panel. If the operator navigated away,
completion sends a separate notification rather than overwriting the newer screen.
Music acquisition is not implemented as a Telegram job; its dangling
Fetch More button is removed. Production's old progress state requires separate
inspection/promotion; dev changes do not alter production records.
