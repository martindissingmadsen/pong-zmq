"""Unit tests for the networked client's clock and rally logic (no sockets):  python -m unittest"""
import csv
import json
import math
import tempfile
import threading
import time
import unittest

import zmq

import client
import game
import telemetry
from client import GLIDE_MAX, Clock, Rally
from game import HEIGHT, PADDLE_H, PADDLE_SPEED, R, WIDTH

LEFT_FACE, RIGHT_FACE = game.face_x(0), game.face_x(1)


def snapshot(seg=None, paddles=None, phase="playing", score=(0, 0), hits=0, players=("clara", "martin")):
    return {"players": list(players), "phase": phase, "ready": [False] * len(players), "score": list(score),
            "hits": hits, "seg": seg or {"id": 1, "p": [WIDTH / 2, HEIGHT / 2], "v": [0, 0], "t": 0.0},
            "paddles": paddles or [{"y": HEIGHT / 2, "dir": 0, "t": 0.0} for _ in players]}


def toward_left(y=300, distance=60, seg_id=5):
    """At time 0 the ball flies straight at the left paddle; it gets there at time distance / 300."""
    return {"id": seg_id, "p": [LEFT_FACE + R + distance, y], "v": [-300.0, 0.0], "t": 0.0}


def rally(name, snap, t=0.0):
    r = Rally(name)
    r.receive(snap, t)
    r.frame(t, 0)
    return r


class TestClock(unittest.TestCase):
    def test_offset_from_the_fastest_round_trip(self):
        clock = Clock()
        clock.pong(sent=10.0, server_t=110.30, received=10.4)  # slow: 0.4 s round trip
        clock.pong(sent=11.0, server_t=111.05, received=11.1)  # fast: the server answered at 11.05 our time
        clock.pong(sent=12.0, server_t=112.50, received=12.6)
        self.assertAlmostEqual(clock.offset, 100.0)
        self.assertAlmostEqual(clock.rtt, 0.6, msg="rtt shows the latest round trip")



class TestLinkPing(unittest.TestCase):
    def test_ping_does_not_wait_for_the_next_frame(self):
        ctx = zmq.Context()
        router, pub = ctx.socket(zmq.ROUTER), ctx.socket(zmq.PUB)
        old_ports = client.STATE_PORT, client.INPUT_PORT
        client.INPUT_PORT = router.bind_to_random_port("tcp://127.0.0.1")
        client.STATE_PORT = pub.bind_to_random_port("tcp://127.0.0.1")
        stop = threading.Event()

        received = []

        def fake_server():  # answers every ping at once
            while not stop.is_set():
                if router.poll(10):
                    ident, raw = router.recv_multipart()
                    received.append(msg := json.loads(raw))
                    if "ping" in msg:
                        router.send_multipart([ident, json.dumps({"pong": msg["ping"], "t": 0.0}).encode()])

        server = threading.Thread(target=fake_server)
        server.start()
        link = client.Link("probe", "127.0.0.1")
        link.paddle = {"y": 250.0, "dir": 1, "t": 3.0}  # as the game loop sets it each frame while playing
        try:
            start = time.perf_counter()
            while len(link.clock.samples) < 3 and time.perf_counter() - start < 3:
                link.update(0)
                time.sleep(0.05)  # a slow game loop: 20 frames a second
            self.assertGreaterEqual(len(link.clock.samples), 3)
            self.assertLess(min(rtt for rtt, _ in link.clock.samples), 0.01, "not rounded up to a 50 ms frame")
            pings = [m for m in received if "ping" in m]
            self.assertTrue(pings and all(m["paddle"] == link.paddle for m in pings), "the paddle rides on every ping")
        finally:
            link.close()
            stop.set()
            server.join()
            ctx.destroy(linger=0)
            client.STATE_PORT, client.INPUT_PORT = old_ports


class TestOwnPaddle(unittest.TestCase):
    def test_moves_at_once_and_reports_each_key_change_once(self):
        r = rally("clara", snapshot())
        r.frame(0.1, 1)
        r.frame(0.2, 1)
        state = r.frame(0.3, 0)
        self.assertAlmostEqual(state["paddles"][0], HEIGHT / 2 + PADDLE_SPEED * 0.2)
        self.assertEqual([m["paddle"]["dir"] for m in r.outbox], [1, 0])

    def test_watcher_sends_nothing(self):
        r = rally("guest", snapshot())
        r.frame(0.1, 1, ready=True)
        self.assertEqual(r.outbox, [])

    def test_ready_only_counts_between_balls(self):
        r = rally("clara", snapshot(phase="playing"))
        r.frame(0.1, 0, ready=True)
        self.assertEqual(r.outbox, [])
        r.receive(snapshot(phase="serve"), 0.2)
        r.frame(0.2, 0, ready=True)
        self.assertEqual(r.outbox, [{"ready": True}])


