# web — the story track and virtual splicing

The screen the editor works on. No build step: the file being served is the source.

## The design, and what was dropped from it

The visual design is **Sunlit Studio**: warm paper surfaces, terracotta as the single
interactive colour, sage for anything verified, generous rounding. Its token set — colour,
type scale, radii, an 8-point spacing grid — lives at the top of `styles.css` as custom
properties.

It arrived as a Tailwind Play CDN mockup and was ported rather than adopted, for three
reasons. Tailwind's own documentation marks the Play CDN as development-only, because it
compiles CSS in the browser on every load. We have no build step and one origin on
purpose, and four third-party requests on the critical path means a judge on a locked-down
network gets unstyled HTML. And the interface is assembled in JS, so there is no HTML file
to hang utility classes on.

The typeface is served from this origin: `web/fonts`, two subsets, variable weight, 49 KB
in total. `latin-ext` is not optional — Turkish needs the g-breve and s-cedilla that
`latin` leaves out. Icons are inline SVG built through `createElementNS`, so there is no
icon font either.

### Numbers on the screen are numbers we measured

The mockup carried several confident-looking figures — a word precision percentage, a
resolution and frame rate, a cloud sync indicator. None of them corresponded to anything
this system measures, and an unverifiable number sitting next to verifiable provenance
costs more than it buys: it is the one claim a judge cannot check, and it sits directly
above the ones they can.

They were replaced with values that come from the session, the library statistics or the
media itself: real take and word counts, the real credit balance, the real container
format, and the WebMCP tool count. `test_ui.mjs` asserts none of the invented strings came
back.

## Why there is no bundler

Nothing in this interface is a problem React or a bundler solves. The only real
requirement is state that reflects what the WebMCP tools change, and that is forty lines
in `store.js`. What dropping the build step buys:

- No node stage in the Cloud Run image, which removes a whole category of deploy risk
- Zero npm supply chain surface
- A judge reading the repository sees the same file the browser runs

The blueprint said React; this is a deliberate departure.

## Files

| File | Job |
| --- | --- |
| `index.html` | One root. No inline script. Preloads the font. |
| `styles.css` | Sunlit Studio tokens and the whole stylesheet. |
| `fonts/` | Plus Jakarta Sans, two subsets, served from this origin. |
| `src/i18n.js` | Interface strings. English default and fallback, Turkish available. |
| `src/api.js` | Server calls, including the upload with its progress events. |
| `src/store.js` | Single source of truth. Tools and panel change the same store. |
| `src/player.js` | Virtual-splice player. Double buffered, rAF driven. |
| `src/ui.js` | Rendering. `el()` only accepts `textContent`, `icon()` builds SVG. |
| `src/webmcp.js` | The five WebMCP tools: registration, state reflection, fallback. |
| `src/approve.js` | Render approval dialog. Closed shadow root. |
| `src/main.js` | Wiring plus the `ready` promise. |
| `test_web.mjs` | Store, WebMCP, injection and i18n tests. |
| `test_ui.mjs` | `render()` driven across every state against a fake DOM. |
| `test_approve.mjs` | Approval dialog tests against a fake DOM. |

## What is on the screen

**Search and delivery filter.** One field and a row of delivery pills. The pills are the
tone values the corpus actually carries, so picking one narrows a real query; picking it
again clears it. It re-runs the search immediately, because a filter that needs a second
click reads as broken.

**Your own footage.** A drop zone that is also a file picker, with the take name and the
spoken language beside it. The zone is a `<label>` wrapping a hidden `<input type=file>`, so
keyboard activation and the file dialog come free rather than being reimplemented.

Two phases are reported separately because they fail differently and take different amounts
of time: the transfer is network-bound and shows a percentage, the ingest is CPU-bound on
the server and shows a stage. Once the bytes are across, the bar stops claiming to know a
fraction and pulses instead — transcription has no measurable progress, and a fake
percentage is worse than none. Transcription is also the slow stage, so it says so; a silent
minute reads as a hang.

The upload goes through `XMLHttpRequest` rather than `fetch`, for one reason: `fetch` has no
upload progress event.

When the server has no transcriber the control is disabled and says why, rather than
accepting a file and failing a minute later.

**Transcript and word picker.** One card per matching take: speaker, take id, delivery with
its confidence, the matched range, and the **whole line as clickable words**.

This is the namesake. Click a word and exactly that word plays — hearing the boundary is
the fastest way to believe a claim about milliseconds. Shift-click a second word and the
span between them is picked, which is the difference between taking "this line" and taking
"these three words of this line". Every token is a real `<button>` in document order, so
Tab walks the sentence and Enter picks; Shift+Enter extends, mirroring the shift-click.

