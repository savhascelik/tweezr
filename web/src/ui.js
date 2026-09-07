/**
 * Arayüz. Kurgucunun zaten çalıştığı ekran: arama, adaylar, timeline, provenance.
 *
 * GÜVENLİK: burada innerHTML YOK, her metin textContent ile yazılıyor.
 * Sebebi somut: geçen projede onay penceresini innerHTML ile kurmuştuk ve
 * zehirli bir araç adı kendi Approve düğmesine basabiliyordu. Buradaki metinlerin
 * kaynağı Whisper çıktısı ve kullanıcı dosya adları — yani kontrol etmediğimiz veri.
 * `el()` yardımcısı bilerek sadece textContent kabul ediyor.
 */

const TONE_LABELS = {
  neutral: "nötr",
  calm: "sakin",
  tense: "gergin",
  angry: "öfkeli",
  whisper: "fısıltı",
  shouted: "bağırma",
};

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") {
      node.textContent = value; // innerHTML asla
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
  const seconds = Math.floor((total % 60000) / 1000);
  const millis = Math.floor(total % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function duration(ms) {
  return `${(ms / 1000).toFixed(2)} sn`;
}

/** Fragmentin kaynağına giden bağlantı. Medya fragment'i tam o aralığı açıyor. */
function sourceHref(segment) {
  const start = (segment.start_ms / 1000).toFixed(3);
  const end = (segment.end_ms / 1000).toFixed(3);
  return `${segment.media_url}#t=${start},${end}`;
}

function toneBadge(tone, score) {
  const label = TONE_LABELS[tone] ?? tone;
  const text = score ? `${label} ${Number(score).toFixed(2)}` : label;
  return el("span", { class: `tone tone-${tone}`, text });
}

export function createUI(root, handlers) {
  const nodes = {};

  // --- Üst bant: oturum, kredi, WebMCP durumu ---
  nodes.role = el("span", { class: "meta-value", text: "…" });
  nodes.credits = el("span", { class: "meta-value", text: "…" });
  nodes.library = el("span", { class: "meta-value", text: "…" });
  nodes.webmcp = el("span", { class: "pill pill-off", text: "WebMCP yok" });

  const bar = el("header", { class: "bar" }, [
    el("div", { class: "brand" }, [
      el("strong", { text: "Replik arama ve kaba kurgu" }),
      el("span", {
        class: "brand-note",
        text: "render etmeden öner, onaydan sonra üret",
      }),
    ]),
    el("div", { class: "meta" }, [
      el("span", { class: "meta-item" }, [el("span", { class: "meta-key", text: "oturum" }), nodes.role]),
      el("span", { class: "meta-item" }, [el("span", { class: "meta-key", text: "kredi" }), nodes.credits]),
      el("span", { class: "meta-item" }, [el("span", { class: "meta-key", text: "kütüphane" }), nodes.library]),
      nodes.webmcp,
    ]),
  ]);

  // --- Arama ---
  nodes.phrase = el("input", {
    type: "text",
    id: "phrase",
    placeholder: "I never asked for this",
    autocomplete: "off",
  });
  nodes.tone = el("select", { id: "tone" }, [
    el("option", { value: "", text: "her ton" }),
    ...Object.entries(TONE_LABELS).map(([value, label]) =>
      el("option", { value, text: label })
    ),
  ]);
  nodes.searchButton = el("button", { type: "submit", class: "primary", text: "Ara" });

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
      el("label", { for: "phrase", text: "Replik" }),
      nodes.phrase,
      el("label", { for: "tone", text: "Ton" }),
      nodes.tone,
      nodes.searchButton,
    ]
  );

  nodes.status = el("p", { class: "status", text: "" });

  // Bu panel WebMCP'siz tarayıcıda aynı akışı elle sürmek için var.
  const searchPanel = el("section", { class: "panel" }, [
    el("h2", { text: "Ara" }),
    el("p", {
      class: "hint",
      text: "Ajan aynı işi WebMCP araçlarıyla yapıyor. Bu panel araçlar yoksa da çalışsın diye burada.",
    }),
    form,
    nodes.status,
  ]);

  // --- Adaylar ---
  nodes.candidates = el("div", { class: "candidates" });
  nodes.candidateCount = el("span", { class: "count", text: "" });
  const candidatePanel = el("section", { class: "panel" }, [
    el("h2", {}, []),
    nodes.candidates,
  ]);
  candidatePanel.firstChild.append(document.createTextNode("Adaylar "), nodes.candidateCount);

  // --- Sahne ve timeline ---
  nodes.stage = el("div", { class: "stage" });
  nodes.nowPlaying = el("p", { class: "now-playing", text: "Oynatılmıyor" });
  nodes.timeline = el("ol", { class: "timeline" });
  nodes.total = el("span", { class: "count", text: "" });

  nodes.playButton = el("button", {
    class: "primary",
    text: "Öneriyi oynat",
    onClick: () => handlers.onPlay(),
  });
  nodes.stopButton = el("button", { text: "Durdur", onClick: () => handlers.onStop() });
  nodes.clearButton = el("button", {
    text: "Temizle",
    onClick: () => handlers.onClearTimeline(),
  });
  nodes.renderButton = el("button", {
    class: "danger",
    text: "Onayla ve render et",
    onClick: () => handlers.onRender(),
  });

  const timelinePanel = el("section", { class: "panel" }, [
    el("h2", {}, []),
    nodes.stage,
    nodes.nowPlaying,
    nodes.timeline,
    el("div", { class: "transport" }, [
      nodes.playButton,
      nodes.stopButton,
      nodes.clearButton,
      nodes.renderButton,
    ]),
  ]);
  timelinePanel.firstChild.append(document.createTextNode("Kaba kurgu "), nodes.total);

  root.append(bar, el("main", { class: "layout" }, [
    el("div", { class: "column" }, [searchPanel, candidatePanel]),
    el("div", { class: "column" }, [timelinePanel]),
  ]));

  function renderCandidates(state) {
    nodes.candidates.replaceChildren();
    nodes.candidateCount.textContent = state.candidates.length
      ? `(${state.candidates.length})`
      : "";

    if (!state.candidates.length) {
      nodes.candidates.appendChild(
        el("p", { class: "empty", text: "Henüz arama yapılmadı." })
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
            el("span", { class: "dim", text: `kam ${candidate.camera || "-"}` }),
            toneBadge(candidate.tone, candidate.tone_score),
            el("span", { class: "dim", text: duration(candidate.duration_ms) }),
          ]),
          el("p", { class: "line", text: candidate.text }),
          el("div", { class: "candidate-actions" }, [
            el("button", {
              text: "Önizle",
              onClick: () => handlers.onPreview(candidate),
            }),
            el("button", {
              class: "primary",
              text: inTimeline ? "Tekrar ekle" : "Kurguya ekle",
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
      ? `(${state.timeline.length} parça, ${duration(total)})`
      : "";

    const hasSegments = state.timeline.length > 0;
    nodes.playButton.disabled = !hasSegments;
    nodes.renderButton.disabled = !hasSegments;
    nodes.clearButton.disabled = !hasSegments;

    if (!hasSegments) {
      nodes.timeline.appendChild(
        el("li", { class: "empty" }, [
          el("p", {
            text: "Kurgu boş. Aday listesinden ekle, ya da ajana söyle.",
          }),
        ])
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
              title: "Bu parçayı çıkar",
              text: "×",
              onClick: () => handlers.onRemove(index),
            }),
          ]),
          // Provenance şeridi: her fragment nereden geldiğini taşıyor ve
          // kaynağına tıklanabiliyor. Bu şerit ürünün "deepfake değil,
          // kaynaklı montaj" iddiasının somut hali.
          el("div", { class: "provenance" }, [
            el("span", { class: "prov-take", text: segment.take_id }),
            el("span", { class: "dim", text: segment.scene || "-" }),
            el("span", { class: "dim", text: `kam ${segment.camera || "-"}` }),
            el("span", { class: "dim", text: segment.speaker || "-" }),
            toneBadge(segment.tone, segment.tone_score),
            el("span", {
              class: "timecode",
              text: `${timecode(segment.start_ms)} → ${timecode(segment.end_ms)}`,
            }),
            el("span", { class: "dim", text: duration(segment.duration_ms) }),
            el("a", {
              class: "source",
              href: sourceHref(segment),
              target: "_blank",
              rel: "noopener noreferrer",
              text: "kaynağı aç",
            }),
          ]),
        ])
      );
    });
  }

  function render(state) {
    if (state.session) {
      nodes.role.textContent = state.session.role;
      nodes.credits.textContent = String(state.session.credits);
    }
    if (state.library) {
      const stats = state.library;
      nodes.library.textContent = `${stats.takes} take, ${stats.words} kelime, ${stats.vocabulary} farklı`;
    }

    nodes.webmcp.textContent = state.webmcp.available
      ? `WebMCP ${state.webmcp.registered} araç`
      : "WebMCP yok — panel devrede";
    nodes.webmcp.className = state.webmcp.available ? "pill pill-on" : "pill pill-off";

    nodes.status.textContent = state.status.message;
    nodes.status.className = `status status-${state.status.kind}`;

    nodes.phrase.value === state.query.phrase || (nodes.phrase.value = state.query.phrase);
    if (nodes.tone.value !== state.query.tone) nodes.tone.value = state.query.tone;

    const playing = state.playback.playing;
    nodes.stopButton.disabled = !playing;
    if (playing && state.timeline[state.playback.index]) {
      const segment = state.timeline[state.playback.index];
      nodes.nowPlaying.textContent =
        `${state.playback.index + 1}/${state.timeline.length}  ${segment.take_id}  ` +
        `${timecode(segment.start_ms + state.playback.offsetMs)}`;
    } else {
      nodes.nowPlaying.textContent = "Oynatılmıyor";
    }

    renderCandidates(state);
    renderTimeline(state);
  }

  return { render, stage: nodes.stage };
}
