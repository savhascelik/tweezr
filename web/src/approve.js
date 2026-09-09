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

/* The palette is written out in full rather than pulled from custom properties. `all:
   initial` on the host stops :root variables inheriting in, which is the point: the page
   cannot restyle this dialog, so it cannot make the Approve button look like something
   else. The webfont still resolves, because @font-face is document scoped and reaches
   into shadow trees. */
const STYLE = `
  :host { all: initial; }
  * {
    box-sizing: border-box;
    font-family: "Plus Jakarta Sans", ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  }
  .backdrop {
    position: fixed; inset: 0; display: flex; align-items: center;
    justify-content: center; padding: 1.5rem;
    background: rgba(52, 47, 46, 0.55);
    backdrop-filter: blur(8px);
  }
  .sheet {
    background: #ffffff; color: #1e1b1a;
    border-radius: 1.5rem; width: min(38rem, 100%); max-height: 100%;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 24px 48px -12px rgba(41, 37, 36, 0.35);
  }
  header, footer { padding: 1.25rem 1.5rem; }
  footer { display: flex; gap: 0.5rem; justify-content: flex-end; background: #fbf2f0; }
  h2 { margin: 0 0 0.35rem; font-size: 1.25rem; font-weight: 600; letter-spacing: -0.01em; }
  p { margin: 0; font-size: 0.875rem; color: #5a4138; line-height: 1.6; }
  .body { overflow-y: auto; padding: 0 1.5rem 1.25rem; }
  ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
  li { border-radius: 1rem; padding: 0.75rem 1rem; background: #fbf2f0; }
  .line { margin: 0 0 0.5rem; font-size: 1rem; color: #1e1b1a; }
  .prov {
    display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
    font-size: 0.75rem; color: #5a4138;
  }
  .take {
    color: #1e1b1a; font-weight: 600;
    font-family: ui-monospace, Consolas, monospace;
  }
  .tc { font-family: ui-monospace, Consolas, monospace; color: #1e1b1a; }
  .cost {
    margin-top: 1rem; padding: 0.75rem 1rem; border-radius: 1rem;
    background: #ffdcc3; color: #2f1500; font-size: 0.875rem; font-weight: 600;
  }
  .who {
    margin-top: 0.5rem; padding: 0.75rem 1rem; border-radius: 1rem;
    background: #d2eac1; color: #0e2006; font-size: 0.875rem; font-weight: 600;
  }
  .trust-label {
    display: flex; align-items: center; gap: 0.5rem; margin-top: 0.75rem;
    font-size: 0.8125rem; color: #5a4138; cursor: pointer; user-select: none;
  }
  .trust-check {
    cursor: pointer; width: 1.05rem; height: 1.05rem; accent-color: #a33900;
  }
  button {
    font: inherit; font-size: 0.875rem; font-weight: 600;
    padding: 0.5rem 1.5rem; border-radius: 9999px;
    border: none; background: #f5ecea; color: #1e1b1a; cursor: pointer;
  }
  button:hover { background: #efe6e4; }
  button.go { background: #a33900; color: #ffffff; }
  button.go:hover { background: #cc4900; }
  button:focus-visible { outline: 2px solid #a33900; outline-offset: 2px; }
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
  autoApprove = false,
  onTrustSession = null,
}) {
  if (autoApprove) {
    return Promise.resolve(true);
  }
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
  const trustCheckbox = el(doc, "input", {
    type: "checkbox",
    id: "trust-session-render",
    class: "trust-check",
  });
  const trustLabel = el(doc, "label", {
    for: "trust-session-render",
    class: "trust-label",
  }, [
    trustCheckbox,
    el(doc, "span", { text: t("approve.trustSession") }),
  ]);

  const body = el(doc, "div", { class: "body" }, [
    list,
    el(doc, "p", {
      class: "cost",
      text: t("approve.cost", { cost, before: credits, after: credits - cost }),
    }),
    requestedBy === "agent"
      ? el(doc, "p", { class: "who", text: t("approve.agentRequested") })
      : null,
    trustLabel,
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
        const focusable = [trustCheckbox, cancel, approve];
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
    approve.addEventListener("click", () => {
      if (trustCheckbox.checked && typeof onTrustSession === "function") {
        try {
          onTrustSession(true);
        } catch (err) {
          console.error("onTrustSession failed", err);
        }
      }
      close(true);
    });
    doc.addEventListener("keydown", onKeyDown, true);

    // If the agent asks and the human walks away, the tool's promise must not wait
    // forever. The timeout resolves as DECLINED, never as approved.
    const timer = setTimeout(() => close(false), autoDeclineMs);

    doc.body.appendChild(host);
    // Focus defaults to cancel, so a stray Enter does not start a render
    cancel.focus();
  });
}
