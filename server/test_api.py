"""API testleri.

    python -m server.test_api

Yerel ClickHouse ve ingest edilmiş fixture gerekiyor:
    docker compose -f dev/docker-compose.yml up -d
    python -m pipeline.ingest pipeline\\fixture.json --replace

test_queries.py ile aynı desen: pytest yok, düz script. Bağımlılık eklemiyoruz.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from . import config

# Testler gerçek oturum defterine dokunmasın. init_db'den ÖNCE değiştirilmeli.
config.SESSION_DB = Path(tempfile.mkdtemp(prefix="cinema-test-")) / "sessions.db"

from fastapi.testclient import TestClient  # noqa: E402

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


def main() -> int:
    app = create_app()

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
    with TestClient(app) as client:
        found = client.post("/api/find_line", json={"phrase": "I never asked for this"})
        if found.status_code == 503:
            print(f"  ATLANDI    ClickHouse yok: {found.json().get('detail')}")
            print("             docker compose -f dev/docker-compose.yml up -d")
            print("             python -m pipeline.ingest pipeline\\fixture.json --replace")
        else:
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

            calm = client.post(
                "/api/find_line", json={"phrase": "I never asked for this", "tone": "calm"}
            ).json()
            check("ton filtresi 1 aday", len(calm["candidates"]), 1)
            check("filtre doğru take", calm["candidates"][0]["take_id"], "S01_T03")

            missing = client.post("/api/find_line", json={"phrase": "helicopter"}).json()
            check("bulunamayan 0 aday", missing["candidates"], [])

            word = client.get("/api/word/asked")
            check("kelime araması 200", word.status_code, 200)
            check("3 geçiş", word.json()["total"], 3)

            stats = client.get("/api/library/stats")
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

    passed = sum(results)
    print(f"\n{passed}/{len(results)} test geçti")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
