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
config.SESSION_DB = Path(tempfile.mkdtemp(prefix="cinema-test-")) / "sessions.db"

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


def seed_clickhouse():
    """Kontrat fixture'ını izole bir projeye yazar. Hazırsa istemciyi döner."""
    fixture = json.loads(
        (config.APP_ROOT / "pipeline" / "fixture.json").read_text(encoding="utf-8")
    )
    fixture["project_id"] = API_TEST_PROJECT
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

    print("=== güvenlik header'ları ===")
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
            check_that("kaynak korundu", best["source_url"].startswith("gs://"), best["source_url"])
            check("oynatma URL'i", best["media_url"], "/media/S01_T03.mp4")

            calm = find(phrase="I never asked for this", tone="calm").json()
            check("ton filtresi 1 aday", len(calm["candidates"]), 1)
            check("filtre doğru take", calm["candidates"][0]["take_id"], "S01_T03")

            check("bulunamayan 0 aday", find(phrase="helicopter").json()["candidates"], [])

            word = client.get(f"/api/word/asked?project={API_TEST_PROJECT}")
            check("kelime araması 200", word.status_code, 200)
            check("3 geçiş", word.json()["total"], 3)

            stats = client.get(f"/api/library/stats?project={API_TEST_PROJECT}")
            check("kütüphane istatistiği", stats.json()["stats"]["takes"], 3)

    print("\n=== render henüz devrede değil ===")
    with TestClient(app) as client:
        client.get("/api/session")
        before = client.get("/api/session").json()["session"]["credits"]
        attempt = client.post(
            "/api/render",
            json={"segments": [{"candidate_id": "S01_T03:1:800", "start_ms": 800, "end_ms": 2700}]},
        )
        check("render 503", attempt.status_code, 503)
        after = client.get("/api/session").json()["session"]["credits"]
        # Çalışmayan bir iş için kredi harcamak sessiz veri kaybı olur
        check("kredi harcanmadı", after, before)

        check(
            "geçersiz aralık 422",
            client.post(
                "/api/render",
                json={"segments": [{"candidate_id": "x", "start_ms": 500, "end_ms": 500}]},
            ).status_code,
            422,
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
        check_that(
            "sebep anahtarı söylüyor",
            "GEMINI_API_KEY" in (body["reason"] or ""),
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
            check_that(
                "aday kaynağını taşıyor",
                found["candidates"][0]["source"].startswith("gs://"),
                found["candidates"][0]["source"],
            )
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