The word timings arrive from `POST /api/lines` in a second request, batched over every
line the search touched. If that request is slow or fails the card falls back to the
matched phrase, which is what it showed before word picking existed.

Marking which words the search matched only became meaningful once the whole sentence was
on screen. Before, the "highlight" covered every word of a phrase-only text.

A picked range becomes a segment shaped exactly like a candidate, so the player, the
timeline and the render request all treat it identically — and `get_timeline_state` reports
it to the agent even though the agent never proposed that id.

**The stage.** The player's two video elements, a caption of the line being played with the
phrase picked out, elapsed time measured across the **cut** rather than the source file, and
the real container format. Audio-only takes have no frame to show, so they say so instead
of presenting a black rectangle.

**Alternative take.** The best-ranked candidate for this line that is not already in the
cut. Swapping replaces the block at its own position, because the editor chose that order
and changing a delivery is not a reason to lose it.

**Vocabulary.** Every word in the library as a search button, sized by how often it is
spoken, with a selector to narrow it to one take.

This exists because of a fair complaint: you upload a recording, the take counter moves, and
nothing else on the page changes. A counter is not an answer to "what is in my footage". And
searching only works if you can guess a word the transcriber actually produced, which on your
own footage you often cannot — the empty result then reads as a broken product rather than a
wrong guess.

After an ingest the panel scopes itself to the take that just arrived, so you see exactly
what came in instead of hunting for it among everything else. A click is exactly what typing
that word and pressing search would do, delivery filter included — one code path, so the two
cannot disagree.

Three things it deliberately does not do:

- **It does not drop stopwords.** A word cloud normally hides "the" and "and". A tool for
  assembling sentences out of recorded speech must not, because those are the words that
  join two fragments.
- **It does not invent variation.** On a small library every count is 1 and every chip is
  the same size. That is the truth about a small library.
- **It does not hide truncation.** Past the cap it says so and suggests narrowing to one
  take, rather than presenting a partial list as the whole vocabulary.

The chips carry a rebuild guard like the other two lists, and it matters most here: a couple
of hundred chips rebuilt sixty times a second during playback would be the most expensive
thing on the page for a list that never changed.

**Story track.** One block per segment, each with a frame from its own start time, its take
id, its line and its duration. Reorderable by drag **or** by arrow keys on the block's
handle — order is the edit, and it costs nothing because nothing has been rendered.

The empty slot at the end doubles as the empty state, so there is always a way in.

## The approval dialog

The gate on the product's single irreversible step. When the agent calls `commit_render`
the dialog opens and **the tool's promise waits on the human's decision** — the HITL gate
made literal. The dialog also states who asked for the render.

Three defences, each with a concrete reason:

**Closed shadow root.** Another script on the page cannot reach in through
`host.shadowRoot` — closed returns `null` — so it cannot find the Approve button and
press it programmatically.

**`textContent` only.** On the previous project we built the approval dialog with
`innerHTML` and a poisoned tool name could click its own Approve button. The strings here
are take ids, dialogue and filenames: data we do not control.

**Host styles inline and `!important`.** So page CSS cannot hide the dialog and get
something approved unseen.

Two small details that matter more than they look: focus defaults to **Cancel**, so a
stray Enter does not start a render. And the five minute timeout resolves as **declined**
— an agent whose human walked away neither waits forever nor gets a silent yes.

## The WebMCP tools

In the usual arrangement an agent tells a backend "cut these ranges, render it" and an
MP4 comes back. If it picked the wrong take you find out after the render finishes,
because the backend is a closed box. Here the tools run in the page, so the proposal
appears on the editor's screen, over the real footage, with the source of every fragment
visible. The decision happens **before** the render.

| Tool | Job | Credits |
| --- | --- | --- |
| `find_line` | Search for a line, return ranked candidates. Results also show on screen. | 0 |
| `propose_cut` | Put the proposal on the timeline. No render; the human can change it. | 0 |
| `preview_segment` | Play a candidate, or the whole proposal. | 0 |
| `get_timeline_state` | Read the timeline — **including what the human changed**. | 0 |
| `commit_render` | Produce a file, after approval. | 1 |

`get_timeline_state`'s description tells the agent the human may have altered the
proposal and asks it to read this before `commit_render`. That is what makes the HITL
loop explicit rather than implied.

### The tools are line-level; the human works at word level

