/**
 * Render onay penceresi. Ürünün tek geri alınamaz adımının kapısı.
 *
 * Üç savunma ve her birinin somut bir sebebi var:
 *
 * 1. **Closed shadow root.** Sayfadaki başka bir script `host.shadowRoot` ile içeriye
 *    ulaşamıyor (closed'da null dönüyor), yani Approve düğmesini programatik olarak
 *    bulup basamıyor. Kök referansı bu modülün kapanışında duruyor.
 *
 * 2. **Sadece textContent.** Geçen projede onay penceresini innerHTML ile kurmuştuk ve
 *    zehirli bir araç adı kendi Approve düğmesine basabiliyordu. Buradaki metinler
 *    take kimlikleri, replik metni ve dosya adları — hepsi Whisper çıktısı ve kullanıcı
 *    dosyaları, yani kontrol etmediğimiz veri.
 *
 * 3. **Host stilleri inline ve !important.** Sayfa CSS'i pencereyi görünmez yapıp
 *    kullanıcıya farkında olmadan onaylatamasın.
 *
 * Ajan `commit_render` çağırdığında bu pencere açılıyor ve aracın promise'i insanın
 * kararını bekliyor. HITL kapısının somut hali bu: ajan isteyebiliyor, insan veriyor.
 */

const AUTO_DECLINE_MS = 5 * 60 * 1000;

const TONE_LABELS = {
  neutral: "nötr",
  calm: "sakin",
  tense: "gergin",
  angry: "öfkeli",
  whisper: "fısıltı",
  shouted: "bağırma",
};

const STYLE = `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
  .backdrop {
    position: fixed; inset: 0; display: flex; align-items: center;
    justify-content: center; padding: 1.5rem;
    background: rgba(8, 9, 11, 0.72);
  }
  .sheet {
    background: #1c1f25; color: #e6e8ec; border: 1px solid #2c313a;
    border-radius: 10px; width: min(38rem, 100%); max-height: 100%;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
  }
  header, footer { padding: 1rem 1.15rem; }
  header { border-bottom: 1px solid #2c313a; }
  footer { border-top: 1px solid #2c313a; display: flex; gap: 0.5rem; justify-content: flex-end; }
  h2 { margin: 0 0 0.3rem; font-size: 1rem; }
  p { margin: 0; font-size: 0.85rem; color: #8b929e; line-height: 1.5; }
  .body { overflow-y: auto; padding: 0.85rem 1.15rem; }
  ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.45rem; }
  li { border: 1px solid #2c313a; border-left: 3px solid #2c313a; border-radius: 6px; padding: 0.5rem 0.6rem; background: #22262d; }
  .line { margin: 0 0 0.4rem; font-size: 0.88rem; color: #e6e8ec; }
  .prov { display: flex; flex-wrap: wrap; gap: 0.45rem; align-items: center; font-size: 0.74rem; color: #8b929e; }
  .take { color: #e6e8ec; font-weight: 600; }
  .tc { font-family: ui-monospace, Consolas, monospace; color: #e6e8ec; }
  .cost { margin-top: 0.85rem; font-size: 0.85rem; color: #d9b26a; }
  .who { margin-top: 0.4rem; font-size: 0.8rem; color: #6ea8fe; }
  button {
    font: inherit; padding: 0.45rem 0.85rem; border-radius: 6px;
    border: 1px solid #2c313a; background: #22262d; color: #e6e8ec; cursor: pointer;
  }
  button:hover { border-color: #495060; }
  button.go { border-color: #8a7440; color: #d9b26a; }
  button:focus-visible { outline: 2px solid #6ea8fe; outline-offset: 1px; }
`;

function timecode(ms) {
  const total = Math.max(0, ms);
  const minutes = String(Math.floor(total / 60000)).padStart(2, "0");
  const seconds = String(Math.floor((total % 60000) / 1000)).padStart(2, "0");
  const millis = String(Math.floor(total % 1000)).padStart(3, "0");
  return `${minutes}:${seconds}.${millis}`;
}

function el(root, tag, props = {}, children = []) {
  const node = root.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    // Sadece textContent. innerHTML yok, olmayacak.
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (value !== null && value !== undefined && value !== false) {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of [].concat(children)) if (child) node.appendChild(child);
  return node;
}

/**
 * Onay ister. Söz verilen değer: onaylandı mı.
 *
 * @param {object}  options
 * @param {Array}   options.segments      timeline parçaları
 * @param {number}  options.cost          düşecek kredi
 * @param {number}  options.credits       mevcut bakiye
 * @param {string}  options.requestedBy   "agent" | "human"
 * @param {Document} [options.doc]        test edilebilirlik için
 * @returns {Promise<boolean>}
 */
