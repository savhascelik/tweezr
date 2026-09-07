/**
 * Sanal kırpma oynatıcısı. Dosya üretmiyor — sadece arıyor ve oynatıyor.
 *
 * Ürünün "render etmeden gör" iddiası burada gerçekleşiyor: bir kaba kurguyu
 * duymak için hiçbir şey encode edilmiyor, kaynak dosyalarda ileri geri
 * atlanıyor. Bu yüzden öneri anında oynatılabilir ve kredi harcamıyor.
 *
 * İki tasarım kararı bunu kullanılabilir kılıyor:
 *
 * 1. `timeupdate` DEĞİL, requestAnimationFrame. timeupdate saniyede ~4 kez
 *    tetikleniyor, yani kesim noktasını 250 ms'e kadar kaçırabilir. Biz kelime
 *    sınırından kesiyoruz; 250 ms sonraki kelimeyi de duyurur. rAF ~16 ms veriyor.
 *
 * 2. Çift tampon. Tek element kullanıp segment başına src/currentTime değiştirmek
 *    her geçişte yükleme boşluğu bırakıyor. İki element dönüşümlü çalışıyor:
 *    biri çalarken diğeri sıradaki segmente konumlanıyor, geçiş anında sadece
 *    play() çağrılıyor.
 *
 * Medya sunucusunun HTTP range desteklemesi şart (StaticFiles destekliyor),
 * yoksa her arama dosyanın tamamını indirir.
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
      reject(new Error(`Medya yüklenemedi: ${element.currentSrc || element.src}`));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Medya zaman aşımına uğradı: ${element.src}`));
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
  let generation = 0; // eski async prepare'lerin yeni oynatmayı bozmasını engelliyor

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
      // Hedefe zaten oturmuşsa 'seeked' tetiklenmeyebilir, yukarıdaki kontrol o yüzden
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

    if (positionMs >= segment.end_ms) {
      element.pause();
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
    cursor += 1;
    if (cursor >= segments.length) {
      stopFrame();
      onEnd?.();
      return;
    }

    // Sıradaki element zaten konumlandırılmış olmalı; geçiş sadece play()
    activeIndex = 1 - activeIndex;
    showActive();

    const segment = segments[cursor];
    try {
      // Ön yükleme yetişmediyse burada bekliyoruz — boşluk olur ama atlama olmaz
      await prepare(active(), segment);
      if (myGeneration !== generation) return;
      await active().play();
    } catch (error) {
      if (myGeneration === generation) onError?.(error);
      return;
    }

    onSegmentChange?.({ index: cursor, segment });
    prepare(standby(), segments[cursor + 1]).catch(() => {
      // Ön yükleme hatası ölümcül değil; sıra gelince tekrar denenecek
    });
    frame = requestAnimationFrame(() => tick(myGeneration));
  }

  async function play(nextSegments) {
    stop();
    if (!nextSegments?.length) return;

    generation += 1;
    const myGeneration = generation;
    segments = nextSegments;
    cursor = 0;
    activeIndex = 0;
    showActive();

    try {
      await prepare(active(), segments[0]);
      if (myGeneration !== generation) return;
      await active().play();
    } catch (error) {
      onError?.(error);
      return;
    }

    onSegmentChange?.({ index: 0, segment: segments[0] });
    prepare(standby(), segments[1]).catch(() => {});
    frame = requestAnimationFrame(() => tick(myGeneration));
  }

  /** Tek segmenti önizler. preview_segment aracının arkası. */
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
