"""Phase 5 experiments: bot-vs-bot matches under network delay, packet loss and CPU/memory load.

    python experiments.py [--reps N] [--seconds S] [--only delay,loss,...] [--out DIR]

For every condition below, and N times each (default 3), it starts docker-compose.yml: the
server, two headless bots (bot.py) and a load generator, all on the same two CPUs. The bots
play for 120 s (60 s under CPU or memory load; --seconds S sets one length for all) or until
the match is over. Network conditions are set with tc netem in every container (netem.sh), so
a delay is one-way and a round trip is twice it; CPU and memory load come from stress-ng (load.sh).

The delay and loss runs barely use the CPU, so up to four of them run side by side, each as its
own compose project on its own two CPUs. The baseline and the load runs run alone on CPUs 0-1,
because the load has to have the game's CPUs to itself.

Results go to results/<date>-<time>/:
    runs.csv                   one row per run: condition, settings, seeds, CPUs, start, duration, exit code
    <condition>/rep<k>/        the server log, both bots' client logs (see telemetry.py) and compose.log
Turn them into plots with plots.py. Runs use fixed seeds (rep k: 2k-1 and 2k), so the bots aim
the same way every time; network and scheduling timing still vary, which is the point.
"""
import csv
import os
import queue
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT = "pong-exp"
ROOT = Path(__file__).parent
SIDE_BY_SIDE = ("0-1", "2-3", "4-5", "6-7")  # CPUs for the network runs; the rest is left to Docker
ALONE = ("0-1",)

# (sweep, value, settings): every sweep varies one thing from the baseline
CONDITIONS = [("baseline", 0, {})]
CONDITIONS += [("delay", ms, {"DELAY_MS": ms, "JITTER_MS": ms // 10}) for ms in (10, 25, 50, 100, 150)]
CONDITIONS += [("loss", pct, {"LOSS_PCT": pct}) for pct in (1, 2, 5, 10)]
CONDITIONS += [("cpu", workers, {"CPU_LOAD": workers}) for workers in (2, 8)]
CONDITIONS += [("memory", workers, {"MEM_LOAD": workers}) for workers in (4,)]
SETTINGS = ("DELAY_MS", "JITTER_MS", "LOSS_PCT", "CPU_LOAD", "MEM_LOAD")
NETWORK = ("delay", "loss")  # the sweeps that may run side by side
SECONDS = {"cpu": 60, "memory": 60}  # frame times come once a second, so a minute is plenty; others 120


def compose(project, *args, env=None, **kwargs):
    return subprocess.run(["docker", "compose", "-p", project, *args], cwd=ROOT, env=env, **kwargs)


def run_one(run_dir, settings, seeds, seconds, project, cpus):
    """One match under one condition. Returns the bots' exit code (or 'timeout') and the time it took."""
    run_dir.mkdir(parents=True)
    env = {**os.environ, "RUN_DIR": str(run_dir.resolve()), "RUN_SECONDS": str(seconds), "CPUSET": cpus,
           "SEED1": str(seeds[0]), "SEED2": str(seeds[1]), **{k: str(v) for k, v in settings.items()}}
    start = time.perf_counter()
    with open(run_dir / "compose.log", "w") as log:
        try:
            done = compose(project, "up", "--abort-on-container-exit", "--exit-code-from", "bot1",
                           env=env, stdout=log, stderr=subprocess.STDOUT, timeout=seconds + 60)
            result = done.returncode
        except subprocess.TimeoutExpired:
            result = "timeout"
        finally:
            compose(project, "down", "-t", "1", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result, time.perf_counter() - start


def run_all(runs, slots, out, done):
    """Runs one match per (name, sweep, value, settings, rep, seconds), as many at a time as there are slots of
    CPUs, each slot its own compose project. Calls done(run, cpus, started, result, took) as each ends."""
    free = queue.Queue()
    for i, cpus in enumerate(slots):
        free.put((f"{PROJECT}-{i}", cpus))

    def play(run):
        name, _, _, settings, rep, seconds = run
        project, cpus = free.get()
        try:
            started = time.strftime("%Y-%m-%d %H:%M:%S")
            seeds = (2 * rep - 1, 2 * rep)
            return run, cpus, started, *run_one(out / name / f"rep{rep}", settings, seeds, seconds, project, cpus)
        finally:
            free.put((project, cpus))

    with ThreadPoolExecutor(len(slots)) as pool:
        for future in as_completed([pool.submit(play, run) for run in runs]):
            done(*future.result())


def main():
    args = sys.argv[1:]
    options = {"--reps": "3", "--seconds": None, "--only": None, "--out": None}
    for flag in options:
        if flag in args:
            i = args.index(flag)
            options[flag] = args[i + 1]
            del args[i:i + 2]
    if args:
        print(__doc__)
        sys.exit(1)
    reps = int(options["--reps"])
    seconds = None if options["--seconds"] is None else float(options["--seconds"])
    only = None if options["--only"] is None else set(options["--only"].split(","))
    conditions = [c for c in CONDITIONS if only is None or c[0] in only]
    out = Path(options["--out"] or ROOT / "results" / time.strftime("%Y%m%d-%H%M%S"))
    runs = {False: [], True: []}  # side by side or not
    for sweep, value, settings in conditions:
        name = sweep if sweep == "baseline" else f"{sweep}-{value}"
        for rep in range(1, reps + 1):
            runs[sweep in NETWORK].append((name, sweep, value, settings, rep, seconds or SECONDS.get(sweep, 120)))

    print(f"building the image, then {len(conditions)} conditions x {reps} reps: {len(runs[True])} network runs "
          f"{len(SIDE_BY_SIDE)} at a time, then {len(runs[False])} alone -> {out}")
    compose(PROJECT, "build", "--quiet", check=True)
    out.mkdir(parents=True)
    with open(out / "runs.csv", "w", newline="", buffering=1) as f:
        writer = csv.writer(f)
        writer.writerow(["condition", "sweep", "value", "rep", *(s.lower() for s in SETTINGS), "seed1", "seed2",
                         "cpus", "started", "seconds", "exit"])

        def done(run, cpus, started, result, took):
            name, sweep, value, settings, rep, _ = run
            writer.writerow([name, sweep, value, rep, *(settings.get(s, 0) for s in SETTINGS), 2 * rep - 1, 2 * rep,
                             cpus, started, f"{took:.1f}", result])
            print(f"{name:>12} rep {rep} on CPUs {cpus}: {took:5.1f} s, exit {result}", flush=True)

        run_all(runs[True], SIDE_BY_SIDE, out, done)
        run_all(runs[False], ALONE, out, done)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
