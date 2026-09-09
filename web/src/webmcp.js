/**
 * The WebMCP tools. The product's differentiating layer.
 *
 * In the usual arrangement an agent tells a backend "cut 1.2-2.5 and 15.1-16.3, render
 * it" and an MP4 comes back. If it picked the wrong take you find out after the render
 * finishes, because the backend is a closed box. Here the tools run in the page itself,
 * so the proposal appears on the editor's screen, over the real footage, with the source
 * of every fragment visible. The decision happens BEFORE the render.
 *
 * The API surface was exercised in a real ChatGPT in-app browser run on the previous
 * project:
 *
 *   document.modelContext.registerTool(tool, { signal })   // native may live on
 *                                                          // navigator only
 *   document.modelContext.getTools()                       // without execute
 *   document.modelContext.executeTool(toolObject, input)   // the object, NOT a name
 *   modelContext.addEventListener("toolchange", ...)
 *
 * Tool names: 1-128 characters of [A-Za-z0-9_.-].
 *
 * `execute` returns a plain JSON object, which is the path that was verified (the
 * polyfill stringifies it). Native may expect MCP's `content` wrapper instead, so every
 * reply carries a human-readable `summary` field: whatever the transport does, the agent
 * still sees a sentence.
 */

import * as defaultApi from "./api.js";

const TOOL_NAME_PATTERN = /^[A-Za-z0-9_.-]+$/;

function resolveModelContext() {
  // The spec says document.modelContext, but native support may live on navigator only.
  // Try both, then mirror whatever we find to where the spec puts it.
  const context =
    (typeof document !== "undefined" && document.modelContext) ||
    (typeof navigator !== "undefined" && navigator.modelContext) ||
    null;

  if (context && typeof document !== "undefined" && !document.modelContext) {
    document.modelContext = context;
  }
  return context;
}

/** A description suffix that tracks the credit state, so the agent learns before it calls. */
function creditNote(session, cost) {
  if (!cost) return " Costs no credits.";
  if (!session) return ` Costs ${cost} credit.`;
  if (session.credits < cost) {
    return (
      ` UNAVAILABLE RIGHT NOW: this session has ${session.credits} credits and this ` +
      `needs ${cost}. Searching, proposing and previewing still work and cost nothing.`
    );
  }
  return ` Costs ${cost} credit; ${session.credits} left.`;
}

function summarizeCandidate(candidate) {
  return {
    id: candidate.id,
    take: candidate.take_id,
    scene: candidate.scene,
    camera: candidate.camera,
    speaker: candidate.speaker,
    tone: candidate.tone,
    tone_score: candidate.tone_score,
    start_ms: candidate.start_ms,
    end_ms: candidate.end_ms,
    duration_ms: candidate.duration_ms,
    text: candidate.text,
    // provenance: the agent should see the source too, not only the human
    source: candidate.source_url,
  };
}

/**
 * Builds the tool definitions from the current state.
 * The descriptions are the agent's only manual, so they describe WHEN to reach for each
 * tool rather than what it does.
 */
