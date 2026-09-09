/**
 * The interface. The screen the editor already works on: search, candidates, timeline,
 * provenance.
 *
 * SECURITY: there is NO innerHTML here, every string is written with textContent.
 * The reason is concrete: on the previous project we built the approval dialog with
 * innerHTML and a poisoned tool name could press its own Approve button. The text here
 * comes from Whisper output and user filenames, which is data we do not control. The
 * `el()` helper deliberately accepts only textContent.
 *
 * All text is written inside `render()` rather than at construction. That way a locale
 * change needs no extra code path: static labels and dynamic text flow through the same
 * place, and the two cannot drift apart.
 */

import { LOCALES, getLocale, seconds, setLocale, t, toneLabel } from "./i18n.js";

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") {
      node.textContent = value; // never innerHTML
    } else if (key === "class") {
      node.className = value;
    } else if (key === "dataset") {
      Object.assign(node.dataset, value);
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value !== null && value !== undefined && value !== false) {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of [].concat(children)) {
    if (child) node.appendChild(child);
  }
  return node;
}

function timecode(ms) {
  const total = Math.max(0, ms);
  const minutes = Math.floor(total / 60000);
  const secs = Math.floor((total % 60000) / 1000);
  const millis = Math.floor(total % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

/** The link to a fragment's source. A media fragment opens exactly that range. */
function sourceHref(segment) {
  const start = (segment.start_ms / 1000).toFixed(3);
  const end = (segment.end_ms / 1000).toFixed(3);
  return `${segment.media_url}#t=${start},${end}`;
}

function toneBadge(tone, score) {
  const label = toneLabel(tone);
  const text = score ? `${label} ${Number(score).toFixed(2)}` : label;
  return el("span", { class: `tone tone-${tone}`, text });
}

export function createUI(root, handlers) {
  const nodes = {};

  // Static labels: node, catalogue key, and which property to write to.
  // applyLabels() refreshes them on every render, which makes a locale change free.
  const localized = [];
  function label(node, key, prop = "textContent") {
    localized.push({ node, key, prop });
    return node;
  }
  function applyLabels() {
    for (const item of localized) item.node[item.prop] = t(item.key);
  }

  // --- Top bar: session, credits, WebMCP state, language ---
  nodes.role = el("span", { class: "meta-value", text: "…" });
  nodes.credits = el("span", { class: "meta-value", text: "…" });
  nodes.library = el("span", { class: "meta-value", text: "…" });
  nodes.webmcp = el("span", { class: "pill pill-off" });

  nodes.locale = el("select", {
    class: "locale",
    "aria-label": "Language",
    onChange: (event) => setLocale(event.target.value),
  });
  for (const code of LOCALES) {
    nodes.locale.appendChild(
      label(el("option", { value: code }), `locale.${code}`)
    );
  }
  nodes.locale.value = getLocale();
  label(nodes.locale, "locale.label", "title");

  const bar = el("header", { class: "bar" }, [
    el("div", { class: "brand" }, [
      label(el("strong"), "app.title"),
      label(el("span", { class: "brand-note" }), "app.tagline"),
    ]),
    el("div", { class: "meta" }, [
      el("span", { class: "meta-item" }, [
        label(el("span", { class: "meta-key" }), "meta.session"),
        nodes.role,
      ]),
      el("span", { class: "meta-item" }, [
        label(el("span", { class: "meta-key" }), "meta.credits"),
        nodes.credits,
      ]),
      el("span", { class: "meta-item" }, [
        label(el("span", { class: "meta-key" }), "meta.library"),
        nodes.library,
      ]),
      nodes.webmcp,
      nodes.locale,
    ]),
  ]);

  // --- Search ---
  // The placeholder is not translated: it is a real line from the demo corpus, so it is
  // language-independent.
  nodes.phrase = el("input", {
    type: "text",
    id: "phrase",
    placeholder: "I never asked for this",
    autocomplete: "off",
  });
  nodes.anyTone = label(el("option", { value: "" }), "search.anyTone");
  nodes.tone = el("select", { id: "tone" }, [
    nodes.anyTone,
    ...["neutral", "calm", "tense", "angry", "whisper", "shouted"].map((value) =>
      label(el("option", { value }), `tone.${value}`)
    ),
  ]);
  nodes.searchButton = label(
    el("button", { type: "submit", class: "primary" }),
    "search.submit"
  );

  const form = el(
    "form",
    {
      class: "search",
      onSubmit: (event) => {
        event.preventDefault();
        handlers.onSearch({ phrase: nodes.phrase.value, tone: nodes.tone.value });
      },
    },
    [
      label(el("label", { for: "phrase" }), "search.phraseLabel"),
      nodes.phrase,
      label(el("label", { for: "tone" }), "search.toneLabel"),
      nodes.tone,
      nodes.searchButton,
    ]
  );

  nodes.status = el("p", { class: "status", text: "" });

  // This panel exists so the same flow can be driven by hand without WebMCP.
  const searchPanel = el("section", { class: "panel" }, [
    label(el("h2"), "search.heading"),
    label(el("p", { class: "hint" }), "search.hint"),
    form,
    nodes.status,
  ]);

  // --- In-page assistant ---
  // Not a replacement for an external agent; for the visitor who has none. Without a key
  // it states the reason and points at the panel rather than quietly disappearing.
  nodes.chatLog = el("div", { class: "chat-log" });
  nodes.chatInput = label(
    el("input", { type: "text", id: "chat", autocomplete: "off" }),
    "assistant.placeholder",
    "placeholder"
  );
  nodes.chatSend = label(
    el("button", { type: "submit", class: "primary" }),
    "assistant.send"
  );
  nodes.chatLeft = el("span", { class: "count", text: "" });
  nodes.chatNote = el("p", { class: "hint", text: "" });
  nodes.chatHeading = label(el("span"), "assistant.heading");

  const chatForm = el(
    "form",
    {
      class: "chat-form",
      onSubmit: (event) => {
        event.preventDefault();
        const message = nodes.chatInput.value.trim();
        if (!message) return;
        nodes.chatInput.value = "";
        handlers.onChat(message);
      },
    },
    [nodes.chatInput, nodes.chatSend]
  );

  nodes.chatPanel = el("section", { class: "panel" }, [
    el("h2", {}, [nodes.chatHeading, document.createTextNode(" "), nodes.chatLeft]),
    nodes.chatNote,
    nodes.chatLog,
    chatForm,
  ]);

  // --- Candidates ---
  nodes.candidates = el("div", { class: "candidates" });
  nodes.candidateCount = el("span", { class: "count", text: "" });
  const candidatePanel = el("section", { class: "panel" }, [
    el("h2", {}, [
      label(el("span"), "candidates.heading"),
      document.createTextNode(" "),
      nodes.candidateCount,
    ]),
    nodes.candidates,
  ]);

  // --- Stage and timeline ---
  nodes.stage = el("div", { class: "stage" });
  nodes.nowPlaying = el("p", { class: "now-playing" });
  nodes.timeline = el("ol", { class: "timeline" });
  nodes.total = el("span", { class: "count", text: "" });

  nodes.playButton = label(
    el("button", { class: "primary", onClick: () => handlers.onPlay() }),
    "timeline.play"
  );
  nodes.stopButton = label(
    el("button", { onClick: () => handlers.onStop() }),
    "timeline.stop"
  );
  nodes.clearButton = label(
    el("button", { onClick: () => handlers.onClearTimeline() }),
    "timeline.clear"
  );
  nodes.renderButton = label(
    el("button", { class: "danger", onClick: () => handlers.onRender() }),
    "timeline.render"
  );
  nodes.renderResult = el("p", { class: "render-result" });

  const timelinePanel = el("section", { class: "panel" }, [
    el("h2", {}, [
      label(el("span"), "timeline.heading"),
      document.createTextNode(" "),
      nodes.total,
    ]),
    nodes.stage,
    nodes.nowPlaying,
    nodes.timeline,
    el("div", { class: "transport" }, [
      nodes.playButton,
      nodes.stopButton,
      nodes.clearButton,
      nodes.renderButton,
    ]),
    nodes.renderResult,
  ]);

  root.append(
    bar,
    el("main", { class: "layout" }, [
      el("div", { class: "column" }, [nodes.chatPanel, searchPanel, candidatePanel]),
      el("div", { class: "column" }, [timelinePanel]),
    ])
  );

  function renderChat(state) {
    const chat = state.chat;
    nodes.chatInput.disabled = !chat.available || chat.busy;
    nodes.chatSend.disabled = !chat.available || chat.busy;
    nodes.chatLeft.textContent = chat.available
      ? t("assistant.messagesLeft", { count: chat.messagesLeft })
      : t("assistant.off");
    nodes.chatNote.textContent = chat.available
      ? t("assistant.hint")
      : chat.reason || t("assistant.offFallback");

    nodes.chatLog.replaceChildren();
    for (const message of chat.messages) {
      const bubble = el("div", { class: `bubble bubble-${message.role}` }, [
        el("p", { class: "bubble-text", text: message.text }),
      ]);
      // Showing which tools the agent called is the most telling part of a demo
      if (message.toolCalls?.length) {
        bubble.appendChild(
          el(
            "div",
            { class: "tool-trace" },
            message.toolCalls.map((call) =>
              el("span", { class: "tool-chip", text: call.name })
            )
          )
        );
      }
      nodes.chatLog.appendChild(bubble);
    }
    if (chat.busy) {
      nodes.chatLog.appendChild(
        el("p", { class: "hint", text: t("assistant.thinking") })
      );
    }
    nodes.chatLog.scrollTop = nodes.chatLog.scrollHeight;
  }

  function renderCandidates(state) {
    nodes.candidates.replaceChildren();
    nodes.candidateCount.textContent = state.candidates.length
      ? `(${state.candidates.length})`
      : "";

    if (!state.candidates.length) {
      nodes.candidates.appendChild(
        el("p", { class: "empty", text: t("candidates.empty") })
      );
      return;
    }

    for (const candidate of state.candidates) {
      const inTimeline = state.timeline.some((segment) => segment.id === candidate.id);
      nodes.candidates.appendChild(
        el("article", { class: "candidate" }, [
          el("div", { class: "candidate-head" }, [
            el("span", { class: "rank", text: `#${candidate.rank}` }),
            el("span", { class: "take", text: candidate.take_id }),
            el("span", {
              class: "dim",
              text: t("field.camera", { value: candidate.camera || "-" }),
            }),
            toneBadge(candidate.tone, candidate.tone_score),
            el("span", { class: "dim", text: seconds(candidate.duration_ms) }),
          ]),
          el("p", { class: "line", text: candidate.text }),
          el("div", { class: "candidate-actions" }, [
            el("button", {
              text: t("candidates.preview"),
              onClick: () => handlers.onPreview(candidate),
            }),
            el("button", {
              class: "primary",
              text: inTimeline ? t("candidates.addAgain") : t("candidates.add"),
              onClick: () => handlers.onAdd(candidate),
            }),
          ]),
        ])
      );
    }
  }

  function renderTimeline(state) {
    nodes.timeline.replaceChildren();
    const total = state.timeline.reduce((sum, segment) => sum + segment.duration_ms, 0);
    nodes.total.textContent = state.timeline.length
      ? t("timeline.summary", {
          count: state.timeline.length,
          duration: seconds(total),
        })
      : "";

    const hasSegments = state.timeline.length > 0;
    nodes.playButton.disabled = !hasSegments;
    nodes.renderButton.disabled = !hasSegments;
    nodes.clearButton.disabled = !hasSegments;

    if (!hasSegments) {
      nodes.timeline.appendChild(
        el("li", { class: "empty" }, [el("p", { text: t("timeline.empty") })])
      );
      return;
    }

    state.timeline.forEach((segment, index) => {
      const playing = state.playback.playing && state.playback.index === index;
      nodes.timeline.appendChild(
        el("li", { class: playing ? "segment is-playing" : "segment" }, [
          el("div", { class: "segment-head" }, [
            el("span", { class: "rank", text: String(index + 1) }),
            el("p", { class: "line", text: segment.text }),
            el("button", {
              class: "ghost",
              title: t("timeline.remove"),
              text: "×",
              onClick: () => handlers.onRemove(index),
            }),
          ]),
          // The provenance strip: every fragment carries where it came from and links
          // back to it. This strip is the concrete form of the product's "sourced
          // assembly, not a deepfake" claim.
          el("div", { class: "provenance" }, [
            el("span", { class: "prov-take", text: segment.take_id }),
            el("span", { class: "dim", text: segment.scene || "-" }),
            el("span", {
              class: "dim",
              text: t("field.camera", { value: segment.camera || "-" }),
            }),
            el("span", { class: "dim", text: segment.speaker || "-" }),
            toneBadge(segment.tone, segment.tone_score),
            el("span", {
              class: "timecode",
              text: `${timecode(segment.start_ms)} → ${timecode(segment.end_ms)}`,
            }),
            el("span", { class: "dim", text: seconds(segment.duration_ms) }),
            el("a", {
              class: "source",
              href: sourceHref(segment),
              target: "_blank",
              rel: "noopener noreferrer",
              text: t("timeline.openSource"),
            }),
          ]),
        ])
      );
    });
  }

  function renderJob(state) {
    const job = state.render ?? { status: "idle" };
    nodes.renderResult.replaceChildren();

    const busy = job.status === "queued" || job.status === "running";
    nodes.renderButton.disabled = nodes.renderButton.disabled || busy;

    if (job.status === "idle") return;

    if (busy) {
      nodes.renderResult.appendChild(
        el("span", { class: "dim", text: t("render.running") })
      );
      return;
    }
    if (job.status === "failed") {
      nodes.renderResult.appendChild(
        el("span", { class: "status-error", text: t("render.failed") })
      );
      return;
    }
    if (job.status === "done" && job.downloadUrl) {
      nodes.renderResult.append(
        el("span", { class: "dim", text: t("render.readyLabel") }),
        el("a", {
          class: "source",
          href: job.downloadUrl,
          // Same origin and a download; we do not open a new tab
          download: "",
          text: t("render.download", {
            format: job.mode === "video" ? "mp4" : "wav",
          }),
        })
      );
    }
  }

  function render(state) {
    applyLabels();
    if (nodes.locale.value !== getLocale()) nodes.locale.value = getLocale();

    if (state.session) {
      nodes.role.textContent = state.session.role;
      nodes.credits.textContent = String(state.session.credits);
    }
    if (state.library) {
      nodes.library.textContent = t("meta.libraryValue", {
        takes: state.library.takes,
        words: state.library.words,
        vocabulary: state.library.vocabulary,
      });
    }

    nodes.webmcp.textContent = state.webmcp.available
      ? t("webmcp.on", { count: state.webmcp.registered })
      : t("webmcp.off");
    nodes.webmcp.className = state.webmcp.available ? "pill pill-on" : "pill pill-off";

    nodes.status.textContent = state.status.message;
    nodes.status.className = `status status-${state.status.kind}`;

    if (nodes.phrase.value !== state.query.phrase) nodes.phrase.value = state.query.phrase;
    if (nodes.tone.value !== state.query.tone) nodes.tone.value = state.query.tone;

    const playing = state.playback.playing;
    nodes.stopButton.disabled = !playing;
    if (playing && state.timeline[state.playback.index]) {
      const segment = state.timeline[state.playback.index];
      nodes.nowPlaying.textContent =
        `${state.playback.index + 1}/${state.timeline.length}  ${segment.take_id}  ` +
        `${timecode(segment.start_ms + state.playback.offsetMs)}`;
    } else {
      nodes.nowPlaying.textContent = t("timeline.notPlaying");
    }

    renderChat(state);
    renderCandidates(state);
    renderTimeline(state);
    renderJob(state);
  }

  return { render, stage: nodes.stage };
}