class TestBall(unittest.TestCase):
    def test_opponent_paddle_follows_its_last_key_change(self):
        r = rally("clara", snapshot(paddles=[{"y": 300, "dir": 0, "t": 0}, {"y": 200, "dir": 1, "t": 1.0}]))
        self.assertAlmostEqual(r.frame(1.25, 0)["paddles"][1], 200 + PADDLE_SPEED * 0.25)

    def test_opponent_paddle_glides_to_a_late_key_change(self):
        moving = [{"y": 300, "dir": 0, "t": 0}, {"y": 300, "dir": 1, "t": 0.0}]
        r = rally("clara", snapshot(paddles=moving))
        before = r.frame(0.1, 0)["paddles"][1]  # 340: martin still looks like he's moving
        stopped = [moving[0], {"y": 320, "dir": 0, "t": 0.05}]  # but he stopped at 0.05; we hear it now
        r.receive(snapshot(paddles=stopped), 0.1)
        self.assertAlmostEqual(r.frame(0.1, 0)["paddles"][1], before, msg="no jump when the news arrives")
        self.assertAlmostEqual(r.frame(1.0, 0)["paddles"][1], 320, delta=0.1)

    def test_opponent_paddle_far_off_jumps(self):
        r = rally("clara", snapshot())
        far = [{"y": HEIGHT / 2, "dir": 0, "t": 0}, {"y": HEIGHT / 2 + PADDLE_H + 10, "dir": 0, "t": 0}]
        r.receive(snapshot(paddles=far), 0.1)
        self.assertEqual(r.frame(0.1, 0)["paddles"][1], HEIGHT / 2 + PADDLE_H + 10)

    def test_ball_follows_its_segment(self):
        seg = {"id": 3, "p": [400, 300], "v": [100, -50], "t": 2.0}
        r = rally("clara", snapshot(seg), t=2.0)
        x, y = r.frame(2.5, 0)["ball"]
        self.assertAlmostEqual(x, 450)
        self.assertAlmostEqual(y, 275)

    def test_serve_waits_until_its_start_time(self):
        seg = {"id": 3, "p": [400, 300], "v": [300, 0], "t": 5.0}
        r = rally("clara", snapshot(seg), t=4.9)
        self.assertEqual(r.frame(4.95, 0)["ball"], [400, 300])

    def test_defender_hits_on_the_spot(self):
        r = rally("clara", snapshot(toward_left(y=300)))
        state = r.frame(0.21, 0)
        self.assertEqual(r.outbox, [{"hit": {"id": 5, "paddle": HEIGHT / 2}}])
        self.assertGreater(state["vel"][0], 0, "already on its way back")
        self.assertEqual(state["hits"], 1, "counted before the server confirms, so the meow isn't late")

    def test_own_paddle_is_judged_at_the_moment_of_contact(self):
        r = rally("clara", snapshot(toward_left(y=300)))  # the ball reaches the paddle at time 0.2
        r.frame(0.19, 1)  # start moving down just before
        r.frame(0.4, 1)  # a stalled frame: by now the paddle is 84 units lower, out of reach
        self.assertEqual(len(r.outbox), 2)
        call = r.outbox[1]
        self.assertIn("hit", call, "at contact it was only 4 units lower")
        self.assertAlmostEqual(call["hit"]["paddle"], 300 + PADDLE_SPEED * 0.01)

    def test_a_key_pressed_after_contact_does_not_count(self):
        r = rally("clara", snapshot(toward_left(y=300)))
        r.frame(0.35, -1)  # the frame that notices the contact also has a new key press
        self.assertEqual(r.outbox[0], {"hit": {"id": 5, "paddle": HEIGHT / 2}})

    def test_defender_misses_and_the_ball_flies_on(self):
        r = rally("clara", snapshot(toward_left(y=300), paddles=[{"y": 300 + PADDLE_H, "dir": 0, "t": 0}] * 2))
        r.mine = {"y": 300 + PADDLE_H, "dir": 0, "t": 0.0}
        state = r.frame(0.21, 0)
        self.assertEqual(r.outbox, [{"miss": {"id": 5}}])
        self.assertLess(state["vel"][0], 0)

    def test_decides_only_once(self):
        r = rally("clara", snapshot(toward_left()))
        r.frame(0.21, 0)
        r.outbox.clear()
        r.frame(0.25, 0)
        r.frame(0.3, 0)
        self.assertEqual(r.outbox, [])

    def test_opponent_call_is_predicted_not_sent(self):
        r = rally("martin", snapshot(toward_left(y=300)))
        state = r.frame(0.21, 0)
        self.assertEqual(r.outbox, [])
        self.assertGreater(state["vel"][0], 0, "predicted from where clara's paddle is")

    def test_confirmed_call_does_not_move_the_ball(self):
        r = rally("clara", snapshot(toward_left()))
        before = r.frame(0.25, 0)["ball"]
        own = r.seg
        r.receive(snapshot({**own}, hits=1), 0.25)  # the server works out the same segment
        state = r.frame(0.25, 0)
        for a, b in zip(state["ball"], before):
            self.assertAlmostEqual(a, b)
        self.assertEqual(state["hits"], 1)

    def test_an_older_snapshot_does_not_undo_our_call(self):
        r = rally("clara", snapshot(toward_left()))
        r.frame(0.21, 0)
        r.receive(snapshot(toward_left()), 0.22)  # sent before the server had our hit
        self.assertGreater(r.frame(0.22, 0)["vel"][0], 0)

    def test_small_correction_glides(self):
        r = rally("martin", snapshot(toward_left(y=300)))
        r.frame(0.21, 0)
        predicted = r.seg
        actual = {**predicted, "v": [predicted["v"][0], 60.0]}  # clara's paddle was a little off
        before = r.frame(0.3, 0)["ball"]
        r.receive(snapshot(actual, hits=1), 0.3)
        shown = r.frame(0.3, 0)["ball"]
        for a, b in zip(shown, before):
            self.assertAlmostEqual(a, b, places=6, msg="no jump at the moment of correction")
        later = r.frame(1.0, 0)["ball"]
        self.assertAlmostEqual(later[1], client.path(actual, 1.0)[0][1], delta=1, msg="but on the right path soon")

    def test_goal_jumps_instead_of_gliding(self):
        r = rally("martin", snapshot(toward_left(y=300)))
        r.frame(0.21, 0)  # predicted a hit
        serve = {"id": 6, "p": [WIDTH / 2, HEIGHT / 2], "v": [0, 0], "t": 0.4}
        r.receive(snapshot(serve, phase="serve", score=(0, 1)), 0.4)  # but clara missed
        self.assertEqual(r.frame(0.4, 0)["ball"], [WIDTH / 2, HEIGHT / 2])
        self.assertGreater(math.dist([LEFT_FACE + R, 300], [WIDTH / 2, HEIGHT / 2]), GLIDE_MAX)


