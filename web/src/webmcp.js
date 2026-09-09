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
function buildTools({ actions, store, costs, session }) {
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
  ];
}

/** Re-registration is needed whenever a description changes. */
function signature(tools) {
  return tools.map((tool) => `${tool.name}:${tool.description}`).join("|");
}

export async function installTools({ actions, store, modelContext = undefined }) {
  const context = modelContext ?? resolveModelContext();

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
    const tools = buildTools({ actions, store, costs, session: state.session });
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
