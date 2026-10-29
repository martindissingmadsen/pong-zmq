"""Unit tests for the bot's paddle decision:  python -m unittest"""
import unittest

from bot import DEADBAND, choose_dir
from game import HEIGHT


def state(ball, vel, left_paddle):
    return {"ball": ball, "vel": vel, "paddles": [left_paddle, HEIGHT / 2]}


class TestChooseDir(unittest.TestCase):
    def test_moves_to_where_the_ball_will_meet_its_paddle(self):
        incoming = ([400, 300], [-300, 0])
        self.assertEqual(choose_dir(state(*incoming, 100), 0, 0), 1)
        self.assertEqual(choose_dir(state(*incoming, 500), 0, 0), -1)

    def test_stops_once_close_enough(self):
        self.assertEqual(choose_dir(state([400, 300], [-300, 0], 300 - DEADBAND), 0, 0), 0)

    def test_aims_off_by_the_given_amount(self):
        self.assertEqual(choose_dir(state([400, 300], [-300, 0], 300), 0, 50), 1)

    def test_returns_to_the_centre_while_the_ball_is_away_or_waiting(self):
        self.assertEqual(choose_dir(state([400, 300], [300, 0], 100), 0, 0), 1)
        self.assertEqual(choose_dir(state([400, 300], [0, 0], 500), 0, 0), -1)


if __name__ == "__main__":
    unittest.main()
