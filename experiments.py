"""Phase 5 experiments: bot-vs-bot matches under network delay, packet loss and CPU/memory load.

    python experiments.py [--reps N] [--seconds S] [--only delay,loss,...] [--out DIR]

For every condition below, and N times each (default 3), it starts docker-compose.yml: the
server, two headless bots (bot.py) and a load generator, all on the same two CPUs. The bots
play for S seconds (default 120) or until the match is over. Network conditions are set with
tc netem in every container (netem.sh), so a delay is one-way and a round trip is twice it;
CPU and memory load come from stress-ng (load.sh).

Results go to results/<date>-<time>/:
    runs.csv                   one row per run: condition, settings, seeds, start, duration, exit code
    <condition>/rep<k>/        the server log, both bots' client logs (see telemetry.py) and compose.log
Turn them into plots with plots.py. Runs use fixed seeds (rep k: 2k-1 and 2k), so the bots aim
the same way every time; network and scheduling timing still vary, which is the point.
"""
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "pong-exp"
ROOT = Path(__file__).parent

# (sweep, value, settings): every sweep varies one thing from the baseline
CONDITIONS = [("baseline", 0, {})]
CONDITIONS += [("delay", ms, {"DELAY_MS": ms, "JITTER_MS": ms // 10}) for ms in (10, 25, 50, 100, 150)]
CONDITIONS += [("loss", pct, {"LOSS_PCT": pct}) for pct in (1, 2, 5, 10)]
CONDITIONS += [("cpu", workers, {"CPU_LOAD": workers}) for workers in (2, 4, 8)]
CONDITIONS += [("memory", workers, {"MEM_LOAD": workers}) for workers in (1, 2, 4)]
SETTINGS = ("DELAY_MS", "JITTER_MS", "LOSS_PCT", "CPU_LOAD", "MEM_LOAD")


def compose(*args, env=None, **kwargs):
    return subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=ROOT, env=env, **kwargs)


def run_one(run_dir, settings, seeds, seconds):
    """One match under one condition. Returns the bots' exit code (or 'timeout') and the time it took."""
    run_dir.mkdir(parents=True)
    env = {**os.environ, "RUN_DIR": str(run_dir.resolve()), "RUN_SECONDS": str(seconds),
           "SEED1": str(seeds[0]), "SEED2": str(seeds[1]), **{k: str(v) for k, v in settings.items()}}
    start = time.perf_counter()
    with open(run_dir / "compose.log", "w") as log:
        try:
            done = compose("up", "--abort-on-container-exit", "--exit-code-from", "bot1",
                           env=env, stdout=log, stderr=subprocess.STDOUT, timeout=seconds + 60)
            result = done.returncode
        except subprocess.TimeoutExpired:
            result = "timeout"
        finally:
            compose("down", "-t", "1", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result, time.perf_counter() - start


def main():
    args = sys.argv[1:]
    options = {"--reps": "3", "--seconds": "120", "--only": None, "--out": None}
    for flag in options:
        if flag in args:
            i = args.index(flag)
            options[flag] = args[i + 1]
            del args[i:i + 2]
    if args:
        print(__doc__)
        sys.exit(1)
    reps, seconds = int(options["--reps"]), float(options["--seconds"])
    only = None if options["--only"] is None else set(options["--only"].split(","))
    conditions = [c for c in CONDITIONS if only is None or c[0] in only]
    out = Path(options["--out"] or ROOT / "results" / time.strftime("%Y%m%d-%H%M%S"))

    print(f"building the image, then {len(conditions)} conditions x {reps} reps, up to {seconds:g} s each -> {out}")
    compose("build", "--quiet", check=True)
    out.mkdir(parents=True)
    with open(out / "runs.csv", "w", newline="", buffering=1) as f:
        writer = csv.writer(f)
        writer.writerow(["condition", "sweep", "value", "rep", *(s.lower() for s in SETTINGS), "seed1", "seed2",
                         "started", "seconds", "exit"])
        for sweep, value, settings in conditions:
            name = sweep if sweep == "baseline" else f"{sweep}-{value}"
            for rep in range(1, reps + 1):
                seeds = (2 * rep - 1, 2 * rep)
                started = time.strftime("%Y-%m-%d %H:%M:%S")
                result, took = run_one(out / name / f"rep{rep}", settings, seeds, seconds)
                writer.writerow([name, sweep, value, rep, *(settings.get(s, 0) for s in SETTINGS), *seeds,
                                 started, f"{took:.1f}", result])
                print(f"{name:>12} rep {rep}: {took:5.1f} s, exit {result}")
    print(f"done: {out}")


if __name__ == "__main__":
    main()