function buildTools({ actions, store, api = defaultApi, costs, session }) {
  return [
    {
      name: "find_line",
      description:
        "Search the editing library for a spoken line and return every take where it " +
        "occurs, best first. Each match carries its take, camera, speaker, vocal tone " +
        "with a confidence score, exact millisecond range and source recording. Use the " +
        "tone filter when the ask is about delivery, for example a calmer or more tense " +
        "reading. Results also appear on the editor's screen. Call this before proposing " +
        "a cut." +
        creditNote(session, costs.find_line),
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          phrase: {
            type: "string",
            description: "The spoken words to look for, for example 'I never asked for this'.",
          },
          tone: {
            type: "string",
            enum: ["neutral", "calm", "tense", "angry", "whisper", "shouted"],
            description: "Optional. Keep only takes delivered in this tone.",
          },
        },
        required: ["phrase"],
      },
      execute: async ({ phrase, tone = "" } = {}) => {
        const result = await actions.search({ phrase, tone });
        const candidates = (result.candidates ?? []).map(summarizeCandidate);
        return {
          summary: candidates.length
            ? `${candidates.length} take(s) contain "${result.phrase}"` +
              (tone ? ` delivered ${tone}` : "") +
              `. Best match: ${candidates[0].take} (${candidates[0].tone}).`
            : `No take in the library contains "${result.phrase}"` +
              (tone ? ` delivered ${tone}` : "") + ".",
          phrase: result.phrase,
          tone: result.tone,
          total: result.total ?? candidates.length,
          candidates,
        };
      },
    },

    {
      name: "propose_cut",
      description:
        "Put a proposed rough cut on the editor's timeline, in the order you give, using " +
        "candidate ids from find_line. Nothing is rendered and nothing is spent: the " +
        "editor sees your proposal over the real footage with the source and timecode of " +
        "every fragment, and can reorder, drop or reject it. This is how you show a cut " +
        "for approval instead of producing a file and hoping it was right." +
        creditNote(session, costs.propose_cut),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          candidate_ids: {
            type: "array",
            items: { type: "string" },
            description:
              "Candidate ids from find_line, in the order they should play. " +
              "Repeating an id is allowed.",
          },
        },
        required: ["candidate_ids"],
      },
      execute: async ({ candidate_ids = [] } = {}) => {
        if (!Array.isArray(candidate_ids) || candidate_ids.length === 0) {
          throw new Error(
            "propose_cut needs at least one candidate id. Call find_line first and pass the ids you want."
          );
        }

        const { candidates } = store.getState();
        const known = new Map(candidates.map((candidate) => [candidate.id, candidate]));
        const missing = candidate_ids.filter((id) => !known.has(id));
        if (missing.length) {
          throw new Error(
            `Unknown candidate id(s): ${missing.join(", ")}. ` +
              `Ids come from the most recent find_line result. Available: ${
                [...known.keys()].join(", ") || "none, call find_line first"
              }`
          );
        }

        const timeline = actions.propose(candidate_ids.map((id) => known.get(id)));
        const totalMs = timeline.reduce((sum, segment) => sum + segment.duration_ms, 0);
        return {
          summary:
            `Proposed a ${timeline.length}-segment cut of ${(totalMs / 1000).toFixed(2)}s ` +
            "on the editor's timeline. Nothing was rendered. The editor can change it " +
            "before you call commit_render, so read get_timeline_state afterwards.",
          segments: timeline.map(summarizeCandidate),
          total_duration_ms: totalMs,
          rendered: false,
        };
      },
    },

    {
      name: "preview_segment",
      description:
        "Play one candidate out loud in the page so the editor can hear it, or play the " +
        "whole proposed cut. Playback seeks inside the original recordings, so nothing is " +
        "rendered. Note that browsers can refuse playback that no human click started; if " +
        "that happens the result says so and the editor can press play." +
        creditNote(session, costs.preview_segment),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          candidate_id: {
            type: "string",
            description:
              "A candidate id from find_line. Omit to play the whole proposed timeline.",
          },
        },
      },
      execute: async ({ candidate_id = "" } = {}) => {
        const state = store.getState();

        if (!candidate_id) {
          if (!state.timeline.length) {
            throw new Error("The timeline is empty. Call propose_cut first, or pass a candidate_id.");
          }
          await actions.play();
          return {
            summary: `Playing the ${state.timeline.length}-segment proposal. Nothing was rendered.`,
            playing: "timeline",
            segments: state.timeline.length,
          };
        }

        const candidate = state.candidates.find((item) => item.id === candidate_id);
        if (!candidate) {
          throw new Error(
            `Unknown candidate id '${candidate_id}'. Ids come from the most recent find_line result.`
          );
        }
        await actions.preview(candidate);
        return {
          summary:
            `Playing ${candidate.take_id} (${candidate.tone}), ` +
            `${(candidate.duration_ms / 1000).toFixed(2)}s. Nothing was rendered.`,
          playing: candidate.id,
          segment: summarizeCandidate(candidate),
        };
      },
    },

    {
      name: "get_timeline_state",
      description:
        "Read the rough cut currently on the editor's timeline, including any changes the " +
        "editor made after your proposal. Read this before commit_render so you are not " +
        "acting on a proposal the human has already altered." +
        creditNote(session, 0),
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        const state = store.getState();
        const totalMs = state.timeline.reduce((sum, segment) => sum + segment.duration_ms, 0);
        return {
          summary: state.timeline.length
            ? `${state.timeline.length} segments on the timeline, ` +
              `${(totalMs / 1000).toFixed(2)}s total, not rendered.`
            : "The timeline is empty.",
          segments: state.timeline.map(summarizeCandidate),
          total_duration_ms: totalMs,
          credits: state.session?.credits ?? null,
          rendered: false,
        };
      },
    },

    {
      name: "commit_render",
      description:
        "Render the cut currently on the timeline to a file. The editor has to approve " +
        "first: this is the one step that produces output and spends credit, so it is " +
        "deliberately gated. Read get_timeline_state first to confirm you are rendering " +
        "what the editor actually wants." +
        creditNote(session, costs.commit_render),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        const { timeline } = store.getState();
        if (!timeline.length) {
          throw new Error("The timeline is empty. Call propose_cut before commit_render.");
        }

        // requestedBy: "agent" — the dialog prints this, so the human sees who asked for
        // the render. This call WAITS on the dialog: that is the gate.
        const result = await actions.render({ requestedBy: "agent" });

        if (!result?.approved) {
          return {
            summary:
              "The editor did not approve the render. Nothing was produced and no " +
              "credit was spent. The proposal is still on the timeline; ask what they " +
              "want changed rather than calling this again.",
            approved: false,
            rendered: false,
            reason: result?.reason ?? "declined",
          };
        }

        return {
          summary:
            `The editor approved it. Rendered ${result.segments} segments, ` +
            `${(result.duration_ms / 1000).toFixed(2)}s, available at ${result.download_url}.`,
          approved: true,
          rendered: true,
          job_id: result.job_id,
          segments: result.segments,
          duration_ms: result.duration_ms,
          download_url: result.download_url,
        };
      },
    },

    {
      name: "tweeze_words",
      description:
        "Tweeze out a specific sub-phrase or word range from a take with millisecond " +
        "precision and add it to the timeline. This is Tweezr's signature capability: " +
        "instead of taking an entire line, extract only the exact words you need." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          candidate_id: {
            type: "string",
            description: "The candidate id (e.g. 'take1:0:800') or take_id.",
          },
          word_indices: {
            type: "array",
            items: { type: "integer" },
            description: "Optional 0-based word indices to extract from the line.",
          },
          phrase: {
            type: "string",
            description: "Optional sub-phrase to tweeze out of the line.",
          },
        },
        required: ["candidate_id"],
      },
      execute: async ({ candidate_id, word_indices = [], phrase = "" } = {}) => {
        const state = store.getState();
        let candidate =
          state.candidates.find((c) => c.id === candidate_id || c.take_id === candidate_id) ||
          state.timeline.find((c) => c.id === candidate_id || c.take_id === candidate_id);

        if (!candidate) {
          throw new Error(`Unknown candidate '${candidate_id}'. Search with find_line first.`);
        }

        const key = store.lineKey(candidate);
        let words = state.lines[key];
        if (!words && api?.readLines) {
          try {
            const fetched = await api.readLines(candidate.take_id);
            if (fetched?.lines) {
              store.setLines(fetched.lines);
              words = fetched.lines[key] || [];
            }
          } catch (_) {}
        }

        if (!words || !words.length) {
          const timeline = store.appendToTimeline(candidate);
          return {
            summary: `Appended ${candidate.take_id} to timeline (${timeline.length} segments).`,
            segment: summarizeCandidate(candidate),
            timeline_count: timeline.length,
          };
        }

        let startIdx = 0;
        let endIdx = words.length - 1;

        if (Array.isArray(word_indices) && word_indices.length > 0) {
          const sorted = [...word_indices].sort((a, b) => a - b);
          startIdx = Math.max(0, sorted[0]);
          endIdx = Math.min(words.length - 1, sorted.at(-1));
        } else if (phrase) {
          const phraseNorm = phrase.toLowerCase().trim();
          const lineWords = words.map((w) => (w.word || "").toLowerCase());
          const joined = lineWords.join(" ");
          const charPos = joined.indexOf(phraseNorm);
          if (charPos >= 0) {
            const before = joined.slice(0, charPos).trim();
            const beforeCount = before ? before.split(/\s+/).length : 0;
            const matchCount = phraseNorm.split(/\s+/).length;
            startIdx = beforeCount;
            endIdx = Math.min(words.length - 1, startIdx + matchCount - 1);
          }
        }

        const picked = words.slice(startIdx, endIdx + 1);
        if (!picked.length) {
          throw new Error("Could not identify word boundaries for tweezing.");
        }

        const start_ms = picked[0].start_ms;
        const end_ms = picked.at(-1).end_ms;
        const tweezedText = picked.map((w) => w.word).join(" ");

        const segment = {
          ...candidate,
          id: `${candidate.take_id}:${candidate.line_id}:${start_ms}`,
          start_ms,
          end_ms,
          duration_ms: end_ms - start_ms,
          text: tweezedText,
          words: picked.length,
        };

        const timeline = store.appendToTimeline(segment);
        return {
          summary: `Tweezed "${tweezedText}" (${((end_ms - start_ms) / 1000).toFixed(2)}s) from ${candidate.take_id}. ${timeline.length} segments on timeline.`,
          segment: summarizeCandidate(segment),
          timeline_count: timeline.length,
        };
      },
    },

    {
      name: "remove_segment",
      description:
        "Remove a clip from the editor's timeline by its 0-based position index." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          index: {
            type: "integer",
            description: "0-based position index of the clip on the timeline.",
          },
        },
        required: ["index"],
      },
      execute: async ({ index } = {}) => {
        const state = store.getState();
        if (typeof index !== "number" || index < 0 || index >= state.timeline.length) {
          throw new Error(`Invalid timeline index ${index}. Timeline has ${state.timeline.length} segments.`);
        }
        const removed = state.timeline[index];
        const timeline = actions.remove ? actions.remove(index) : store.removeFromTimeline(index);
        return {
          summary: `Removed segment #${index + 1} (${removed.take_id}: "${removed.text || ""}"). ${timeline.length} segments remaining.`,
          remaining_count: timeline.length,
          removed_segment: summarizeCandidate(removed),
        };
      },
    },

    {
      name: "reorder_timeline",
      description:
        "Move a clip from one position to another on the timeline to change story pacing." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          from_index: {
            type: "integer",
            description: "Current 0-based index of the clip.",
          },
          to_index: {
            type: "integer",
            description: "Target 0-based index to place the clip.",
          },
        },
        required: ["from_index", "to_index"],
      },
      execute: async ({ from_index, to_index } = {}) => {
        const state = store.getState();
        if (from_index < 0 || from_index >= state.timeline.length || to_index < 0 || to_index >= state.timeline.length) {
          throw new Error(`Indices out of range. Timeline has ${state.timeline.length} segments.`);
        }
        const timeline = actions.reorder ? actions.reorder(from_index, to_index) : store.reorderTimeline(from_index, to_index);
        return {
          summary: `Moved clip from position ${from_index + 1} to ${to_index + 1}. Timeline has ${timeline.length} segments.`,
          timeline: timeline.map(summarizeCandidate),
        };
      },
    },

    {
      name: "swap_take",
      description:
        "Swap an existing clip on the timeline with another reading/take while preserving position." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          index: {
            type: "integer",
            description: "0-based position index on the timeline to swap.",
          },
          candidate_id: {
            type: "string",
            description: "Candidate id from find_line to place at this position.",
          },
        },
        required: ["index", "candidate_id"],
      },
      execute: async ({ index, candidate_id } = {}) => {
        const state = store.getState();
        if (index < 0 || index >= state.timeline.length) {
          throw new Error(`Invalid timeline index ${index}. Timeline has ${state.timeline.length} segments.`);
        }
        const candidate = state.candidates.find((c) => c.id === candidate_id);
        if (!candidate) {
          throw new Error(`Unknown candidate id '${candidate_id}'. Available candidates come from find_line.`);
        }
        const timeline = actions.swap ? actions.swap(index, candidate) : (() => {
          const next = [...state.timeline];
          next[index] = candidate;
          return store.setTimeline(next);
        })();
        return {
          summary: `Swapped clip #${index + 1} with ${candidate.take_id} (${candidate.tone}).`,
          swapped_segment: summarizeCandidate(candidate),
          timeline: timeline.map(summarizeCandidate),
        };
      },
    },

    {
      name: "clear_timeline",
      description:
        "Clear all clips from the editor's timeline." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        if (actions.clear) actions.clear();
        else store.clearTimeline();
        return {
          summary: "Timeline cleared. 0 segments remaining.",
          segments_count: 0,
        };
      },
    },

    {
      name: "play_timeline",
      description:
        "Play the timeline rough cut in the browser audio player from the start or a given index." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          start_index: {
            type: "integer",
            description: "Optional 0-based clip index to start playback from. Default 0.",
          },
        },
      },
      execute: async ({ start_index = 0 } = {}) => {
        const { timeline } = store.getState();
        if (!timeline.length) {
          throw new Error("Timeline is empty. Propose or add clips before playing.");
        }
        if (actions.jump) actions.jump(start_index);
        else if (actions.play) actions.play();
        return {
          summary: `Playing timeline cut (${timeline.length} segments) from segment #${start_index + 1}.`,
          playing: true,
          start_index,
          total_segments: timeline.length,
        };
      },
    },

    {
      name: "stop_playback",
      description:
        "Stop or pause currently playing audio in the browser." +
        creditNote(session, 0),
      annotations: { readOnlyHint: false, untrustedContentHint: false },
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        if (actions.stop) actions.stop();
        return {
          summary: "Playback stopped.",
          playing: false,
        };
      },
    },

    {
      name: "get_library_stats",
      description:
        "Inspect overall footage inventory: total takes, total spoken lines, total duration." +
        creditNote(session, 0),
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        let stats = null;
        try {
          if (api?.libraryStats) {
            stats = await api.libraryStats();
          }
        } catch {
          // fallback to store
        }
        const s = stats?.stats || store.getState().library || {};
        return {
          summary: `Library contains ${s.takes ?? 0} takes, ${s.lines ?? 0} lines, ` +
            `${((s.duration_seconds ?? 0) / 60).toFixed(1)} minutes of footage across ` +
            `${(s.languages ?? []).join(", ") || "various languages"}.`,
          stats: s,
        };
      },
    },

    {
      name: "get_vocabulary",
      description:
        "Get frequent words spoken across the library or within a specific take." +
        creditNote(session, 0),
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          take: {
            type: "string",
            description: "Optional take_id to restrict vocabulary scope.",
          },
          tone: {
            type: "string",
            description: "Optional tone filter.",
          },
          limit: {
            type: "integer",
            description: "Maximum number of words to return. Default 60.",
          },
        },
      },
      execute: async ({ take = "", tone = "", limit = 60 } = {}) => {
        let result = null;
        try {
          if (api?.vocabulary) {
            result = await api.vocabulary({ take, tone, limit });
          }
        } catch {
          // fallback to store
        }
        const words = result?.words ?? store.getState().vocabulary?.words ?? [];
        const takes = result?.takes ?? store.getState().vocabulary?.takes ?? [];
        return {
          summary: `Retrieved ${words.length} vocabulary words for scope '${take || "all"}'.`,
          take: result?.take ?? take,
          words,
          takes,
          truncated: result?.truncated ?? false,
        };
      },
    },

    {
      name: "get_line_transcript",
      description:
        "Retrieve exact word timings, millisecond intervals, and confidence scores for a take or line." +
        creditNote(session, 0),
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      inputSchema: {
        type: "object",
        properties: {
          take_id: {
            type: "string",
            description: "The take id to inspect lines for.",
          },
          line_id: {
            type: "integer",
            description: "Optional specific line id within the take.",
          },
        },
        required: ["take_id"],
      },
      execute: async ({ take_id, line_id = null } = {}) => {
        let linesData = {};
        try {
          if (api?.readLines) {
            const fetched = await api.readLines(take_id);
            if (fetched?.lines) {
              linesData = fetched.lines;
              store.setLines(linesData);
            }
          }
        } catch {
          // fallback to store
        }
        if (!Object.keys(linesData).length) {
          linesData = store.getState().lines ?? {};
        }

        let entries = Object.entries(linesData).filter(([k]) => k.startsWith(`${take_id}:`));
        if (typeof line_id === "number") {
          const key = `${take_id}:${line_id}`;
          entries = entries.filter(([k]) => k === key);
        }
        const lines = entries.map(([key, words]) => ({
          key,
          take_id,
          line_id: Number(key.split(":")[1]),
          text: (words || []).map((w) => w.word).join(" "),
          start_ms: words?.[0]?.start_ms ?? 0,
          end_ms: words?.at(-1)?.end_ms ?? 0,
          words,
        }));
        return {
          summary: `Retrieved ${lines.length} line transcript(s) for ${take_id}.`,
          take_id,
          lines,
        };
      },
    },
  ];
}

