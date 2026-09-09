"""API tests.

    python -m server.test_api

Needs a local ClickHouse:
    docker compose -f dev/docker-compose.yml up -d

Same pattern as test_queries.py: no pytest, just a script. We are not adding a dependency
for this.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from . import config

# Keep the tests away from the real session ledger. Must be set BEFORE init_db.
_scratch = Path(tempfile.mkdtemp(prefix="cinema-test-"))
config.SESSION_DB = _scratch / "sessions.db"

# The media directory is temporary too: the tests generate their own WAV and it must not
# land inside the committed demo corpus.
config.MEDIA_DIR = _scratch / "media"

# The tests set up their own ClickHouse project. They used to assert against whatever
# happened to be in "demo", and three of them broke the moment dev/seed_demo replaced that
# data — so they were not hermetic. Now the contract fixture goes into its own project.
API_TEST_PROJECT = "__api_test__"
config.ALLOWED_PROJECTS = frozenset({config.DEMO_PROJECT, API_TEST_PROJECT})

from fastapi.testclient import TestClient  # noqa: E402

from pipeline import db as ch  # noqa: E402
from pipeline import ingest as ingest_module  # noqa: E402
from pipeline import queries  # noqa: E402
from pipeline import schema  # noqa: E402

from . import sessions  # noqa: E402
from .main import create_app  # noqa: E402

results: list[bool] = []


def check(name: str, actual, expected) -> None:
    ok = actual == expected
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL':<10} {name}")
    if not ok:
        print(f"             expected: {expected}")
        print(f"             actual  : {actual}")


def check_that(name: str, condition: bool, detail: str = "") -> None:
    results.append(bool(condition))
    print(f"  {'PASS' if condition else 'FAIL':<10} {name}")
    if not condition and detail:
        print(f"             {detail}")


TEST_MEDIA_NAME = "__api_test_take__.wav"


def write_test_media() -> None:
    """Generates a real WAV for the render tests. Stdlib only, platform independent.

    We do not lean on seed_demo's SAPI: these tests have to run outside Windows and must
    not depend on the seed data. The content is irrelevant, the duration is not — the
    fixture's ranges reach 4.3 seconds.
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
    """Writes the contract fixture into an isolated project. Returns a client if ready."""
    fixture = json.loads(
        (config.APP_ROOT / "pipeline" / "fixture.json").read_text(encoding="utf-8")
    )
    fixture["project_id"] = API_TEST_PROJECT

    # The render tests need a real file. Instead of the fixture's gs:// paths we point at
    # the WAV we generate; the media path is derived from the DATABASE anyway, so this
    # redirection exercises render's real code path.
    write_test_media()
    for take in fixture["takes"]:
        take["source_url"] = TEST_MEDIA_NAME

    try:
        client = ch.connect()
        ch.create_table(client)
        ingest_module.ingest(client, fixture, replace=True)
        return client
    except Exception as error:
        print(f"  SKIPPED    could not prepare ClickHouse: {error}")
        print("             docker compose -f dev/docker-compose.yml up -d")
        return None


