"""Render işçisi. Onaylanmış kesimi tek dosyaya birleştiriyor.

GÜVENLİK — kesilecek dosya yolu İSTEKTEN GELMİYOR.

İstek sadece `candidate_id` (`take_id:line_id:start_ms`) ve zaman aralığı taşıyor.
Medya yolu ClickHouse'daki `source_url`'den türetiliyor, `MEDIA_DIR` altına çözülüyor
ve gerçekten orada olduğu doğrulanıyor. İstemciye dosya adı söyletmek path traversal
demek olurdu (`../../.env`), ve ffmpeg'e verilen her yol okunabilir bir dosyadır.

Zaman aralığı da doğrulanıyor: take'in bilinen süresinin dışına taşamıyor ve toplam
çıktı süresi sınırlı. Aksi halde tek istekle saatlerce CPU yakılabilir.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import ch, config

# Bir çıktının en fazla süresi. Tek istekle saatlerce CPU yakılmasını engelliyor.
MAX_OUTPUT_MS = 10 * 60 * 1000
# Segment kenarlarında izin verilen pay: kurgucu birkaç kare nefes bırakmak isteyebilir.
EDGE_TOLERANCE_MS = 500
FFMPEG_TIMEOUT_S = 300


@dataclass
class Job:
    id: str
    session_id: str
    status: str = "queued"          # queued -> running -> done | failed
    segments: int = 0
    duration_ms: int = 0
    mode: str = ""                  # "audio" | "video"
    output: Path | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict:
        payload = {
            "job_id": self.id,
            "status": self.status,
            "segments": self.segments,
            "duration_ms": self.duration_ms,
            "mode": self.mode or None,
        }
        if self.status == "done":
            payload["download_url"] = f"/api/render/{self.id}/file"
            payload["seconds"] = round((self.finished_at or 0) - self.created_at, 1)
        if self.error:
            payload["error"] = self.error
        return payload


# İşler bellekte. Oturumlar gibi geçici: yeniden başlatmada kayboluyorlar ve bu
# kabul edilen davranış, kalıcı bir kuyruk bu ölçekte gereksiz karmaşıklık.
_jobs: dict[str, Job] = {}


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def active_for_session(session_id: str) -> int:
    return sum(
        1
        for job in _jobs.values()
        if job.session_id == session_id and job.status in ("queued", "running")
    )


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


class RenderRejected(Exception):
    """İstek doğrulamayı geçemedi. Kredi harcanmadan reddediliyor."""


def resolve_media(source_url: str) -> Path:
    """`source_url` -> MEDIA_DIR altındaki gerçek dosya.

    Sadece taban adı alınıyor, sonra çözülen yolun MEDIA_DIR içinde kaldığı
    doğrulanıyor. İkisi birlikte path traversal'ı kapatıyor.
    """
    name = Path(source_url.replace("\\", "/")).name
    if not name or name in (".", ".."):
        raise RenderRejected(f"Kullanılamaz kaynak adı: {source_url!r}")

    root = config.MEDIA_DIR.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise RenderRejected(f"Medya yolu izinli dizinin dışında: {name!r}")
    if not candidate.is_file():
        raise RenderRejected(f"Medya bulunamadı: {name!r}")
    return candidate


def take_bounds(project: str, take_ids: list[str]) -> dict[str, dict]:
    """Take başına kaynak dosya ve bilinen süre. Doğrulamanın dayanağı bu."""
    result = ch.client().query(
        """
        SELECT take_id, any(source_url) AS source_url, max(end_ms) AS last_ms
        FROM words
        WHERE project_id = {project:String} AND take_id IN {takes:Array(String)}
        GROUP BY take_id
        """,
        parameters={"project": project, "takes": take_ids},
    )
    return {
        row[0]: {"source_url": row[1], "last_ms": int(row[2])}
        for row in result.result_rows
    }


def plan(project: str, requested: list[dict]) -> tuple[list[dict], int]:
    """İsteği doğrulanmış render planına çevirir. (plan, toplam süre) döner."""
    if not requested:
        raise RenderRejected("Kesim boş.")

    take_ids: list[str] = []
    for item in requested:
        # candidate_id formatı: take_id:line_id:start_ms
        take_id = str(item["candidate_id"]).split(":")[0]
        if not take_id:
            raise RenderRejected(f"Kullanılamaz aday kimliği: {item['candidate_id']!r}")
        take_ids.append(take_id)

    bounds = take_bounds(project, sorted(set(take_ids)))
    unknown = sorted(set(take_ids) - set(bounds))
    if unknown:
        raise RenderRejected(f"Kütüphanede olmayan take: {', '.join(unknown)}")

    steps: list[dict] = []
    total = 0
    for item, take_id in zip(requested, take_ids):
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
        if end_ms <= start_ms:
            raise RenderRejected(f"Geçersiz aralık: {start_ms}-{end_ms}")

        limit = bounds[take_id]["last_ms"] + EDGE_TOLERANCE_MS
        if start_ms < 0 or end_ms > limit:
            raise RenderRejected(
                f"{take_id} için aralık kaydın dışında: {start_ms}-{end_ms} ms, "
                f"kayıt {limit} ms'e kadar"
            )

        total += end_ms - start_ms
        if total > MAX_OUTPUT_MS:
            raise RenderRejected(
                f"Çıktı çok uzun: {total} ms, en fazla {MAX_OUTPUT_MS} ms"
            )

        steps.append(
            {
                "take_id": take_id,
                "media": resolve_media(bounds[take_id]["source_url"]),
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )

    return steps, total


def has_video(media: Path) -> bool:
    """Dosyada video akışı var mı.

    imageio-ffmpeg ffprobe getirmiyor, o yüzden ffmpeg'in kendi çıktısını okuyoruz.
    Girdisiz çalıştırıldığında ffmpeg akış özetini stderr'e yazıp hata veriyor;
    bize gereken tam olarak o özet.
    """
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(media)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    return "Video:" in (result.stderr or "")


def build_command(steps: list[dict], out: Path, mode: str) -> list[str]:
    """concat FILTRESI kullanıyoruz, concat demuxer değil.

    Demuxer tüm girdilerin aynı codec ve parametrelerde olmasını istiyor; farklı
    take'ler farklı kayıtlardan gelebilir. Filtre yeniden encode ediyor ve bunu
    tolere ediyor. Ortak örnekleme hızına normalize etmek de aynı sebeple.
    """
    command = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"]
    for step in steps:
        command += [
            "-ss", f"{step['start_ms'] / 1000:.3f}",
            "-t", f"{(step['end_ms'] - step['start_ms']) / 1000:.3f}",
            "-i", str(step["media"]),
        ]

    count = len(steps)
    if mode == "video":
        labels = "".join(f"[{index}:v:0][{index}:a:0]" for index in range(count))
        filtergraph = f"{labels}concat=n={count}:v=1:a=1[v][a]"
        command += [
            "-filter_complex", filtergraph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000",
        ]
    else:
        parts = "".join(
            f"[{index}:a:0]aformat=sample_rates=48000:channel_layouts=stereo[a{index}];"
            for index in range(count)
        )
        labels = "".join(f"[a{index}]" for index in range(count))
        filtergraph = f"{parts}{labels}concat=n={count}:v=0:a=1[a]"
        command += ["-filter_complex", filtergraph, "-map", "[a]", "-c:a", "pcm_s16le"]

    return command + [str(out)]


def run_blocking(job: Job, steps: list[dict]) -> None:
    """FFmpeg'i çalıştırır. Ayrı bir thread'de, event loop'u bloke etmesin."""
    try:
        job.status = "running"
        job.mode = "video" if all(has_video(step["media"]) for step in steps) else "audio"
        suffix = ".mp4" if job.mode == "video" else ".wav"

        output_dir = config.APP_ROOT / "scratch" / "renders"
        output_dir.mkdir(parents=True, exist_ok=True)
        # Geçici dosyaya yazıp taşıyoruz: yarım kalmış çıktı indirilebilir olmasın
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / f"out{suffix}"
            result = subprocess.run(
                build_command(steps, staging, job.mode),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=FFMPEG_TIMEOUT_S,
            )
            if result.returncode != 0 or not staging.is_file():
                raise RuntimeError((result.stderr or "ffmpeg başarısız").strip()[:500])

            final = output_dir / f"{job.id}{suffix}"
            final.write_bytes(staging.read_bytes())

        job.output = final
        job.status = "done"
    except Exception as error:
        job.status = "failed"
        job.error = str(error)
    finally:
        job.finished_at = time.time()


def enqueue(session_id: str, project: str, requested: list[dict]) -> tuple[Job, list[dict], int]:
    """Doğrular ve iş kaydı oluşturur. Doğrulama başarısızsa kredi harcanmıyor."""
    if active_for_session(session_id) >= config.MAX_CONCURRENT_RENDERS_PER_SESSION:
        raise RenderRejected(
            "Bu oturumda zaten bir render sürüyor. Bitmesini bekle."
        )

    steps, total = plan(project, requested)
    job = Job(
        id=uuid.uuid4().hex[:12],
        session_id=session_id,
        segments=len(steps),
        duration_ms=total,
    )
    _jobs[job.id] = job
    return job, steps, total


async def start(job: Job, steps: list[dict]) -> None:
    """İşi arka planda başlatır."""
    await asyncio.to_thread(run_blocking, job, steps)
