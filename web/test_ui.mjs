/**
 * Interface tests.
 *
 *   node web/test_ui.mjs
 *
 * ui.js builds the whole page from JS, so "the file is served" says nothing about
 * whether it renders. This drives `createUI` and `render()` against a minimal fake DOM
 * across the states that matter: empty, with candidates, with a cut, playing, rendered,
 * assistant off, Turkish.
 *
 * No jsdom, same reason as test_approve.mjs: the surface ui.js needs is small and known,
 * and a fake makes the assertions about behaviour rather than about a DOM library.
 *
 * What this cannot check is whether it LOOKS right. Layout and colour get looked at in a
 * browser; this file is here so that a typo in a node name fails a test run instead of a
 * demo.
 */

const results = [];

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push(ok);
  console.log(`  ${ok ? "PASS" : "FAIL"}      ${name}`);
  if (!ok) {
    console.log(`               expected: ${JSON.stringify(expected)}`);
    console.log(`               actual  : ${JSON.stringify(actual)}`);
  }
}

function checkThat(name, condition, detail = "") {
  results.push(Boolean(condition));
  console.log(`  ${condition ? "PASS" : "FAIL"}      ${name}`);
  if (!condition && detail) console.log(`               ${detail}`);
}

// --- Minimal fake DOM -------------------------------------------------------

function makeNode(tag, namespace = null) {
  const node = {
    tag,
    namespace,
    children: [],
    attributes: {},
    handlers: new Map(),
    style: {},
    dataset: {},
    textContent: "",
    className: "",
    parent: null,
    hidden: false,
    disabled: false,
    focused: false,

    get lastChild() {
      return this.children.at(-1) ?? null;
    },

    get classes() {
      return new Set(String(this.className).split(/\s+/).filter(Boolean));
    },

    classList: {
      add(...names) {
        const set = node.classes;
        for (const name of names) set.add(name);
        node.className = [...set].join(" ");
      },
      remove(...names) {
        const set = node.classes;
        for (const name of names) set.delete(name);
        node.className = [...set].join(" ");
      },
      contains(name) {
        return node.classes.has(name);
      },
      toggle(name, force) {
        const on = force === undefined ? !node.classes.has(name) : Boolean(force);
        if (on) node.classList.add(name);
        else node.classList.remove(name);
        return on;
      },
    },

    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return this.attributes[name] ?? null;
    },
    appendChild(child) {
      child.parent = this;
      this.children.push(child);
      return child;
    },
    append(...items) {
      for (const item of items) if (item) this.appendChild(item);
    },
    replaceChildren(...items) {
      for (const child of this.children) child.parent = null;
      this.children = [];
      for (const item of items) if (item) this.appendChild(item);
    },
    addEventListener(type, handler) {
      if (!this.handlers.has(type)) this.handlers.set(type, []);
      this.handlers.get(type).push(handler);
    },
    fire(type, event = {}) {
      const base = { preventDefault() {}, stopPropagation() {}, ...event };
      for (const handler of this.handlers.get(type) ?? []) handler(base);
      const inline = this[`on${type}`];
      if (typeof inline === "function") inline(base);
    },
    click() {
      this.fire("click");
    },
    focus() {
      this.focused = true;
      doc.activeElement = this;
    },
    select() {},
  };
  return node;
}

const doc = {
  activeElement: null,
  documentElement: makeNode("html"),
  title: "",
  createElement: (tag) => makeNode(tag),
  createElementNS: (ns, tag) => makeNode(tag, ns),
  createTextNode: (text) => {
    const node = makeNode("#text");
    node.textContent = text;
    return node;
  },
  addEventListener() {},
  removeEventListener() {},
};
doc.body = makeNode("body");

globalThis.document = doc;

// --- Tree helpers -----------------------------------------------------------

function walk(node, visit) {
  if (!node) return;
  visit(node);
  for (const child of node.children ?? []) walk(child, visit);
}