export function confirmRender({
  segments,
  cost = 1,
  credits = 0,
  requestedBy = "human",
  doc = typeof document !== "undefined" ? document : null,
  autoDeclineMs = AUTO_DECLINE_MS,
}) {
  if (!doc) return Promise.resolve(false);

  const host = doc.createElement("div");
  // Sayfa CSS'i pencereyi gizleyip farkında olmadan onaylatamasın
  host.setAttribute(
    "style",
    "all:initial!important;position:fixed!important;inset:0!important;" +
      "z-index:2147483647!important;display:block!important;visibility:visible!important;" +
      "opacity:1!important;pointer-events:auto!important"
  );

  // closed: sayfadaki başka script host.shadowRoot ile içeriye ulaşamıyor
  const root = host.attachShadow({ mode: "closed" });
  root.appendChild(el(doc, "style", { text: STYLE }));

  const totalMs = segments.reduce((sum, item) => sum + (item.end_ms - item.start_ms), 0);

  const list = el(doc, "ol");
  segments.forEach((segment, index) => {
    list.appendChild(
      el(doc, "li", {}, [
        el(doc, "p", { class: "line", text: `${index + 1}. ${segment.text || "(metin yok)"}` }),
        el(doc, "div", { class: "prov" }, [
          el(doc, "span", { class: "take", text: segment.take_id }),
          el(doc, "span", { text: `kam ${segment.camera || "-"}` }),
          el(doc, "span", { text: TONE_LABELS[segment.tone] ?? segment.tone ?? "-" }),
          el(doc, "span", {
            class: "tc",
            text: `${timecode(segment.start_ms)} → ${timecode(segment.end_ms)}`,
          }),
          el(doc, "span", { text: segment.source_url || "" }),
        ]),
      ])
    );
  });

  const cancel = el(doc, "button", { type: "button", text: "Vazgeç" });
  const approve = el(doc, "button", { type: "button", class: "go", text: "Onayla ve render et" });

  const heading = el(doc, "h2", { id: "render-title", text: "Bu kesim render edilsin mi?" });
  const body = el(doc, "div", { class: "body" }, [
    list,
    el(doc, "p", {
      class: "cost",
      text: `${cost} kredi düşecek. Bakiye ${credits} → ${credits - cost}.`,
    }),
    requestedBy === "agent"
      ? el(doc, "p", {
          class: "who",
          text: "Bu render'ı asistan istedi. Onayı sen veriyorsun.",
        })
      : null,
  ]);

  const sheet = el(doc, "div", { class: "sheet", role: "document" }, [
    el(doc, "header", {}, [
      heading,
      el(doc, "p", {
        text:
          `${segments.length} parça, toplam ${(totalMs / 1000).toFixed(2)} saniye. ` +
          "Buraya kadar hiçbir şey render edilmedi; onaylarsan dosya üretilecek.",
      }),
    ]),
    body,
    el(doc, "footer", {}, [cancel, approve]),
  ]);

  root.appendChild(
    el(doc, "div", {
      class: "backdrop",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "render-title",
    }, [sheet])
  );

  const previouslyFocused = doc.activeElement;

  return new Promise((resolve) => {
    let settled = false;

    function close(approved) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      doc.removeEventListener("keydown", onKeyDown, true);
      host.remove();
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
      resolve(approved);
    }

    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
        return;
      }
      // Odak tuzağı: Tab pencereden çıkıp arkadaki sayfaya gitmesin
      if (event.key === "Tab") {
        const focusable = [cancel, approve];
        const active = root.activeElement;
        const index = focusable.indexOf(active);
        const next = event.shiftKey
          ? focusable[(index - 1 + focusable.length) % focusable.length]
          : focusable[(index + 1) % focusable.length];
        event.preventDefault();
        next.focus();
      }
    }

    cancel.addEventListener("click", () => close(false));
    approve.addEventListener("click", () => close(true));
    doc.addEventListener("keydown", onKeyDown, true);

    // Ajan çağırıp insan masadan kalkarsa aracın promise'i sonsuza kadar
    // beklemesin. Zaman aşımı ONAY DEĞİL, reddetme yönünde.
    const timer = setTimeout(() => close(false), autoDeclineMs);

    doc.body.appendChild(host);
    // Varsayılan odak Vazgeç'te: yanlışlıkla Enter'a basmak render başlatmasın
    cancel.focus();
  });
}
