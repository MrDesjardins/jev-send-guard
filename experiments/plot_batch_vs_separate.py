"""Graph the CSV produced by batch_vs_separate.py.

    uv run --with matplotlib python experiments/plot_batch_vs_separate.py
"""

import csv
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
rows = list(csv.DictReader((HERE / "results_batch_vs_separate.csv").open()))
trial = [int(r["trial"]) for r in rows]
b = [float(r["batched_sec"]) * 1000 for r in rows]
s = [float(r["separate_sec"]) * 1000 for r in rows]
n_q = rows[0]["n_questions"]

fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(16, 5))
fig.suptitle(
    f"Jev: 1 batched request ({n_q} questions) vs {n_q} parallel single-question requests "
    f"— n={len(rows)}, run simultaneously per trial"
)

ax1.plot(trial, b, "o-", ms=3, lw=1, label="batched")
ax1.plot(trial, s, "o-", ms=3, lw=1, label="separate")
ax1.axhline(statistics.median(b), color="C0", ls="--", lw=1)
ax1.axhline(statistics.median(s), color="C1", ls="--", lw=1)
ax1.set(title="Latency per trial (dashed = median)", xlabel="trial", ylabel="ms")
ax1.legend()

ax2.boxplot([b, s], tick_labels=["batched", "separate"], showmeans=True)
ax2.set(title="Distribution", ylabel="ms")

delta = [y - x for x, y in zip(b, s)]
ax3.hist(delta, bins=25, color="C2")
ax3.axvline(0, color="k", lw=1)
wins = sum(d > 0 for d in delta)
ax3.set(
    title=f"separate − batched (>0 means batched faster: {wins}/{len(delta)})",
    xlabel="ms", ylabel="trials",
)

fig.tight_layout()
# Lives with the README's other images, which embed it.
out = HERE.parent / "docs" / "media" / "batch_vs_separate.png"
fig.savefig(out, dpi=120)
print(out)