function allText(node) {
  const out = [];
  walk(node, (item) => {
    if (item.textContent) out.push(item.textContent);
  });
  return out.join("\n");
}

function findAll(node, predicate) {
  const found = [];
  walk(node, (item) => {
    if (predicate(item)) found.push(item);
  });
  return found;
}

function byClass(node, name) {
  return findAll(node, (item) => item.classes.has(name));
}

function firstByClass(node, name) {
  return byClass(node, name)[0] ?? null;
}

function buttonWithText(node, text) {
  return findAll(
    node,
    (item) => item.tag === "button" && allText(item).includes(text)
  )[0] ?? null;
}

// --- Fixtures ---------------------------------------------------------------

const { createUI } = await import("./src/ui.js");
const i18n = await import("./src/i18n.js");
i18n.setLocale("en");

const candidate = (id, tone, start, end, text) => ({
  id,
  rank: 1,
  take_id: id.split(":")[0],
  line_id: 1,
  scene: "S01",
  camera: "A",
  speaker: "MAYA",
  tone,
  tone_score: 0.9,
  start_ms: start,
  end_ms: end,
  duration_ms: end - start,
  text,
  source_url: `${id.split(":")[0]}.wav`,
  media_url: `/media/${id.split(":")[0]}.wav`,
});

const CALM = candidate("S01_T03:1:800", "calm", 800, 2700, "I never asked for this");
const TENSE = candidate("S01_T01:1:0", "tense", 0, 820, "I never asked for this");

function segmentOf(item) {
  return {
    ...item,
    duration_ms: item.end_ms - item.start_ms,
  };
}

function baseState(overrides = {}) {
  return {
    session: { role: "guest", credits: 10, costs: { commit_render: 1 } },
    library: { takes: 3, words: 27, vocabulary: 9 },
    query: { phrase: "", tone: "" },
    candidates: [],
    timeline: [],
    playback: { playing: false, index: -1, offsetMs: 0 },
    status: { kind: "idle", message: "" },
    webmcp: { available: false, registered: 0 },
    chat: { available: false, reason: "", messagesLeft: 0, busy: false, messages: [] },
    render: { status: "idle", jobId: null, downloadUrl: null, mode: null },
    ...overrides,
  };
}

function mount() {
  const calls = [];
  const record = (name) => (...args) => calls.push({ name, args });
  const root = makeNode("div");
  const ui = createUI(root, {
    onSearch: record("search"),
    onAdd: record("add"),
    onPreview: record("preview"),
    onRemove: record("remove"),
    onReorder: record("reorder"),
    onSwap: record("swap"),
    onClearTimeline: record("clear"),
    onPlay: record("play"),
    onStop: record("stop"),
    onJump: record("jump"),
    onRender: record("render"),
    onChat: record("chat"),
  });
  return { root, ui, calls };
}

// --- Tests ------------------------------------------------------------------

console.log("=== empty state ===");
{
  const { root, ui } = mount();
  ui.render(baseState());
  const text = allText(root);

  checkThat("the product name is on the page", text.includes("Tweezr"), "");
  checkThat("the hero headline renders", text.includes("tweezing words"), "");
  checkThat("library stats come from the library slice", text.includes("3 takes"), text);
  checkThat("credits come from the session", text.includes("10 credits"), "");
  checkThat("WebMCP absence is stated", text.includes("No WebMCP"), "");
  checkThat(
    "the empty candidate list explains itself",
    text.includes("Search a line"),
    ""
  );

  // Nothing to act on, so the acting buttons are off
  const exportButton = buttonWithText(root, "Export cut");
  checkThat("export is disabled with an empty track", exportButton.disabled);
  checkThat(
    "preview is disabled with an empty track",
    buttonWithText(root, "Preview sequence").disabled
  );

  // The empty slot doubles as the empty state, so there is always a way in
  checkThat("the pluck slot is present", Boolean(buttonWithText(root, "Pluck a line")));
  check("no blocks yet, only the pluck slot", byClass(root, "block").length, 1);

  // No frame, no caption, nothing to source
  check("provenance says the track is empty", firstByClass(root, "provenance-detail").textContent, "Nothing on the track yet.");
  checkThat("the audio-only notice is off", firstByClass(root, "stage-fallback").hidden);
}

