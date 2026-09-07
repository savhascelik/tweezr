/**
 * Onay penceresi testleri.
 *
 *   node web/test_approve.mjs
 *
 * jsdom yok. approve.js'in DOM'dan istediği yüzey küçük olduğu için burada minimal
 * bir sahte DOM var. Test edilen şeyler kozmetik değil, güvenlik davranışları:
 * shadow root'un closed açılması, metnin textContent ile yazılması, varsayılan odağın
 * Vazgeç'te olması, zaman aşımının ONAY DEĞİL red yönünde çözülmesi.
 */

import { confirmRender } from "./src/approve.js";

const results = [];

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push(ok);
  console.log(`  ${ok ? "GEÇTİ" : "BAŞARISIZ"}      ${name}`);
  if (!ok) {
    console.log(`               beklenen: ${JSON.stringify(expected)}`);
    console.log(`               gelen   : ${JSON.stringify(actual)}`);
  }
}

function checkThat(name, condition, detail = "") {
  results.push(Boolean(condition));
  console.log(`  ${condition ? "GEÇTİ" : "BAŞARISIZ"}      ${name}`);
  if (!condition && detail) console.log(`               ${detail}`);
}

// --- Minimal sahte DOM ------------------------------------------------------

function createFakeDocument() {
  const shadowCalls = [];
  const listeners = new Map();

  function createElement(tag) {
    const node = {
      tag,
      children: [],
      attributes: {},
      textContent: "",
      className: "",
      parent: null,
      shadow: null,
      handlers: new Map(),
      focused: false,
      setAttribute(name, value) {
        this.attributes[name] = value;
      },
      appendChild(child) {
        child.parent = this;
        this.children.push(child);
        return child;
      },
      addEventListener(type, handler) {
        if (!this.handlers.has(type)) this.handlers.set(type, []);
        this.handlers.get(type).push(handler);
      },
      click() {
        for (const handler of this.handlers.get("click") ?? []) handler({});
      },
      focus() {
        doc.activeElement = this;
        if (this.parent?.isShadowRoot) this.parent.activeElement = this;
        // Shadow içindeki odak, kökün activeElement'ine yansısın
        let node = this.parent;
        while (node) {
          if (node.isShadowRoot) node.activeElement = this;
          node = node.parent;
        }
        this.focused = true;
      },
      remove() {
        if (!this.parent) return;
        const index = this.parent.children.indexOf(this);
        if (index >= 0) this.parent.children.splice(index, 1);
        this.parent = null;
      },
      attachShadow(options) {
        shadowCalls.push(options);
        this.shadow = {
          isShadowRoot: true,
          children: [],
          parent: this,
          activeElement: null,
          appendChild(child) {
            child.parent = this;
            this.children.push(child);
            return child;
          },
        };
        return this.shadow;
      },
    };
    return node;
  }

  const body = createElement("body");
  const doc = {
    body,
    activeElement: null,
    shadowCalls,
    createElement,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      const list = listeners.get(type) ?? [];
      const index = list.indexOf(handler);
      if (index >= 0) list.splice(index, 1);
    },
    fire(type, event) {
      for (const handler of [...(listeners.get(type) ?? [])]) handler(event);
    },
    listenerCount(type) {
      return (listeners.get(type) ?? []).length;
    },
  };
  return doc;
}

/** Sahte ağacı dolaşıp koşula uyan ilk düğümü bulur. */
function find(node, predicate) {
  if (!node) return null;
  if (predicate(node)) return node;
  const children = node.children ?? [];
  for (const child of children) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}

function collectText(node, out = []) {
  if (!node) return out;
  if (node.textContent) out.push(node.textContent);
  for (const child of node.children ?? []) collectText(child, out);
  return out;
}

function shadowOf(doc) {
  const host = doc.body.children.at(-1);
  return { host, root: host?.shadow };
}

const SEGMENTS = [
  {
    id: "S01_T03:1:800",
    take_id: "S01_T03",
    camera: "A",
    tone: "calm",
    start_ms: 800,
    end_ms: 2700,
    text: "I never asked for this",
    source_url: "S01_T03.wav",
  },
  {
    id: "S01_T01:2:3400",
    take_id: "S01_T01",
    camera: "B",
    tone: "tense",
    start_ms: 3400,
    end_ms: 4380,
    text: "Just let me go",
    source_url: "S01_T01.wav",
  },
];

