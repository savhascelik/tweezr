/**
 * The render approval dialog. The gate on the product's single irreversible step.
 *
 * Three defences, each with a concrete reason:
 *
 * 1. **Closed shadow root.** Another script on the page cannot reach in through
 *    host.shadowRoot — closed returns null — so it cannot find the Approve button and
 *    press it programmatically. The root reference stays in this module's closure.
 *
 * 2. **textContent only.** On the previous project we built the approval dialog with
 *    innerHTML and a poisoned tool name could press its own Approve button. The strings
 *    here are take ids, dialogue and filenames — Whisper output and user files, which is
 *    data we do not control.
 *
 * 3. **Host styles inline and !important.** So page CSS cannot hide the dialog and get
 *    something approved without the user noticing.
 *
 * When the agent calls commit_render this dialog opens and the tool's promise waits on
 * the human's decision. That is the HITL gate made literal: the agent can ask, the human
 * decides.
 */

import { seconds, t, toneLabel } from "./i18n.js";

const AUTO_DECLINE_MS = 5 * 60 * 1000;

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
    // textContent only. No innerHTML, and there will not be.
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
 * Asks for approval. Resolves with whether it was approved.
 *
 * @param {object}  options
 * @param {Array}   options.segments      the timeline segments
 * @param {number}  options.cost          credits that will be spent
 * @param {number}  options.credits       the current balance
 * @param {string}  options.requestedBy   "agent" | "human"
 * @param {Document} [options.doc]        injected for testability
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
  // So page CSS cannot hide the dialog and get something approved unnoticed
  host.setAttribute(
    "style",
    "all:initial!important;position:fixed!important;inset:0!important;" +
      "z-index:2147483647!important;display:block!important;visibility:visible!important;" +
      "opacity:1!important;pointer-events:auto!important"
  );

  // closed: another script on the page cannot reach in through host.shadowRoot
  const root = host.attachShadow({ mode: "closed" });
  root.appendChild(el(doc, "style", { text: STYLE }));

  const totalMs = segments.reduce((sum, item) => sum + (item.end_ms - item.start_ms), 0);

  const list = el(doc, "ol");
  segments.forEach((segment, index) => {
    list.appendChild(
      el(doc, "li", {}, [
        el(doc, "p", {
          class: "line",
          text: `${index + 1}. ${segment.text || t("approve.noText")}`,
        }),
        el(doc, "div", { class: "prov" }, [
          el(doc, "span", { class: "take", text: segment.take_id }),
          el(doc, "span", { text: t("field.camera", { value: segment.camera || "-" }) }),
          el(doc, "span", { text: segment.tone ? toneLabel(segment.tone) : "-" }),
          el(doc, "span", {
            class: "tc",
            text: `${timecode(segment.start_ms)} → ${timecode(segment.end_ms)}`,
          }),
          el(doc, "span", { text: segment.source_url || "" }),
        ]),
      ])
    );
  });

  const cancel = el(doc, "button", { type: "button", text: t("approve.cancel") });
  const approve = el(doc, "button", {
    type: "button",
    class: "go",
    text: t("approve.confirm"),
  });

  const heading = el(doc, "h2", { id: "render-title", text: t("approve.title") });
  const body = el(doc, "div", { class: "body" }, [
    list,
    el(doc, "p", {
      class: "cost",
      text: t("approve.cost", { cost, before: credits, after: credits - cost }),
    }),
    requestedBy === "agent"
      ? el(doc, "p", { class: "who", text: t("approve.agentRequested") })
      : null,
  ]);

  const sheet = el(doc, "div", { class: "sheet", role: "document" }, [
    el(doc, "header", {}, [
      heading,
      el(doc, "p", {
        text: t("approve.summary", {
          count: segments.length,
          duration: seconds(totalMs),
        }),
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
      // Focus trap: Tab must not leave the dialog for the page behind it
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

    // If the agent asks and the human walks away, the tool's promise must not wait
    // forever. The timeout resolves as DECLINED, never as approved.
    const timer = setTimeout(() => close(false), autoDeclineMs);

    doc.body.appendChild(host);
    // Focus defaults to cancel, so a stray Enter does not start a render
    cancel.focus();
  });
}