console.log("\n=== no invented metrics ===");
{
  // The mockup carried a handful of confident numbers we never measured. An
  // unverifiable figure next to verifiable provenance costs more than it buys, so this
  // asserts they did not survive the port.
  const { root, ui } = mount();
  ui.render(
    baseState({
      candidates: [CALM, TENSE],
      timeline: [segmentOf(CALM)],
      query: { phrase: "I never asked for this", tone: "" },
      webmcp: { available: true, registered: 5 },
    })
  );
  const text = allText(root);
  for (const claim of ["99.4", "4K", "24fps", "Cloud Sync", "Free Trial", "Senate"]) {
    checkThat(`"${claim}" is not on the page`, !text.includes(claim), text.slice(0, 300));
  }
}

console.log("\n=== candidates ===");
{
  const { root, ui, calls } = mount();
  ui.render(
    baseState({
      candidates: [CALM, TENSE],
      query: { phrase: "I never asked for this", tone: "" },
    })
  );

  check("one card per candidate", byClass(root, "take").length, 2);
  const text = allText(root);
  checkThat("the take id is shown", text.includes("S01_T03"), "");
  checkThat("the speaker is shown", text.includes("MAYA"), "");
  checkThat("the delivery is shown", text.includes("calm"), "");
  checkThat("the confidence is shown", text.includes("0.90"), "");
  checkThat("word-level timecode is shown", text.includes("00:00.800"), "");
  check("match count reported", firstByClass(root, "chip-value").textContent, "3 takes · 27 words");

  // The whole line is on screen with the searched phrase marked, which answers
  // "why did this take come back" without a legend
  const hits = byClass(root, "take-hit");
  check("the searched phrase is highlighted once per card", hits.length, 2);
  check("the highlight is the phrase itself", hits[0].textContent, "I never asked for this");

  buttonWithText(root, "Preview").click();
  check("preview reached the handler", calls.at(-1).name, "preview");
  buttonWithText(root, "Add to cut").click();
  check("add reached the handler", calls.at(-1).name, "add");
  check("add carried the candidate", calls.at(-1).args[0].id, "S01_T03:1:800");
}

console.log("\n=== highlight is not fooled ===");
{
  const { root, ui } = mount();
  // Case differs and the phrase is absent: neither must produce a bogus highlight
  ui.render(
    baseState({
      candidates: [candidate("S01_T09:1:0", "calm", 0, 900, "I Never Asked For This")],
      query: { phrase: "i never asked for this", tone: "" },
    })
  );
  check("case-insensitive match still highlights", byClass(root, "take-hit").length, 1);

  const other = mount();
  other.ui.render(
    baseState({
      candidates: [CALM],
      query: { phrase: "something else entirely", tone: "" },
    })
  );
  check("a phrase that is absent highlights nothing", byClass(other.root, "take-hit").length, 0);
  checkThat(
    "the line is still shown in full",
    allText(other.root).includes("I never asked for this")
  );
}

console.log("\n=== delivery filter ===");
{
  const { root, ui, calls } = mount();
  ui.render(baseState({ query: { phrase: "I never asked for this", tone: "" } }));

  const moods = byClass(root, "mood");
  check("one filter per tone plus 'any'", moods.length, 7);
  check("'any' starts selected", moods[0].getAttribute("aria-pressed"), "true");

  const calm = moods.find((button) => button.dataset.tone === "calm");
  calm.click();
  check("picking a delivery re-runs the search", calls.at(-1).name, "search");
  check("the tone went with it", calls.at(-1).args[0].tone, "calm");
  check("the selected filter is marked", calm.getAttribute("aria-pressed"), "true");

  calm.click();
  check("clicking the same filter clears it", calls.at(-1).args[0].tone, "");
}

