"""Unit tests for the server's match flow (no sockets):  python -m unittest"""
import contextlib
import io
import unittest

from game import HEIGHT, WIDTH
from server import TIMEOUT, Match

CENTRE = [WIDTH / 2, HEIGHT / 2]


def two_players():
    match = Match()
    match.on_input("clara", {"dir": 0}, 0)
    match.on_input("martin", {"dir": 0}, 0)
    return match


def both_ready(match, now=0):
    for name in ("clara", "martin"):
        match.on_input(name, {"dir": 0, "ready": True}, now)
    match.tick(now)


def goal_for_right(match):
    """Put the ball just past the left goal line, well clear of the left paddle."""
    match.state = {**match.state, "ball": [1, 50], "vel": [-1200, 0], "paddles": [HEIGHT - 50, HEIGHT / 2]}
    match.tick(0)


class TestMatch(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())  # the server prints joins and scores
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_first_two_names_play_and_a_third_watches(self):
        match = Match()
        match.on_input("clara", {}, 0)
        self.assertEqual(match.phase, "waiting")
        match.on_input("martin", {}, 0)
        match.on_input("guest", {"ready": True}, 0)
        self.assertEqual(match.players, ["clara", "martin"])
        self.assertEqual(match.phase, "serve")

    def test_ball_waits_but_paddles_move_until_both_are_ready(self):
        match = two_players()
        match.on_input("clara", {"dir": 1, "ready": True}, 0)
        for _ in range(10):
            match.tick(0)
        self.assertEqual(match.phase, "serve")
        self.assertEqual((match.state["ball"], match.state["vel"]), (CENTRE, [0, 0]))
        self.assertGreater(match.state["paddles"][0], HEIGHT / 2)
        self.assertEqual(match.message()["ready"], [True, False])

        both_ready(match)
        self.assertEqual(match.phase, "playing")
        self.assertNotEqual(match.state["vel"], [0, 0])
        self.assertEqual(match.message()["ready"], [False, False])

    def test_space_during_play_does_not_count_for_the_next_serve(self):
        match = two_players()
        both_ready(match)
        match.on_input("clara", {"dir": 0, "ready": True}, 0)
        goal_for_right(match)
        self.assertEqual(match.phase, "serve")
        self.assertEqual(match.message()["ready"], [False, False])

    def test_after_a_goal_both_must_press_space_again(self):
        match = two_players()
        both_ready(match)
        goal_for_right(match)
        self.assertEqual(match.state["score"], [0, 1])
        self.assertEqual(match.phase, "serve")
        self.assertEqual((match.state["ball"], match.state["vel"]), (CENTRE, [0, 0]))
        both_ready(match)
        self.assertLess(match.state["vel"][0], 0)  # served toward the left player, who conceded

    def test_rematch_needs_both_and_starts_from_zero(self):
        match = two_players()
        both_ready(match)
        match.state = {**match.state, "score": [0, 10]}
        goal_for_right(match)
        self.assertEqual(match.phase, "over")
        match.on_input("clara", {"ready": True}, 0)
        match.tick(0)
        self.assertEqual(match.phase, "over")
        both_ready(match)
        self.assertEqual(match.phase, "playing")
        self.assertEqual(match.state["score"], [0, 0])

    def test_silent_player_is_dropped_and_the_match_resets(self):
        match = two_players()
        both_ready(match)
        goal_for_right(match)
        match.on_input("clara", {}, TIMEOUT)
        match.tick(TIMEOUT)  # exactly TIMEOUT of silence is still fine
        self.assertEqual(match.players, ["clara", "martin"])
        match.on_input("clara", {}, TIMEOUT + 0.5)
        match.tick(TIMEOUT + 0.5)
        self.assertEqual(match.players, ["clara"])
        self.assertEqual((match.phase, match.state["score"]), ("waiting", [0, 0]))

        match.on_input("martin", {}, TIMEOUT + 0.6)
        self.assertEqual(match.phase, "serve")

    def test_bad_inputs_are_ignored(self):
        match = two_players()
        match.on_input("clara", "not a dict", 0)
        match.on_input("martin", {"dir": 99, "ready": "yes"}, 0)
        self.assertEqual(match.dirs, {"clara": 0, "martin": 1})
        self.assertEqual(match.message()["ready"], [False, False])


if __name__ == "__main__":
    unittest.main()
