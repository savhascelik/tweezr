/**
 * The wiring.
 *
 * The `ready` promise is exported because WebMCP registration has to happen AFTER the
 * session and the library are ready. Skipping that on the previous project let a
 * registration race show the agent an empty tool list. The tool descriptions also carry
 * the credit balance, so the right description cannot be produced without the session.
 */

import * as api from "./api.js";
import { confirmRender } from "./approve.js";
import { applyDocumentLocale, onLocaleChange, seconds, t } from "./i18n.js";
import { createPlayer } from "./player.js";
import * as store from "./store.js";
import { createUI } from "./ui.js";
import { installTools } from "./webmcp.js";

const root = document.getElementById("app");

applyDocumentLocale();

const actions = {
  async search({ phrase, tone }) {
    const trimmed = (phrase ?? "").trim();
    if (!trimmed) {
      store.setStatus("warn", t("status.needPhrase"));
      return { candidates: [], total: 0 };
    }

    store.patch({ query: { phrase: trimmed, tone: tone ?? "" } });
    store.setStatus("busy", t("status.searching"));
    try {
      const result = await api.findLine({ phrase: trimmed, tone });
      // A pick from the previous search points at a line that may no longer be shown.
      store.clearSelection();
      store.patch({ candidates: result.candidates, session: result.session });
      store.setStatus(
        result.total ? "ok" : "warn",
        result.total
          ? t("status.found", { count: result.total })
          : t("status.notFound", { phrase: result.phrase })
      );

      // The transcript is only clickable once the words are in. A failure here degrades
      // to the plain matched phrase rather than taking the search down with it.
      loadLines(result.candidates).catch((error) =>
        console.warn("could not load line words", error)
      );
      // The vocabulary follows the delivery filter, so it has to be re-read when the
      // filter changes — and a mood button changes it by running a search. Otherwise a
      // chip would report five occurrences under a filter that excludes all five.
      actions.loadVocabulary().catch(() => {});
      return result;
    } catch (error) {
      store.setStatus("error", error.message);
      throw error;
    }
  },

  add(candidate) {
    const timeline = store.appendToTimeline(candidate);
    store.setStatus(
      "ok",
      t("status.added", { take: candidate.take_id, count: timeline.length })
    );
    return timeline;
  },

  /**
   * Picks a word, and plays exactly that word.
   *
   * Hearing the thing you just clicked is the point: the millisecond boundaries are the
   * product's claim, and the fastest way to believe a claim about audio is to hear it.
   */
  selectWord(candidate, index, { extend = false } = {}) {
    const key = store.lineKey(candidate);
    const selection = store.selectWord(key, index, { extend });
    if (!selection) {
      store.setStatus("idle", "");
      player.stop();
      return null;
    }

    const segment = store.selectionSegment();
    if (!segment) return selection;

    store.setStatus(
      "ok",
      t("status.picked", {
        words: segment.words,
        text: segment.text,
        duration: seconds(segment.duration_ms),
      })
    );
    player.preview(segment);
    return selection;
  },

  /** Adds the picked words rather than the whole match. The namesake operation. */
  addSelection() {
    const segment = store.selectionSegment();
    if (!segment) return null;
    const timeline = store.appendToTimeline(segment);
    store.setStatus(
      "ok",
      t("status.tweezed", {
        text: segment.text,
        take: segment.take_id,
        count: timeline.length,
      })
    );
    store.clearSelection();
    return timeline;
  },

  propose(candidates) {
    const timeline = store.setTimeline(candidates);
    store.setStatus("ok", t("status.proposed", { count: timeline.length }));
    return timeline;
  },

  remove(index) {
    const timeline = store.removeFromTimeline(index);
    store.setStatus("ok", t("status.removed", { count: timeline.length }));
    return timeline;
  },

  /** Order is the edit, and it costs nothing because nothing has been rendered. */
  reorder(from, to) {
    const timeline = store.reorderTimeline(from, to);
    store.setStatus("ok", t("status.reordered", { count: timeline.length }));
    return timeline;
  },

  /**
   * Replaces one block with a different take of the same line, in place.
   *
   * In place matters: the editor chose the order, and swapping a delivery is not a
   * reason to lose it.
   */
  swap(index, candidate) {
    const { timeline } = store.getState();
    if (!timeline[index]) return timeline;
    const next = [...timeline];
    next[index] = candidate;
    const applied = store.setTimeline(next);
    store.setStatus(
      "ok",
      t("status.swapped", { take: candidate.take_id, position: index + 1 })
    );
    return applied;
  },

  /**
   * Jumps to a segment of the cut.
   *
   * The whole timeline is handed to the player with a start index rather than a slice,
   * so the index that comes back still refers to the same list the store holds.
   */
  jump(index) {
    const { timeline } = store.getState();
    if (!timeline.length) return;
    const target = Math.min(Math.max(index, 0), timeline.length - 1);
    store.setPlayback({ playing: true, index: target, offsetMs: 0 });
    player.play(timeline, target);
  },

  clear() {
    store.clearTimeline();
    player.stop();
    store.setStatus("idle", "");
  },

  play() {
    const { timeline } = store.getState();
    if (!timeline.length) {
      store.setStatus("warn", t("status.emptyCut"));
      return;
    }
    store.setPlayback({ playing: true, index: 0, offsetMs: 0 });
    store.setStatus("ok", t("status.playing"));
    player.play(timeline);
  },

  preview(candidate) {
    store.setStatus("ok", t("status.previewing", { take: candidate.take_id }));
    player.preview(candidate);
  },

  stop() {
    player.stop();
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
  },

  /**
   * Reads the library's vocabulary, optionally narrowed to one take.
   *
   * The tone is not a parameter: it is whatever the last search used, read from the store.
   * Two places deciding the filter is how the chip counts and the search results drift
   * apart, and the panel's whole promise is that clicking a chip finds something.
   *
   * `take` left undefined keeps the current scope; passing "" widens it back to the whole
   * library. Distinguishing the two is the difference between "refresh" and "reset".
   */
  async loadVocabulary({ take } = {}) {
    const state = store.getState();
    const scope = take === undefined ? state.vocabulary.scope : take;
    store.setVocabulary({ loading: true, scope, error: "" });
    try {
      const result = await api.vocabulary({ take: scope, tone: state.query.tone });
      return store.setVocabulary({
        loading: false,
        takes: result.takes,
        words: result.words,
        truncated: result.truncated,
      });
    } catch (error) {
      // A failure here costs the shortcut, not the page: the search box still works.
      return store.setVocabulary({ loading: false, words: [], error: error.message });
    }
  },

  /**
   * Brings the visitor's own recording into the library.
   *
   * Two phases, because they fail differently and take different amounts of time. The
   * upload is network-bound and reports progress; the ingest is CPU-bound on the server
   * and reports a stage. Collapsing them into one spinner would hide which of the two is
   * slow, and on a long clip that is the only question worth answering.
   *
   * On success the library statistics are re-read rather than patched locally, so the take
   * count and the search example come from the same place they always do.
   */
  async upload(file, { label = "", language = "" } = {}) {
    if (!file) return null;

    store.setUpload({
      status: "sending",
      stage: "",
      progress: 0,
      filename: file.name,
      error: "",
      result: null,
    });
    store.setStatus("busy", t("status.uploading", { name: file.name }));

    let queued;
    try {
      queued = await api.uploadMedia(file, {
        label,
        language,
        onProgress: (fraction) => store.setUpload({ progress: fraction }),
      });
    } catch (error) {
      store.setUpload({ status: "failed", error: error.message, progress: 0 });
      store.setStatus("error", error.message);
      throw error;
    }

    store.setUpload({
      status: queued.status,
      stage: queued.stage,
      progress: 1,
      takeId: queued.take_id,
    });
    if (queued.session) store.patch({ session: queued.session });
    store.setStatus(
      "busy",
      t("status.ingesting", { seconds: Math.round(queued.media_seconds) })
    );

    try {
      const job = await api.waitForUpload(queued.job_id);
      if (job.status !== "done") {
        throw new Error(job.error || `The ingest failed (${job.status})`);
      }

      // Re-read rather than patch: the take count and the search example both come from
      // here, and a locally invented number would drift from the real library.
      const library = await api.libraryStats();
      store.patch({ library: library.stats });
      if (job.session) store.patch({ session: job.session });

      // Scoped to the take that just arrived, not merely refreshed. A counter going from
      // 3 to 4 is not an answer to "what is in my footage" — the words are, and in a
      // library of any size they would be lost among everything else.
      actions.loadVocabulary({ take: job.take_id }).catch(() => {});

      store.setUpload({
        status: "done",
        stage: "",
        result: job,
        count: (store.getState().upload.count ?? 0) + 1,
      });
      store.setStatus(
        "ok",
        t("status.ingested", {
          take: job.take_id,
          lines: job.lines,
          language: job.language,
        })
      );
      return job;
    } catch (error) {
      store.setUpload({ status: "failed", error: error.message });
      store.setStatus("error", error.message);
      // The server refunds a charged ingest that failed, so the balance has to be re-read
      // or the interface would keep showing the deducted figure.
      api
        .readSession()
        .then((session) => store.patch({ session: session.session }))
        .catch(() => {});
      throw error;
    }
  },

  /**
   * The in-page assistant. The candidates the agent found and the proposal it placed go
   * through the SAME store operations the WebMCP tools use — so both entry points change
   * one timeline, and the human never has to tell which was used.
   */
  async chat(message) {
    store.appendChatMessage({ role: "user", text: message });
    store.setChat({ busy: true });
    try {
      const result = await api.sendChat(message);
      const applied = store.applyAgentResult(result);
      if (applied.dropped.length) {
        console.warn("assistant returned unresolvable candidate ids", applied.dropped);
      }

      store.appendChatMessage({
        role: "agent",
        text: result.reply || t("assistant.emptyReply"),
        toolCalls: result.tool_calls ?? [],
      });
      store.setChat({ busy: false, messagesLeft: result.messages_left ?? 0 });
      if (result.session) store.patch({ session: result.session });
      return result;
    } catch (error) {
      store.appendChatMessage({ role: "error", text: error.message });
      store.setChat({ busy: false });
      throw error;
    }
  },

  /**
   * The single irreversible step, and the only one that spends credit.
   *
   * Both the agent and the human arrive here, but in both cases the approval is the
   * human's. When the agent calls it, the tool's promise waits on the dialog — the HITL
   * gate made literal.
   */
  async render({ requestedBy = "human" } = {}) {
    const { timeline, session } = store.getState();
    if (!timeline.length) {
      store.setStatus("warn", t("status.emptyCut"));
      return { approved: false, reason: "the timeline is empty" };
    }

    const cost = session?.costs?.commit_render ?? 1;
    const approved = await confirmRender({
      segments: timeline,
      cost,
      credits: session?.credits ?? 0,
      requestedBy,
    });

    if (!approved) {
      store.setStatus("warn", t("status.renderDeclined"));
      return { approved: false, reason: "the editor declined" };
    }

    store.setStatus("busy", t("status.rendering"));
    store.patch({ render: { status: "queued", jobId: null, downloadUrl: null } });
    try {
      const queued = await api.requestRender(timeline);
      store.patch({
        render: { status: queued.status, jobId: queued.job_id, downloadUrl: null },
        session: queued.session ?? session,
      });

      const job = await api.waitForRender(queued.job_id);
      if (job.status !== "done") {
        throw new Error(job.error || `render failed (${job.status})`);
      }

      store.patch({
        render: {
          status: "done",
          jobId: job.job_id,
          downloadUrl: job.download_url,
          mode: job.mode,
        },
      });
      store.setStatus(
        "ok",
        t("status.renderDone", {
          count: job.segments,
          duration: seconds(job.duration_ms),
          charged: queued.charged,
          left: queued.credits_left,
        })
      );
      return { approved: true, ...job, download_url: job.download_url };
    } catch (error) {
      store.patch({ render: { status: "failed", jobId: null, downloadUrl: null } });
      store.setStatus("error", error.message);
      throw error;
    }
  },
};