console.log("\n=== story track ===");
{
  const { root, ui, calls } = mount();
  ui.render(
    baseState({
      candidates: [CALM, TENSE],
      timeline: [segmentOf(CALM), segmentOf(TENSE)],
      query: { phrase: "I never asked for this", tone: "" },
    })
  );

  check("a block per segment plus the pluck slot", byClass(root, "block").length, 3);
  const text = allText(root);
  checkThat("blocks are numbered", text.includes("01") && text.includes("02"), "");
  checkThat("total duration is shown", text.includes("00:02.7"), text);
  checkThat("the assembled count is shown", text.includes("2 assembled"), "");
  checkThat(
    "it says nothing is synthesised",
    text.includes("Nothing is synthesised"),
    ""
  );

  // A thumbnail is a frame from the take at its own start time
  const thumbs = byClass(root, "block-thumb");
  const frame = thumbs[0].children.find((child) => child.tag === "video");
  check("the thumbnail seeks to the segment start", frame.attributes.src, "/media/S01_T03.wav#t=0.800");

  buttonWithText(root, "Export cut").click();
  check("export reached the handler", calls.at(-1).name, "render");
  byClass(root, "block-remove")[0].click();
  check("remove reached the handler", calls.at(-1).name, "remove");
  check("remove carried the index", calls.at(-1).args[0], 0);
}

console.log("\n=== reordering ===");
{
  const { root, ui, calls } = mount();
  ui.render(
    baseState({ timeline: [segmentOf(CALM), segmentOf(TENSE)] })
  );

  // Reordering must not be pointer-only, so the handle takes arrow keys
  const grips = byClass(root, "block-grip");
  grips[1].fire("keydown", { key: "ArrowLeft" });
  check("arrow key reorders", calls.at(-1).name, "reorder");
  check("it moved the block left", calls.at(-1).args, [1, 0]);

  grips[0].fire("keydown", { key: "ArrowRight" });
  check("the other direction works too", calls.at(-1).args, [0, 1]);

  const before = calls.length;
  grips[0].fire("keydown", { key: "a" });
  check("an unrelated key does nothing", calls.length, before);

  // Pointer drag: start on one block, drop on another
  const blocks = byClass(root, "block").filter((block) => block.tag === "li");
  blocks[0].fire("dragstart", {
    dataTransfer: { setData() {}, effectAllowed: "" },
  });
  blocks[1].fire("drop", {});
  check("dragging one block onto another reorders", calls.at(-1).args, [0, 1]);
}

