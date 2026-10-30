"""Unit tests for plots.py's metrics (skipped without matplotlib):  python -m unittest"""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

HAVE_MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None
if HAVE_MATPLOTLIB:
    import plots


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


@unittest.skipUnless(HAVE_MATPLOTLIB, "plots.py needs matplotlib (requirements-plots.txt)")
class TestMetrics(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(plots.pct([4, 1, 3, 2], 50), 2.5)
        self.assertEqual(plots.pct([10, 20], 95), 19.5)
        self.assertIsNone(plots.pct([], 50))

    def test_one_run(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write(run / "server-1.csv", ["t", "event", "player", "seq", "ts", "value"],
                  [[1, "hit", "bot1", "", "", "0.030"], [2, "miss", "bot2", "", "", "0.050"], [3, "recv", "bot1", 1, 1, ""]])
            for bot in ("bot1", "bot2"):
                write(run / f"{bot}-1-pings.csv", ["wall", "server_t", "rtt_ms", "sample_offset_ms", "offset_ms"],
                      [[0, 0, 50, 0, 0], [0, 0, 70, 0, 0]])
                write(run / f"{bot}-1-contacts.csv",
                      ["wall", "server_t", "seg", "side", "mine", "call", "late_ms", "answer_ms", "outcome", "agreed", "correction"],
                      [[0, 0, 1, 0, 1, "hit", 4, 60, "hit", 1, 0], [0, 0, 2, 1, 0, "hit", 2, 80, "goal", 0, ""]])
                write(run / f"{bot}-1-seconds.csv",
                      ["wall", "server_t", "frames", "mean_frame_ms", "max_frame_ms", "late_frames", "msgs_out", "msgs_in",
                       "bytes_out", "bytes_in", "rtt_ms", "offset_ms"],
                      [[0, 0, 100, 10, 12, 2, 5, 7, 500, 900, 60, 0]])
                write(run / f"{bot}-1-snapshots.csv",
                      ["wall", "server_t", "seq", "skipped", "bytes", "latency_ms", "phase", "score_left", "score_right",
                       "hits", "seg"], [[0, 0, 1, "", 200, 25, "playing", 0, 0, 0, 1]])
            m = plots.run_metrics(run)
        self.assertEqual(m["rtt_p50"], 60)
        self.assertEqual(m["call_to_server_p50"], 40)
        self.assertEqual(m["wrong_predictions_pct"], 100, "both predictions (mine=0) were wrong")
        self.assertEqual(m["late_frames_pct"], 2)
        self.assertEqual(m["msgs_per_s"], 12, "per client: (5 out + 7 in) a second")
        self.assertEqual(m["contacts"], 2)

    def test_what_the_opponent_saw(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            write(run / "bot1-1-contacts.csv",
                  ["wall", "server_t", "seg", "side", "mine", "call", "late_ms", "answer_ms", "outcome", "agreed", "correction"],
                  [[0, 0, 1, 0, 1, "hit", 4, 60, "hit", 1, 50],  # our own call: never a prediction
                   [0, 0, 2, 1, 0, "hit", 2, 80, "hit", 1, 0.2],
                   [0, 0, 3, 1, 0, "hit", 2, 80, "hit", 1, 30],  # glided
                   [0, 0, 4, 1, 0, "hit", 2, 80, "hit", 1, 150],  # jumped
                   [0, 0, 5, 1, 0, "hit", 2, 80, "goal", 0, ""]])  # hit or miss wrong
            m = plots.run_metrics(run)
        self.assertEqual(m["fixed_smoothly_pct"], 25)
        self.assertEqual(m["visibly_wrong_pct"], 50, "a jump and a wrong hit or miss, of four predictions")

    def test_bars_are_not_all_zero(self):
        fig, ax = plots.plt.subplots()
        ax.bar([0, 1], [14, 13])
        plots.zero_based(ax)
        self.assertGreater(ax.get_ylim()[1], 13)
        self.assertEqual(len(ax.texts), 0, "no '0 in every run' over real bars")
        plots.plt.close(fig)


if __name__ == "__main__":
    unittest.main()