class TestContactLog(unittest.TestCase):
    def test_our_hit_confirmed(self):
        r = rally("clara", snapshot(toward_left(y=300)))
        r.frame(0.21, 0)
        r.receive(snapshot({**r.seg}, hits=1), 0.31, arrived=0.3)
        (kind, t, seg, side, mine, call, late_ms, answer_ms, outcome, agreed, correction), = r.events
        self.assertEqual((kind, seg, side, mine, call, outcome, agreed), ("contacts", 5, 0, 1, "hit", "hit", 1))
        self.assertAlmostEqual(late_ms, 10)  # contact at 0.2, noticed in the frame at 0.21
        self.assertAlmostEqual(answer_ms, 100)
        self.assertAlmostEqual(correction, 0)

    def test_wrong_prediction(self):
        r = rally("martin", snapshot(toward_left(y=300)))
        r.frame(0.21, 0)  # predicts clara hits
        serve = {"id": 6, "p": [WIDTH / 2, HEIGHT / 2], "v": [0, 0], "t": 0.3}
        r.receive(snapshot(serve, phase="serve", score=(0, 1)), 0.3)  # she missed
        event = r.events[0]
        self.assertEqual(event[4:6] + event[8:10], (0, "hit", "goal", 0))
        self.assertIsNone(event[10], "after a goal the ball is reset, so there is no correction to measure")


class TestRecorder(unittest.TestCase):
    def test_files_have_headers_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = telemetry.Recorder(directory, "clara")
            rec.row("contacts", 12.5, 5, 0, 1, "hit", 10.0, 100.0, "hit", 1, 0.0)
            rec.close()
            with open(f"{rec.prefix}-contacts.csv") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], telemetry.COLUMNS["contacts"])
            self.assertEqual(rows[1][1:], ["12.500", "5", "0", "1", "hit", "10.000", "100.000", "hit", "1", "0.000"])
            for kind in telemetry.COLUMNS:
                with open(f"{rec.prefix}-{kind}.csv") as f:
                    self.assertEqual(next(csv.reader(f)), telemetry.COLUMNS[kind])


class TestMeow(unittest.TestCase):
    def test_synthetic_meow_is_a_short_wav(self):
        import wave
        with wave.open(client.synth_meow()) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (1, 2))
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 0.45, places=2)


if __name__ == "__main__":
    unittest.main()
