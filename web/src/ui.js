/**
 * The interface.
 *
 * SECURITY: there is NO innerHTML here, every string is written with textContent. The
 * reason is concrete: on the previous project we built the approval dialog with innerHTML
 * and a poisoned tool name could press its own Approve button. The text here comes from
 * Whisper output and user filenames, which is data we do not control. The `el()` helper
 * deliberately accepts only textContent, and `icon()` builds SVG through
 * createElementNS for the same reason.
 *
 * All text is written inside `render()` rather than at construction. That way a locale
 * change needs no extra code path: static labels and dynamic text flow through the same
 * place, and the two cannot drift apart.
 *
 * On the numbers shown: every figure on screen comes from the session, the library
 * statistics or the media itself. The design mockup carried a few confident-looking
 * metrics that we do not measure, and an unverifiable number sitting next to verifiable
 * provenance costs more than it buys.
 */

import { LOCALES, getLocale, seconds, setLocale, t, toneLabel } from "./i18n.js";

const BRAND = "Tweezr"; // a proper noun, so it does not go through the catalogue

const SVG_NS = "http://www.w3.org/2000/svg";

/* Stroke paths on a 24x24 grid. An inline set rather than an icon font: one less
   external request, and it inherits currentColor. */
const ICONS = {
  tweezers: "M9 3l2.4 12.2M15 3l-2.4 12.2M12 15.2l-1.2 5.3h2.4z",
  search: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM20 20l-4.9-4.9",
  sparkle: "M12 3l1.6 4.9 4.9 1.6-4.9 1.6L12 16l-1.6-4.9L5.5 9.5l4.9-1.6z",
  film: "M4 5h16v14H4zM8 5v14M16 5v14M4 12h16",
  coin: "M12 4c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7",
  transcript: "M6 3h9l4 4v14H6zM15 3v4h4M9 12h7M9 16h5",
  play: "M8 5l11 7-11 7z",
  stop: "M6 6h12v12H6z",
  prev: "M18 5l-9 7 9 7zM6 5v14",
  next: "M6 5l9 7-9 7zM18 5v14",
  check: "M4.5 12.5l5 5 10-11",
  verified: "M12 3l2.6 1.9 3.2-.2.9 3.1 2.6 1.9-1.4 2.9 1.4 2.9-2.6 1.9-.9 3.1-3.2-.2L12 22.4l-2.6-1.9-3.2.2-.9-3.1-2.6-1.9 1.4-2.9-1.4-2.9 2.6-1.9.9-3.1 3.2.2z",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z",
  track: "M3 6h11M3 12h18M3 18h8M17 4v4M9 10v4M12 16v4",
  plus: "M12 5v14M5 12h14",
  grip: "M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01",
  close: "M6 6l12 12M18 6L6 18",
  upload: "M12 16V4M7.5 8.5L12 4l4.5 4.5M4 16v3h16v-3",
  wave: "M3 12h2l2-6 2 12 2-16 2 20 2-14 2 8 2-4h2",
  swap: "M4 8h13l-3-3M20 16H7l3 3",
  send: "M4 12l16-8-6 8 6 8z",
};

/* Delivery glyphs. Not translated: an emoji is not language, and the same glyph appears
   on the mood filter, the badge and the thumbnail so the eye can follow one take. */
const TONE_GLYPH = {
  neutral: "\u25CE",
  calm: "\u{1F331}",
  tense: "\u26A1",
  angry: "\u{1F525}",
  whisper: "\u{1F92B}",
  shouted: "\u{1F4E3}",
};

const TONES = ["neutral", "calm", "tense", "angry", "whisper", "shouted"];

/* Fallback for the search example, used only before the library has loaded. The real
   example is a line out of the library itself — a hardcoded English sentence would be a
   misleading hint the moment the footage is in another language. */
const EXAMPLE_FALLBACK = "\u2026";

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

function icon(name, extra = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", extra ? `icon ${extra}` : "icon");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] ?? "");
  svg.appendChild(path);
  return svg;
}

function dot() {
  return el("span", { class: "dot" });
}

