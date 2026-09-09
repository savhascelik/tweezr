"""API testleri.

    python -m server.test_api

Yerel ClickHouse ve ingest edilmiş fixture gerekiyor:
    docker compose -f dev/docker-compose.yml up -d
    python -m pipeline.ingest pipeline\\fixture.json --replace

test_queries.py ile aynı desen: pytest yok, düz script. Bağımlılık eklemiyoruz.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from . import config

# Testler gerçek oturum defterine dokunmasın. init_db'den ÖNCE değiştirilmeli.
_scratch = Path(tempfile.mkdtemp(prefix="cinema-test-"))
config.SESSION_DB = _scratch / "sessions.db"

# Medya dizini de geçici: testler kendi WAV'ını üretiyor ve o dosyanın commit edilen
# demo korpusunun içine düşmemesi gerekiyor.
config.MEDIA_DIR = _scratch / "media"

# Testler kendi ClickHouse projesini kuruyor. Önceden "demo"da ne varsa ona
# bakıyorlardı ve dev/seed_demo o veriyi değiştirince üç test düştü — yani testler
# hermetik değildi. Artık kontrat fixture'ını ayrı bir projeye yazıyoruz.
API_TEST_PROJECT = "__api_test__"
config.ALLOWED_PROJECTS = frozenset({config.DEMO_PROJECT, API_TEST_PROJECT})

from fastapi.testclient import TestClient  # noqa: E402

from pipeline import db as ch  # noqa: E402
from pipeline import ingest as ingest_module  # noqa: E402
from pipeline import queries  # noqa: E402

from . import sessions  # noqa: E402
from .main import create_app  # noqa: E402

results: list[bool] = []


def check(name: str, actual, expected) -> None:
    ok = actual == expected
    results.append(ok)
    print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ':<10} {name}")
    if not ok:
        print(f"             beklenen: {expected}")
        print(f"             gelen   : {actual}")


def check_that(name: str, condition: bool, detail: str = "") -> None:
    results.append(bool(condition))
    print(f"  {'GEÇTİ' if condition else 'BAŞARISIZ':<10} {name}")
    if not condition and detail:
        print(f"             {detail}")


TEST_MEDIA_NAME = "__api_test_take__.wav"


def write_test_media() -> None:
    """Render testleri için gerçek bir WAV üretir. Sadece stdlib, platform bağımsız.

    seed_demo'nun SAPI'sine dayanmıyoruz: testler Windows dışında da koşabilmeli ve
    seed verisine bağlı olmamalı. İçerik önemsiz, süresi önemli — fixture aralıkları
    4.3 saniyeye kadar gidiyor.
    """
    import math
    import struct
    import wave

    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.MEDIA_DIR / TEST_MEDIA_NAME
    if path.is_file():
        return

    rate = 22050
    seconds = 6
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(rate * seconds):
            value = int(8000 * math.sin(2 * math.pi * 220 * index / rate))
            frames += struct.pack("<h", value)
        handle.writeframes(bytes(frames))


def seed_clickhouse():
    """Kontrat fixture'ını izole bir projeye yazar. Hazırsa istemciyi döner."""
    fixture = json.loads(
        (config.APP_ROOT / "pipeline" / "fixture.json").read_text(encoding="utf-8")
    )
    fixture["project_id"] = API_TEST_PROJECT

    # Render testleri gerçek bir dosya istiyor. Fixture'ın gs:// yolları yerine
    # ürettiğimiz WAV'a bakıyoruz; medya yolu zaten VERİTABANINDAN türetiliyor,
    # yani bu yönlendirme render'ın gerçek yolunu test ediyor.
    write_test_media()
    for take in fixture["takes"]:
        take["source_url"] = TEST_MEDIA_NAME

    try:
        client = ch.connect()
        ch.create_table(client)
        ingest_module.ingest(client, fixture, replace=True)
        return client
    except Exception as error:
        print(f"  ATLANDI    ClickHouse hazırlanamadı: {error}")
        print("             docker compose -f dev/docker-compose.yml up -d")
        return None