console.log("\n=== playback ===");
{
  const { root, ui, calls } = mount();
  const timeline = [segmentOf(CALM), segmentOf(TENSE)];

  ui.render(baseState({ timeline }));
  buttonWithText(root, "Preview sequence").click();
  check("preview sequence starts playback", calls.at(-1).name, "play");

  // Playing the second segment, 200ms in
  ui.render(
    baseState({
      timeline,
      query: { phrase: "I never asked for this", tone: "" },
      playback: { playing: true, index: 1, offsetMs: 200 },
    })
  );

  const text = allText(root);
  checkThat("the playing block is marked", byClass(root, "is-playing").length > 0, "");
  checkThat("position counts across the whole cut", text.includes("2/2"), text);

  // Elapsed = the first segment in full plus the offset into the second
  const badge = firstByClass(root, "stage-badge");
  check("elapsed is cut time, not file time", badge.lastChild.textContent, "00:02.1 / 00:02.7");
  checkThat("the position badge is live", badge.classes.has("is-live"));
  // 1900ms for the first segment plus 200ms into the second, over 2720ms of cut
  check(
    "the scrubber follows",
    firstByClass(root, "scrubber-fill").style.width,
    `${((1900 + 200) / 2720) * 100}%`
  );

  // Caption shows the line being played, with the searched phrase picked out
  const caption = firstByClass(root, "stage-caption");
  checkThat("the caption shows the current line", allText(caption).includes("I never asked for this"), "");
  check("the caption marks the phrase", byClass(caption, "caption-hit").length, 1);

  // The format badge states the real container, and audio says so
  check("the format badge reports the container", firstByClass(root, "stage-badge-format").textContent, "wav");
  checkThat("audio-only is announced", !firstByClass(root, "stage-fallback").hidden);

  // Provenance is the database's source_url, never a guess
  const provenance = firstByClass(root, "provenance-detail");
  checkThat("provenance names the source file", allText(provenance).includes("S01_T01.wav"), allText(provenance));
  checkThat("provenance carries the timecode", allText(provenance).includes("00:00.000"), "");
  const link = findAll(provenance, (node) => node.tag === "a")[0];
  check("the source link is a media fragment", link.attributes.href, "/media/S01_T01.wav#t=0.000,0.820");
  check("external link is safe", link.attributes.rel, "noopener noreferrer");

  // While playing, the primary button stops rather than pausing: the virtual-splice
  // player seeks across files and has no paused position to resume from
  const toggle = firstByClass(root, "iconbtn-primary");
  check("the toggle offers Stop while playing", toggle.attributes["aria-label"], "Stop");
  toggle.click();
  check("it reached the stop handler", calls.at(-1).name, "stop");

  const [prev, , next] = byClass(root, "iconbtn");
  checkThat("previous is available on segment 2", !prev.disabled);
  checkThat("next is not, this is the last segment", next.disabled);
  prev.click();
  check("previous jumps back one segment", calls.at(-1).args[0], 0);
}

console.log("\n=== the lists are not rebuilt every frame ===");
{
  // render() runs on every store notification, and the rAF loop calls setPlayback about
  // sixty times a second during playback. Rebuilding the story track that often would
  // recreate every block's thumbnail <video> just as often, which is a flood of media
  // loads for a picture that did not change. So both lists carry a signature.
  const { root, ui } = mount();
  const timeline = [segmentOf(CALM), segmentOf(TENSE)];
  const state = baseState({
    candidates: [CALM, TENSE],
    timeline,
    query: { phrase: "I never asked for this", tone: "" },
    playback: { playing: true, index: 0, offsetMs: 0 },
  });

  ui.render(state);
  const blockBefore = byClass(root, "block")[0];
  const takeBefore = byClass(root, "take")[0];
  const thumbBefore = byClass(root, "block-thumb")[0].children.find((c) => c.tag === "video");

  // A frame later: only the offset moved
  ui.render({ ...state, playback: { playing: true, index: 0, offsetMs: 16 } });
  checkThat("an offset-only change does not rebuild a block", byClass(root, "block")[0] === blockBefore);
  checkThat("nor the take cards", byClass(root, "take")[0] === takeBefore);
  checkThat(
    "so the thumbnail is not reloaded",
    byClass(root, "block-thumb")[0].children.find((c) => c.tag === "video") === thumbBefore
  );

  // But the scrubber still follows, because that is a style write not a rebuild
  check("the scrubber still advances", firstByClass(root, "scrubber-fill").style.width, `${(16 / 2720) * 100}%`);

  // Real changes must still rebuild
  ui.render({ ...state, playback: { playing: true, index: 1, offsetMs: 0 } });
  checkThat("moving to the next segment rebuilds", byClass(root, "block")[0] !== blockBefore);

  const reordered = mount();
  reordered.ui.render(state);
  const first = byClass(reordered.root, "block")[0];
  reordered.ui.render({ ...state, timeline: [segmentOf(TENSE), segmentOf(CALM)] });
  checkThat("reordering rebuilds", byClass(reordered.root, "block")[0] !== first);
  check(
    "and in the new order",
    byClass(reordered.root, "block-take")[0].textContent,
    "S01_T01"
  );

  const relocalised = mount();
  relocalised.ui.render(state);
  const label = byClass(relocalised.root, "block-add-label")[0].textContent;
  i18n.setLocale("tr");
  relocalised.ui.render(state);
  checkThat(
    "a language change rebuilds despite the same data",
    byClass(relocalised.root, "block-add-label")[0].textContent !== label,
    byClass(relocalised.root, "block-add-label")[0].textContent
  );
  i18n.setLocale("en");
}

