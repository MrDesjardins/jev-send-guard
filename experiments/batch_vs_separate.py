"""Experiment: is one batched Jev request (all questions) faster than one
request per question sent in parallel?

Each trial fires BOTH strategies at the same moment on the same draft, so
they see the same network/API conditions:

  * batched  — 1 POST containing every active question (current behavior)
  * separate — N POSTs, one question each, all in flight concurrently

The measured time for a strategy is wall-clock until it has all answers
(for `separate`, that's the slowest of the N requests). Trials repeat until
TARGET successful samples (both strategies returned 200 with answers) are
collected, capped at MAX_ATTEMPTS. Results are appended to a CSV as they
come in so a partial run is still usable.

    uv run --with matplotlib python experiments/batch_vs_separate.py
    python experiments/plot_batch_vs_separate.py   # graph from the CSV
"""

import csv
import itertools
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from core import api_key as api_key_store  # noqa: E402
from core import jev_client  # noqa: E402

TARGET = 100
MAX_ATTEMPTS = 160
PAUSE_BETWEEN_TRIALS_SEC = 0.5
# Generous so we measure the real latency distribution instead of
# truncating at the app's 4s timeout; `over_app_timeout` columns flag those.
REQUEST_TIMEOUT_SEC = 20.0
OUT_CSV = Path(__file__).resolve().parent / "results_batch_vs_separate.csv"

DRAFTS = [
    "Can you send me the Q3 numbers by Friday? We need them for the board deck.",
    "This is wrong. Fix it.",
    "Wow, great job breaking prod again. Really impressive work as always.",
    "hey so the build is broken lol idk what happened, anyone??",
    "Thanks for jumping on this so quickly, really appreciate the help!",
    "The migration failed last night and the reporting tables are stale.",
    "I already told you this twice. Please read my messages before asking.",
    "Could we move our 1:1 to Thursday afternoon? Let me know what works.",
    "FYI the office will be closed Monday for the holiday.",
    "WHY IS NOBODY RESPONDING TO THE INCIDENT CHANNEL",
]


def make_client():
    # Same transport settings as core/jev_client.py's shared client.
    return httpx.Client(
        transport=httpx.HTTPTransport(local_address="0.0.0.0"),
        trust_env=False,
    )


def post(client, key, draft, questions):
    """Returns (elapsed_sec, ok, scores_dict_or_None, error_str)."""
    start = time.perf_counter()
    try:
        resp = client.post(
            jev_client.API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=jev_client._build_body(draft, questions),
            timeout=REQUEST_TIMEOUT_SEC,
        )
    except httpx.HTTPError as e:
        return time.perf_counter() - start, False, None, type(e).__name__
    elapsed = time.perf_counter() - start
    if resp.status_code != 200:
        return elapsed, False, None, f"HTTP {resp.status_code}"
    try:
        answers = resp.json().get("answers") or {}
    except ValueError:
        return elapsed, False, None, "bad JSON"
    scores = {k: (answers.get(k) or {}).get("noul") for k in questions}
    if any(not isinstance(s, (int, float)) for s in scores.values()):
        return elapsed, False, scores, "missing answer"
    return elapsed, True, scores, ""


def run_batched(client, key, draft, questions, start_gate):
    start_gate.wait()
    t0 = time.perf_counter()
    _, ok, scores, err = post(client, key, draft, questions)
    return time.perf_counter() - t0, ok, scores, err, []


def run_separate(pool, client, key, draft, questions, start_gate):
    start_gate.wait()
    t0 = time.perf_counter()
    futures = {k: pool.submit(post, client, key, draft, {k: q}) for k, q in questions.items()}
    results = {k: f.result() for k, f in futures.items()}
    wall = time.perf_counter() - t0
    ok = all(r[1] for r in results.values())
    scores = {k: (r[2] or {}).get(k) for k, r in results.items()}
    err = ";".join(f"{k}:{r[3]}" for k, r in results.items() if r[3])
    per_request = [r[0] for r in results.values()]
    return wall, ok, scores, err, per_request


