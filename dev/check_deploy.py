"""Verifies a deployed instance over HTTP.

    python -m dev.check_deploy https://xxx.run.app

What it verifies: the PRECONDITIONS for WebMCP, and that the product works.
What it cannot verify: that `registerTool` actually succeeded -- that is a browser
concern and needs a client with WebMCP support. The last step is done by hand and
the script says so out loud.
"""

from __future__ import annotations

import sys
import time

import httpx

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL':<10} {name}")
    if not ok and detail:
        print(f"             {detail}")
    return bool(ok)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m dev.check_deploy https://xxx.run.app", file=sys.stderr)
        return 2

    base = sys.argv[1].rstrip("/")
    print(f"Target: {base}\n")

    with httpx.Client(base_url=base, timeout=60, follow_redirects=True) as client:
        print("=== WebMCP preconditions ===")
        try:
            home = client.get("/")
        except Exception as error:
            check(False, "page loaded", str(error))
            return 1

        check(home.status_code == 200, "page returns 200", str(home.status_code))

        # No secure context without HTTPS, and no WebMCP without a secure context
        check(
            base.startswith("https://") or "127.0.0.1" in base or "localhost" in base,
            "secure context (https)",
            f"scheme {base.split('://')[0]}",
        )

        # If this is missing, registerTool rejects with SecurityError and gives no hint
        oac = home.headers.get("origin-agent-cluster")
        check(oac == "?1", "Origin-Agent-Cluster: ?1", f"got: {oac!r}")

        csp = home.headers.get("content-security-policy")
        check(
            csp is None,
            "CSP does not block the tools",
            f"a CSP is present, confirm whether it breaks WebMCP: {csp}",
        )

        print("\n=== assets ===")
        for path in ["/styles.css", "/src/main.js", "/src/webmcp.js", "/src/approve.js"]:
            asset = client.get(path)
            check(asset.status_code == 200, f"{path}", str(asset.status_code))

        print("\n=== session (asks for no login) ===")
        session = client.get("/api/session")
        check(session.status_code == 200, "session established", str(session.status_code))
        if session.status_code == 200:
            payload = session.json()["session"]
            check(payload["role"] == "guest", "role is guest", str(payload))
            check(payload["credits"] > 0, "opening credits present", str(payload["credits"]))
            check(payload["costs"]["find_line"] == 0, "search is free")

        print("\n=== library and search ===")
        stats = client.get("/api/library/stats")
        takes = 0
        if check(stats.status_code == 200, "stats read", stats.text[:120]):
            takes = stats.json()["stats"]["takes"]
            check(takes > 0, f"{takes} take(s) in the library", "empty: did dev.load_demo run")

        found = client.post("/api/find_line", json={"phrase": "I never asked for this"})
        if check(found.status_code == 200, "search ran", found.text[:200]):
            body = found.json()
            check(body["total"] > 0, f"{body['total']} candidate(s) returned", "no matches")
            if body["candidates"]:
                best = body["candidates"][0]
                check(bool(best["source_url"]), "provenance carried through", str(best))
                media = client.get(best["media_url"])
                check(media.status_code == 200, f"media reachable {best['media_url']}",
                      str(media.status_code))
                ranged = client.get(best["media_url"], headers={"Range": "bytes=0-99"})
                # The virtual-trim player relies on range requests
                check(ranged.status_code == 206, "media supports HTTP range",
                      f"expected 206, got {ranged.status_code}")

        print("\n=== vocabulary ===")
        # The way in for a visitor who does not know what the corpus says. If this is empty
        # on a deployment with takes in it, the page is a search box with nothing to type.
        vocab = client.get("/api/vocabulary")
        if check(vocab.status_code == 200, "vocabulary read", vocab.text[:120]):
            body = vocab.json()
            words = body["words"]
            check(len(words) > 0, f"{len(words)} distinct word(s) offered", "nothing to click")
            check(
                len(body["takes"]) == takes,
                f"{len(body['takes'])} take(s) listed for the scope selector",
                f"stats said {takes}",
            )
            if words:
                # Every chip is a search button, so its own text has to find it. A chip that
                # comes back empty is worse than no chip.
                sample = words[0]["word"]
                hit = client.post("/api/find_line", json={"phrase": sample})
                check(
                    hit.status_code == 200 and hit.json()["total"] > 0,
                    f"clicking {sample!r} finds something",
                    hit.text[:160],
                )

        print("\n=== uploads ===")
        upload = client.get("/api/upload/status")
        if check(upload.status_code == 200, "upload status read", upload.text[:120]):
            info = upload.json()
            if info["available"]:
                check(True, f"uploads on (up to {info['limits']['max_mb']} MB, "
                            f"{info['limits']['max_seconds']}s, "
                            f"{info['cost_per_minute']} credit/min)")
            else:
                # Not a failure: the product works without it, and the interface says so.
                # But on a deployment meant to be tried by strangers it is the difference
                # between a demo and a tool, so it is called out rather than passed over.
                print(f"  INFO       uploads OFF: {info['reason']}")
                print("             the image needs requirements-ingest.txt for this")

        print("\n=== assistant ===")
        chat = client.get("/api/chat/status")
        if check(chat.status_code == 200, "chat status read", chat.text[:120]):
            info = chat.json()
            if info["available"]:
                check(True, f"assistant enabled ({info['model']})")
            else:
                # Not a failure: the product works without a key, but make the
                # deliberate choice visible
                print(f"  INFO       assistant disabled: {info['reason']}")

        print("\n=== render ===")
        if found.status_code == 200 and found.json()["candidates"]:
            picked = found.json()["candidates"][:2]
            queued = client.post("/api/render", json={
                "segments": [
                    {"candidate_id": p["id"], "start_ms": p["start_ms"], "end_ms": p["end_ms"]}
                    for p in picked
                ]
            })
            if check(queued.status_code == 200, "render accepted", queued.text[:200]):
                job = queued.json()
                check(job["charged"] == 1, "1 credit charged", str(job.get("charged")))
                status = job
                for _ in range(120):
                    status = client.get(f"/api/render/{job['job_id']}").json()
                    if status["status"] not in ("queued", "running"):
                        break
                    time.sleep(1)
                if check(status["status"] == "done", "render finished",
                         status.get("error", status["status"])):
                    output = client.get(status["download_url"])
                    check(output.status_code == 200, "output downloaded", str(output.status_code))
                    check(len(output.content) > 1000, "output is not empty",
                          f"{len(output.content)} bytes")

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")

    print(
        "\nFROM HERE ON, BY HAND:\n"
        "  1. Open the URL in the ChatGPT in-app browser (or Chrome with WebMCP on)\n"
        "  2. Ask the agent what tools it has -- five should show up:\n"
        "     find_line, propose_cut, preview_segment, get_timeline_state, commit_render\n"
        "  3. Ask it to find 'I never asked for this' in the calmest take\n"
        "  4. A proposal should appear on the timeline, with the provenance strip visible\n"
        "  5. Trigger commit_render: the approval dialog must open and the agent must wait\n"
        "\nIf the tools do not show up, the browser console is the first place to look:\n"
        "when registerTool is rejected the reason is printed there (most often a\n"
        "missing Origin-Agent-Cluster)."
    )

    failures = [name for ok, name, _ in results if not ok]
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