console.log("\n=== alternative take ===");
{
  const { root, ui, calls } = mount();
  // The cut holds the calm take; the tense one is the ranked alternative
  ui.render(
    baseState({
      candidates: [CALM, TENSE],
      timeline: [segmentOf(CALM)],
      query: { phrase: "I never asked for this", tone: "" },
    })
  );

  const alt = firstByClass(root, "alt");
  checkThat("the alternative card is shown", !alt.hidden);
  checkThat(
    "it names the other take and its delivery",
    allText(alt).includes("S01_T01") && allText(alt).includes("tense"),
    allText(alt)
  );

  buttonWithText(alt, "Swap take").click();
  check("swapping reached the handler", calls.at(-1).name, "swap");
  check("it swaps in place, position 0", calls.at(-1).args[0], 0);
  check("with the alternative take", calls.at(-1).args[1].take_id, "S01_T01");

  // With every candidate already in the cut there is no alternative to offer
  const full = mount();
  full.ui.render(
    baseState({
      candidates: [CALM, TENSE],
      timeline: [segmentOf(CALM), segmentOf(TENSE)],
    })
  );
  checkThat("hidden when there is no alternative", firstByClass(full.root, "alt").hidden);
}

console.log("\n=== WebMCP state ===");
{
  const { root, ui } = mount();
  ui.render(baseState({ webmcp: { available: true, registered: 5 } }));
  const chip = firstByClass(root, "chip-webmcp");
  checkThat("the tool count is shown", allText(chip).includes("5 tools"), allText(chip));
  checkThat("the chip reads as on", chip.classes.has("is-on"));

  const off = mount();
  off.ui.render(baseState());
  checkThat("and as off when there are none", !firstByClass(off.root, "chip-webmcp").classes.has("is-on"));
}

console.log("\n=== credits ===");
{
  const { root, ui } = mount();
  ui.render(baseState({ session: { role: "guest", credits: 0, costs: {} } }));
  const chip = firstByClass(root, "chip-credits");
  checkThat("an empty balance is marked", chip.classes.has("is-empty"));
  checkThat("and stated", allText(chip).includes("0 credits"), "");
}

console.log("\n=== assistant ===");
{
  const { root, ui, calls } = mount();
  ui.render(
    baseState({
      chat: {
        available: false,
        reason: "No GEMINI_API_KEY on the server.",
        messagesLeft: 0,
        busy: false,
        messages: [],
      },
    })
  );
  const text = allText(root);
  checkThat("the reason it is off is shown", text.includes("No GEMINI_API_KEY"), "");
  checkThat("the input is disabled", firstByClass(root, "chat-input").disabled);

  const live = mount();
  live.ui.render(
    baseState({
      chat: {
        available: true,
        reason: "",
        messagesLeft: 38,
        busy: false,
        messages: [
          { role: "user", text: "which take is calmer" },
          { role: "agent", text: "S01_T03", toolCalls: [{ name: "find_line" }] },
        ],
      },
    })
  );
  check("both messages render", byClass(live.root, "bubble").length, 2);
  // Which tools the agent called is the most telling part of a demo
  check("the tool trace is shown", firstByClass(live.root, "tool-chip").textContent, "find_line");
  checkThat("the remaining quota is shown", allText(live.root).includes("38 messages left"), "");

  firstByClass(live.root, "chat-input").value = "  find the calm take  ";
  findAll(live.root, (node) => node.classes.has("chat-form"))[0].fire("submit");
  check("asking reached the handler", live.calls.at(-1).name, "chat");
  check("the message was trimmed", live.calls.at(-1).args[0], "find the calm take");

  // Whitespace alone must not spend a message from the quota
  const before = live.calls.length;
  firstByClass(live.root, "chat-input").value = "   ";
  findAll(live.root, (node) => node.classes.has("chat-form"))[0].fire("submit");
  check("an empty message is not sent", live.calls.length, before);
}

