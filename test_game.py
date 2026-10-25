"""Unit tests for game.py:  python -m unittest"""
import copy
import math
import random
import unittest

import game
from game import HEIGHT, PADDLE_H, PADDLE_SPEED, PADDLE_W, PADDLE_X, R, WIDTH, step

LEFT_FACE = PADDLE_X[0] + PADDLE_W / 2
RIGHT_FACE = PADDLE_X[1] - PADDLE_W / 2


def state_with(**fields):
    return {**game.new_state(), **fields}


def speed(state):
    return math.hypot(*state["vel"])


class TestNewState(unittest.TestCase):
    def test_fields_match_the_state_message(self):
        state = game.new_state()
        self.assertEqual(set(state), {"ball", "vel", "paddles", "score", "hits"})
        self.assertEqual(state["ball"], [WIDTH / 2, HEIGHT / 2])
        self.assertEqual(state["paddles"], [HEIGHT / 2, HEIGHT / 2])
        self.assertEqual((state["score"], state["hits"]), ([0, 0], 0))

    def test_serve_speed_and_angle(self):
        for _ in range(200):
            vx, vy = game.new_state()["vel"]
            self.assertAlmostEqual(math.hypot(vx, vy), game.BALL_SPEED)
            self.assertLessEqual(abs(math.atan2(vy, abs(vx))), game.MAX_SERVE_ANGLE + 1e-9)

    def test_serve_goes_both_ways(self):
        self.assertEqual({math.copysign(1, game.new_state()["vel"][0]) for _ in range(100)}, {-1, 1})


class TestPaddles(unittest.TestCase):
    def test_move_by_direction_and_speed(self):
        state = step(state_with(paddles=[300, 300]), -1, 1, 0.1)
        self.assertEqual(state["paddles"], [300 - PADDLE_SPEED * 0.1, 300 + PADDLE_SPEED * 0.1])

    def test_still_when_dir_is_zero(self):
        self.assertEqual(step(state_with(paddles=[200, 400]), 0, 0, 0.1)["paddles"], [200, 400])

    def test_stop_at_the_field_edges(self):
        state = step(state_with(paddles=[60, 540]), -1, 1, 1)
        self.assertEqual(state["paddles"], [PADDLE_H / 2, HEIGHT - PADDLE_H / 2])


class TestWalls(unittest.TestCase):
    def test_bounce_off_top(self):
        state = step(state_with(ball=[400, R + 1], vel=[0, -100]), 0, 0, 0.1)
        self.assertGreater(state["vel"][1], 0)
        self.assertGreaterEqual(state["ball"][1], R)

    def test_bounce_off_bottom(self):
        state = step(state_with(ball=[400, HEIGHT - R - 1], vel=[0, 100]), 0, 0, 0.1)
        self.assertLess(state["vel"][1], 0)
        self.assertLessEqual(state["ball"][1], HEIGHT - R)

    def test_walls_do_not_count_as_hits(self):
        self.assertEqual(step(state_with(ball=[400, R + 1], vel=[0, -100]), 0, 0, 0.1)["hits"], 0)


class TestPaddleHits(unittest.TestCase):
    def test_left_paddle_returns_the_ball(self):
        before = state_with(ball=[LEFT_FACE + R + 2, 300], vel=[-300, 0], paddles=[300, 300])
        after = step(before, 0, 0, 0.1)
        self.assertGreater(after["vel"][0], 0)
        self.assertEqual(after["hits"], 1)
        self.assertAlmostEqual(speed(after), 300 * game.SPEEDUP)

    def test_right_paddle_returns_the_ball(self):
        before = state_with(ball=[RIGHT_FACE - R - 2, 300], vel=[300, 0], paddles=[300, 300])
        after = step(before, 0, 0, 0.1)
        self.assertLess(after["vel"][0], 0)
        self.assertEqual(after["hits"], 1)

    def test_centre_hit_goes_straight_back(self):
        after = step(state_with(ball=[LEFT_FACE + R + 2, 300], vel=[-300, 0], paddles=[300, 300]), 0, 0, 0.1)
        self.assertAlmostEqual(after["vel"][1], 0)

    def test_hit_near_the_top_sends_the_ball_up(self):
        after = step(state_with(ball=[LEFT_FACE + R + 2, 260], vel=[-300, 0], paddles=[300, 300]), 0, 0, 0.1)
        self.assertLess(after["vel"][1], 0)

    def test_speed_is_capped(self):
        v = game.MAX_SPEED
        after = step(state_with(ball=[LEFT_FACE + R + 2, 300], vel=[-v, 0], paddles=[300, 300]), 0, 0, 0.01)
        self.assertAlmostEqual(speed(after), game.MAX_SPEED)

    def test_fast_ball_does_not_tunnel_through_paddle(self):
        # moves 300 units in one step, far more than the paddle is wide
        before = state_with(ball=[LEFT_FACE + R + 100, 300], vel=[-3000, 0], paddles=[300, 300])
        self.assertEqual(step(before, 0, 0, 0.1)["hits"], 1)

    def test_ball_passes_when_paddle_is_elsewhere(self):
        after = step(state_with(ball=[LEFT_FACE + R + 2, 300], vel=[-300, 0], paddles=[100, 300]), 0, 0, 0.1)
        self.assertEqual(after["hits"], 0)
        self.assertLess(after["vel"][0], 0)