def main() -> int:
    app = create_app()
    clickhouse = seed_clickhouse()

    print("=== media URL mapping ===")
    from . import render as render_worker
    from . import routes

    check(
        "gs:// reduced to a basename",
        routes.media_url("gs://bucket/S01_T03.mp4"),
        "/media/S01_T03.mp4",
    )
    check("plain name", routes.media_url("take.wav"), "/media/take.wav")
    check("http passes through", routes.media_url("https://cdn/x.mp4"), "https://cdn/x.mp4")
    check("absolute path preserved", routes.media_url("/media/x.wav"), "/media/x.wav")

    print("\n=== media path resolution (path traversal) ===")
    # The path does not come from the request, but we still assert it cannot escape the
    # directory: this function decides which file gets handed to ffmpeg.
    write_test_media()
    resolved = render_worker.resolve_media(f"gs://bucket/{TEST_MEDIA_NAME}")
    check_that(
        "resolved inside the allowed directory",
        resolved.parent.resolve() == config.MEDIA_DIR.resolve(),
        str(resolved),
    )
    for hostile in ["../../.env", "..\\..\\.env", "/etc/passwd", "..", ""]:
        rejected = False
        try:
            render_worker.resolve_media(hostile)
        except Exception:
            rejected = True
        check_that(f"rejected: {hostile!r}", rejected)

    print("\n=== security headers ===")
    with TestClient(app) as client:
        health = client.get("/healthz")
        check("healthz 200", health.status_code, 200)
        # WebMCP depends on this. Without it registerTool refuses without saying why.
        check(
            "Origin-Agent-Cluster: ?1",
            health.headers.get("origin-agent-cluster"),
            "?1",
        )
        check("nosniff", health.headers.get("x-content-type-options"), "nosniff")

    print("\n=== session ===")
    with TestClient(app) as client:
        first = client.get("/api/session")
        check("first call 200", first.status_code, 200)
        payload = first.json()
        check("role guest", payload["session"]["role"], "guest")
        check("opening credits", payload["session"]["credits"], config.GUEST_CREDITS)
        check("search is free", payload["session"]["costs"]["find_line"], 0)
        check("render costs 1", payload["session"]["costs"]["commit_render"], 1)
        check_that(
            "cookie set",
            config.SESSION_COOKIE in client.cookies,
            f"cookies: {dict(client.cookies)}",
        )
        # The session id must NOT come back in the body
        check_that(
            "session id absent from the body",
            "id" not in payload["session"] and "session_id" not in payload,
            str(payload),
        )

        second = client.get("/api/session")
        check(
            "session reused",
            second.json()["session"]["credits"],
            config.GUEST_CREDITS,
        )

    print("\n=== input validation ===")
    with TestClient(app) as client:
        check(
            "unknown tone 422",
            client.post("/api/find_line", json={"phrase": "x", "tone": "sarcastic"}).status_code,
            422,
        )
        check(
            "unknown project 404",
            client.post("/api/find_line", json={"phrase": "x", "project": "secret"}).status_code,
            404,
        )
        check(
            "empty phrase 422",
            client.post("/api/find_line", json={"phrase": "   "}).status_code,
            422,
        )
        check(
            "over-long phrase 422",
            client.post(
                "/api/find_line", json={"phrase": " ".join(["go"] * 50)}
            ).status_code,
            422,
        )

    print("\n=== search (ClickHouse) ===")
    if clickhouse is None:
        print("  SKIPPED    no ClickHouse, search tests did not run")
    else:
        with TestClient(app) as client:
            def find(**kwargs):
                return client.post(
                    "/api/find_line", json={"project": API_TEST_PROJECT, **kwargs}
                )

            found = find(phrase="I never asked for this")
            check("search 200", found.status_code, 200)
            body = found.json()
            check("3 candidates", len(body["candidates"]), 3)
            check("no credit spent", body["session"]["credits"], config.GUEST_CREDITS)

            best = body["candidates"][0]
            # Ranking is product logic: highest delivery confidence first (S01_T03, calm, 0.91)
            check("best candidate is calm", best["tone"], "calm")
            check("rank 1", best["rank"], 1)
            check("duration derived", best["duration_ms"], best["end_ms"] - best["start_ms"])
            check("stable id", best["id"], f"{best['take_id']}:{best['line_id']}:{best['start_ms']}")
            # provenance, so a fragment can be traced back to its source
            check("source preserved", best["source_url"], TEST_MEDIA_NAME)
            check("playback URL", best["media_url"], f"/media/{TEST_MEDIA_NAME}")

            calm = find(phrase="I never asked for this", tone="calm").json()
            check("tone filter leaves 1", len(calm["candidates"]), 1)
            check("filter picked the right take", calm["candidates"][0]["take_id"], "S01_T03")

            check("no match, no candidates", find(phrase="helicopter").json()["candidates"], [])

            word = client.get(f"/api/word/asked?project={API_TEST_PROJECT}")
            check("word search 200", word.status_code, 200)
            check("3 occurrences", word.json()["total"], 3)

            stats = client.get(f"/api/library/stats?project={API_TEST_PROJECT}")
            check("library stats", stats.json()["stats"]["takes"], 3)
            # The search box's example comes from the library, not from a constant. A
            # hardcoded English sentence is a misleading hint the moment the footage is
            # in another language.
            check(
                "the example line comes from the data",
                stats.json()["stats"]["sample_line"],
                "I never asked for this",
            )

    print("\n=== line words (the clickable transcript) ===")
    if clickhouse is None:
        print("  SKIPPED    no ClickHouse")
    else:
        with TestClient(app) as client:
            def lines(refs, project=API_TEST_PROJECT):
                return client.post("/api/lines", json={"lines": refs, "project": project})

            answer = lines([{"take_id": "S01_T01", "line_id": 1}])
            check("lines 200", answer.status_code, 200)
            line = answer.json()["lines"]["S01_T01:1"]
            check("five words", len(line["words"]), 5)
            check(
                "in spoken order",
                [word["word"] for word in line["words"]],
                ["I", "never", "asked", "for", "this"],
            )
            # The full sentence, which a phrase match does NOT give you
            check("the sentence is joined", line["text"], "I never asked for this")
            check("bounds are the first and last word", [line["start_ms"], line["end_ms"]], [1200, 2620])
            check("each word carries its own range", line["words"][1]["start_ms"], 1330)
            check("and its own confidence", line["words"][1]["confidence"], 0.98)
            check("normalised form travels too", line["words"][0]["word_norm"], "i")

            # Several candidates can be hits inside one line, so duplicates collapse
            both = lines(
                [
                    {"take_id": "S01_T01", "line_id": 1},
                    {"take_id": "S01_T01", "line_id": 1},
                    {"take_id": "S01_T01", "line_id": 2},
                ]
            ).json()["lines"]
            check("duplicates collapse", sorted(both), ["S01_T01:1", "S01_T01:2"])
            check("the second line came too", both["S01_T01:2"]["text"], "Just let me go")

            # Reading the transcript you are already looking at must not cost anything
            before = client.get("/api/session").json()["session"]["credits"]
            lines([{"take_id": "S01_T03", "line_id": 1}])
            check(
                "reading a line costs nothing",
                client.get("/api/session").json()["session"]["credits"],
                before,
            )

    print("\n=== vocabulary (what the library can say) ===")
    if clickhouse is None:
        print("  SKIPPED    no ClickHouse")
    else:
        with TestClient(app) as client:
            def vocab(**params):
                return client.get(
                    "/api/vocabulary", params={"project": API_TEST_PROJECT, **params}
                )

            answer = vocab()
            check("vocabulary 200", answer.status_code, 200)
            body = answer.json()
            words = {entry["key"]: entry for entry in body["words"]}

            # The fixture is three takes of the same two lines, so every word is spoken
            # three times.
            check("nine distinct words", len(body["words"]), 9)
            check("counted across takes", words["asked"]["count"], 3)
            check("and the takes it spans", words["asked"]["takes"], 3)
            check("most spoken first", body["words"][0]["count"], 3)

            # The display spelling is cleaned but keeps its case: a chip reading "go." next
            # to a search box that fills with "go." looks like a defect.
            check("trailing punctuation is gone", words["go"]["word"], "go")
            check("case is preserved", words["i"]["word"], "I")

            # THE INVARIANT the panel rests on: clicking a chip searches for its own text,
            # so that text has to normalise back to the key it was listed under. If it did
            # not, a chip promising three occurrences would return nothing.
            check_that(
                "every spelling normalises back to its key",
                all(
                    schema.normalize_word(entry["word"]) == entry["key"]
                    for entry in body["words"]
                ),
                "a chip would search for a different word than the one it lists",
            )
            for entry in body["words"]:
                found = client.post(
                    "/api/find_line",
                    json={"phrase": entry["word"], "project": API_TEST_PROJECT},
                )
                if found.json()["total"] < 1:
                    check(f"clicking {entry['word']!r} finds something", 0, 1)
                    break
            else:
                check_that("clicking any chip finds something", True)

            # The takes list is what the scope selector is built from
            check(
                "takes listed",
                [take["take_id"] for take in body["takes"]],
                ["S01_T01", "S01_T03", "S01_T05"],
            )
            check("with their word counts", body["takes"][0]["words"], 9)
            # The last word of the whole take, not of its first line: the fixture's second
            # line ("Just let me go") runs to 4380.
            check("and their length", body["takes"][0]["duration_ms"], 4380)
            check("with their line counts", body["takes"][0]["lines"], 2)

            # Narrowed to one recording. This is what the interface asks for right after an
            # ingest, so the words that just arrived are visible on their own.
            scoped = vocab(take="S01_T01").json()
            check("scope echoed back", scoped["take"], "S01_T01")
            check("scoped words are that take's", len(scoped["words"]), 9)
            check_that(
                "and each is spoken once there",
                all(entry["count"] == 1 for entry in scoped["words"]),
                "a single take cannot contain the same fixture line three times",
            )

            # An unknown take needs no validation: the project filter is what confines the
            # read, so it matches nothing rather than leaking anything.
            check("unknown take is empty, not an error", vocab(take="nope").status_code, 200)
            check("and returns no words", vocab(take="nope").json()["words"], [])

            # The vocabulary follows the delivery filter, otherwise a chip could report
            # three occurrences under a filter that excludes all three.
            calm = vocab(tone="calm").json()
            check_that(
                "the tone filter narrows the counts",
                all(entry["count"] == 1 for entry in calm["words"]),
                "only one of the three takes is calm",
            )
            check("an unknown tone is rejected", vocab(tone="nope").status_code, 422)
            check("an unknown project is rejected", vocab(project="secret").status_code, 404)

            # A display limit, so it is clamped rather than failing a page load
            check("limit clamped to the ceiling", vocab(limit=99999).json()["limit"], config.MAX_VOCABULARY)
            check("and never to zero", vocab(limit=0).json()["limit"], 1)
            check("truncation is reported", vocab(limit=1).json()["truncated"], True)
            check("and not claimed when false", vocab().json()["truncated"], False)

            before = client.get("/api/session").json()["session"]["credits"]
            vocab()
            check(
                "reading the vocabulary costs nothing",
                client.get("/api/session").json()["session"]["credits"],
                before,
            )

            # A line that does not exist is absent from the map rather than an error: the
            # interface asks for whatever a search returned and degrades on its own.
            check("unknown line is simply absent", lines([{"take_id": "NOPE", "line_id": 9}]).json()["lines"], {})

            check("empty list rejected", lines([]).status_code, 422)
            check(
                "over the cap rejected",
                lines([{"take_id": "S01_T01", "line_id": 1}] * 101).status_code,
                422,
            )
            check(
                "project stays inside the allowlist",
                lines([{"take_id": "S01_T01", "line_id": 1}], project="../etc").status_code,
                404,
            )

    print("\n=== uploads ===")
    with TestClient(app) as client:
        status = client.get("/api/upload/status")
        check("upload status 200", status.status_code, 200)
        info = status.json()
        check("limits are stated up front", info["limits"]["max_mb"], config.MAX_UPLOAD_MB)
        check("and the price", info["cost_per_minute"], config.COST_INGEST_PER_MINUTE)
        check_that(
            "the accepted containers are listed",
            ".mp4" in info["limits"]["suffixes"] and ".wav" in info["limits"]["suffixes"],
            info["limits"]["suffixes"],
        )

        # A container we do not read is refused immediately, before any bytes are stored
        rejected = client.post(
            "/api/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        check_that(
            "an unknown container is refused",
            rejected.status_code in (422, 503),
            f"{rejected.status_code} {rejected.text[:120]}",
        )

        # Someone else's job is absent rather than forbidden: 403 would confirm the id
        check("an unknown job is 404", client.get("/api/upload/nope").status_code, 404)

    print("\n=== upload isolation ===")
    # A visitor's upload goes into a project derived from their session cookie, so it is
    # not in anyone else's library. That id cannot be supplied by a caller, which is what
    # makes the isolation hold rather than depending on a validation rule.
    check(
        "the upload project comes from the session id",
        config.session_project("abc123"),
        f"{config.UPLOAD_PROJECT_PREFIX}abc123",
    )
    check_that(
        "and is not in the queryable allowlist",
        config.session_project("abc123") not in config.ALLOWED_PROJECTS,
    )
    with TestClient(app) as client:
        first = client.get("/api/session")
        # Naming another project is still bounded by the allowlist
        check(
            "an unknown project is still refused",
            client.post(
                "/api/find_line", json={"phrase": "anything", "project": "../etc"}
            ).status_code,
            404,
        )
        check_that("the session was established", first.status_code == 200)

    if clickhouse is not None:
        print("\n=== ingest pricing and refund ===")
        # Priced per started minute, rounded up: a twenty second clip still costs one.
        from . import uploads as upload_worker

        check("20 seconds costs one credit", upload_worker.credits_for(20), 1)
        check("60 seconds costs one", upload_worker.credits_for(60), 1)
        check("61 seconds costs two", upload_worker.credits_for(61), 2)
        check("180 seconds costs three", upload_worker.credits_for(180), 3)

        # A take id ends up in URLs and in ClickHouse, so it is sanitised rather than trusted
        check("a name is sanitised", upload_worker.take_id_for("Rooftop take", 0), "ROOFTOP_TAKE")
        check("traversal cannot survive it", upload_worker.take_id_for("../../etc", 0), "ETC")
        check("an empty name gets a sequence", upload_worker.take_id_for("", 3), "UP04")
        check_that(
            "the stored filename is generated, not the browser's",
            upload_worker.stored_name("UP01", ".MP4").startswith("UP01_")
            and upload_worker.stored_name("UP01", ".MP4").endswith(".mp4"),
            upload_worker.stored_name("UP01", ".MP4"),
        )

        # Work that was charged but not delivered is refunded. The alternative, charging
        # only on success, would mean doing the work before knowing it can be paid for.
        refund_session = sessions.create(ip="test", role="guest")
        after_charge = sessions.charge(refund_session["id"], 2, "ingest test")
        check("charged", after_charge, config.GUEST_CREDITS - 2)
        check("refunded", sessions.refund(refund_session["id"], 2, "ingest failed"), config.GUEST_CREDITS)
        entries = sessions.history(refund_session["id"])
        check_that(
            "the refund is a ledger row, not a rewritten balance",
            any(entry["delta"] == 2 for entry in entries),
            entries,
        )

    print("\n=== render ===")
    if clickhouse is None:
        print("  SKIPPED    no ClickHouse")
    else:
        with TestClient(app) as client:
            def render(segments):
                return client.post(
                    "/api/render", json={"project": API_TEST_PROJECT, "segments": segments}
                )

            def credits():
                return client.get("/api/session").json()["session"]["credits"]

            check(
                "invalid range 422",
                render([{"candidate_id": "S01_T03:1:800", "start_ms": 500, "end_ms": 500}]).status_code,
                422,
            )

            # A take that is not in the library. Because the path does not come from the
            # request, what gets rejected is the "take" rather than a "file" — the
            # traversal attempt never reaches the filesystem at all.
            before = credits()
            traversal = render(
                [{"candidate_id": "../../../../etc/passwd:1:0", "start_ms": 0, "end_ms": 500}]
            )
            check("invented take rejected", traversal.status_code, 422)
            check_that(
                "reason says it is not in the library",
                "take" in traversal.json()["detail"].lower(),
                traversal.json()["detail"],
            )
            # Validation comes BEFORE the charge: a rejected request is not billed
            check("rejected request spent nothing", credits(), before)

            # A range outside the recording
            outside = render(
                [{"candidate_id": "S01_T03:1:800", "start_ms": 0, "end_ms": 999_999}]
            )
            check("out-of-range 422", outside.status_code, 422)
            check("still nothing spent", credits(), before)

            # A real render
            job_id = None
            ok = render(
                [
                    {"candidate_id": "S01_T03:1:800", "start_ms": 800, "end_ms": 2700},
                    {"candidate_id": "S01_T01:1:1200", "start_ms": 1200, "end_ms": 2620},
                ]
            )
            check("render accepted", ok.status_code, 200)
            if ok.status_code == 200:
                body = ok.json()
                job_id = body["job_id"]
                check("1 credit charged", body["charged"], 1)
                check("balance updated", body["credits_left"], before - 1)
                check("two segments", body["segments"], 2)
                check("durations summed", body["total_duration_ms"], (2700 - 800) + (2620 - 1200))

                # The job runs in the background; TestClient runs BackgroundTasks after the response
                status = client.get(f"/api/render/{job_id}").json()
                check("job finished", status["status"], "done")
                check("output is audio", status["mode"], "audio")
                check_that("download link present", "download_url" in status, status)

                downloaded = client.get(f"/api/render/{job_id}/file")
                check("file downloaded", downloaded.status_code, 200)
                check_that(
                    "wav header correct",
                    downloaded.content[:4] == b"RIFF",
                    downloaded.content[:12],
                )
                # Duration check: 1900 + 1420 = 3320 ms at 48 kHz stereo 16-bit
                expected = int(48000 * 2 * 2 * 3.32)
                check_that(
                    "output length close to expected",
                    abs(len(downloaded.content) - expected) < expected * 0.1,
                    f"{len(downloaded.content)} bytes, expected ~{expected}",
                )

            check(
                "unknown job 404",
                client.get("/api/render/nosuchthing").status_code,
                404,
            )

        # Another session must not reach the same job
        if job_id:
            with TestClient(app) as other:
                other.get("/api/session")
                check(
                    "another session cannot see the job",
                    other.get(f"/api/render/{job_id}").status_code,
                    404,
                )
                check(
                    "another session cannot download it",
                    other.get(f"/api/render/{job_id}/file").status_code,
                    404,
                )

        # Out of credits gives 402 and creates no job
        with TestClient(app) as broke:
            broke.get("/api/session")
            cookie = broke.cookies[config.SESSION_COOKIE]
            sessions.charge(cookie, config.GUEST_CREDITS, "drain the balance in a test")
            denied = broke.post(
                "/api/render",
                json={
                    "project": API_TEST_PROJECT,
                    "segments": [
                        {"candidate_id": "S01_T03:1:800", "start_ms": 800, "end_ms": 2700}
                    ],
                },
            )
            check("no credits gives 402", denied.status_code, 402)
            check_that(
                "402 says search is still free",
                "cost nothing" in denied.json()["detail"],
                denied.json()["detail"],
            )

    print("\n=== credit ledger atomicity ===")
    session = sessions.create(ip="test", role="guest")
    spent = 0
    failures = 0
    lock = threading.Lock()

    def spend() -> None:
        nonlocal spent, failures
        try:
            sessions.charge(session["id"], 1, "concurrency test")
            with lock:
                spent += 1
        except sessions.InsufficientCredits:
            with lock:
                failures += 1

    # Twice the balance in concurrent requests. Exactly the balance should succeed.
    threads = [threading.Thread(target=spend) for _ in range(config.GUEST_CREDITS * 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("exactly the balance was spent", spent, config.GUEST_CREDITS)
    check("the rest were refused", failures, config.GUEST_CREDITS)
    check("balance is zero", sessions.get(session["id"])["credits"], 0)
    check(
        "ledger rows match",
        len([e for e in sessions.history(session["id"], limit=100) if e["delta"] < 0]),
        config.GUEST_CREDITS,
    )

    # A free operation must not add a ledger row, or every search would inflate it
    free_session = sessions.create(ip="test")
    sessions.charge(free_session["id"], 0, "free")
    check(
        "free operations do not inflate the ledger",
        len(sessions.history(free_session["id"], limit=100)),
        1,  # only the opening balance
    )

    print("\n=== assistant endpoint (no key) ===")
    with TestClient(app) as client:
        status = client.get("/api/chat/status")
        check("status 200", status.status_code, 200)
        body = status.json()
        check("assistant off", body["available"], False)
        # The code is machine-readable and the text is an English fallback. The client
        # sees the code and writes its own sentence; the server does not know the
        # reader's language.
        check("reason code", body["reason_code"], "no_api_key")
        check_that(
            "reason names the key",
            "GEMINI_API_KEY" in (body["reason"] or ""),
            body["reason"],
        )
        check_that(
            "user-facing message is English",
            all(ord(ch) < 128 for ch in (body["reason"] or "")),
            body["reason"],
        )
        check_that(
            "reason says the product still works",
            "timeline" in (body["reason"] or "").lower(),
            body["reason"],
        )

        attempt = client.post("/api/chat", json={"message": "which take is calmest"})
        check("assistant 503", attempt.status_code, 503)
        check_that(
            "503 offers an alternative",
            "panel" in attempt.json()["detail"].lower(),
            attempt.json()["detail"],
        )

        check("empty message 422", client.post("/api/chat", json={"message": ""}).status_code, 422)
        check(
            "over-long message 422",
            client.post("/api/chat", json={"message": "x" * 1001}).status_code,
            422,
        )

    print("\n=== assistant counter atomicity ===")
    # Same reason as credits: two concurrent messages reading the same counter and both
    # passing would make the limit meaningless. The assistant is metered by a separate
    # counter, NOT by credits.
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

    check("exactly the limit passed", allowed, 5)
    check("the rest were blocked", blocked, 10)
    check("credits untouched", sessions.get(chat_session["id"])["credits"], config.GUEST_CREDITS)

    print("\n=== agent tools (without an LLM) ===")
    if clickhouse is None:
        print("  SKIPPED    no ClickHouse")
    else:
        from server import agent_tools

        # The tools read config.DEMO_PROJECT at call time. That is a security property:
        # the agent cannot be talked into querying another project. For the test we point
        # it at the isolated project temporarily.
        original_project = config.DEMO_PROJECT
        config.DEMO_PROJECT = API_TEST_PROJECT
        try:
            agent_tools.new_collection()

            found = agent_tools.find_line("I never asked for this")
            check("find_line found 3", found["found"], 3)
            check("best is calm", found["candidates"][0]["tone"], "calm")
            check("candidate carries its source", found["candidates"][0]["source"], TEST_MEDIA_NAME)
            check("call recorded", agent_tools.collection()["tool_calls"][0]["name"], "find_line")

            calm = agent_tools.find_line("I never asked for this", tone="calm")
            check("tone filter", calm["found"], 1)

            # Failures are dicts the agent can read and correct, not exceptions
            bad_tone = agent_tools.find_line("x", tone="sarcastic")
            check_that("unknown tone returned an error", "error" in bad_tone, bad_tone)
            check("allowed tones listed", len(bad_tone["allowed_tones"]), 6)

            long_phrase = agent_tools.find_line(" ".join(["go"] * 50))
            check_that("over-long phrase rejected", "error" in long_phrase, long_phrase)

            empty = agent_tools.find_line("   ")
            check_that("empty phrase rejected", "error" in empty, empty)

            missing = agent_tools.find_line("helicopter")
            check("no match returns 0", missing["found"], 0)
            check_that(
                "it points somewhere useful",
                "get_library_stats" in missing["message"],
                missing["message"],
            )

            # Proposals: the candidates have to have been found in this conversation
            agent_tools.new_collection()
            orphan = agent_tools.assemble_proposal(["S01_T03:1:800"])
            check_that("no candidates means no proposal", "error" in orphan, orphan)

            agent_tools.new_collection()
            agent_tools.find_line("I never asked for this")
            ids = [c["id"] for c in agent_tools.collection()["candidates"][:2]]
            proposed = agent_tools.assemble_proposal(ids)
            check("two segments proposed", proposed["proposed_segments"], 2)
            check("nothing rendered", proposed["rendered"], False)
            check("proposal in the collector", agent_tools.collection()["proposal"], ids)
            check_that(
                "message says the human can change it",
                "change it" in proposed["message"],
                proposed["message"],
            )

            unknown = agent_tools.assemble_proposal(["nope:1:0"])
            check_that("unknown id rejected", "error" in unknown, unknown)
            check_that(
                "available ids listed",
                len(unknown["available_ids"]) == 3,
                unknown,
            )

            check_that("empty list rejected", "error" in agent_tools.assemble_proposal([]), "")

            stats = agent_tools.get_library_stats()
            check("library stats", stats["takes"], 3)
            check_that("tones are JSON-serialisable", isinstance(stats["tones"], list), stats)
        finally:
            config.DEMO_PROJECT = original_project

    if clickhouse is not None:
        # Do not leave the test project behind; it would pollute the development library
        clickhouse.command(
            queries.DROP_PROJECT, parameters={"project": API_TEST_PROJECT}
        )

    passed = sum(results)
    print(f"\n{passed}/{len(results)} tests passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
