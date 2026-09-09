/**
 * Frontend tests. No dependencies, plain node.
 *
 *   node web/test_web.mjs
 *
 * Anything that genuinely needs a browser (the rAF loop, the double-buffer
 * handoff, media seeking) is NOT here -- jsdom does not implement media
 * playback, currentTime never advances. Those get verified by hand. What is
 * here is pure logic and static discipline.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
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

function sourceFiles() {
  const dir = join(here, "src");
  return readdirSync(dir)
    .filter((name) => name.endsWith(".js"))
    .map((name) => ({ name, body: readFileSync(join(dir, name), "utf8") }));
}

// --- Static discipline -------------------------------------------------------
// On the last project the approval dialog was built with innerHTML and a poisoned
// tool name could press its own Approve button. This test stops that class of bug
// from coming back: the text here originates from Whisper output and file names,
// i.e. data we do not control.
console.log("=== HTML injection discipline ===");
{
  // Strip comments first: these files EXPLAIN why they avoid innerHTML, so the
  // word itself shows up in prose. What we are looking for is real usage.
  const stripComments = (body) =>
    body
      .replace(/\/\*[\s\S]*?\*\//g, "")   // block comments
      .replace(/(^|[^:"'`\w])\/\/.*$/gm, "$1"); // line comments, but not http://

  // Matched with the dot: dangerous usage is always `something.innerHTML`.
  const banned = [
    /\.(innerHTML|outerHTML|insertAdjacentHTML)\b/,
    /document\s*\.\s*write\s*\(/,
  ];

  for (const { name, body } of sourceFiles()) {
    const code = stripComments(body);
    const found = banned.filter((pattern) => pattern.test(code)).map(String);
    checkThat(`${name} does no HTML injection`, found.length === 0, `found: ${found}`);
  }

  // Prove the test actually measures something: deliberately bad input must trip it
  checkThat(
    "the check catches real usage",
    banned.some((pattern) => pattern.test('node.innerHTML = data')),
    "the check does not validate itself"
  );
  checkThat(
    "the check ignores comments",
    !banned.some((pattern) => pattern.test(stripComments("// never innerHTML"))),
    "comments produce false positives"
  );

  const ui = sourceFiles().find((file) => file.name === "ui.js").body;
  checkThat(
    "the el() helper uses textContent",
    ui.includes("node.textContent = value"),
    "el() must write text through textContent"
  );
  checkThat(
    "external links carry noopener",
    ui.includes('rel: "noopener noreferrer"'),
    "target=_blank links must not open without noopener"
  );
}

// --- store.js pure logic -----------------------------------------------------
console.log("\n=== store ===");
{
  // structuredClone is global as of node 24
  const store = await import("./src/store.js");

  const candidate = (id, start, end, extra = {}) => ({
    id,
    take_id: id.split(":")[0],
    line_id: 1,
    start_ms: start,
    end_ms: end,
    text: "I never asked for this",
    media_url: `/media/${id.split(":")[0]}.wav`,
    source_url: `${id.split(":")[0]}.wav`,
    ...extra,
  });

  check("timeline starts empty", store.getState().timeline, []);

  store.appendToTimeline(candidate("S01_T01:1:0", 0, 820));
  store.appendToTimeline(candidate("S01_T03:1:0", 0, 1340));
  check("two segments appended", store.getState().timeline.length, 2);
  check("total duration", store.timelineDurationMs(), 820 + 1340);

  const first = store.getState().timeline[0];
  check("duration derived", first.duration_ms, 820);
  check("tone default", first.tone, "neutral");

  store.removeFromTimeline(0);
  check("segment removed", store.getState().timeline.length, 1);
  check("the right segment survived", store.getState().timeline[0].take_id, "S01_T03");

  // What the propose_cut tool does: replace the whole timeline
  store.setTimeline([candidate("S01_T05:1:0", 100, 1300)]);
  check("proposal replaced the timeline", store.getState().timeline.length, 1);
  check("proposal kept the right segment", store.getState().timeline[0].take_id, "S01_T05");

  // getState must hand back a copy, otherwise outside mutation silently corrupts the store
  const snapshot = store.getState();
  snapshot.timeline.push(candidate("HACK:1:0", 0, 1));
  check("getState returns a copy", store.getState().timeline.length, 1);

  let notified = 0;
  const unsubscribe = store.subscribe(() => (notified += 1));
  store.setStatus("ok", "test");
  check("subscriber was notified", notified, 1);
  unsubscribe();
  store.setStatus("idle", "");
  check("unsubscribe took effect", notified, 1);

  // One subscriber throwing must not take the others down
  let second = 0;
  const offBad = store.subscribe(() => {
    throw new Error("broken subscriber");
  });
  const offGood = store.subscribe(() => (second += 1));
  store.setStatus("ok", "after the throw");
  check("a broken subscriber does not block the next one", second, 1);
  offBad();
  offGood();

  store.clearTimeline();
  check("cleared", store.getState().timeline, []);

  // --- Applying an assistant result ---
  // The in-page chat and the WebMCP tools write to the same timeline. Because the
  // rule lives in one place in the store, the two entry points cannot drift apart.
  console.log("\n=== assistant result ===");
  const agentCandidates = [
    candidate("S01_T03:1:0", 0, 1340),
    candidate("S01_T01:1:0", 0, 820),
  ];

  const applied = store.applyAgentResult({
    candidates: agentCandidates,
    proposal: ["S01_T01:1:0", "S01_T03:1:0"],
  });
  check("candidates stored", applied.candidates, 2);
  check("proposal applied", applied.segments, 2);
  check("no ids dropped", applied.dropped, []);
  check("the order the agent gave was kept", store.getState().timeline[0].take_id, "S01_T01");

  // An unresolvable id must not be swallowed
  const partial = store.applyAgentResult({
    candidates: agentCandidates,
    proposal: ["S01_T03:1:0", "ghost:9:9"],
  });
  check("unresolvable id reported", partial.dropped, ["ghost:9:9"]);
  check("the resolvable ones were still applied", store.getState().timeline.length, 1);

  // With no proposal the timeline must be left alone
  store.setTimeline(agentCandidates);
  store.applyAgentResult({ candidates: agentCandidates, proposal: [] });
  check("a reply without a proposal left the timeline intact", store.getState().timeline.length, 2);

  store.clearTimeline();
  store.patch({ candidates: [] });

  console.log("\n=== chat state ===");
  store.setChat({ available: false, reason: "no api key" });
  check("chat disabled", store.getState().chat.available, false);
  store.appendChatMessage({ role: "user", text: "hello" });
  store.appendChatMessage({ role: "agent", text: "found it", toolCalls: [{ name: "find_line" }] });
  check("two messages accumulated", store.getState().chat.messages.length, 2);
  check(
    "tool trace preserved",
    store.getState().chat.messages[1].toolCalls[0].name,
    "find_line"
  );
  store.setChat({ messages: [] });
}

// --- WebMCP tools ----------------------------------------------------------
// Tested against a fake modelContext. The surface verified on the last project:
// registerTool(tool, {signal}), a second registration under the same name is
// rejected, aborting the signal drops the registration. There is no `updateTool`
// API.
console.log("\n=== WebMCP tools ===");
{
  const { installTools } = await import("./src/webmcp.js");
  const store = await import("./src/store.js");
  store.clearTimeline();

  function fakeModelContext() {
    const tools = new Map();
    const rejected = [];
    return {
      tools,
      rejected,
      async registerTool(tool, { signal } = {}) {
        if (tools.has(tool.name)) {
          const error = new Error(`Tool '${tool.name}' is already registered.`);
          rejected.push(tool.name);
          throw error;
        }
        tools.set(tool.name, tool);
        signal?.addEventListener("abort", () => tools.delete(tool.name), { once: true });
      },
      async getTools() {
        return [...tools.values()].map(({ execute, ...rest }) => rest);
      },
    };
  }

  const searchCalls = [];
  const candidate = (id, tone, start, end) => ({
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
    text: "I never asked for this",
    source_url: `${id.split(":")[0]}.wav`,
    media_url: `/media/${id.split(":")[0]}.wav`,
  });

  const pool = [
    candidate("S01_T03:1:0", "calm", 0, 1340),
    candidate("S01_T01:1:0", "tense", 0, 820),
  ];

  const actions = {
    async search({ phrase, tone }) {
      searchCalls.push({ phrase, tone });
      const found = tone ? pool.filter((item) => item.tone === tone) : pool;
      store.patch({ candidates: found, query: { phrase, tone } });
      return { phrase, tone: tone || null, total: found.length, candidates: found };
    },
    propose(candidates) {
      return store.setTimeline(candidates);
    },
    async play() {
      return true;
    },
    async preview() {
      return true;
    },
    async render() {
      return { job_id: "job-1" };
    },
  };

  store.patch({
    session: {
      role: "guest",
      credits: 10,
      costs: { find_line: 0, propose_cut: 0, preview_segment: 0, commit_render: 1 },
    },
  });

  const context = fakeModelContext();
  const install = await installTools({ actions, store, modelContext: context });

  check("five tools registered", install.registered.length, 5);
  check(
    "tool names",
    [...context.tools.keys()].sort(),
    ["commit_render", "find_line", "get_timeline_state", "preview_segment", "propose_cut"]
  );
  check("no registration rejected", context.rejected, []);
  check("store state updated", store.getState().webmcp, { available: true, registered: 5 });

  const namePattern = /^[A-Za-z0-9_.-]{1,128}$/;
  checkThat(
    "names match the spec pattern",
    [...context.tools.keys()].every((name) => namePattern.test(name))
  );
  checkThat(
    "every tool carries a description and a schema",
    [...context.tools.values()].every(
      (tool) =>
        typeof tool.description === "string" &&
        tool.description.trim().length > 20 &&
        tool.inputSchema?.type === "object" &&
        typeof tool.execute === "function"
    )
  );
  check(
    "read-only tools are flagged",
    ["find_line", "get_timeline_state"].map(
      (name) => context.tools.get(name).annotations.readOnlyHint
    ),
    [true, true]
  );
  check(
    "writing tools are not flagged read-only",
    ["propose_cut", "commit_render"].map(
      (name) => context.tools.get(name).annotations.readOnlyHint
    ),
    [false, false]
  );

  // --- execute paths ---
  const found = await context.tools.get("find_line").execute({ phrase: "I never asked for this" });
  check("find_line ran the search", searchCalls.at(-1), {
    phrase: "I never asked for this",
    tone: "",
  });
  check("find_line returned two candidates", found.candidates.length, 2);
  checkThat("find_line has a summary sentence", found.summary.includes("2 take(s)"), found.summary);
  checkThat(
    "candidate carries provenance",
    found.candidates[0].source === "S01_T03.wav",
    JSON.stringify(found.candidates[0])
  );

  const toneFiltered = await context.tools
    .get("find_line")
    .execute({ phrase: "I never asked for this", tone: "calm" });
  check("tone filter narrowed to one candidate", toneFiltered.candidates.length, 1);

  // Restore the candidate pool
  await context.tools.get("find_line").execute({ phrase: "I never asked for this" });

  const proposed = await context.tools
    .get("propose_cut")
    .execute({ candidate_ids: ["S01_T03:1:0", "S01_T01:1:0"] });
  check("propose_cut placed two segments", proposed.segments.length, 2);
  check("propose_cut did not render", proposed.rendered, false);
  check("timeline written to the store", store.getState().timeline.length, 2);
  check("order preserved", store.getState().timeline[0].take_id, "S01_T03");
  check("total duration", proposed.total_duration_ms, 1340 + 820);

  // An unknown id must not be swallowed; the agent needs to be told what to do
  let proposeError = null;
  try {
    await context.tools.get("propose_cut").execute({ candidate_ids: ["missing:1:0"] });
  } catch (error) {
    proposeError = error.message;
  }
  checkThat("unknown id rejected", proposeError !== null);
  checkThat(
    "the error message points somewhere",
    proposeError?.includes("find_line") && proposeError?.includes("missing:1:0"),
    proposeError
  );

  let emptyError = null;
  try {
    await context.tools.get("propose_cut").execute({ candidate_ids: [] });
  } catch (error) {
    emptyError = error.message;
  }
  checkThat("empty list rejected", emptyError?.includes("at least one"), emptyError);

  // get_timeline_state must see the human's edit -- the proof the HITL loop closes
  store.removeFromTimeline(0);
  const readBack = await context.tools.get("get_timeline_state").execute();
  check("the segment the human removed is reflected", readBack.segments.length, 1);
  check("the right segment remains", readBack.segments[0].take, "S01_T01");
  check("still not rendered", readBack.rendered, false);

  const previewed = await context.tools
    .get("preview_segment")
    .execute({ candidate_id: "S01_T03:1:0" });
  checkThat("preview does not render", previewed.summary.includes("Nothing was rendered"), previewed.summary);

  // --- Description updates that follow state ---
  // When credits run out the agent should learn that before calling.
  const before = context.tools.get("commit_render").description;
  checkThat("available while credits remain", before.includes("10 left"), before);

  store.patch({
    session: { ...store.getState().session, credits: 0 },
  });
  await install.sync();

  const after = context.tools.get("commit_render").description;
  checkThat("says UNAVAILABLE once credits run out", after.includes("UNAVAILABLE"), after);
  checkThat(
    "still says search keeps working",
    after.includes("cost nothing"),
    after
  );
  check("re-registration does not duplicate", context.tools.size, 5);
  check("no duplicate registration attempted", context.rejected, []);
  checkThat(
    "free tools untouched",
    !context.tools.get("find_line").description.includes("UNAVAILABLE")
  );

  // --- Browser without WebMCP ---
  const withoutContext = await installTools({ actions, store, modelContext: null });
  check("does not blow up without WebMCP", withoutContext.available, false);
  check("state flagged unavailable", store.getState().webmcp.available, false);
}

// --- i18n -------------------------------------------------------------------
console.log("\n=== i18n ===");
{
  const i18n = await import("./src/i18n.js");
  i18n.setLocale("en");

  check("locales", i18n.LOCALES.sort(), ["en", "tr"]);

  // A missing key is not fatal (it falls back to English) but someone reading
  // Turkish then sees one English label in the middle of the page. Nobody
  // notices that; a judge does.
  for (const locale of i18n.LOCALES) {
    check(`${locale}: no missing keys`, i18n.missingKeys(locale), []);
    check(`${locale}: no stray keys`, i18n.strayKeys(locale), []);
  }

  check("English default reads back", i18n.t("timeline.stop"), "Stop");
  check("interpolation", i18n.t("field.camera", { value: "B" }), "cam B");
  check(
    "multiple parameters",
    i18n.t("approve.cost", { cost: 1, before: 10, after: 9 }),
    "1 credit will be spent. Balance 10 \u2192 9."
  );
  check("a missing parameter leaves the placeholder", i18n.t("field.camera"), "cam {value}");
  check("duration unit", i18n.seconds(1340), "1.34 s");
  check("tone label", i18n.toneLabel("whisper"), "whisper");
  check("unknown tone passes through", i18n.toneLabel("sarcastic"), "sarcastic");

  i18n.setLocale("tr");
  check("locale changed", i18n.getLocale(), "tr");
  check("Turkish text", i18n.t("timeline.stop"), "Durdur");
  check("Turkish unit", i18n.seconds(1340), "1.34 sn");
  check("Turkish tone", i18n.toneLabel("whisper"), "fısıltı");

  let notified = null;
  const off = i18n.onLocaleChange((next) => (notified = next));
  i18n.setLocale("en");
  check("locale change notified", notified, "en");

  // Switching to the same locale must not fire, otherwise every render loops
  notified = null;
  i18n.setLocale("en");
  check("same locale fires nothing", notified, null);

  // An unknown locale must be swallowed without disturbing the current one
  i18n.setLocale("de");
  check("unknown locale ignored", i18n.getLocale(), "en");
  off();

  // An unknown key returns the key itself: it stays readable
  check("unknown key", i18n.t("nope.missing"), "nope.missing");
}

const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} tests passed`);
process.exit(passed === results.length ? 0 : 1);