class TestScoring(unittest.TestCase):
    def test_right_scores_when_ball_leaves_left_side(self):
        after = step(state_with(ball=[5, 300], vel=[-300, 0], paddles=[100, 300]), 0, 0, 0.1)
        self.assertEqual(after["score"], [0, 1])
        self.assertEqual(after["ball"], [WIDTH / 2, HEIGHT / 2])
        self.assertLess(after["vel"][0], 0, "serve goes to the player who conceded")

    def test_left_scores_when_ball_leaves_right_side(self):
        after = step(state_with(ball=[WIDTH - 5, 300], vel=[300, 0], paddles=[300, 100]), 0, 0, 0.1)
        self.assertEqual(after["score"], [1, 0])
        self.assertGreater(after["vel"][0], 0)

    def test_serve_resets_speed(self):
        after = step(state_with(ball=[5, 300], vel=[-800, 0], paddles=[100, 300]), 0, 0, 0.1)
        self.assertAlmostEqual(speed(after), game.BALL_SPEED)

    def test_match_ends_at_eleven(self):
        after = step(state_with(ball=[5, 300], vel=[-300, 0], paddles=[100, 300], score=[3, 10]), 0, 0, 0.1)
        self.assertEqual(after["score"], [3, 11])
        self.assertEqual(game.winner(after), 1)
        self.assertEqual(after["vel"], [0, 0])

    def test_nothing_moves_after_the_match(self):
        over = state_with(score=[11, 4])
        self.assertIs(step(over, 1, 1, 1), over)

    def test_no_winner_before_eleven(self):
        self.assertIsNone(game.winner(state_with(score=[10, 10])))


class TestStepContract(unittest.TestCase):
    def test_does_not_modify_its_input(self):
        before = state_with(ball=[LEFT_FACE + R + 2, 300], vel=[-300, 0])
        snapshot = copy.deepcopy(before)
        step(before, 1, -1, 0.1)
        self.assertEqual(before, snapshot)

    def test_two_half_steps_equal_one_step_in_free_flight(self):
        start = state_with(ball=[400, 300], vel=[200, 100])
        whole = step(start, 0, 0, 0.1)
        halves = step(step(start, 0, 0, 0.05), 0, 0, 0.05)
        for a, b in zip(whole["ball"], halves["ball"]):
            self.assertAlmostEqual(a, b)

    def test_long_match_keeps_ball_in_field(self):
        random.seed(1)
        state = game.new_state()
        for i in range(60 * 600):  # up to 10 minutes at 60 Hz, paddles chase the ball imperfectly
            dirs = [0 if abs(state["ball"][1] - y) < 30 else (1 if state["ball"][1] > y else -1)
                    for y in state["paddles"]]
            dirs[i // 300 % 2] = 0  # each side freezes in turn, so points get scored
            state = step(state, *dirs, 1 / 60)
            x, y = state["ball"]
            self.assertTrue(-R <= x <= WIDTH + R and R <= y <= HEIGHT - R, state)
            if game.winner(state) is not None:
                break
        self.assertIsNotNone(game.winner(state))
        self.assertGreater(state["hits"], 0)


if __name__ == "__main__":
    unittest.main()