There is deliberately no `tweeze_words` tool. The agent proposes lines, the human refines
to the word, and `get_timeline_state` carries the refinement back. That split is the point
rather than a gap: the agent is good at "which take reads this more calmly" and the human
is the one who can hear that the take should start half a word later.

It also keeps the tool surface honest. Giving the agent word-level picking means giving it
the whole transcript in a tool result, and it would still be guessing at a judgement that
takes ears.

### Verified API surface

Exercised in a real ChatGPT in-app browser run on the previous project:

```js
document.modelContext.registerTool(tool, { signal })  // native may live on navigator only
document.modelContext.getTools()                      // without execute
document.modelContext.executeTool(toolObject, input)  // the object, NOT a name
```

Tool names are 1–128 characters of `[A-Za-z0-9_.-]`. `execute` returns a plain JSON
object.

**There is no `updateTool`.** Changing a description means aborting the registration with
an `AbortController` and registering again, which is what `sync()` does.

### When credits run out the agent learns before it calls

`commit_render`'s description carries the credit state. If the balance is short the
description leads with `UNAVAILABLE RIGHT NOW` and says search is still free. The agent
finds out up front instead of failing mid-task.

That re-registration only happens **when the session changes**. Doing it on every state
change meant building five tool definitions about sixty times a second during playback,
because the rAF loop calls `setPlayback` on every frame.

### We do not install a polyfill

With no WebMCP the tools are simply not registered and the interface panel stays in
charge. Faking agent support would produce silently wrong behaviour in a browser that
cannot do it.

If registration is refused the reason goes to the console. The most common cause is a
missing `Origin-Agent-Cluster` header and a `SecurityError` — the server does send that
header, and `server/test_api.py` asserts it.

## How virtual splicing works

The product's "see it without rendering" claim happens in `player.js`. Hearing a rough cut
encodes nothing; it seeks around the source files.

Two decisions make it usable:

**`requestAnimationFrame`, not `timeupdate`.** `timeupdate` fires about four times a
second, so it can overshoot a cut point by up to 250ms. We cut on word boundaries — 250ms
means you also hear the next word. rAF gives roughly 16ms.

**Double buffering.** One element changing `src`/`currentTime` per segment leaves a
loading gap at every join. Two `<video>` elements alternate: while one plays, the other is
already positioned on the next segment, so the transition is just a `play()` call.

The media server **must** support HTTP range. Verified: `/media/*` returns 206 with
`Content-Range`. Without it every seek would download the whole file.

## Provenance

Under the stage, for whatever is loaded: the source file, the take id and the millisecond
range. The "open the source" link opens exactly that range through a media fragment
(`#t=start,end`). Every story-track block carries its take id and duration, and the
approval dialog repeats all of it per segment before anything is produced.

The source string is whatever the database recorded, and nothing else. That constraint is
the whole point — a source label that does not name the actual source defeats itself, so
`test_ui.mjs` asserts the rendered text is the `source_url` from the row.

This is more than an editor convenience: it is what makes the product **"I assembled what
they said, with sources"** rather than "I made them say something". The first is a
deepfake tool, the second is journalism. The difference is this.

## HTML injection discipline

There is no `innerHTML` anywhere. The `el()` helper deliberately only accepts
`textContent`.

The reason is concrete: on the previous project we built the approval dialog with
`innerHTML` and a poisoned tool name could press its own Approve button. The text here
comes from Whisper output and user filenames — data we do not control.

`test_web.mjs` checks this on every run, and also tests the check itself: does it catch
real usage, and does it produce false positives on comments. The first version did, on the
comment explaining why `innerHTML` is avoided.

## Interface language

English is the default and the fallback, Turkish is a choice, and the locale comes from
`localStorage` or `navigator.language`. Anything the **agent** reads — tool descriptions,
the assistant's instruction — is English only and does not go through the catalogue: a
model should not get a different contract depending on who is looking at the screen.

All text is written in `render()` rather than at construction, which is what made this
cheap. Labels and dynamic values share one code path, so switching language needs no
second mechanism and static labels cannot drift out of sync.

`missingKeys` and `strayKeys` compare each locale against English and the tests assert
both are empty. A missing key is not fatal, since `t()` falls back, but it means a Turkish
reader sees one stray English label — exactly the sort of thing nobody notices until a
judge does.

The redesign added a third check, because those two only compare the locales to each other
and both can be wrong together. `test_web.mjs` now scans the source for every key it asks
for and reconciles it against the catalogue in **both** directions: a key the code wants
and the catalogue lacks, and a key the catalogue defines that nothing uses. Rewriting the
interface produced several of each, and neither is visible at runtime — an orphan simply
never appears, and a missing key only warns in a console nobody is watching.