/**
 * Fetches the words of every line the candidates sit in.
 *
 * Separate from the search rather than folded into `find_line`, for two reasons. The
 * agent's tool result does not need the transcript and would only be padded by it. And
 * this can fail without costing the search: the interface falls back to the matched
 * phrase, which is what it showed before word tweezing existed.
 */
async function loadLines(candidates) {
  if (!candidates?.length) return {};
  const seen = new Set();
  const refs = [];
  for (const candidate of candidates) {
    const key = store.lineKey(candidate);
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push({ take_id: candidate.take_id, line_id: candidate.line_id });
  }
  const result = await api.readLines(refs);
  return store.setLines(
    Object.fromEntries(
      Object.entries(result.lines).map(([key, line]) => [key, line.words])
    )
  );
}

const ui = createUI(root, {
  onSearch: (query) => actions.search(query).catch(() => {}),
  onAdd: actions.add,
  onSelectWord: actions.selectWord,
  onAddSelection: actions.addSelection,
  onClearSelection: () => store.clearSelection(),
  onPreview: actions.preview,
  onRemove: actions.remove,
  onReorder: actions.reorder,
  onSwap: actions.swap,
  onClearTimeline: actions.clear,
  onPlay: actions.play,
  onStop: actions.stop,
  onJump: actions.jump,
  onRender: () => actions.render().catch(() => {}),
  onChat: (message) => actions.chat(message).catch(() => {}),
  onUpload: (file, options) => actions.upload(file, options).catch(() => {}),
  onScope: (take) => actions.loadVocabulary({ take }).catch(() => {}),
});

