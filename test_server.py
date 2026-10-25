"""Unit tests for the server's match flow and hit/miss calls (no sockets):  python -m unittest"""
import contextlib
import io
import unittest

import game
from game import HEIGHT, PADDLE_H, R, WIDTH
from server import SERVE_DELAY, TIMEOUT, Match

CENTRE = [WIDTH / 2, HEIGHT / 2]
LEFT_FACE = game.face_x(0)


def two_players():
    match = Match()
    match.on_message("clara", {"ping": 1.0}, 0)
    match.on_message("martin", {"ping": 1.0}, 0)
    return match


def both_ready(match, now=0):
    for name in ("clara", "martin"):
        match.on_message(name, {"ready": True}, now)
    match.tick(now)


def heading_left(match, y=300):
    """A rally in progress: at time 0 the ball flies straight at the left paddle, 60 units away."""
    both_ready(match)
    match.seg = {**match.seg, "p": [LEFT_FACE + R + 60, y], "v": [-300.0, 0.0], "t": 0.0}
    return match.seg["id"], 60 / 300


class TestMatch(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())  # the server prints joins and scores
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_first_two_names_play_and_a_third_watches(self):
        match = Match()
        match.on_message("clara", {}, 0)
        self.assertEqual(match.phase, "waiting")
        match.on_message("martin", {}, 0)
        reply = match.on_message("guest", {"ready": True, "ping": 7.5}, 2)
        self.assertEqual(match.players, ["clara", "martin"])
        self.assertEqual(match.phase, "serve")
        self.assertEqual(reply, {"pong": 7.5, "t": 2}, "a watcher still gets its clock")

    def test_ball_waits_but_paddles_move_until_both_are_ready(self):
        match = two_players()
        match.on_message("clara", {"paddle": {"y": 300, "dir": 1, "t": 0}, "ready": True}, 0)
        for _ in range(10):
            match.tick(0)
        self.assertEqual(match.phase, "serve")
        self.assertEqual((match.seg["p"], match.seg["v"]), (CENTRE, [0, 0]))
        self.assertEqual(match.message()["paddles"][0], {"y": 300, "dir": 1, "t": 0})
        self.assertEqual(match.message()["ready"], [True, False])

        both_ready(match, now=5)
        self.assertEqual(match.phase, "playing")
        self.assertNotEqual(match.seg["v"], [0, 0])
        self.assertEqual(match.seg["t"], 5 + SERVE_DELAY, "the serve leaves a moment later, so everyone hears it")
        self.assertEqual(match.message()["ready"], [False, False])

    def test_space_during_play_does_not_count_for_the_next_serve(self):
        match = two_players()
        both_ready(match)
        match.on_message("clara", {"ready": True}, 0)
        self.assertEqual(match.message()["ready"], [False, False])

    def test_hit_starts_the_next_segment_at_the_paddle(self):
        match = two_players()
        seg_id, contact = heading_left(match, y=300)
        match.on_message("clara", {"hit": {"id": seg_id, "paddle": 300}}, contact + 0.03)
        self.assertEqual(match.hits, 1)
        self.assertEqual(match.seg["id"], seg_id + 1)
        self.assertAlmostEqual(match.seg["t"], contact, msg="from the moment of contact, not when the call arrived")
        self.assertAlmostEqual(match.seg["p"][0], LEFT_FACE + R)
        self.assertGreater(match.seg["v"][0], 0)
        self.assertAlmostEqual(match.seg["v"][1], 0, msg="a centre hit goes straight back")

    def test_a_hit_out_of_reach_is_a_miss(self):
        match = two_players()
        seg_id, contact = heading_left(match, y=300)
        match.on_message("clara", {"hit": {"id": seg_id, "paddle": 300 + PADDLE_H}}, contact)
        self.assertEqual((match.hits, match.score), (0, [0, 1]))

    def test_miss_scores_and_serves_to_the_player_who_conceded(self):
        match = two_players()
        seg_id, contact = heading_left(match)
        match.on_message("clara", {"miss": {"id": seg_id}}, contact)
        self.assertEqual(match.score, [0, 1])
        self.assertEqual(match.phase, "serve")
        self.assertEqual((match.seg["p"], match.seg["v"]), (CENTRE, [0, 0]))
        self.assertLess(match.serve_vel[0], 0)

    def test_only_the_defender_calls_and_only_about_the_current_segment(self):
        match = two_players()
        seg_id, contact = heading_left(match)
        match.on_message("martin", {"miss": {"id": seg_id}}, contact)  # the ball isn't heading for martin
        match.on_message("clara", {"miss": {"id": seg_id - 1}}, contact)  # an old segment
        self.assertEqual((match.score, match.seg["id"]), ([0, 0], seg_id))

    def test_after_a_goal_both_must_press_space_again(self):
        match = two_players()
        seg_id, contact = heading_left(match)
        match.on_message("clara", {"miss": {"id": seg_id}}, contact)
        match.on_message("martin", {"ready": True}, 1)
        match.tick(1)
        self.assertEqual(match.phase, "serve")
        both_ready(match, now=2)
        self.assertEqual(match.phase, "playing")

    def test_rematch_needs_both_and_starts_from_zero(self):
        match = two_players()
        match.score = [4, 10]
        seg_id, contact = heading_left(match)
        match.on_message("clara", {"miss": {"id": seg_id}}, contact)
        self.assertEqual((match.phase, match.score), ("over", [4, 11]))
        self.assertEqual(match.seg["v"], [0, 0])

        now = contact + 0.1
        match.on_message("clara", {"ready": True}, now)
        match.tick(now)
        self.assertEqual(match.phase, "over")
        both_ready(match, now=now + 0.1)
        self.assertEqual((match.phase, match.score, match.hits), ("playing", [0, 0], 0))

    def test_silent_player_is_dropped_and_the_match_resets(self):
        match = two_players()
        match.score = [3, 2]
        match.on_message("martin", {"ping": 1.0}, TIMEOUT)
        match.tick(TIMEOUT + 0.1)
        self.assertEqual(match.players, ["martin"])
        self.assertEqual((match.phase, match.score), ("waiting", [0, 0]))
        match.on_message("clara", {}, TIMEOUT + 0.2)
        self.assertEqual(match.players, ["martin", "clara"])

    def test_bad_messages_are_ignored(self):
        match = two_players()
        seg_id, contact = heading_left(match)
        for msg in ([1, 2], "hi", {"ping": "x"}, {"ping": float("nan")}, {"paddle": {"y": "top"}},
                    {"paddle": 5}, {"hit": {"id": seg_id, "paddle": "x"}}, {"ready": "yes"}):
            self.assertIsNone(match.on_message("martin", msg, contact))
        self.assertEqual((match.score, match.hits, match.seg["id"]), ([0, 0], 0, seg_id))

    def test_paddle_changes_are_clamped(self):
        match = two_players()
        match.on_message("clara", {"paddle": {"y": -500, "dir": 9, "t": -100}}, 10)
        self.assertEqual(match.paddles["clara"], {"y": PADDLE_H / 2, "dir": 1, "t": 9.5})

    def test_snapshot_is_complete(self):
        self.assertEqual(set(two_players().message()),
                         {"players", "phase", "ready", "score", "hits", "seg", "paddles"})


if __name__ == "__main__":
    unittest.main()
