"""Phase 5 plots: turn an experiments.py results folder into figures and a summary table.

    python plots.py results/<date>-<time>

Needs matplotlib (pip install -r requirements-plots.txt; not needed to play). Writes into the
results folder:
    summary.csv     one row per condition: every metric below, averaged over its reps (the table view)
    network.png     delay and loss: what the opponent saw after a hit, and how long the answer took
    load.png        CPU and memory load sweeps
    traffic.png     messages and bytes per second under each condition

Each metric is first computed per run from the logs (both bots pooled), then averaged over the
condition's reps; the whiskers show the lowest and highest rep. Every chart has the baseline
(no delay, loss or load) as its leftmost point.

Metrics, and where they come from:
    rtt                round trip of the pings (client pings.csv)
    snapshot delay     one-way, server send to client arrival (client snapshots.csv)
    call to server     contact moment to the defender's hit/miss reaching the server (server log)
    answer             contact moment to the server's answer reaching a client (client contacts.csv)
    wrong predictions  share of the opponent's calls a client predicted wrongly (contacts.csv)
    opponent saw       per predicted call: right at once, fixed smoothly, or visibly wrong (contacts.csv)
    call lateness      contact moment to the frame that noticed it (contacts.csv)
    frame time         frame intervals; late frames are over 1.5x that second's median (seconds.csv)
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # files only, no window
import matplotlib.pyplot as plt  # noqa: E402

# reference palette: categorical slots in fixed order, text and grid in neutral inks
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # blue, orange, aqua
SURFACE, TEXT, TEXT_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1"
SWEEPS = ("baseline", "delay", "loss", "cpu", "memory")  # the order of summary.csv and the traffic chart
GLIDE_MAX = 120  # units; client.py glides over a smaller correction and jumps over a bigger one
DPI = 600  # print quality at full page width (6.8 in is 4080 px)


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def numbers(rows, key, where=lambda r: True):
    return [float(r[key]) for r in rows if r[key] != "" and where(r)]


def pct(values, q):
    """The q-th percentile (0..100) by linear interpolation; None for no values."""
    if not values:
        return None
    values = sorted(values)
    i = (len(values) - 1) * q / 100
    lo = int(i)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (i - lo)


def opponent_saw(contact):
    """What the opponent's screen showed for the defender's call: "right" at once, a small error that the
    client glided away ("smooth"), or "wrong": the hit or miss itself, or an error so big the ball jumped."""
    correction = float(contact["correction"]) if contact["correction"] else 0  # none after a goal
    if contact["agreed"] == "0" or correction > GLIDE_MAX:
        return "wrong"
    return "smooth" if correction > 1 else "right"


def run_metrics(run_dir):
    """The metrics of one run, from its server log and both bots' client logs."""
    logs = defaultdict(list)
    for kind in ("pings", "snapshots", "contacts", "seconds"):
        for f in run_dir.glob(f"bot*-{kind}.csv"):
            logs[kind] += read(f)
    server = [r for f in run_dir.glob("server-*.csv") for r in read(f)]
    rtt = numbers(logs["pings"], "rtt_ms")
    delay = numbers(logs["snapshots"], "latency_ms")
    calls = [1000 * float(r["value"]) for r in server if r["event"] in ("hit", "miss")]
    answers = numbers(logs["contacts"], "answer_ms")
    predicted = [r for r in logs["contacts"] if r["mine"] == "0"]
    lateness = numbers(logs["contacts"], "late_ms", lambda r: r["mine"] == "1")
    seen = [opponent_saw(r) for r in predicted]
    seconds = logs["seconds"]
    frames = sum(numbers(seconds, "frames"))
    duration = len(seconds) / 2 or None  # two bots, one row per second each
    return {
        "rtt_p50": pct(rtt, 50), "rtt_p95": pct(rtt, 95),
        "snapshot_delay_p50": pct(delay, 50), "snapshot_delay_p95": pct(delay, 95),
        "call_to_server_p50": pct(calls, 50), "call_to_server_p95": pct(calls, 95),
        "answer_p50": pct(answers, 50), "answer_p95": pct(answers, 95),
        "wrong_predictions_pct": 100 * sum(r["agreed"] == "0" for r in predicted) / len(predicted) if predicted else None,
        "call_lateness_p95": pct(lateness, 95),
        "fixed_smoothly_pct": 100 * seen.count("smooth") / len(seen) if seen else None,
        "visibly_wrong_pct": 100 * seen.count("wrong") / len(seen) if seen else None,
        "frame_mean_ms": statistics.fmean(numbers(seconds, "mean_frame_ms")) if seconds else None,
        "frame_max_ms": pct(numbers(seconds, "max_frame_ms"), 95),
        "late_frames_pct": 100 * sum(numbers(seconds, "late_frames")) / frames if frames else None,
        "msgs_per_s": (sum(numbers(seconds, "msgs_out")) + sum(numbers(seconds, "msgs_in"))) / 2 / duration if duration else None,
        "bytes_per_s": (sum(numbers(seconds, "bytes_out")) + sum(numbers(seconds, "bytes_in"))) / 2 / duration if duration else None,
        "contacts": len(calls),
    }


