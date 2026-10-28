# SHOWCASE-REPORT.md — how docs/index.html was built

**Date:** 2026-10-27 · **Branch:** `showcase` (from main @ 3569b75, v0.4.0)
· **Deliverable:** one self-contained file, `docs/index.html` (31,150 bytes).
No library code was touched; the repo's AGENTS.md invariants (11 stitches,
byte-exactness, zero dependencies) extend to this page by construction —
it loads nothing.

## Governing rule

The build brief (`~/workspace/stitch-showcase/BRIEF.md`, outside the repo)
listed every fact allowed on the page. **No number, quote, or result was
invented**; every figure on the page traces to the brief, which itself
traces to VERIFICATION.md. Where the brief's numbers differ in rounding
from looser summaries (e.g. sign "~360 ms → ~1.5 ms" rather than a single
baseline figure), the brief's wording wins.

## Decisions

- **One file, zero external resources** — all CSS in one `<style>`, all JS
  in one `<script>`, the mint diagram as inline SVG, system font stacks
  only (no webfonts). Emojis (🎴 🖥️ 💻) are the only "iconography"; they
  are text glyphs, not loaded assets. The page works from `file://`,
  offline, forever — matching the library's copy-paste distribution model.
- **Dark theme, card motif throughout** — the centrepiece is a card game,
  so the v0.4 scoreboard is also a row of cards and the palette gives each
  machine a stable identity colour (thor green, orin blue, proart purple,
  dealer amber) used consistently in the route diagram, the card accents,
  and the winner outlines.
- **The replay is data-driven** — the six plays live in one `PLAYS` array
  (turn, player, machine, trick, card, xchg, payload hash), transcribed
  from the brief. Rendering is a pure function of `shown` (0–6), which is
  what made the logic mechanically testable (below).
- **Honesty surfaces kept on the page, not footnoted away** — the final
  banner states the draw (orin 1 · proart 1 · thor 0); the signed-bookings
  card carries the verbatim-replay residual in a visibly separate
  "honest residual" block; the rehearsal tamper (`payload_mismatch`,
  scored nothing) is stated under the replay. The footer repeats the
  house rule: "this page shows only what the record proves."
- **Growth point** — `<!-- NEXT SECTION -->` sits at the end of `<main>`,
  after the timeline, where future sections (e.g. a second game, an
  install-route walkthrough) slot in without disturbing the replay.
- **Footer links are relative** (`../SPEC.md`, `../VERIFICATION.md`,
  `../AGENTS.md`, `../tutorials/`) so they resolve both on GitHub and in a
  local checkout; the one absolute URL is the repo link, an anchor, not a
  loaded resource.

## How each section was built

1. **Header** — the one-line definition, the conservation law, and status
   chips (v0.4.0 · 174 Python tests · 10 Rust tests · cross-language
   PASSED · 11 stitches · 0 dependencies · 5 surfaces). Every chip is a
   brief fact.
2. **Stitch War replay** — route diagram (dealer + three machine nodes,
   dashed hops labelled `scp → wake`, the full relay route printed under
   it), the three hand chips, and a two-row table. **Step** books the next
   play: the node of the player lights in its colour, the card is dealt
   into its trick slot (rank large, Ace for 14, player + machine under
   it), and the caption records `xchg` + payload hash and the hand-off
   to the next machine. After turn 2 the trick-1 banner shows; after
   turn 5 the trick-2 banner and the final score banner show, ending
   with *"Derived from the ledgers alone — the record is the game."*
   **Reset** returns to the dealt-but-unplayed state.
3. **How a mint works** — one inline SVG, four beats (two sovereign
   ledgers → both book the same exchange, hash committed at booking time
   → files meet, `agree()` compares → MINTED / REFUSED), with the five
   refusal reasons as chips under it, each with a one-line gloss, and the
   caption *"Shared state is never stored. It is derived, on demand,
   where records agree."*
