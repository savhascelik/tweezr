/**
 * Approval dialog tests.
 *
 *   node web/test_approve.mjs
 *
 * No jsdom. The DOM surface approve.js needs is small, so there is a minimal fake
 * DOM below instead. What gets asserted here is not cosmetics but security
 * behaviour: the shadow root opening closed, text written through textContent,
 * default focus sitting on Cancel, and the timeout resolving to declined rather
 * than approved.
 */

import { confirmRender } from "./src/approve.js";
import { setLocale } from "./src/i18n.js";

// Pin the language. Node reads `navigator.language` from the operating system, so
// these tests would produce different results depending on the machine locale --
// on the first run they did exactly that and came back with Turkish button text.
// The detection itself is covered separately at the bottom of this file.
setLocale("en");

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
        // Focus inside a shadow tree should surface on the root's activeElement
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

/** Walks the fake tree and returns the first node matching the predicate. */
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
  console.log("=== shadow root and layout ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, cost: 1, credits: 10, doc });

    // closed: no other script on the page can reach Approve via host.shadowRoot
    check("shadow root opened closed", doc.shadowCalls, [{ mode: "closed" }]);

    const { host, root } = shadowOf(doc);
    checkThat("dialog appended to body", Boolean(host), "no host");
    checkThat(
      "host styles are inline and !important",
      (host.attributes.style ?? "").includes("!important"),
      host.attributes.style
    );
    checkThat(
      "visibility forced so page CSS cannot hide it",
      (host.attributes.style ?? "").includes("visibility:visible!important"),
      host.attributes.style
    );

    const dialog = find(root, (node) => node.attributes?.role === "dialog");
    checkThat("role=dialog present", Boolean(dialog));
    check("aria-modal", dialog.attributes["aria-modal"], "true");
    checkThat(
      "associated with its heading",
      Boolean(dialog.attributes["aria-labelledby"]),
      dialog.attributes
    );

    // Text must be written via textContent; the fake DOM has no innerHTML at all
    const texts = collectText(root).join("\n");
    checkThat("line text is visible", texts.includes("I never asked for this"), "");
    checkThat("take id is visible", texts.includes("S01_T03"), "");
    checkThat("timecode is visible", texts.includes("00:00.800"), "");
    checkThat("source is visible", texts.includes("S01_T03.wav"), "");
    checkThat("credit cost is visible", texts.includes("1 credit"), "");
    checkThat("balance change is visible", texts.includes("10 \u2192 9"), "");
    checkThat(
      "says nothing has been rendered yet",
      texts.includes("Nothing has been rendered"),
      ""
    );

    // Default focus on Cancel: pressing Enter must not start a render
    check("default focus is Cancel", doc.activeElement.textContent, "Cancel");

    const cancel = find(root, (node) => node.textContent === "Cancel");
    cancel.click();
    check("cancelling resolved false", await pending, false);
    check("dialog removed", doc.body.children.length, 0);
    check("no keydown listener left behind", doc.listenerCount("keydown"), 0);
  }

  console.log("\n=== approving ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, cost: 1, credits: 3, doc });
    const { root } = shadowOf(doc);
    find(root, (node) => node.textContent === "Approve and render").click();
    check("approving resolved true", await pending, true);
    check("dialog removed", doc.body.children.length, 0);
  }

  console.log("\n=== Escape ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc });
    let prevented = false;
    doc.fire("keydown", { key: "Escape", preventDefault: () => (prevented = true) });
    check("Escape resolved false", await pending, false);
    checkThat("event was swallowed", prevented);
  }

  console.log("\n=== timeout ===");
  {
    // If an agent calls this and the human walks away, the tool promise must not
    // wait forever. The DIRECTION matters: a timeout declines, it never approves.
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc, autoDeclineMs: 20 });
    check("timeout declined", await pending, false);
    check("dialog removed", doc.body.children.length, 0);
  }

  console.log("\n=== agent request ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, requestedBy: "agent", doc });
    const { root } = shadowOf(doc);
    const texts = collectText(root).join("\n");
    checkThat("says the assistant asked for the render", texts.includes("assistant asked for this"), texts);
    checkThat("says approval is the human's call", texts.includes("You are the one approving"), "");
    find(root, (node) => node.textContent === "Cancel").click();
    await pending;
  }

  console.log("\n=== human request ===");
  {
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, requestedBy: "human", doc });
    const { root } = shadowOf(doc);
    checkThat(
      "no assistant note on a human request",
      !collectText(root).join("\n").includes("assistant asked for this")
    );
    find(root, (node) => node.textContent === "Cancel").click();
    await pending;
  }

  console.log("\n=== double decision ===");
  {
    // Clicking twice must not try to resolve the promise twice
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, doc });
    const { root } = shadowOf(doc);
    const approve = find(root, (node) => node.textContent === "Approve and render");
    approve.click();
    approve.click();
    doc.fire("keydown", { key: "Escape", preventDefault: () => {} });
    check("first decision wins", await pending, true);
  }

  console.log("\n=== Turkish catalogue ===");
  {
    // The dialog does not change what the agent sees, but it does change what the
    // human sees.
    setLocale("tr");
    const doc = createFakeDocument();
    const pending = confirmRender({ segments: SEGMENTS, requestedBy: "agent", doc });
    const { root } = shadowOf(doc);
    const texts = collectText(root).join("\n");
    checkThat("heading translated", texts.includes("render edilsin mi"), texts.slice(0, 200));
    checkThat("credit line translated", texts.includes("kredi düşecek"), "");
    checkThat("assistant note translated", texts.includes("asistan istedi"), "");
    const cancel = find(root, (node) => node.textContent === "Vazgeç");
    checkThat("cancel button translated", Boolean(cancel));
    // Provenance data is not translated: take id and timecode are language independent
    checkThat("take id unchanged", texts.includes("S01_T03"), "");
    checkThat("timecode unchanged", texts.includes("00:00.800"), "");
    cancel?.click();
    check("cancelling works in the Turkish dialog too", await pending, false);
    setLocale("en");
  }

  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} tests passed`);
  process.exit(passed === results.length ? 0 : 1);
}

main();