def main() -> int:
    app = create_app()
    clickhouse = seed_clickhouse()

    print("=== medya URL eşlemesi ===")
    from . import render as render_worker
    from . import routes

    check(
        "gs:// taban adına indi",
        routes.media_url("gs://bucket/S01_T03.mp4"),
        "/media/S01_T03.mp4",
    )
    check("düz ad", routes.media_url("take.wav"), "/media/take.wav")
    check("http olduğu gibi kalıyor", routes.media_url("https://cdn/x.mp4"), "https://cdn/x.mp4")
    check("mutlak yol korunuyor", routes.media_url("/media/x.wav"), "/media/x.wav")

    print("\n=== medya yolu çözümü (path traversal) ===")
    # Yol istekten gelmiyor ama yine de dizin dışına çıkmadığını doğruluyoruz:
    # bu fonksiyon ffmpeg'e verilecek dosyayı belirliyor.
    write_test_media()
    resolved = render_worker.resolve_media(f"gs://bucket/{TEST_MEDIA_NAME}")
    check_that(
        "izinli dizin içinde çözüldü",
        resolved.parent.resolve() == config.MEDIA_DIR.resolve(),
        str(resolved),
    )
    for hostile in ["../../.env", "..\\..\\.env", "/etc/passwd", "..", ""]:
        rejected = False
        try:
            render_worker.resolve_media(hostile)
        except Exception:
            rejected = True
        check_that(f"reddedildi: {hostile!r}", rejected)

    print("\n=== güvenlik header'ları ===")
    with TestClient(app) as client:
        health = client.get("/healthz")
        check("healthz 200", health.status_code, 200)
        # WebMCP'nin çalışması buna bağlı. Eksikse registerTool sessizce reddediyor.
        check(
            "Origin-Agent-Cluster: ?1",
            health.headers.get("origin-agent-cluster"),
            "?1",
        )
        check("nosniff", health.headers.get("x-content-type-options"), "nosniff")

    print("\n=== oturum ===")
    with TestClient(app) as client:
        first = client.get("/api/session")
        check("ilk çağrı 200", first.status_code, 200)
        payload = first.json()
        check("rol guest", payload["session"]["role"], "guest")
        check("açılış kredisi", payload["session"]["credits"], config.GUEST_CREDITS)
        check("arama bedava", payload["session"]["costs"]["find_line"], 0)
        check("render 1 kredi", payload["session"]["costs"]["commit_render"], 1)
        check_that(
            "çerez kuruldu",
            config.SESSION_COOKIE in client.cookies,
            f"çerezler: {dict(client.cookies)}",
        )
        # Oturum kimliği gövdede DÖNMEMELİ
        check_that(
            "oturum kimliği gövdede yok",
            "id" not in payload["session"] and "session_id" not in payload,
            str(payload),
        )

        second = client.get("/api/session")
        check(
            "oturum korunuyor",
            second.json()["session"]["credits"],
            config.GUEST_CREDITS,
        )

    print("\n=== girdi doğrulama ===")
    with TestClient(app) as client:
        check(
            "bilinmeyen ton 422",
            client.post("/api/find_line", json={"phrase": "x", "tone": "sarcastic"}).status_code,
            422,
        )
        check(
            "bilinmeyen proje 404",
            client.post("/api/find_line", json={"phrase": "x", "project": "gizli"}).status_code,
            404,
        )
        check(
            "boş cümle 422",
            client.post("/api/find_line", json={"phrase": "   "}).status_code,
            422,
        )
        check(
            "çok uzun cümle 422",
            client.post(
                "/api/find_line", json={"phrase": " ".join(["go"] * 50)}
            ).status_code,
            422,
        )

    print("\n=== arama (ClickHouse) ===")
    if clickhouse is None:
        print("  ATLANDI    ClickHouse yok, arama testleri koşulmadı")
    else:
        with TestClient(app) as client:
            def find(**kwargs):
                return client.post(
                    "/api/find_line", json={"project": API_TEST_PROJECT, **kwargs}
                )

            found = find(phrase="I never asked for this")
            check("arama 200", found.status_code, 200)
            body = found.json()
            check("3 aday", len(body["candidates"]), 3)
            check("kredi harcanmadı", body["session"]["credits"], config.GUEST_CREDITS)

            best = body["candidates"][0]
            # Sıralama ürün mantığı: en yüksek ton skoru önce (S01_T03, calm, 0.91)
            check("en iyi aday calm", best["tone"], "calm")
            check("rank 1", best["rank"], 1)
            check("süre hesaplandı", best["duration_ms"], best["end_ms"] - best["start_ms"])
            check("kararlı kimlik", best["id"], f"{best['take_id']}:{best['line_id']}:{best['start_ms']}")
            # provenance: fragment kaynağına geri gidebilsin
            check("kaynak korundu", best["source_url"], TEST_MEDIA_NAME)
            check("oynatma URL'i", best["media_url"], f"/media/{TEST_MEDIA_NAME}")

            calm = find(phrase="I never asked for this", tone="calm").json()
            check("ton filtresi 1 aday", len(calm["candidates"]), 1)
            check("filtre doğru take", calm["candidates"][0]["take_id"], "S01_T03")

            check("bulunamayan 0 aday", find(phrase="helicopter").json()["candidates"], [])

            word = client.get(f"/api/word/asked?project={API_TEST_PROJECT}")
            check("kelime araması 200", word.status_code, 200)
            check("3 geçiş", word.json()["total"], 3)

            stats = client.get(f"/api/library/stats?project={API_TEST_PROJECT}")
            check("kütüphane istatistiği", stats.json()["stats"]["takes"], 3)

    print("\n=== render ===")
    if clickhouse is None:
        print("  ATLANDI    ClickHouse yok")
    else:
        with TestClient(app) as client:
            def render(segments):
                return client.post(
                    "/api/render", json={"project": API_TEST_PROJECT, "segments": segments}
                )

            def credits():
                return client.get("/api/session").json()["session"]["credits"]

            check(
                "geçersiz aralık 422",
                render([{"candidate_id": "S01_T03:1:800", "start_ms": 500, "end_ms": 500}]).status_code,
                422,
            )

            # Kütüphanede olmayan take. Yol istekten gelmediği için "dosya" değil
            # "take" reddediliyor — path traversal denemesi buraya bile ulaşmıyor.
            before = credits()
            traversal = render(
                [{"candidate_id": "../../../../etc/passwd:1:0", "start_ms": 0, "end_ms": 500}]
            )
            check("uydurma take reddedildi", traversal.status_code, 422)
            check_that(
                "sebep kütüphanede yok diyor",
                "take" in traversal.json()["detail"].lower(),
                traversal.json()["detail"],
            )
            # Doğrulama krediden ÖNCE: reddedilen istek kullanıcıya ödetilmiyor
            check("reddedilen istek kredi harcamadı", credits(), before)

            # Kaydın dışına taşan aralık
            outside = render(
                [{"candidate_id": "S01_T03:1:800", "start_ms": 0, "end_ms": 999_999}]
            )
            check("kayıt dışı aralık 422", outside.status_code, 422)
            check("hâlâ kredi harcanmadı", credits(), before)

            # Gerçek render
            job_id = None
            ok = render(
                [
                    {"candidate_id": "S01_T03:1:800", "start_ms": 800, "end_ms": 2700},
                    {"candidate_id": "S01_T01:1:1200", "start_ms": 1200, "end_ms": 2620},
                ]
            )
            check("render kabul edildi", ok.status_code, 200)
            if ok.status_code == 200:
                body = ok.json()
                job_id = body["job_id"]
                check("1 kredi düştü", body["charged"], 1)
                check("bakiye güncellendi", body["credits_left"], before - 1)
                check("iki parça", body["segments"], 2)
                check("süre toplandı", body["total_duration_ms"], (2700 - 800) + (2620 - 1200))

                # İş arka planda; TestClient BackgroundTasks'ı cevap sonrası koşturuyor
                status = client.get(f"/api/render/{job_id}").json()
                check("iş bitti", status["status"], "done")
                check("çıktı ses", status["mode"], "audio")
                check_that("indirme bağlantısı var", "download_url" in status, status)

                downloaded = client.get(f"/api/render/{job_id}/file")
                check("dosya indirildi", downloaded.status_code, 200)
                check_that(
                    "wav başlığı doğru",
                    downloaded.content[:4] == b"RIFF",
                    downloaded.content[:12],
                )
                # Süre kontrolü: 1900 + 1420 = 3320 ms, 48 kHz stereo 16-bit
                expected = int(48000 * 2 * 2 * 3.32)
                check_that(
                    "çıktı süresi beklenene yakın",
                    abs(len(downloaded.content) - expected) < expected * 0.1,
                    f"{len(downloaded.content)} byte, beklenen ~{expected}",
                )

            check(
                "olmayan iş 404",
                client.get("/api/render/yokboyle").status_code,
                404,
            )

        # Başka oturum aynı işe erişememeli
        if job_id:
            with TestClient(app) as other:
                other.get("/api/session")
                check(
                    "başka oturum işi göremiyor",
                    other.get(f"/api/render/{job_id}").status_code,
                    404,
                )
                check(
                    "başka oturum dosyayı indiremiyor",
                    other.get(f"/api/render/{job_id}/file").status_code,
                    404,
                )

        # Kredi bitince 402 ve iş oluşmuyor
        with TestClient(app) as broke:
            broke.get("/api/session")
            cookie = broke.cookies[config.SESSION_COOKIE]
            sessions.charge(cookie, config.GUEST_CREDITS, "testte bakiyeyi bitir")
            denied = broke.post(
                "/api/render",
                json={
                    "project": API_TEST_PROJECT,
                    "segments": [
                        {"candidate_id": "S01_T03:1:800", "start_ms": 800, "end_ms": 2700}
                    ],
                },
            )
            check("kredi yoksa 402", denied.status_code, 402)
            check_that(
                "402 aramanın bedava olduğunu söylüyor",
                "cost nothing" in denied.json()["detail"],
                denied.json()["detail"],
            )

    print("\n=== kredi defteri atomikliği ===")
    session = sessions.create(ip="test", role="guest")
    spent = 0
    failures = 0
    lock = threading.Lock()

    def spend() -> None:
        nonlocal spent, failures
        try:
            sessions.charge(session["id"], 1, "eşzamanlılık testi")
            with lock:
                spent += 1
        except sessions.InsufficientCredits:
            with lock:
                failures += 1

    # Bakiyenin iki katı kadar eşzamanlı istek. Tam bakiye kadarı geçmeli.
    threads = [threading.Thread(target=spend) for _ in range(config.GUEST_CREDITS * 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("tam bakiye kadar harcandı", spent, config.GUEST_CREDITS)
    check("fazlası reddedildi", failures, config.GUEST_CREDITS)
    check("bakiye sıfır", sessions.get(session["id"])["credits"], 0)
    check(
        "defter satırları eşleşiyor",
        len([e for e in sessions.history(session["id"], limit=100) if e["delta"] < 0]),
        config.GUEST_CREDITS,
    )

    # Bedava işlem deftere satır eklememeli, yoksa her arama defteri şişirir
    free_session = sessions.create(ip="test")
    sessions.charge(free_session["id"], 0, "bedava")
    check(
        "bedava işlem defteri şişirmiyor",
        len(sessions.history(free_session["id"], limit=100)),
        1,  # sadece açılış bakiyesi
    )

    print("\n=== sohbet ucu (anahtar yok) ===")
    with TestClient(app) as client:
        status = client.get("/api/chat/status")
        check("durum 200", status.status_code, 200)
        body = status.json()
        check("sohbet kapalı", body["available"], False)
        # Kod makine okunur, metin İngilizce yedek. İstemci kodu görüp kendi
        # dilinde yazıyor, sunucu kullanıcının dilini bilmiyor.
        check("sebep kodu", body["reason_code"], "no_api_key")
        check_that(
            "sebep anahtarı söylüyor",
            "GEMINI_API_KEY" in (body["reason"] or ""),
            body["reason"],
        )
        check_that(
            "kullanıcıya dönen mesaj İngilizce",
            all(ord(ch) < 128 for ch in (body["reason"] or "")),
            body["reason"],
        )
        check_that(
            "sebep ürünün çalıştığını söylüyor",
            "timeline" in (body["reason"] or "").lower(),
            body["reason"],
        )

        attempt = client.post("/api/chat", json={"message": "en sakin take hangisi"})
        check("sohbet 503", attempt.status_code, 503)
        check_that(
            "503 alternatif sunuyor",
            "panel" in attempt.json()["detail"].lower(),
            attempt.json()["detail"],
        )

        check("boş mesaj 422", client.post("/api/chat", json={"message": ""}).status_code, 422)
        check(
            "çok uzun mesaj 422",
            client.post("/api/chat", json={"message": "x" * 1001}).status_code,
            422,
        )

    print("\n=== sohbet sayacı atomikliği ===")
    # Kredi ile aynı sebep: iki eşzamanlı mesaj aynı sayacı okuyup ikisi de geçerse
    # sınır anlamsızlaşır. Sohbet krediyle DEĞİL ayrı sayaçla ölçülüyor.
    chat_session = sessions.create(ip="test")
    allowed = 0
    blocked = 0
    chat_lock = threading.Lock()

    def send() -> None:
        nonlocal allowed, blocked
        try:
            sessions.consume_chat(chat_session["id"], 5)
            with chat_lock:
                allowed += 1
        except sessions.ChatLimitReached:
            with chat_lock:
                blocked += 1

    threads = [threading.Thread(target=send) for _ in range(15)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("sınır kadar geçti", allowed, 5)
    check("fazlası engellendi", blocked, 10)
    check("kredi etkilenmedi", sessions.get(chat_session["id"])["credits"], config.GUEST_CREDITS)

    print("\n=== ajan araçları (LLM olmadan) ===")
    if clickhouse is None:
        print("  ATLANDI    ClickHouse yok")
    else:
        from server import agent_tools

        # Araçlar config.DEMO_PROJECT'i çağrı anında okuyor. Bu bir güvenlik
        # özelliği: ajan başka bir projeye bakmaya ikna edilemiyor. Test için
        # geçici olarak izole projeye yönlendiriyoruz.
        original_project = config.DEMO_PROJECT
        config.DEMO_PROJECT = API_TEST_PROJECT
        try:
            agent_tools.new_collection()

            found = agent_tools.find_line("I never asked for this")
            check("find_line 3 buldu", found["found"], 3)
            check("en iyi calm", found["candidates"][0]["tone"], "calm")
            check("aday kaynağını taşıyor", found["candidates"][0]["source"], TEST_MEDIA_NAME)
            check("çağrı kaydedildi", agent_tools.collection()["tool_calls"][0]["name"], "find_line")

            calm = agent_tools.find_line("I never asked for this", tone="calm")
            check("ton filtresi", calm["found"], 1)

            # Hatalar exception değil, ajanın okuyup düzeltebileceği sözlük
            bad_tone = agent_tools.find_line("x", tone="sarcastic")
            check_that("bilinmeyen ton hata döndü", "error" in bad_tone, bad_tone)
            check("izin verilen tonlar listelendi", len(bad_tone["allowed_tones"]), 6)

            long_phrase = agent_tools.find_line(" ".join(["go"] * 50))
            check_that("çok uzun cümle reddedildi", "error" in long_phrase, long_phrase)

            empty = agent_tools.find_line("   ")
            check_that("boş cümle reddedildi", "error" in empty, empty)

            missing = agent_tools.find_line("helicopter")
            check("bulunamayan 0", missing["found"], 0)
            check_that(
                "yol gösteriyor",
                "get_library_stats" in missing["message"],
                missing["message"],
            )

            # Öneri: adaylar bu konuşmada bulunmuş olmalı
            agent_tools.new_collection()
            orphan = agent_tools.assemble_proposal(["S01_T03:1:800"])
            check_that("aday yoksa öneri reddedildi", "error" in orphan, orphan)

            agent_tools.new_collection()
            agent_tools.find_line("I never asked for this")
            ids = [c["id"] for c in agent_tools.collection()["candidates"][:2]]
            proposed = agent_tools.assemble_proposal(ids)
            check("iki parça önerildi", proposed["proposed_segments"], 2)
            check("render edilmedi", proposed["rendered"], False)
            check("öneri toplayıcıda", agent_tools.collection()["proposal"], ids)
            check_that(
                "mesaj insanın değiştirebileceğini söylüyor",
                "change it" in proposed["message"],
                proposed["message"],
            )

            unknown = agent_tools.assemble_proposal(["yok:1:0"])
            check_that("bilinmeyen id reddedildi", "error" in unknown, unknown)
            check_that(
                "mevcut id'ler listelendi",
                len(unknown["available_ids"]) == 3,
                unknown,
            )

            check_that("boş liste reddedildi", "error" in agent_tools.assemble_proposal([]), "")

            stats = agent_tools.get_library_stats()
            check("kütüphane istatistiği", stats["takes"], 3)
            check_that("tonlar JSON'a çevrilebilir", isinstance(stats["tones"], list), stats)
        finally:
            config.DEMO_PROJECT = original_project

    if clickhouse is not None:
        # Test projesini bırakmıyoruz, geliştirme kütüphanesini kirletmesin
        clickhouse.command(
            queries.DROP_PROJECT, parameters={"project": API_TEST_PROJECT}
        )

    passed = sum(results)
    print(f"\n{passed}/{len(results)} test geçti")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