4. **v0.4 scoreboard** — three cards, one per R&D track (fuzz 1,884
   payloads, 139/776 drift, cargo 6 → 10; Ed25519 360 ms → 1.5 ms sign,
   ~290 ms → ~4 ms verify, byte-identical signatures; signed bookings with
   the impostor refusal and the residual), plus a release-gates strip
   (174 + 10 tests, cross-language PASSED, examples/tutorials re-run,
   v0.3's five-surface verification).
5. **Timeline** — v0.1 → v0.4, one paragraph each, ending on the release
   line: "Plus: three agents played cards over it."
6. **Footer** — the links above and the provenance line from the brief.

## Validation (all run 2026-10-27, before commit)

- **External resources: none.** `grep` over `src=`/`href=` attributes
  finds only the five footer anchors (four relative, one github.com
  link). The document contains **zero `src=` attributes at all** — no
  scripts, images, stylesheets, iframes, or fonts are loaded. The only
  `url(...)` occurrences are `url(#arr)`, internal SVG marker fragments.
  The single `https://` string in the file is the footer repo anchor.
- **Well-formedness:** parsed with Python's `html.parser` in a
  stack-checking harness — no mismatched or unclosed tags.
- **JS syntax:** the inline script extracted and passed `node --check`.
- **Replay logic:** the script was executed in Node against a minimal
  DOM stub and walked through all states: 0→6 plays deal in brief order;
  trick-1 banner appears exactly at 3 plays, trick-2 + final exactly at
  6; Step disables at completion with the final caption showing turn 5 /
  `t2-proart` / `f70d9f18a97a…`; Reset restores the initial state
  (0 dealt, banners hidden, progress `0 / 6 plays`).
- **Facts audit:** every number on the page was re-read against the brief
  after the build (test counts, timings, payload-hash prefixes, hands,
  trick winners, the draw). No discrepancies found.

## Known limits

- No real-browser screenshot check was available to this builder; the
  layout relies on standard flex/grid with a small-screen fallback
  (hops hide, nodes wrap) rather than any exotic CSS.
- The replay's card entrance animation is CSS-only and degrades to a
  static deal where animations are unsupported; the game state does not
  depend on it.

## Fix pass — replay legibility (2026-10-27, after real-browser screenshots)

The original build was validated without a browser (see Known limits,
now discharged). Headless Chromium screenshots of the stepped states
(3 and 6 auto-clicks of Step) showed three defects in the replay area;
all three are fixed in `docs/index.html`, presentation only — no facts,
numbers, play data, or section copy changed:

1. **Played cards nearly invisible** — the `deal` entrance animation
   (opacity 0 → 1) could be captured mid-fade, leaving dealt cards as
   faint silhouettes. The animation is removed from cards and banners;
   `.pcard` is pinned `opacity:1` on its solid cream face, the rank
   suit line is darker (opacity .55 → .75), and the winner highlight is
   now outline **plus** a green glow ring/shadow.
2. **Winner/final banners near-invisible** — the translucent
   `rgba(…, .09)` backgrounds are replaced with solid fills
   (`#173324` trick, `#182a44` final), 1.5px solid accent borders, and
   near-white text.
3. **Caption garbled** — root cause was `.caption{display:flex}`: flex
   layout turns each text run and inline element into a separate flex
   item, which shrank and split words mid-word ("pro/art", "t1-proar/t")
   and overlapped fragments around the hash. The caption is now a plain
   block that wraps at word boundaries (`overflow-wrap:break-word`,
   line-height 1.65), and the exchange id and payload hash are inline
   `<code class="nw">` spans with `white-space:nowrap`, so they never
   split.

**Verification (same method that caught the defects):** headless
Chromium screenshots of the plain page and of injected auto-click
copies (3 and 6 Step clicks, `--virtual-time-budget=5000`,
`--window-size=1280,3200`), read directly. State 0 is unchanged.
State 3: cards 12 / 4 / A solid and fully legible, Ace outlined and
glowing, trick-1 banner solid with crisp text, caption flows in clean
lines with `t1-proart` and the hash intact. State 6: all six cards
vivid, the winning 8 highlighted, trick-2 and final banners solid and
fully legible, caption clean. JS re-checked with `node --check`; the
file still has zero `src=` attributes and no external resources.