const player = createPlayer({
  mount: ui.stage,
  onProgress: ({ index, offsetMs }) => store.setPlayback({ index, offsetMs }),
  onSegmentChange: ({ index }) => store.setPlayback({ playing: true, index, offsetMs: 0 }),
  onEnd: () => {
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
    store.setStatus("ok", t("status.playbackDone"));
  },
  onError: (error) => {
    store.setPlayback({ playing: false, index: -1, offsetMs: 0 });
    store.setStatus("error", error.message);
  },
});

store.subscribe((state) => ui.render(state));

// A locale change refreshes all the text. There is no second code path: because `render`
// also writes the static labels, it proceeds exactly like a state change.
onLocaleChange(() => {
  ui.render(store.getState());
  refreshChatStatus().catch(() => {});
});

ui.render(store.getState());

/**
 * Localises why the assistant is off.
 *
 * The server returns a machine-readable `reason_code` and the client produces the text —
 * the server does not know the reader's language and has no business guessing. For a code
 * we do not recognise we fall back to the server's English text rather than showing
 * nothing.
 */
async function refreshChatStatus() {
  const chat = await api.chatStatus();
  const localized = chat.reason_code
    ? t(`assistant.reason.${chat.reason_code}`)
    : chat.reason ?? "";
  store.setChat({
    available: chat.available,
    reason: chat.available ? "" : localized,
    messagesLeft: chat.messages_left ?? 0,
  });
  return chat;
}

