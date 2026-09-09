/**
 * The virtual-splice player. It produces no file — it only seeks and plays.
 *
 * The product's "see it without rendering" claim happens here: hearing a rough cut
 * encodes nothing, it jumps back and forth inside the source files. Which is why a
 * proposal is playable immediately and costs no credit.
 *
 * Two design decisions make it usable:
 *
 * 1. requestAnimationFrame, NOT `timeupdate`. timeupdate fires about four times a
 *    second, so it can overshoot a cut point by up to 250ms. We cut on word
 *    boundaries, and 250ms means you also hear the next word. rAF gives about 16ms.
 *
 * 2. Double buffering. One element changing src/currentTime per segment leaves a
 *    loading gap at every transition. Two elements alternate: while one plays, the
 *    other is positioned on the next segment, so the transition is just a play() call.
 *
 * The media server must support HTTP range, which StaticFiles does; without it every
 * seek would download the whole file.
 */

const SEEK_TOLERANCE_S = 0.005;
const LOAD_TIMEOUT_MS = 10000;

function waitFor(element, eventName, timeoutMs = LOAD_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      element.removeEventListener(eventName, onEvent);
      element.removeEventListener("error", onError);
      clearTimeout(timer);
    };
    const onEvent = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error(`Could not load media: ${element.currentSrc || element.src}`));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Media timed out: ${element.src}`));
    }, timeoutMs);

    element.addEventListener(eventName, onEvent, { once: true });
    element.addEventListener("error", onError, { once: true });
  });
}

function createElement() {
  const element = document.createElement("video");
  element.preload = "auto";
  element.playsInline = true;
  element.controls = false;
  element.className = "stage-media";
  return element;
}

export function createPlayer({ mount, onProgress, onSegmentChange, onEnd, onError }) {
  const elements = [createElement(), createElement()];
  elements.forEach((element) => mount.appendChild(element));

  let activeIndex = 0;
  let segments = [];
  let cursor = -1;
  let frame = null;
  let generation = 0; // stops a stale async prepare from disturbing a new playback

  const active = () => elements[activeIndex];
  const standby = () => elements[1 - activeIndex];

  async function prepare(element, segment) {
    if (!segment) return;
    const target = segment.start_ms / 1000;

    if (element.dataset.src !== segment.media_url) {
      element.src = segment.media_url;
      element.dataset.src = segment.media_url;
      await waitFor(element, "loadedmetadata");
    }

    if (Math.abs(element.currentTime - target) > SEEK_TOLERANCE_S) {
      element.currentTime = target;
      // 'seeked' may not fire if we are already on target, hence the check above
      await waitFor(element, "seeked");
    }
  }

  function showActive() {
    elements.forEach((element, index) => {
      element.classList.toggle("is-active", index === activeIndex);
    });
  }

  function stopFrame() {
    if (frame !== null) {
      cancelAnimationFrame(frame);
      frame = null;
    }
  }

  function tick(myGeneration) {
    if (myGeneration !== generation) return;

    const segment = segments[cursor];
    if (!segment) return;

    const element = active();
    const positionMs = element.currentTime * 1000;

    if (positionMs >= segment.end_ms || element.ended) {
      advance(myGeneration);
      return;
    }

    onProgress?.({
      index: cursor,
      offsetMs: Math.max(0, positionMs - segment.start_ms),
      segment,
    });
    frame = requestAnimationFrame(() => tick(myGeneration));
  }

  async function advance(myGeneration) {
    const prevIndex = activeIndex;
    const prevElement = elements[prevIndex];

    cursor += 1;
    if (cursor >= segments.length) {
      stopFrame();
      elements.forEach((element) => {
        element.pause();
      });
      cursor = -1;
      segments = [];
      onEnd?.();
      return;
    }

    const nextIndex = 1 - activeIndex;
    const nextElement = elements[nextIndex];
    const segment = segments[cursor];

    try {
      // Pause the previous element immediately so unselected footage does not leak
      prevElement.pause();

      // If preloading did not keep up we wait here: a gap, but never a skip
      await prepare(nextElement, segment);
      if (myGeneration !== generation) return;
      await nextElement.play();
      if (myGeneration !== generation) {
        nextElement.pause();
        return;
      }

      // Smooth handoff: next element is actively playing, switch visibility
      activeIndex = nextIndex;
      showActive();
    } catch (error) {
      if (myGeneration === generation) onError?.(error);
      return;
    }

    onSegmentChange?.({ index: cursor, segment });
    prepare(standby(), segments[cursor + 1]).catch(() => {
      // A preload failure is not fatal; it will be retried when its turn comes
    });
    frame = requestAnimationFrame(() => tick(myGeneration));
  }

  /**
   * Plays a cut, optionally starting partway in.
   *
   * `startIndex` takes the WHOLE cut and begins at one of its segments rather than
   * receiving a slice. That matters because the reported index has to line up with the
   * timeline the store holds: hand in a slice and segment 3 comes back as segment 0,
   * and the interface highlights the wrong block.
   */
  async function play(nextSegments, startIndex = 0) {
    stop();
    if (!nextSegments?.length) return;

    const first = Math.min(Math.max(startIndex, 0), nextSegments.length - 1);

    generation += 1;
    const myGeneration = generation;
    segments = nextSegments;
    cursor = first;
    activeIndex = 0;
    showActive();

    try {
      await prepare(active(), segments[first]);
      if (myGeneration !== generation) return;
      await active().play();
    } catch (error) {
      onError?.(error);
      return;
    }

    onSegmentChange?.({ index: first, segment: segments[first] });
    prepare(standby(), segments[first + 1]).catch(() => {});
    frame = requestAnimationFrame(() => tick(myGeneration));
  }

  /** Previews one segment. What sits behind the preview_segment tool. */
  function preview(segment) {
    return play([segment]);
  }

  function stop() {
    generation += 1;
    stopFrame();
    elements.forEach((element) => {
      element.pause();
    });
    cursor = -1;
    segments = [];
  }

  return { play, preview, stop };
}