/** mm:ss.mmm — the form an editor reads without converting anything. */
function timecode(ms) {
  const total = Math.max(0, ms);
  const minutes = Math.floor(total / 60000);
  const secs = Math.floor((total % 60000) / 1000);
  const millis = Math.floor(total % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

/** mm:ss.s — the shorter form, for the running position badge. */
function shortTimecode(ms) {
  const total = Math.max(0, ms);
  const minutes = Math.floor(total / 60000);
  const secs = (total % 60000) / 1000;
  return `${String(minutes).padStart(2, "0")}:${secs.toFixed(1).padStart(4, "0")}`;
}

/** The link to a fragment's source. A media fragment opens exactly that range. */
function sourceHref(segment) {
  const start = (segment.start_ms / 1000).toFixed(3);
  const end = (segment.end_ms / 1000).toFixed(3);
  return `${segment.media_url}#t=${start},${end}`;
}

/** The container's own extension, so the format badge states what is really playing. */
function mediaFormat(segment) {
  const name = segment?.source_url || segment?.media_url || "";
  const match = /\.([A-Za-z0-9]{2,5})(?:[?#]|$)/.exec(name);
  return match ? match[1].toLowerCase() : "";
}

function toneBadge(tone, score, withScore = true) {
  const glyph = TONE_GLYPH[tone] ?? TONE_GLYPH.neutral;
  const children = [
    el("span", { text: glyph, "aria-hidden": "true" }),
    el("span", { text: toneLabel(tone) }),
  ];
  if (withScore && score) {
    children.push(
      el("span", { class: "tone-score", text: Number(score).toFixed(2) })
    );
  }
  return el("span", { class: `tone tone-${tone}` }, children);
}

/**
 * Whether a word falls inside a range. Used to mark which words the search matched.
 *
 * Midpoint rather than full containment: alignment boundaries and phrase boundaries come
 * from the same rows, so they agree, but a word that merely touches the edge should not
 * count as part of the match.
 */
function wordInRange(word, start_ms, end_ms) {
  const middle = (word.start_ms + word.end_ms) / 2;
  return middle >= start_ms && middle <= end_ms;
}

/**
 * Splits a line so the searched phrase can be emphasised inside it.
 *
 * Three spans instead of one, because the highlight has to survive the no-innerHTML
 * rule. The comparison is case-insensitive on purpose: the phrase is typed by a human
 * and the transcript is whatever Whisper heard.
 */
function highlightParts(text, phrase) {
  const needle = (phrase ?? "").trim();
  if (!needle) return [{ text, hit: false }];
  const at = text.toLowerCase().indexOf(needle.toLowerCase());
  if (at < 0) return [{ text, hit: false }];
  return [
    { text: text.slice(0, at), hit: false },
    { text: text.slice(at, at + needle.length), hit: true },
    { text: text.slice(at + needle.length), hit: false },
  ].filter((part) => part.text.length > 0);
}

export function createUI(root, handlers) {
  const nodes = {};

  // Static labels: node, catalogue key, and which property to write to. applyLabels()
  // refreshes them on every render, which makes a locale change free.
  const localized = [];
  function label(node, key, prop = "textContent") {
    localized.push({ node, key, prop });
    return node;
  }
  function applyLabels() {
    for (const item of localized) item.node[item.prop] = t(item.key);
  }

  /* ================= Top bar ================= */

  nodes.libraryChip = el("span", { class: "chip-value", text: "\u2026" });
  nodes.webmcpChip = el("span", { class: "chip-value" });
  nodes.creditsChip = el("span", { class: "chip-value", text: "\u2026" });

  nodes.webmcpWrap = el("span", { class: "chip chip-webmcp" }, [
    dot(),
    nodes.webmcpChip,
  ]);
  nodes.creditsWrap = el("span", { class: "chip chip-credits" }, [
    icon("coin"),
    nodes.creditsChip,
  ]);

  nodes.locale = el("select", {
    class: "locale",
    onChange: (event) => setLocale(event.target.value),
  });
  for (const code of LOCALES) {
    nodes.locale.appendChild(label(el("option", { value: code }), `locale.${code}`));
  }
  nodes.locale.value = getLocale();
  label(nodes.locale, "locale.label", "title");
  label(nodes.locale, "locale.label", "ariaLabel");

  const topbar = el("header", { class: "topbar" }, [
    el("div", { class: "topbar-inner" }, [
      el("div", { class: "brand" }, [
        el("span", { class: "brand-mark" }, [icon("tweezers")]),
        el("span", { class: "brand-text" }, [
          el("strong", { class: "brand-name", text: BRAND }),
          label(el("span", { class: "brand-note" }), "brand.note"),
        ]),
      ]),
      el("div", { class: "topbar-meta" }, [
        el("span", { class: "chip chip-library" }, [icon("film"), nodes.libraryChip]),
        nodes.webmcpWrap,
        nodes.creditsWrap,
        nodes.locale,
      ]),
    ]),
  ]);

  /* ================= Hero and search ================= */

  nodes.phrase = el("input", {
    type: "text",
    id: "phrase",
    class: "searchbar-input",
    autocomplete: "off",
  });
  label(nodes.phrase, "search.label", "ariaLabel");

  nodes.searchButton = el("button", { type: "submit", class: "btn btn-primary" }, [
    icon("sparkle"),
    label(el("span"), "search.submit"),
  ]);

  const searchForm = el(
    "form",
    {
      class: "searchbar",
      role: "search",
      onSubmit: (event) => {
        event.preventDefault();
        handlers.onSearch({ phrase: nodes.phrase.value, tone: currentTone });
      },
    },
    [
      el("span", { class: "searchbar-icon" }, [icon("search")]),
      nodes.phrase,
      nodes.searchButton,
    ]
  );

  // The delivery filter. These are the tone values the corpus actually carries, so
  // picking one narrows a real query rather than setting a mood.
  let currentTone = "";
  nodes.moods = el("div", { class: "moods", role: "group" });
  label(nodes.moods, "moods.label", "ariaLabel");
  nodes.moods.appendChild(label(el("span", { class: "moods-label" }), "moods.label"));

  nodes.moodButtons = new Map();
  function addMood(value, key, glyph) {
    const button = el("button", {
      type: "button",
      class: "mood",
      dataset: { tone: value },
      onClick: () => {
        currentTone = currentTone === value ? "" : value;
        syncMoods();
        // Re-run the search straight away: a filter that needs a second click to take
        // effect reads as if it did not work.
        if (nodes.phrase.value.trim()) {
          handlers.onSearch({ phrase: nodes.phrase.value, tone: currentTone });
        }
      },
    });
    if (glyph) button.appendChild(el("span", { text: glyph, "aria-hidden": "true" }));
    label(button.appendChild(el("span")), key);
    nodes.moods.appendChild(button);
    nodes.moodButtons.set(value, button);
  }

  addMood("", "moods.any", "");
  for (const tone of TONES) addMood(tone, `tone.${tone}`, TONE_GLYPH[tone]);

  function syncMoods() {
    for (const [value, button] of nodes.moodButtons) {
      const active = value === currentTone;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }
  syncMoods();

  nodes.status = el("p", { class: "status", role: "status" });

  const hero = el("section", { class: "hero" }, [
    el("span", { class: "hero-badge" }, [
      icon("sparkle", "icon-sm"),
      label(el("span"), "hero.badge"),
      el("span", { class: "sep" }),
      label(el("span", { class: "sub" }), "hero.badgeState"),
    ]),
    label(el("h1", { class: "hero-title" }), "hero.title"),
    label(el("p", { class: "hero-sub" }), "hero.sub"),
    searchForm,
    nodes.moods,
    nodes.status,
  ]);

  /* ================= Transcript and candidates ================= */

  nodes.takes = el("div", { class: "takes" });
  nodes.takeCount = el("span", { class: "chip-value" });

  const transcriptCard = el("section", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("div", { class: "card-lead" }, [
        el("div", { class: "card-icon card-icon-sage" }, [icon("transcript")]),
        el("div", {}, [
          label(el("h2", { class: "card-title" }), "transcript.heading"),
          label(el("p", { class: "card-sub" }), "transcript.sub"),
        ]),
      ]),
      el("span", { class: "chip" }, [icon("film"), nodes.takeCount]),
    ]),
    nodes.takes,
    el("div", { class: "card-foot" }, [
      el("span", { class: "tip" }, [
        icon("bulb", "icon-sm"),
        label(el("span"), "transcript.tip"),
      ]),
    ]),
  ]);

  /* ================= In-page assistant ================= */
  // Not a replacement for an external agent; for the visitor who has none. Without a key
  // it states the reason and points at the panel rather than quietly disappearing.

  nodes.chatLog = el("div", { class: "chat-log" });
  nodes.chatInput = el("input", { type: "text", id: "chat", class: "chat-input", autocomplete: "off" });
  label(nodes.chatInput, "assistant.placeholder", "placeholder");
  label(nodes.chatInput, "assistant.heading", "ariaLabel");

  nodes.chatSend = el("button", { type: "submit", class: "btn btn-primary btn-sm" }, [
    icon("send", "icon-sm"),
    label(el("span"), "assistant.send"),
  ]);
  nodes.chatLeft = el("span", { class: "chip-value" });
  nodes.chatNote = el("p", { class: "hint" });

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

  nodes.chatCard = el("section", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("div", { class: "card-lead" }, [
        el("div", { class: "card-icon card-icon-terracotta" }, [icon("sparkle")]),
        el("div", {}, [
          label(el("h2", { class: "card-title" }), "assistant.heading"),
          nodes.chatNote,
        ]),
      ]),
      el("span", { class: "chip" }, [nodes.chatLeft]),
    ]),
    nodes.chatLog,
    chatForm,
  ]);

  /* ================= Bring your own footage ================= */
  // The library was a fixed corpus, which made the product a demo of itself. This is the
  // control that turns it into something a visitor can try on their own material.

  nodes.uploadInput = el("input", {
    type: "file",
    id: "upload",
    class: "visually-hidden",
    accept: "audio/*,video/*",
    onChange: (event) => {
      const [file] = event.target.files ?? [];
      if (file) submitUpload(file);
      // Cleared so choosing the same file twice still fires a change event
      event.target.value = "";
    },
  });

  nodes.uploadLabel = el("input", {
    type: "text",
    class: "drop-field",
    autocomplete: "off",
    maxlength: "32",
  });
  label(nodes.uploadLabel, "upload.labelPlaceholder", "placeholder");
  label(nodes.uploadLabel, "upload.labelField", "ariaLabel");

  nodes.uploadLanguage = el("select", { class: "drop-field drop-language" });
  label(nodes.uploadLanguage, "upload.languageField", "ariaLabel");
  nodes.uploadLanguage.appendChild(label(el("option", { value: "" }), "upload.detect"));
  // A short list rather than every ISO code: naming the language beats detection, and a
  // hundred-item select is a worse affordance than a sensible few plus detection.
  for (const code of ["en", "tr", "de", "fr", "es", "it", "ru", "ar"]) {
    nodes.uploadLanguage.appendChild(el("option", { value: code, text: code }));
  }

  nodes.uploadNote = el("span", { class: "drop-note" });
  nodes.uploadBar = el("div", { class: "drop-bar-fill" });
  nodes.uploadBarWrap = el("div", { class: "drop-bar" }, [nodes.uploadBar]);
  nodes.uploadState = el("p", { class: "drop-state" });

  function submitUpload(file) {
    handlers.onUpload(file, {
      label: nodes.uploadLabel.value,
      language: nodes.uploadLanguage.value,
    });
    nodes.uploadLabel.value = "";
  }

  // The zone is a label wrapping a hidden input: that gets keyboard activation and the
  // file dialog for free, without re-implementing either.
  nodes.dropZone = el(
    "label",
    {
      class: "drop",
      for: "upload",
      onDragOver: (event) => {
        event.preventDefault();
        nodes.dropZone.classList.add("is-over");
      },
      onDragLeave: () => nodes.dropZone.classList.remove("is-over"),
      onDrop: (event) => {
        event.preventDefault();
        nodes.dropZone.classList.remove("is-over");
        const [file] = event.dataTransfer?.files ?? [];
        if (file) submitUpload(file);
      },
    },
    [
      el("span", { class: "drop-mark" }, [icon("upload")]),
      el("span", { class: "drop-text" }, [
        label(el("strong", { class: "drop-title" }), "upload.title"),
        nodes.uploadNote,
      ]),
    ]
  );

  nodes.uploadCard = el("section", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("div", { class: "card-lead" }, [
        el("div", { class: "card-icon card-icon-sage" }, [icon("film")]),
        el("div", {}, [
          label(el("h2", { class: "card-title" }), "upload.heading"),
          label(el("p", { class: "card-sub" }), "upload.sub"),
        ]),
      ]),
    ]),
    nodes.dropZone,
    nodes.uploadInput,
    el("div", { class: "drop-fields" }, [nodes.uploadLabel, nodes.uploadLanguage]),
    nodes.uploadBarWrap,
    nodes.uploadState,
  ]);

  /* ================= Stage ================= */

  // The player mounts into this layer; the overlays are siblings after it so they paint
  // above the picture.
  nodes.stage = el("div", { class: "stage-layer" });
  nodes.stageFallback = el("div", { class: "stage-fallback" }, [
    icon("wave"),
    label(el("span"), "stage.audioOnly"),
  ]);
  nodes.caption = el("p");
  nodes.captionWrap = el("div", { class: "stage-caption" });
  nodes.positionBadge = el("span", { class: "stage-badge" }, [dot(), el("span")]);
  nodes.formatBadge = el("span", { class: "stage-badge stage-badge-format" });

  const stageBox = el("div", { class: "stage" }, [
    nodes.stage,
    nodes.stageFallback,
    nodes.captionWrap,
    el("div", { class: "stage-badges" }, [nodes.positionBadge, nodes.formatBadge]),
  ]);

  nodes.scrubberFill = el("div", { class: "scrubber-fill" });
  nodes.prevButton = el("button", {
    type: "button",
    class: "iconbtn",
    onClick: () => handlers.onJump(currentIndex() - 1),
  }, [icon("prev")]);
  nodes.playButton = el("button", {
    type: "button",
    class: "iconbtn iconbtn-primary",
    onClick: () => (isPlaying ? handlers.onStop() : handlers.onPlay()),
  }, [icon("play")]);
  nodes.nextButton = el("button", {
    type: "button",
    class: "iconbtn",
    onClick: () => handlers.onJump(currentIndex() + 1),
  }, [icon("next")]);
  label(nodes.prevButton, "transport.previous", "title");
  label(nodes.prevButton, "transport.previous", "ariaLabel");
  label(nodes.nextButton, "transport.next", "title");
  label(nodes.nextButton, "transport.next", "ariaLabel");

  nodes.nowPlaying = el("span", { class: "now-playing" });

  nodes.provenanceDetail = el("span", { class: "provenance-detail" });
  const provenance = el("div", { class: "provenance" }, [
    el("span", { class: "provenance-badge" }, [icon("verified")]),
    el("div", { class: "provenance-body" }, [
      label(el("span", { class: "provenance-title" }), "provenance.title"),
      nodes.provenanceDetail,
    ]),
  ]);

  const stageCard = el("section", { class: "card" }, [
    stageBox,
    el("div", { class: "scrubber" }, [nodes.scrubberFill]),
    el("div", { class: "transport" }, [
      el("div", { class: "transport-group" }, [
        nodes.prevButton,
        nodes.playButton,
        nodes.nextButton,
      ]),
      nodes.nowPlaying,
    ]),
    provenance,
  ]);

  /* ================= Alternative take ================= */
  // Real: the next-best candidate for the same line that is not already in the cut.
  // Swapping replaces the block in place, so the order the editor set is preserved.

  nodes.altDetail = el("span", { class: "alt-detail" });
  nodes.altButton = el("button", { type: "button", class: "btn btn-sm" }, [
    icon("swap", "icon-sm"),
    label(el("span"), "alt.swap"),
  ]);
  nodes.altCard = el("section", { class: "card alt" }, [
    el("div", { class: "alt-lead" }, [
      el("span", { class: "alt-badge" }, [icon("sparkle")]),
      el("div", {}, [
        label(el("span", { class: "alt-title" }), "alt.title"),
        nodes.altDetail,
      ]),
    ]),
    nodes.altButton,
  ]);

  /* ================= Story track ================= */

  nodes.blocks = el("ol", { class: "blocks" });
  nodes.trackCount = el("span", { class: "chip-value" });
  nodes.trackTotal = el("span", { class: "track-total-value", text: "00:00.0" });

  nodes.previewButton = el("button", {
    type: "button",
    class: "btn",
    onClick: () => handlers.onPlay(),
  }, [icon("play", "icon-sm"), label(el("span"), "track.preview")]);

  nodes.clearButton = el("button", {
    type: "button",
    class: "btn",
    onClick: () => handlers.onClearTimeline(),
  }, [icon("close", "icon-sm"), label(el("span"), "track.clear")]);

  // The one irreversible action on the page. It opens the approval dialog rather than
  // rendering, which is the whole point of the flow.
  nodes.exportButton = el("button", {
    type: "button",
    class: "btn btn-primary",
    onClick: () => handlers.onRender(),
  }, [icon("sparkle", "icon-sm"), label(el("span"), "track.export")]);

  nodes.renderResult = el("p", { class: "render-result" });

  const trackCard = el("section", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("div", { class: "card-lead" }, [
        el("div", { class: "card-icon card-icon-terracotta" }, [icon("track")]),
        el("div", {}, [
          el("div", { class: "card-titleline" }, [
            label(el("h2", { class: "card-title" }), "track.heading"),
            el("span", { class: "chip" }, [nodes.trackCount]),
          ]),
          label(el("p", { class: "card-sub" }), "track.sub"),
        ]),
      ]),
      el("div", { class: "track-stats" }, [
        el("div", { class: "track-total" }, [
          label(el("span", { class: "track-total-label" }), "track.total"),
          nodes.trackTotal,
        ]),
        nodes.previewButton,
        nodes.clearButton,
        nodes.exportButton,
      ]),
    ]),
    nodes.blocks,
    el("div", { class: "card-foot" }, [
      el("span", { class: "track-note" }, [
        icon("check", "icon-sm"),
        label(el("span"), "track.note"),
      ]),
      nodes.renderResult,
    ]),
  ]);

  /* ================= Assembly ================= */

  const pagefoot = el("footer", { class: "pagefoot" }, [
    el("div", { class: "pagefoot-inner" }, [
      el("span", { class: "pagefoot-mark" }, [
        dot(),
        label(el("span"), "foot.tagline"),
      ]),
      label(el("span"), "foot.note"),
    ]),
  ]);

  root.append(
    topbar,
    el("main", { class: "shell" }, [
      hero,
      el("div", { class: "grid" }, [
        el("div", { class: "col" }, [transcriptCard, nodes.chatCard]),
        el("div", { class: "col" }, [stageCard, nodes.altCard, nodes.uploadCard]),
      ]),
      trackCard,
    ]),
    pagefoot
  );

  /* ================= Render ================= */

  let isPlaying = false;
  let playbackIndex = -1;
  let dragFrom = -1;

  /**
   * Rebuild guards for the two lists.
   *
   * `render()` runs on every store notification, and during playback the rAF loop calls
   * setPlayback roughly sixty times a second. Rebuilding the story track that often would
   * recreate each block's thumbnail `<video>` sixty times a second too — three blocks is
   * around 180 media loads per second for a picture that never changed.
   *
   * So each list carries a signature of the things that actually affect its markup.
   * `offsetMs` is deliberately not in either: it moves every frame and changes nothing
   * except the scrubber, which is a style write rather than a rebuild.
   */
  let takesSignature = null;
  let trackSignature = null;

  function currentIndex() {
    return playbackIndex >= 0 ? playbackIndex : 0;
  }

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
      nodes.chatLog.appendChild(el("p", { class: "hint", text: t("assistant.thinking") }));
    }
    nodes.chatLog.scrollTop = nodes.chatLog.scrollHeight;
  }

  /** The line as plain text, for when the word timings are not in yet. */
  function plainLine(candidate, phrase) {
    const line = el("p", { class: "take-line" });
    for (const part of highlightParts(candidate.text, phrase)) {
      line.appendChild(el("span", { class: part.hit ? "take-hit" : "", text: part.text }));
    }
    return line;
  }

  /**
   * The line as clickable words. The product's namesake.
   *
   * Each token carries its own millisecond range, so clicking one plays exactly that word
   * and shift-clicking a second picks the span between them. That is the difference
   * between taking "this line" and taking "these three words of this line".
   *
   * Keyboard: the tokens are real buttons in document order, so Tab walks the sentence and
   * Enter picks. Shift+Enter extends, which mirrors the shift-click.
   */
  function wordTokens(candidate, words, selection) {
    const line = el("p", { class: "take-line take-words" });

    words.forEach((word, index) => {
      const matched = wordInRange(word, candidate.start_ms, candidate.end_ms);
      const picked =
        selection && index >= selection.from && index <= selection.to;

      const classes = ["word"];
      if (matched) classes.push("is-match");
      if (picked) classes.push("is-picked");

      line.appendChild(
        el("button", {
          type: "button",
          class: classes.join(" "),
          text: word.word,
          // Read out as a range so a screen reader user knows what picking does
          title: `${timecode(word.start_ms)} \u2013 ${timecode(word.end_ms)}`,
          "aria-pressed": picked ? "true" : "false",
          // Coerced, so the handler always receives a boolean whatever the event carries
          onClick: (event) =>
            handlers.onSelectWord(candidate, index, { extend: Boolean(event.shiftKey) }),
          onKeyDown: (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            handlers.onSelectWord(candidate, index, { extend: Boolean(event.shiftKey) });
          },
        })
      );
    });

    return line;
  }

  /** What the editor picked, and the one button that acts on it. */
  function selectionBar(state, selection) {
    const segment = selectionSegmentOf(state, selection);
    if (!segment) return null;

    return el("div", { class: "picked" }, [
      el("span", { class: "picked-label" }, [
        icon("tweezers", "icon-sm"),
        el("span", {
          text: t("pick.summary", {
            words: segment.words,
            duration: seconds(segment.duration_ms),
          }),
        }),
      ]),
      el("span", { class: "picked-text", text: segment.text }),
      el("div", { class: "take-actions" }, [
        el("button", {
          type: "button",
          class: "btn btn-quiet btn-sm",
          text: t("pick.clear"),
          onClick: () => handlers.onClearSelection(),
        }),
        el("button", {
          type: "button",
          class: "btn btn-primary btn-sm",
          text: t("pick.add"),
          onClick: () => handlers.onAddSelection(),
        }),
      ]),
    ]);
  }

  /**
   * The picked range, derived from state rather than read back from the store.
   *
   * `render` is handed a snapshot, so reading the live store here could describe a
   * different moment than the rest of the frame.
   */
  function selectionSegmentOf(state, selection) {
    if (!selection) return null;
    const words = state.lines?.[selection.key];
    if (!words?.length) return null;

    const picked = words.slice(selection.from, selection.to + 1);
    if (!picked.length) return null;

    return {
      words: picked.length,
      text: picked.map((word) => word.word).join(" "),
      duration_ms: picked.at(-1).end_ms - picked[0].start_ms,
    };
  }

  function renderTakes(state) {
    // Which candidates, which phrase is highlighted, which of them are already in the
    // cut, which words arrived, what is picked, and the language.
    const signature = [
      getLocale(),
      state.query.phrase,
      state.candidates.map((candidate) => candidate.id).join("|"),
      state.timeline.map((segment) => segment.id).join("|"),
      Object.keys(state.lines ?? {}).sort().join("|"),
      state.selection
        ? `${state.selection.key}:${state.selection.from}:${state.selection.to}`
        : "",
    ].join("\u0001");
    if (signature === takesSignature) return;
    takesSignature = signature;

    nodes.takes.replaceChildren();
    nodes.takeCount.textContent = state.candidates.length
      ? t("transcript.count", { count: state.candidates.length })
      : t("transcript.countEmpty");

    if (!state.candidates.length) {
      nodes.takes.appendChild(el("p", { class: "empty", text: t("candidates.empty") }));
      return;
    }

    for (const candidate of state.candidates) {
      const inCut = state.timeline.some((segment) => segment.id === candidate.id);
      const key = `${candidate.take_id}:${candidate.line_id}`;
      const words = state.lines?.[key];
      const selection =
        state.selection && state.selection.key === key ? state.selection : null;

      // With the words in, the whole sentence is on screen and every word is clickable.
      // Without them — the request failed, or is still in flight — fall back to the
      // matched phrase, which is what this showed before word tweezing existed.
      const line = words?.length
        ? wordTokens(candidate, words, selection)
        : plainLine(candidate, state.query.phrase);

      const previewButton = el(
        "button",
        {
          type: "button",
          class: "btn btn-quiet btn-sm",
          onClick: () => handlers.onPreview(candidate),
        },
        [icon("play", "icon-sm"), el("span", { text: t("candidates.preview") })]
      );

      const addButton = el("button", {
        type: "button",
        class: "btn btn-soft btn-sm",
        text: inCut ? t("candidates.addAgain") : t("candidates.add"),
        onClick: () => handlers.onAdd(candidate),
      });

      const foot = el("div", { class: "take-foot" }, [
        inCut
          ? el("span", { class: "take-state" }, [
              icon("check", "icon-sm"),
              el("span", { text: t("candidates.inCut") }),
            ])
          : el("span", { class: "take-time", text: seconds(candidate.duration_ms) }),
        el("div", { class: "take-actions" }, [previewButton, addButton]),
      ]);

      nodes.takes.appendChild(
        el("article", { class: inCut ? "take is-in-cut" : "take" }, [
          el("div", { class: "take-head" }, [
            el("div", { class: "take-who" }, [
              el("span", { class: "take-speaker", text: candidate.speaker || "\u2014" }),
              el("span", { class: "take-id", text: candidate.take_id }),
              toneBadge(candidate.tone, candidate.tone_score),
            ]),
            el("span", {
              class: "take-time",
              text: `${timecode(candidate.start_ms)} \u2013 ${timecode(candidate.end_ms)}`,
            }),
          ]),
          line,
          selectionBar(state, selection),
          foot,
        ])
      );
    }
  }

  /**
   * The thumbnail: a frame from the take at its own start time.
   *
   * A media fragment is enough, no server work and no stored poster. An audio-only take
   * has no frame to show, so the delivery glyph behind it stays visible instead of a
   * broken image.
   */
  function thumbnail(segment) {
    const box = el("div", { class: "block-thumb" }, [
      el("span", {
        class: "block-thumb-glyph",
        text: TONE_GLYPH[segment.tone] ?? TONE_GLYPH.neutral,
        "aria-hidden": "true",
      }),
    ]);
    if (segment.media_url) {
      const frame = el("video", {
        preload: "metadata",
        muted: true,
        playsinline: true,
        tabindex: "-1",
        "aria-hidden": "true",
        src: `${segment.media_url}#t=${(segment.start_ms / 1000).toFixed(3)}`,
      });
      box.appendChild(frame);
    }
    return box;
  }

  function renderTrack(state) {
    const timeline = state.timeline;
    const total = timeline.reduce((sum, segment) => sum + segment.duration_ms, 0);

    nodes.trackCount.textContent = t("track.assembled", { count: timeline.length });
    nodes.trackTotal.textContent = shortTimecode(total);

    const hasSegments = timeline.length > 0;
    nodes.previewButton.disabled = !hasSegments;
    nodes.clearButton.disabled = !hasSegments;
    nodes.exportButton.disabled = !hasSegments;

    // Which segments, in what order, which one is lit, and the language.
    const litIndex = state.playback.playing ? state.playback.index : -1;
    const signature = [
      getLocale(),
      litIndex,
      timeline.map((segment) => segment.id).join("|"),
    ].join("\u0001");
    if (signature === trackSignature) return;
    trackSignature = signature;

    nodes.blocks.replaceChildren();

    timeline.forEach((segment, index) => {
      const playing = state.playback.playing && state.playback.index === index;

      const grip = el("button", {
        type: "button",
        class: "block-grip",
        title: t("track.move"),
        "aria-label": t("track.move"),
        onKeyDown: (event) => {
          // Reordering must not be pointer-only. Arrow keys on the grip move the block.
          const delta =
            event.key === "ArrowLeft" || event.key === "ArrowUp"
              ? -1
              : event.key === "ArrowRight" || event.key === "ArrowDown"
                ? 1
                : 0;
          if (!delta) return;
          event.preventDefault();
          handlers.onReorder(index, index + delta);
        },
      }, [icon("grip", "icon-sm")]);

      const remove = el("button", {
        type: "button",
        class: "block-remove",
        title: t("track.remove"),
        "aria-label": t("track.remove"),
        onClick: () => handlers.onRemove(index),
      }, [icon("close", "icon-sm")]);

      const block = el(
        "li",
        {
          class: playing ? "block is-playing" : "block",
          draggable: "true",
          onDragStart: (event) => {
            dragFrom = index;
            block.classList.add("is-dragging");
            event.dataTransfer.effectAllowed = "move";
            // Firefox refuses to start a drag without payload
            event.dataTransfer.setData("text/plain", String(index));
          },
          onDragEnd: () => {
            dragFrom = -1;
            block.classList.remove("is-dragging");
          },
          onDragOver: (event) => {
            if (dragFrom < 0) return;
            event.preventDefault();
            block.classList.add("is-drop-target");
          },
          onDragLeave: () => block.classList.remove("is-drop-target"),
          onDrop: (event) => {
            event.preventDefault();
            block.classList.remove("is-drop-target");
            if (dragFrom >= 0 && dragFrom !== index) handlers.onReorder(dragFrom, index);
            dragFrom = -1;
          },
        },
        [
          el("div", { class: "block-head" }, [
            el("span", {
              class: "block-index",
              text: String(index + 1).padStart(2, "0"),
            }),
            grip,
          ]),
          el("div", { class: "block-body" }, [
            thumbnail(segment),
            el("div", { class: "block-meta" }, [
              el("span", { class: "block-take", text: segment.take_id }),
              el("span", { class: "block-line", text: segment.text }),
            ]),
          ]),
          el("div", { class: "block-foot" }, [
            el("span", { class: `tone tone-${segment.tone}` }, [
              el("span", {
                text: TONE_GLYPH[segment.tone] ?? TONE_GLYPH.neutral,
                "aria-hidden": "true",
              }),
              el("span", { text: seconds(segment.duration_ms) }),
            ]),
            remove,
          ]),
        ]
      );

      nodes.blocks.appendChild(block);
    });

    // The empty slot doubles as the empty state: there is always somewhere to start.
    nodes.blocks.appendChild(
      el("li", {}, [
        el("button", {
          type: "button",
          class: "block block-add",
          onClick: () => {
            nodes.phrase.focus();
            nodes.phrase.select();
          },
        }, [
          el("span", { class: "block-add-mark" }, [icon("plus")]),
          el("span", { class: "block-add-label", text: t("track.pluck") }),
          el("span", { class: "block-add-note", text: t("track.pluckNote") }),
        ]),
      ])
    );
  }

  function renderStage(state) {
    const timeline = state.timeline;
    const playing = state.playback.playing;
    const index = state.playback.index;
    const current = playing && timeline[index] ? timeline[index] : timeline[0] ?? null;

    // The play button is a play/stop toggle rather than play/pause: the virtual-splice
    // player seeks across files, and there is no paused position to resume from.
    nodes.playButton.replaceChildren(icon(playing ? "stop" : "play"));
    const toggleLabel = playing ? t("transport.stop") : t("transport.play");
    nodes.playButton.title = toggleLabel;
    nodes.playButton.setAttribute("aria-label", toggleLabel);
    nodes.playButton.disabled = !timeline.length;
    nodes.prevButton.disabled = !playing || index <= 0;
    nodes.nextButton.disabled = !playing || index >= timeline.length - 1;

    // Caption: the line being played, with the searched phrase emphasised.
    nodes.captionWrap.replaceChildren();
    if (current?.text) {
      const caption = el("p");
      for (const part of highlightParts(current.text, state.query.phrase)) {
        caption.appendChild(
          el("span", { class: part.hit ? "caption-hit" : "", text: part.text })
        );
      }
      nodes.captionWrap.appendChild(caption);
    }

    // Position badge: elapsed over the whole cut, not over the source file.
    const before = timeline
      .slice(0, Math.max(0, index))
      .reduce((sum, segment) => sum + segment.duration_ms, 0);
    const total = timeline.reduce((sum, segment) => sum + segment.duration_ms, 0);
    const elapsed = playing ? before + state.playback.offsetMs : 0;

    nodes.positionBadge.classList.toggle("is-live", playing);
    nodes.positionBadge.lastChild.textContent = total
      ? `${shortTimecode(elapsed)} / ${shortTimecode(total)}`
      : t("stage.idle");

    // The real container, not a resolution we never measured.
    const format = mediaFormat(current);
    nodes.formatBadge.textContent = format;
    nodes.formatBadge.hidden = !format;

    nodes.scrubberFill.style.width = total ? `${(elapsed / total) * 100}%` : "0%";

    nodes.nowPlaying.textContent = playing && current
      ? t("transport.position", {
          index: index + 1,
          count: timeline.length,
          take: current.take_id,
          time: timecode(current.start_ms + state.playback.offsetMs),
        })
      : t("transport.idle");

    // Provenance always describes what is loaded, straight from the database record.
    nodes.provenanceDetail.replaceChildren();
    if (current) {
      nodes.provenanceDetail.append(
        el("span", {
          text: `${current.source_url || t("provenance.unknown")} \u00b7 ${current.take_id} \u00b7 ${timecode(current.start_ms)} \u2013 ${timecode(current.end_ms)} `,
        }),
        el("a", {
          href: sourceHref(current),
          target: "_blank",
          rel: "noopener noreferrer",
          text: t("provenance.open"),
        })
      );
    } else {
      nodes.provenanceDetail.textContent = t("provenance.empty");
    }

    // Audio-only takes have no frame; say so instead of showing a black rectangle.
    // With nothing loaded there is nothing to caption either, so the notice stays off.
    const AUDIO_ONLY = new Set(["wav", "mp3", "m4a", "flac", "ogg", "opus", "aac"]);
    nodes.stageFallback.hidden = !current || !AUDIO_ONLY.has(format);
  }

  /**
   * The alternative take: the best-ranked candidate for this line that is NOT in the cut.
   *
   * Real, and cheap — the ranking already came back from the search. Swapping replaces
   * the block at its own position so the order the editor chose survives.
   */
  function renderAlt(state) {
    const inCut = new Set(state.timeline.map((segment) => segment.id));
    const alternative = state.candidates.find((candidate) => !inCut.has(candidate.id));
    const replaceIndex = state.timeline.findIndex((segment) =>
      state.candidates.some((candidate) => candidate.id === segment.id)
    );

    const usable = Boolean(alternative) && replaceIndex >= 0;
    nodes.altCard.hidden = !usable;
    if (!usable) return;

    nodes.altDetail.textContent = t("alt.detail", {
      take: alternative.take_id,
      tone: toneLabel(alternative.tone),
    });
    nodes.altButton.onclick = () => handlers.onSwap(replaceIndex, alternative);
  }

  function renderUpload(state) {
    const upload = state.upload ?? {};
    const busy = upload.status === "sending" || upload.status === "queued" || upload.status === "running";

    // A control that cannot work should say why rather than wait to fail on use.
    nodes.dropZone.classList.toggle("is-off", !upload.available);
    nodes.uploadInput.disabled = !upload.available || busy;
    nodes.uploadLabel.disabled = !upload.available || busy;
    nodes.uploadLanguage.disabled = !upload.available || busy;

    if (!upload.available) {
      nodes.uploadNote.textContent = upload.reason || t("upload.offFallback");
    } else if (upload.limits) {
      nodes.uploadNote.textContent = t("upload.limits", {
        mb: upload.limits.max_mb,
        seconds: upload.limits.max_seconds,
        cost: upload.costPerMinute,
      });
    } else {
      nodes.uploadNote.textContent = "";
    }

    // The bar tracks the network transfer only. Once the bytes are there the server is
    // transcribing, which has no measurable fraction, so the bar sits full and the text
    // carries the stage instead of a progress number nobody can trust.
    const showBar = busy || upload.status === "failed";
    nodes.uploadBarWrap.hidden = !showBar;
    nodes.uploadBar.style.width = `${Math.round((upload.progress ?? 0) * 100)}%`;
    nodes.uploadBarWrap.classList.toggle("is-working", upload.status === "running" || upload.status === "queued");
    nodes.uploadBarWrap.classList.toggle("is-failed", upload.status === "failed");

    if (upload.status === "sending") {
      nodes.uploadState.textContent = t("upload.sending", {
        name: upload.filename,
        percent: Math.round((upload.progress ?? 0) * 100),
      });
    } else if (busy) {
      nodes.uploadState.textContent = t(`upload.stage.${upload.stage || "queued"}`);
    } else if (upload.status === "failed") {
      nodes.uploadState.textContent = upload.error || t("upload.failed");
    } else if (upload.status === "done" && upload.result) {
      nodes.uploadState.textContent = t("upload.done", {
        take: upload.result.take_id,
        lines: upload.result.lines,
        language: upload.result.language,
        seconds: upload.result.elapsed,
      });
    } else {
      nodes.uploadState.textContent = "";
    }

    nodes.uploadState.className = `drop-state${upload.status === "failed" ? " status-error" : ""}`;
  }

  function renderJob(state) {
    const job = state.render ?? { status: "idle" };
    nodes.renderResult.replaceChildren();

    const busy = job.status === "queued" || job.status === "running";
    if (busy) nodes.exportButton.disabled = true;

    if (job.status === "idle") return;

    if (busy) {
      nodes.renderResult.appendChild(el("span", { text: t("render.running") }));
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
        icon("check", "icon-sm"),
        el("span", { text: t("render.readyLabel") }),
        el("a", {
          href: job.downloadUrl,
          // Same origin and a download; we do not open a new tab
          download: "",
          text: t("render.download", { format: job.mode === "video" ? "mp4" : "wav" }),
        })
      );
    }
  }

  function render(state) {
    applyLabels();
    if (nodes.locale.value !== getLocale()) nodes.locale.value = getLocale();

    isPlaying = state.playback.playing;
    playbackIndex = state.playback.index;

    if (state.session) {
      const credits = state.session.credits;
      nodes.creditsChip.textContent = t("meta.creditsChip", { count: credits });
      nodes.creditsWrap.classList.toggle("is-empty", credits <= 0);
    }
    if (state.library) {
      nodes.libraryChip.textContent = t("meta.libraryChip", {
        takes: state.library.takes,
        words: state.library.words,
      });
    }

    nodes.webmcpChip.textContent = state.webmcp.available
      ? t("webmcp.on", { count: state.webmcp.registered })
      : t("webmcp.off");
    nodes.webmcpWrap.classList.toggle("is-on", state.webmcp.available);

    nodes.status.textContent = state.status.message;
    nodes.status.className = `status status-${state.status.kind}`;

    if (nodes.phrase.value !== state.query.phrase) {
      nodes.phrase.value = state.query.phrase;
    }
    // The example is a real line from this library, whatever language it is in.
    const example = state.library?.sample_line || EXAMPLE_FALLBACK;
    nodes.phrase.placeholder = t("search.example", { line: example });
    if (currentTone !== state.query.tone) {
      currentTone = state.query.tone;
      syncMoods();
    }

    renderChat(state);
    renderTakes(state);
    renderStage(state);
    renderAlt(state);
    renderTrack(state);
    renderUpload(state);
    renderJob(state);
  }

  return { render, stage: nodes.stage };
}
