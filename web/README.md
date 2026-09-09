# web — the timeline and virtual splicing

The screen the editor works on. No build step: the file being served is the source.

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
| `index.html` | One root. No inline script. |
| `styles.css` | Edit-room palette: dark, low saturation, one accent colour. |
| `src/i18n.js` | Interface strings. English default and fallback, Turkish available. |
| `src/store.js` | Single source of truth. Tools and panel change the same store. |
| `src/api.js` | Server calls. The session cookie is HttpOnly; fetch sends it. |
| `src/player.js` | Virtual-splice player. Double buffered, rAF driven. |
| `src/ui.js` | Rendering. The `el()` helper only accepts `textContent`. |
| `src/webmcp.js` | The five WebMCP tools: registration, state reflection, fallback. |
| `src/approve.js` | Render approval dialog. Closed shadow root. |
| `src/main.js` | Wiring plus the `ready` promise. |
| `test_web.mjs` | Store, WebMCP, injection and i18n tests. |
| `test_approve.mjs` | Approval dialog tests against a fake DOM. |

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

## The provenance strip

Under every segment on the timeline: which take, which scene, which camera, which
speaker, which delivery and which millisecond range. The "open source" link opens exactly
that range through a media fragment (`#t=start,end`).

That strip is more than an editor convenience: it is what makes the product **"I
assembled what they said, with sources"** rather than "I made them say something". The
first is a deepfake tool, the second is journalism. The difference is this strip.

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

## Tests

```powershell
node web\test_web.mjs
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

`test_web.mjs`, 89 tests: injection discipline, the `el()` contract, `noopener` on external
links, store timeline operations, `getState` returning a copy, subscription lifecycle, one
subscriber's failure not taking down the others, and the i18n catalogue contract.

The WebMCP side is tested against a fake `modelContext`: registration of the five tools,
name pattern, schemas, `readOnlyHint` flags, every `execute` path, the unknown-candidate
error telling the agent where ids come from, `get_timeline_state` reflecting a segment the
human removed, the description changing when credits run out, no duplicates after
re-registration, and graceful fallback in a browser with no WebMCP.

Those tests caught two real bugs: `sync()` returned without waiting for an in-flight
sync, so `await sync()` did not guarantee a settled registry, and the subscriber triggered
re-registration on every state change.

## What has to be verified by hand in a browser

`jsdom` does not implement media playback — `currentTime` does not advance and `play()`
does nothing. So these cannot be tested automatically and need a look:

1. Play the proposal: do the segments run in order, is there a gap or a click at the joins
2. Cut points: does it slice through the middle of a word
3. Is the active segment highlighted on the timeline, does the counter advance
4. Does "open source" open the right range
5. Does Preview play one segment and stop
6. Does the approval dialog look and read as expected in a real shadow DOM
7. Does the download link work after a render