def summarise(results):
    """{condition: {"sweep", "value", metric: (mean, lowest, highest)}} over the reps in runs.csv."""
    by_condition = defaultdict(list)
    info = {}
    for run in read(results / "runs.csv"):
        name = run["condition"]
        info[name] = {"sweep": run["sweep"], "value": float(run["value"])}
        by_condition[name].append(run_metrics(results / name / f"rep{run['rep']}"))
    summary = {}
    order = lambda name: (SWEEPS.index(info[name]["sweep"]), info[name]["value"])  # runs side by side end in any order
    for name, runs in sorted(by_condition.items(), key=lambda item: order(item[0])):
        summary[name] = dict(info[name])
        for metric in runs[0]:
            values = [r[metric] for r in runs if r[metric] is not None]
            summary[name][metric] = (statistics.fmean(values), min(values), max(values)) if values else None
    return summary


def write_summary(results, summary):
    metrics = [m for m in next(iter(summary.values())) if m not in ("sweep", "value")]
    with open(results / "summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["condition", "sweep", "value", *metrics, *(f"{m}_lowest_rep" for m in metrics),
                         *(f"{m}_highest_rep" for m in metrics)])
        for name, row in summary.items():
            cells = [row[m] for m in metrics]
            writer.writerow([name, row["sweep"], f"{row['value']:g}",
                             *(fmt(c and c[0]) for c in cells), *(fmt(c and c[1]) for c in cells),
                             *(fmt(c and c[2]) for c in cells)])


def fmt(value):
    return "" if value is None else f"{value:.3f}"


def style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=TEXT, pad=10)
    ax.set_ylabel(ylabel, color=TEXT_2, fontsize=9)
    ax.tick_params(colors=TEXT_2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)


def zero_based(ax):
    """Start the y-axis at 0. Only after drawing: setting a limit switches autoscaling off."""
    ax.set_ylim(bottom=0)
    values = [v for line in ax.get_lines() for v in line.get_ydata()] + [bar.get_height() for bar in ax.patches]
    if ax.get_ylim()[1] < 1e-9 or all(abs(v) < 1e-9 for v in values):
        ax.set_ylim(0, 1)  # all zero: show a plain 0-1 scale and say it, instead of zooming into nothing
        ax.text(0.5, 0.5, "0 in every run", transform=ax.transAxes, ha="center", fontsize=9, color=TEXT_2)


def sweep_points(summary, sweeps):
    """The baseline plus the conditions of the given sweeps, ordered by their value."""
    rows = [r for r in summary.values() if r["sweep"] == "baseline"]
    rows += sorted((r for r in summary.values() if r["sweep"] in sweeps), key=lambda r: r["value"])
    return rows


