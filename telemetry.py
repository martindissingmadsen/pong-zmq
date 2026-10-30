"""CSV logs for the client (Phase 4).

    python client.py NAME HOST --log           writes logs/NAME-YYYYmmdd-HHMMSS-*.csv

Four files, one row per event. Every row starts with `wall` (time.time(), for lining up
with other logs) and `server_t` (this client's estimate of server time, which is what
lines up rows from different machines). Times in the other columns are in ms.

- pings.csv, per pong: `rtt_ms` round trip; `sample_offset_ms` the clock offset this
  ping suggests; `offset_ms` the offset the client uses (fastest of the last 15).
- snapshots.csv, per snapshot from the server: `seq`, `skipped` (seq numbers missing
  since the previous one: dropped by CONFLATE or lost), `bytes`, `latency_ms` (one-way,
  server send to client arrival, by the synced clock), then phase, score, hits, seg id.
- contacts.csv, per paddle contact this client saw: `mine` (1 if it was our call, 0 if
  we predicted the opponent's), `call` (hit/miss), `late_ms` (how long after the contact
  the client noticed it: frame lateness), `answer_ms` (contact to the server's answer
  arriving), `outcome` (hit/goal by the server), `agreed`, `correction` (after a hit: how
  many units the path we showed was off from the server's when its answer arrived; empty
  after a goal, where the ball is reset anyway).
- seconds.csv, once a second: frames and frame times (`late_frames`: longer than 1.5x
  the median), messages and bytes in and out, latest rtt and clock offset.

Each file is written by one thread only (pings and snapshots by the socket thread, the
rest by the game loop), so no locking is needed. Lines are flushed as they are written.
"""
import csv
import statistics
import time
from pathlib import Path

COLUMNS = {
    "pings": ["wall", "server_t", "rtt_ms", "sample_offset_ms", "offset_ms"],
    "snapshots": ["wall", "server_t", "seq", "skipped", "bytes", "latency_ms", "phase", "score_left",
                  "score_right", "hits", "seg"],
    "contacts": ["wall", "server_t", "seg", "side", "mine", "call", "late_ms", "answer_ms", "outcome", "agreed",
                 "correction"],
    "seconds": ["wall", "server_t", "frames", "mean_frame_ms", "max_frame_ms", "late_frames", "msgs_out",
                "msgs_in", "bytes_out", "bytes_in", "rtt_ms", "offset_ms"],
}


class Recorder:
    def __init__(self, directory, name):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.files, self.writers = {}, {}
        for kind, columns in COLUMNS.items():
            f = open(Path(directory) / f"{name}-{stamp}-{kind}.csv", "w", newline="", buffering=1)
            self.files[kind], self.writers[kind] = f, csv.writer(f)
            self.writers[kind].writerow(columns)
        self.prefix = Path(directory) / f"{name}-{stamp}"
        self.frames, self.second = [], None

    def row(self, kind, server_t, *values):
        self.writers[kind].writerow([f"{time.time():.6f}", fmt(server_t), *map(fmt, values)])

    def frame(self, interval, server_t, traffic, clock):
        """Called once per frame; writes a seconds.csv row when a wall-clock second is over.
        `traffic` is the running (msgs_out, msgs_in, bytes_out, bytes_in) since the start."""
        now = time.time()
        if self.second is None:
            self.second, self.traffic = int(now), traffic
        if int(now) > self.second:
            if self.frames:
                median = statistics.median(self.frames)
                late = sum(f > 1.5 * median for f in self.frames)
                self.row("seconds", server_t, len(self.frames), 1000 * statistics.fmean(self.frames),
                         1000 * max(self.frames), late, *(a - b for a, b in zip(traffic, self.traffic)),
                         ms(clock.rtt), ms(clock.offset))
            self.second, self.frames, self.traffic = int(now), [], traffic
        if interval is not None:
            self.frames.append(interval)

    def close(self):
        for f in self.files.values():
            f.close()


def ms(seconds):
    return None if seconds is None else 1000 * seconds


def fmt(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return value