def main():
    key = api_key_store.get_api_key()
    if not key:
        sys.exit("No API key (set one via the tray Settings or TYPESAFE_API_KEY).")
    questions = jev_client.get_active_questions()
    n_q = len(questions)
    print(f"{n_q} active questions: {', '.join(questions)}")

    batched_client, separate_client = make_client(), make_client()
    # Each separate request needs its own connection, so give that pool room.
    separate_pool = ThreadPoolExecutor(max_workers=n_q)
    outer_pool = ThreadPoolExecutor(max_workers=2)

    # Warm up DNS/TLS/keep-alive on both clients (n_q connections for the
    # separate client) so trial 1 isn't dominated by handshake cost.
    print("warming up...")
    warm_gate = threading.Barrier(2)
    list(outer_pool.map(
        lambda f: f(),
        [
            lambda: run_batched(batched_client, key, DRAFTS[0], questions, warm_gate),
            lambda: run_separate(separate_pool, separate_client, key, DRAFTS[0], questions, warm_gate),
        ],
    ))

    fields = [
        "trial", "draft_idx", "n_questions",
        "batched_sec", "separate_sec", "separate_min_req_sec", "separate_max_req_sec",
        "faster", "delta_sec", "batched_over_app_timeout", "separate_over_app_timeout",
        "verdicts_agree", "batched_ok", "separate_ok", "errors",
    ]
    new_file = not OUT_CSV.exists()
    collected = 0
    attempts = 0
    with OUT_CSV.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new_file:
            writer.writeheader()
        draft_cycle = itertools.cycle(range(len(DRAFTS)))
        while collected < TARGET and attempts < MAX_ATTEMPTS:
            attempts += 1
            idx = next(draft_cycle)
            draft = DRAFTS[idx]
            gate = threading.Barrier(2)
            fb = outer_pool.submit(run_batched, batched_client, key, draft, questions, gate)
            fs = outer_pool.submit(run_separate, separate_pool, separate_client, key, draft, questions, gate)
            b_wall, b_ok, b_scores, b_err, _ = fb.result()
            s_wall, s_ok, s_scores, s_err, s_reqs = fs.result()

            if b_ok and s_ok:
                collected += 1
                thr = jev_client.NOUL_THRESHOLD
                agree = all((b_scores[k] >= thr) == (s_scores[k] >= thr) for k in questions)
                writer.writerow({
                    "trial": collected, "draft_idx": idx, "n_questions": n_q,
                    "batched_sec": f"{b_wall:.4f}", "separate_sec": f"{s_wall:.4f}",
                    "separate_min_req_sec": f"{min(s_reqs):.4f}",
                    "separate_max_req_sec": f"{max(s_reqs):.4f}",
                    "faster": "batched" if b_wall < s_wall else "separate",
                    "delta_sec": f"{s_wall - b_wall:.4f}",
                    "batched_over_app_timeout": b_wall > jev_client.TIMEOUT_SEC,
                    "separate_over_app_timeout": s_wall > jev_client.TIMEOUT_SEC,
                    "verdicts_agree": agree,
                    "batched_ok": True, "separate_ok": True, "errors": "",
                })
                fh.flush()
                print(f"[{collected:3d}/{TARGET}] batched={b_wall:.2f}s separate={s_wall:.2f}s agree={agree}")
            else:
                print(f"[fail attempt {attempts}] batched_ok={b_ok} ({b_err}) separate_ok={s_ok} ({s_err})")
            time.sleep(PAUSE_BETWEEN_TRIALS_SEC)

    separate_pool.shutdown()
    outer_pool.shutdown()
    print(f"\ncollected {collected} samples in {attempts} attempts -> {OUT_CSV}")
    summarize(OUT_CSV)


def summarize(path):
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return
    b = [float(r["batched_sec"]) for r in rows]
    s = [float(r["separate_sec"]) for r in rows]

    def p(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]

    for name, xs in (("batched", b), ("separate", s)):
        print(
            f"{name:9s} n={len(xs)} mean={statistics.mean(xs):.3f}s "
            f"median={statistics.median(xs):.3f}s p90={p(xs, .9):.3f}s "
            f"p99={p(xs, .99):.3f}s min={min(xs):.3f}s max={max(xs):.3f}s"
        )
    wins = sum(1 for r in rows if r["faster"] == "batched")
    print(f"batched faster in {wins}/{len(rows)} trials")
    agree = sum(1 for r in rows if r["verdicts_agree"] == "True")
    print(f"verdicts agree in {agree}/{len(rows)} trials")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        summarize(OUT_CSV)
    else:
        main()
