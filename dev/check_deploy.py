"""Dağıtılmış bir örneği HTTP üzerinden doğrular.

    python -m dev.check_deploy https://xxx.run.app

Neyi doğruluyor: WebMCP'nin ÖN KOŞULLARI ve ürünün çalıştığı.
Neyi doğrulayamaz: `registerTool`'un gerçekten başarılı olduğunu — o bir tarayıcı
işi ve WebMCP destekleyen bir istemci gerektiriyor. Son adım elle yapılır ve script
bunu açıkça söylüyor.
"""

from __future__ import annotations

import sys
import time

import httpx

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    print(f"  {'GEÇTİ' if ok else 'BAŞARISIZ':<10} {name}")
    if not ok and detail:
        print(f"             {detail}")
    return bool(ok)


def main() -> int:
    if len(sys.argv) < 2:
        print("Kullanım: python -m dev.check_deploy https://xxx.run.app", file=sys.stderr)
        return 2

    base = sys.argv[1].rstrip("/")
    print(f"Hedef: {base}\n")

    with httpx.Client(base_url=base, timeout=60, follow_redirects=True) as client:
        print("=== WebMCP ön koşulları ===")
        try:
            home = client.get("/")
        except Exception as error:
            check(False, "sayfa açıldı", str(error))
            return 1

        check(home.status_code == 200, "sayfa 200", str(home.status_code))

        # HTTPS olmadan secure context yok, secure context olmadan WebMCP yok
        check(
            base.startswith("https://") or "127.0.0.1" in base or "localhost" in base,
            "secure context (https)",
            f"şema {base.split('://')[0]}",
        )

        # Eksikse registerTool SecurityError ile reddediyor ve hiçbir ipucu vermiyor
        oac = home.headers.get("origin-agent-cluster")
        check(oac == "?1", "Origin-Agent-Cluster: ?1", f"gelen: {oac!r}")

        csp = home.headers.get("content-security-policy")
        check(
            csp is None,
            "CSP araçları engellemiyor",
            f"CSP var, WebMCP'yi kırıp kırmadığını doğrula: {csp}",
        )

        print("\n=== varlıklar ===")
        for path in ["/styles.css", "/src/main.js", "/src/webmcp.js", "/src/approve.js"]:
            asset = client.get(path)
            check(asset.status_code == 200, f"{path}", str(asset.status_code))

        print("\n=== oturum (giriş istemiyor) ===")
        session = client.get("/api/session")
        check(session.status_code == 200, "oturum kuruldu", str(session.status_code))
        if session.status_code == 200:
            payload = session.json()["session"]
            check(payload["role"] == "guest", "rol guest", str(payload))
            check(payload["credits"] > 0, "açılış kredisi var", str(payload["credits"]))
            check(payload["costs"]["find_line"] == 0, "arama bedava")

        print("\n=== kütüphane ve arama ===")
        stats = client.get("/api/library/stats")
        takes = 0
        if check(stats.status_code == 200, "istatistik okundu", stats.text[:120]):
            takes = stats.json()["stats"]["takes"]
            check(takes > 0, f"kütüphanede {takes} take var", "boş: dev.load_demo koştu mu")

        found = client.post("/api/find_line", json={"phrase": "I never asked for this"})
        if check(found.status_code == 200, "arama çalıştı", found.text[:200]):
            body = found.json()
            check(body["total"] > 0, f"{body['total']} aday döndü", "eşleşme yok")
            if body["candidates"]:
                best = body["candidates"][0]
                check(bool(best["source_url"]), "provenance taşınıyor", str(best))
                media = client.get(best["media_url"])
                check(media.status_code == 200, f"medya erişilebilir {best['media_url']}",
                      str(media.status_code))
                ranged = client.get(best["media_url"], headers={"Range": "bytes=0-99"})
                # Sanal kırpma oynatıcısı range'e dayanıyor
                check(ranged.status_code == 206, "medya HTTP range destekliyor",
                      f"beklenen 206, gelen {ranged.status_code}")

        print("\n=== asistan ===")
        chat = client.get("/api/chat/status")
        if check(chat.status_code == 200, "sohbet durumu okundu", chat.text[:120]):
            info = chat.json()
            if info["available"]:
                check(True, f"asistan açık ({info['model']})")
            else:
                # Hata değil: ürün anahtarsız çalışıyor, ama bilerek yapıldığını gör
                print(f"  BİLGİ      asistan kapalı: {info['reason']}")

        print("\n=== render ===")
        if found.status_code == 200 and found.json()["candidates"]:
            picked = found.json()["candidates"][:2]
            queued = client.post("/api/render", json={
                "segments": [
                    {"candidate_id": p["id"], "start_ms": p["start_ms"], "end_ms": p["end_ms"]}
                    for p in picked
                ]
            })
            if check(queued.status_code == 200, "render kabul edildi", queued.text[:200]):
                job = queued.json()
                check(job["charged"] == 1, "1 kredi düştü", str(job.get("charged")))
                status = job
                for _ in range(120):
                    status = client.get(f"/api/render/{job['job_id']}").json()
                    if status["status"] not in ("queued", "running"):
                        break
                    time.sleep(1)
                if check(status["status"] == "done", "render bitti",
                         status.get("error", status["status"])):
                    output = client.get(status["download_url"])
                    check(output.status_code == 200, "çıktı indirildi", str(output.status_code))
                    check(len(output.content) > 1000, "çıktı boş değil",
                          f"{len(output.content)} byte")

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{passed}/{len(results)} kontrol geçti")

    print(
        "\nBURADAN SONRASI ELLE:\n"
        "  1. URL'yi ChatGPT in-app browser'da (ya da WebMCP açık Chrome'da) aç\n"
        "  2. Ajana araçlarını sor — beş araç görünmeli:\n"
        "     find_line, propose_cut, preview_segment, get_timeline_state, commit_render\n"
        "  3. 'I never asked for this' repliğini en sakin okunduğu take'te bulmasını iste\n"
        "  4. Timeline'da öneri çıkmalı, provenance şeridi görünmeli\n"
        "  5. commit_render çağırt: onay penceresi açılmalı ve ajan beklemeli\n"
        "\nAraçlar görünmüyorsa ilk bakılacak yer tarayıcı konsolu: registerTool\n"
        "reddedildiyse sebebi orada yazıyor (en sık: Origin-Agent-Cluster eksik)."
    )

    failures = [name for ok, name, _ in results if not ok]
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