Two things stay out of the catalogue on purpose. The product name, because it is a proper
noun. And the delivery glyphs, because an emoji is not language.

## Tests

```powershell
node web\test_web.mjs
node web\test_ui.mjs
node web\test_approve.mjs
```

`test_approve.mjs`, 35 tests against a small hand-written fake DOM: the shadow root
opening `closed`, host styles being forced, `role=dialog` and `aria-modal`, the provenance
text appearing, focus defaulting to Cancel, Escape declining, the timeout resolving as
**declined**, an agent request being marked as such, listeners not being left behind,
protection against a double decision, and the dialog rendering in Turkish while take ids
and timecodes stay untranslated.

Those tests were reading the machine's locale through Node's `navigator.language`, so on a
Turkish Windows they asserted against Turkish text and failed. They now pin the locale,
which is the same hermeticity lesson the API tests learned from the demo seed.

`test_web.mjs`, 128 tests: injection discipline, the `el()` contract, `noopener` on external
links, store timeline operations including reordering and its clamping, word picking and
what a pick resolves to, the vocabulary slice merging rather than replacing, `getState`
returning a copy, subscription lifecycle, one subscriber's failure not taking down the
others, and the i18n catalogue contract in both directions.

The word-picking tests pin the awkward cases: extending backwards widens instead of
inverting, extending across two lines moves the pick because a cut cannot span two
recordings, clicking the one picked word clears it so there is always a way out, an index
past the end is refused rather than producing a broken range, and a picked range carries the
candidate's provenance since the word rows do not have any.

`test_ui.mjs`, 215 tests: `createUI` and `render()` driven against a fake DOM across empty,
candidates, cut, playing, rendered, failed, assistant-off and Turkish states. Serving the
file says nothing about whether it renders, and this interface is built entirely in JS, so
a typo in a node name would otherwise surface in a demo rather than a test run. It also
pins the decisions: that no invented metric came back, that elapsed time is measured over
the cut and not the source file, that the highlight is not fooled by case or by a phrase
that is absent, that reordering works from the keyboard and not only from a pointer, that
whitespace alone does not spend a message from the assistant quota, and that take ids,
source filenames and timecodes are identical in both languages.

The vocabulary tests pin what the panel promises rather than how it looks: connective words
are kept, a library with no repetition renders without dividing by zero, a chip click reaches
the same search handler that typing would, the count is shown only when it means something,
a scope the take list does not contain is still reported instead of silently falling back to
the first option, truncation is admitted, and the chips are not rebuilt on an offset-only
render.

The scrubber assertion was the one failure on the first run, and it was the test's
arithmetic rather than the code's.

The word-token failure was more useful. The fake DOM fired clicks without modifier flags,
so `event.shiftKey` read as `undefined` and the assertion about "no extend" saw `{}`
instead of `{extend: false}`. A real MouseEvent always carries those flags, so the fake now
does too — and `ui.js` coerces, which makes the handler contract a boolean either way.

The WebMCP side is tested against a fake `modelContext`: registration of the five tools,
name pattern, schemas, `readOnlyHint` flags, every `execute` path, the unknown-candidate
error telling the agent where ids come from, `get_timeline_state` reflecting a segment the
human removed, the description changing when credits run out, no duplicates after
re-registration, and graceful fallback in a browser with no WebMCP.

Those tests caught two real bugs: `sync()` returned without waiting for an in-flight
sync, so `await sync()` did not guarantee a settled registry, and the subscriber triggered
re-registration on every state change.

## What has to be verified by hand in a browser

`test_ui.mjs` proves the interface renders and that the wiring reaches the right handler.
What it cannot prove is that it **looks** right, and no fake DOM implements media playback
— `currentTime` does not advance and `play()` does nothing. So these need a browser:

1. Play the proposal: do the segments run in order, is there a gap or a click at the joins
2. Cut points: does it slice through the middle of a word
3. Is the active block highlighted, does the scrubber and the counter advance
4. Does "open the source" open the right range
5. Does Preview play one segment and stop
6. Does the approval dialog look and read as expected in a real shadow DOM
7. Does the download link work after a render
8. Layout and colour at each breakpoint, and does the self-hosted font actually load
9. Do the story-track thumbnails show a frame on video takes
10. Does drag-to-reorder feel right, and does the keyboard path work with a screen reader
11. Does the vocabulary read as a cloud, and does clicking a chip land on real candidates
12. Upload a recording: does the vocabulary scope itself to the new take without being asked