/** Re-registration is needed whenever a description changes. */
function signature(tools) {
  return tools.map((tool) => `${tool.name}:${tool.description}`).join("|");
}

export async function installTools({ actions, store, api = null, modelContext = undefined }) {
  const context = modelContext ?? resolveModelContext();
  const activeApi = api || defaultApi;

  if (!context || typeof context.registerTool !== "function") {
    // We do NOT install a polyfill. Faking agent support produces silently wrong
    // behaviour in a browser that cannot do it. The on-page panel drives the same flow.
    store.patch({ webmcp: { available: false, registered: 0 } });
    return { available: false, registered: [] };
  }

  // Registration state belongs to the install, not the module: at module scope two
  // installTools calls would abort each other's tools.
  const registrations = new Map(); // name -> { controller, description }

  let lastSignature = "";
  let chain = Promise.resolve();

  /**
   * Serialises the syncs.
   *
   * Registration is async, and between the abort and the re-register the tools briefly do
   * not exist. Two overlapping syncs would let the agent see a half-registered list. The
   * chain prevents that and also makes `await sync()` mean the caller is looking at a
   * settled state — the earlier "one is in flight, return immediately" behaviour did not
   * give that guarantee.
   */
  function sync() {
    chain = chain.then(syncOnce, syncOnce);
    return chain;
  }

  async function syncOnce() {
    const state = store.getState();
    const costs = state.session?.costs ?? {
      find_line: 0,
      propose_cut: 0,
      preview_segment: 0,
      commit_render: 1,
    };
    const tools = buildTools({ actions, store, api: activeApi, costs, session: state.session });
    const next = signature(tools);
    if (next === lastSignature) return;

    // Abort the old registration before re-registering: registerTool rejects a second
    // registration under the same name. There is no `updateTool` API.
    for (const { controller } of registrations.values()) controller.abort();
    registrations.clear();

    const registered = [];
    for (const tool of tools) {
      if (!TOOL_NAME_PATTERN.test(tool.name)) {
        console.error(`Invalid tool name, skipped: ${tool.name}`);
        continue;
      }
      const controller = new AbortController();
      try {
        await context.registerTool(tool, { signal: controller.signal });
        registrations.set(tool.name, { controller, description: tool.description });
        registered.push(tool.name);
      } catch (error) {
        // If registration is refused we need the reason. The most common cause is a
        // missing Origin-Agent-Cluster header and a SecurityError.
        console.error(`registerTool('${tool.name}') was refused:`, error);
      }
    }

    lastSignature = next;
    store.patch({ webmcp: { available: true, registered: registered.length } });
    return registered;
  }

  const registered = await sync();

  // The descriptions depend ONLY on the session (credits, prices). Syncing on every state
  // change would mean building five tool definitions about sixty times a second during
  // playback, because the rAF loop calls setPlayback on every frame. So a cheap comparison
  // limits re-registration to when the session actually changes.
  let lastSessionKey = JSON.stringify(store.getState().session ?? null);
  store.subscribe((state) => {
    const key = JSON.stringify(state.session ?? null);
    if (key === lastSessionKey) return;
    lastSessionKey = key;
    sync().catch((error) => console.error("tool update failed", error));
  });

  return { available: true, registered: registered ?? [], sync };
}