def line(ax, rows, metric, label, slot, xs=None, scale=1, whiskers=True):
    """One series, by default with min-max whiskers over the reps, and a direct label at its end."""
    points = [(x, r[metric]) for x, r in zip(xs or [r["value"] for r in rows], rows) if r[metric] is not None]
    if not points:
        return
    x = [p[0] for p in points]
    y = [p[1][0] * scale for p in points]
    err = [[(p[1][0] - p[1][1]) * scale for p in points], [(p[1][2] - p[1][0]) * scale for p in points]]
    ax.errorbar(x, y, yerr=err if whiskers else None, color=SERIES[slot], linewidth=2, marker="o", markersize=5,
                markeredgecolor=SURFACE, markeredgewidth=1.5, capsize=0, elinewidth=1, ecolor=SERIES[slot])
    if label:  # a lone series needs none: the title names it
        ax.annotate(label, (x[-1], y[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=8, color=TEXT_2)


def figure(panels, path, suptitle):
    fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 3.6), facecolor=SURFACE)
    for ax, draw in zip(axes if len(panels) > 1 else [axes], panels):
        draw(ax)
        ax.margins(x=0.12)  # room for the direct labels at the line ends
        zero_based(ax)
    fig.suptitle(suptitle, x=0.01, ha="left", fontsize=13, color=TEXT)
    fig.tight_layout()
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def plot_network(summary, results):
    """What the players saw under delay and loss: whether the opponent's screen showed the defender's
    call right, and how long until the server's answer arrived. One row per condition, baseline on top."""
    names = [n for n, r in summary.items() if r["sweep"] in ("baseline", "delay", "loss")]  # in sweep order
    ys, y = [], 0
    for i, name in enumerate(names):
        if i and summary[name]["sweep"] != summary[names[i - 1]]["sweep"]:
            y += 0.6  # a gap between the sweeps
        ys.append(y)
        y += 1
    mean = lambda name, metric: summary[name][metric][0] if summary[name][metric] else 0
    fig, (saw, waited) = plt.subplots(1, 2, figsize=(6.8, 3.4), sharey=True, facecolor=SURFACE)

    style(saw, "What the other player's screen showed", "")
    smooth = [mean(n, "fixed_smoothly_pct") for n in names]
    wrong = [mean(n, "visibly_wrong_pct") for n in names]
    right = [100 - s - w for s, w in zip(smooth, wrong)]
    left = [0] * len(names)
    for widths, color, label in ((right, "#dddcd7", "right away"), (smooth, "#f4b394", "fixed smoothly"),
                                 (wrong, SERIES[1], "visibly wrong")):
        saw.barh(ys, widths, left=left, height=0.7, color=color, edgecolor=SURFACE, linewidth=1, label=label)
        left = [a + b for a, b in zip(left, widths)]
    saw.set_xlim(0, 100)
    saw.set_xticks([0, 25, 50, 75, 100])
    saw.set_xlabel("% of times the ball reached a paddle", color=TEXT_2, fontsize=8)

    style(waited, "When the server's answer arrived", "")
    waited.barh(ys, [mean(n, "answer_p95") for n in names], height=0.7, color="#a9c8ee", label="95 % arrived by")
    waited.barh(ys, [mean(n, "answer_p50") for n in names], height=0.7, color=SERIES[0], label="half arrived by")
    waited.set_xlabel("ms after the ball reached the paddle", color=TEXT_2, fontsize=8)

    labels = {"baseline": lambda v: "No delay or loss", "delay": lambda v: f"Delay {v:g} ms", "loss": lambda v: f"Loss {v:g} %"}
    saw.set_yticks(ys, [labels[summary[n]["sweep"]](summary[n]["value"]) for n in names], fontsize=8, color=TEXT)
    saw.invert_yaxis()
    for ax in (saw, waited):
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_title(ax.get_title(loc="left"), loc="left", fontsize=10, color=TEXT, pad=22)
        handles, texts = ax.get_legend_handles_labels()
        ax.legend(handles[::-1] if ax is waited else handles, texts[::-1] if ax is waited else texts,
                  loc="lower left", bbox_to_anchor=(0, 1), ncol=3, frameon=False, fontsize=7,
                  handlelength=1, columnspacing=1, borderaxespad=0.3)
    fig.tight_layout()
    fig.savefig(results / "network.png", dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def plot_load(summary, results):
    sweeps = [("cpu", "CPU load (busy workers on the game's 2 CPUs)"),
              ("memory", "Memory load (workers of 256 MB on the game's 2 CPUs)")]
    fig = plt.figure(figsize=(12.6, 7.4), facecolor=SURFACE, layout="constrained")
    for (sweep, title), sub in zip(sweeps, fig.subfigures(2, 1, hspace=0.06)):
        sub.set_facecolor(SURFACE)
        sub.suptitle(title, x=0.01, ha="left", fontsize=12, color=TEXT)
        row_axes = sub.subplots(1, 3)
        rows = sweep_points(summary, (sweep,))
        style(row_axes[0], "Frame time", "ms")
        line(row_axes[0], rows, "frame_mean_ms", "mean", 0)
        line(row_axes[0], rows, "frame_max_ms", "longest", 1)
        style(row_axes[1], "Late frames (> 1.5x median)", "% of frames")
        line(row_axes[1], rows, "late_frames_pct", "", 0)
        style(row_axes[2], "Contact noticed late by", "ms, 95th percentile")
        line(row_axes[2], rows, "call_lateness_p95", "", 0)
        for ax in row_axes:
            ax.set_xlabel("workers", color=TEXT_2, fontsize=9)
            ax.margins(x=0.12)  # room for the direct labels at the line ends
            zero_based(ax)
    fig.savefig(results / "load.png", dpi=DPI, facecolor=SURFACE)
    plt.close(fig)


def plot_traffic(summary, results):
    rows = list(summary.values())
    names = list(summary)
    xs = list(range(len(rows)))

    def bars(ax, metric, title, unit, slot):
        style(ax, title, unit)
        values = [r[metric][0] if r[metric] else 0 for r in rows]
        ax.bar(xs, values, width=0.7, color=SERIES[slot])
        ax.set_xticks(xs, names, rotation=60, ha="right", fontsize=7)

    figure([lambda ax: bars(ax, "msgs_per_s", "Messages per second, per client (in + out)", "messages/s", 0),
            lambda ax: bars(ax, "bytes_per_s", "Bytes per second, per client (in + out)", "bytes/s", 0)],
           results / "traffic.png", "Traffic")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    results = Path(sys.argv[1])
    summary = summarise(results)
    write_summary(results, summary)
    sweeps = {r["sweep"] for r in summary.values()}
    if sweeps & {"delay", "loss"}:
        plot_network(summary, results)
    if sweeps & {"cpu", "memory"}:
        plot_load(summary, results)
    plot_traffic(summary, results)
    print(f"wrote summary.csv and plots to {results}")


if __name__ == "__main__":
    main()