console.log("\n=== render job ===");
{
  const { root, ui } = mount();
  ui.render(
    baseState({
      timeline: [segmentOf(CALM)],
      render: { status: "running", jobId: "job-1", downloadUrl: null, mode: null },
    })
  );
  checkThat("a running job is reported", allText(root).includes("Rendering"), "");
  checkThat("export is locked while rendering", buttonWithText(root, "Export cut").disabled);

  const done = mount();
  done.ui.render(
    baseState({
      timeline: [segmentOf(CALM)],
      render: {
        status: "done",
        jobId: "job-1",
        downloadUrl: "/api/render/job-1/file",
        mode: "video",
      },
    })
  );
  const link = findAll(done.root, (node) => node.tag === "a" && node.attributes.download !== undefined)[0];
  check("the finished file is offered", link.attributes.href, "/api/render/job-1/file");
  check("named by its real container", link.textContent, "roughcut.mp4");

  const failed = mount();
  failed.ui.render(
    baseState({
      timeline: [segmentOf(CALM)],
      render: { status: "failed", jobId: null, downloadUrl: null, mode: null },
    })
  );
  checkThat("a failure is not swallowed", allText(failed.root).includes("Render failed"), "");
}

console.log("\n=== status line ===");
{
  const { root, ui } = mount();
  ui.render(baseState({ status: { kind: "error", message: "ClickHouse is down" } }));
  const status = findAll(root, (node) => node.classes.has("status"))[0];
  check("the message is shown", status.textContent, "ClickHouse is down");
  check("and classed by kind", status.className, "status status-error");
}

console.log("\n=== Turkish ===");
{
  // The interface changes language; nothing the agent reads does.
  i18n.setLocale("tr");
  const { root, ui } = mount();
  ui.render(
    baseState({
      candidates: [CALM],
      timeline: [segmentOf(CALM)],
      query: { phrase: "I never asked for this", tone: "" },
      webmcp: { available: true, registered: 5 },
    })
  );
  const text = allText(root);
  checkThat("the headline is translated", text.includes("cımbızlayarak"), text.slice(0, 200));
  checkThat("the track is translated", text.includes("Hikâye şeridi"), "");
  checkThat("the export button is translated", text.includes("Kurguyu çıkart"), "");
  checkThat("the delivery is translated", text.includes("sakin"), "");
  checkThat("credits are translated", text.includes("10 kredi"), "");

  // Data does not translate: the take id, the source file and the timecode are the
  // same string in any language, because they identify a real file.
  checkThat("the take id is unchanged", text.includes("S01_T03"), "");
  checkThat("the source file is unchanged", text.includes("S01_T03.wav"), "");
  checkThat("the timecode is unchanged", text.includes("00:00.800"), "");
  checkThat("the product name is unchanged", text.includes("Tweezr"), "");
  i18n.setLocale("en");
}

console.log("\n=== locale switch ===");
{
  // A locale change is not a special code path: render() writes the static labels too,
  // so it proceeds exactly like a state change.
  const { root, ui } = mount();
  ui.render(baseState());
  checkThat("English first", allText(root).includes("Story track"), "");

  i18n.setLocale("tr");
  ui.render(baseState());
  const text = allText(root);
  checkThat("Turkish after a re-render", text.includes("Hikâye şeridi"), "");
  checkThat("with no English left behind", !text.includes("Story track"), "");

  const picker = findAll(root, (node) => node.classes.has("locale"))[0];
  check("the picker follows the locale", picker.value, "tr");
  i18n.setLocale("en");
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} tests passed`);
process.exit(passed === results.length ? 0 : 1);