async function main() {
  console.log("=== shadow root ve yerleşim ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, cost: 1, credits: 10, doc });

    // closed: sayfadaki başka script host.shadowRoot ile Approve'a ulaşamıyor
    check("shadow root closed açıldı", doc.shadowCalls, [{ mode: "closed" }]);

    const { host, root } = shadowOf(doc);
    checkThat("pencere body'ye eklendi", Boolean(host), "host yok");
    checkThat(
      "host stilleri inline ve !important",
      (host.attributes.style ?? "").includes("!important"),
      host.attributes.style
    );
    checkThat(
      "sayfa CSS'i gizleyemesin diye visibility zorlanmış",
      (host.attributes.style ?? "").includes("visibility:visible!important"),
      host.attributes.style
    );

    const dialog = find(root, (node) => node.attributes?.role === "dialog");
    checkThat("role=dialog var", Boolean(dialog));
    check("aria-modal", dialog.attributes["aria-modal"], "true");
    checkThat(
      "başlıkla ilişkilendirilmiş",
      Boolean(dialog.attributes["aria-labelledby"]),
      dialog.attributes
    );

    // Metin textContent ile yazılmış olmalı; innerHTML sahte DOM'da hiç yok
    const texts = collectText(root).join("\n");
    checkThat("replik metni görünüyor", texts.includes("I never asked for this"), "");
    checkThat("take kimliği görünüyor", texts.includes("S01_T03"), "");
    checkThat("timecode görünüyor", texts.includes("00:00.800"), "");
    checkThat("kaynak görünüyor", texts.includes("S01_T03.wav"), "");
    checkThat("kredi bilgisi görünüyor", texts.includes("1 kredi"), "");
    checkThat("bakiye değişimi görünüyor", texts.includes("10 → 9"), "");
    checkThat(
      "render edilmediği söyleniyor",
      texts.includes("hiçbir şey render edilmedi"),
      ""
    );

    // Varsayılan odak Vazgeç'te: Enter'a basmak render başlatmasın
    check("varsayılan odak Vazgeç", doc.activeElement.textContent, "Vazgeç");

    const cancel = find(root, (node) => node.textContent === "Vazgeç");
    cancel.click();
    check("vazgeçmek false döndü", await pending, false);
    check("pencere kaldırıldı", doc.body.children.length, 0);
    check("keydown dinleyicisi bırakılmadı", doc.listenerCount("keydown"), 0);
  }

  console.log("\n=== onaylama ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, cost: 1, credits: 3, doc });
    const { root } = shadowOf(doc);
    find(root, (node) => node.textContent === "Onayla ve render et").click();
    check("onaylamak true döndü", await pending, true);
    check("pencere kaldırıldı", doc.body.children.length, 0);
  }

  console.log("\n=== Escape ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc });
    let prevented = false;
    doc.fire("keydown", { key: "Escape", preventDefault: () => (prevented = true) });
    check("Escape false döndü", await pending, false);
    checkThat("olay yutuldu", prevented);
  }

  console.log("\n=== zaman aşımı ===");
  {
    // Ajan çağırıp insan masadan kalkarsa aracın promise'i sonsuza beklemesin.
    // Yön ÖNEMLİ: zaman aşımı onay değil red.
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc, autoDeclineMs: 20 });
    check("zaman aşımı reddetti", await pending, false);
    check("pencere kaldırıldı", doc.body.children.length, 0);
  }

  console.log("\n=== ajan isteği ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, requestedBy: "agent", doc });
    const { root } = shadowOf(doc);
    const texts = collectText(root).join("\n");
    checkThat("render'ı asistanın istediği yazıyor", texts.includes("asistan istedi"), texts);
    checkThat("onayın insanda olduğu yazıyor", texts.includes("Onayı sen veriyorsun"), "");
    find(root, (node) => node.textContent === "Vazgeç").click();
    await pending;
  }

  console.log("\n=== insan isteği ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, requestedBy: "human", doc });
    const { root } = shadowOf(doc);
    checkThat(
      "insan isteğinde asistan notu yok",
      !collectText(root).join("\n").includes("asistan istedi")
    );
    find(root, (node) => node.textContent === "Vazgeç").click();
    await pending;
  }

  console.log("\n=== çift karar ===");
  {
    // İki kez basmak promise'i iki kez çözmeye çalışmasın
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc });
    const { root } = shadowOf(doc);
    const approve = find(root, (node) => node.textContent === "Onayla ve render et");
    approve.click();
    approve.click();
    doc.fire("keydown", { key: "Escape", preventDefault: () => {} });
    check("ilk karar geçerli", await pending, true);
  }

  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} test geçti`);
  process.exit(passed === results.length ? 0 : 1);
}

main();