/** Resolves once the session, the library AND tool registration are all done. */
export const ready = (async () => {
  try {
    const session = await api.readSession();
    store.patch({ session: session.session });

    const library = await api.libraryStats();
    store.patch({ library: library.stats });

    // The assistant is off without a key. The rest of the product does not depend on it,
    // so even a failure here must not bring startup down.
    try {
      await refreshChatStatus();
    } catch (error) {
      store.setChat({
        available: false,
        reason: t("assistant.statusFailed", { error: error.message }),
      });
    }

    // Same shape: uploads need a transcriber on the server, and if there is none the
    // control says so instead of failing when used.
    try {
      const upload = await api.uploadStatus();
      store.setUpload({
        available: upload.available,
        reason: upload.available
          ? ""
          : upload.reason_code
            ? t(`upload.reason.${upload.reason_code}`)
            : upload.reason,
        limits: upload.limits,
        costPerMinute: upload.cost_per_minute,
        count: upload.uploads ?? 0,
      });
    } catch (error) {
      store.setUpload({ available: false, reason: error.message });
    }

    // The vocabulary is a shortcut into search, not a dependency of it, so a failure here
    // is swallowed inside the action rather than taking startup down.
    await actions.loadVocabulary({ take: "" });

    const webmcp = await installTools({ actions, store });

    store.setStatus(
      "ok",
      webmcp.available
        ? t("status.readyWithTools", {
            takes: library.stats.takes,
            tools: webmcp.registered.length,
          })
        : t("status.readyNoTools", { takes: library.stats.takes })
    );
    return { actions, store, player, webmcp };
  } catch (error) {
    store.setStatus("error", t("status.startFailed", { error: error.message }));
    throw error;
  }
})();

// For the WebMCP registration layer, and for driving this by hand from the console.
window.__cinema = { ready, actions, store, player };
