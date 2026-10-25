"""Unit tests for the client's smoothing between server states:  python -m unittest"""
import unittest

import client
import game
from client import CORRECTION, MAX_AHEAD, SETTLE, SNAP, ball_ahead, predict_paddle
from game import BALL_SIZE, PADDLE_H, PADDLE_SPEED

R = BALL_SIZE / 2


def state_with(**fields):
    return {**game.new_state(), "players": ["clara", "martin"], **fields}


class TestPredictPaddle(unittest.TestCase):
    def test_moves_with_input_before_the_server_does(self):
        self.assertEqual(predict_paddle(300, 300, -1, 0, 0.1), 300 - PADDLE_SPEED * 0.1)

    def test_stays_inside_the_field(self):
        self.assertEqual(predict_paddle(60, 60, -1, 0, 1), PADDLE_H / 2)

    def test_keeps_its_lead_while_moving(self):
        # the server lags behind while the key is held; that must not pull the paddle back
        self.assertEqual(predict_paddle(300, 310, -1, 0, 0.01), 300 - PADDLE_SPEED * 0.01)

    def test_keeps_its_lead_just_after_letting_go(self):
        self.assertEqual(predict_paddle(300, 310, 0, SETTLE / 2, 0.01), 300)

    def test_eases_back_once_settled(self):
        y = predict_paddle(300, 310, 0, SETTLE, 0.01)
        self.assertAlmostEqual(y, 300 + 10 * CORRECTION * 0.01)

    def test_converges_to_the_server(self):
        y = 300
        for _ in range(120):
            y = predict_paddle(y, 320, 0, SETTLE, 1 / 120)
        self.assertAlmostEqual(y, 320, places=2)

    def test_snaps_when_far_off(self):
        self.assertEqual(predict_paddle(300, 300 + SNAP + 1, 0, 0, 0.01), 300 + SNAP + 1)


class TestBallAhead(unittest.TestCase):
    def test_no_age_means_the_server_position(self):
        state = state_with(ball=[400, 300], vel=[200, 100])
        self.assertEqual(ball_ahead(state, 0), [400, 300])

    def test_moves_along_its_velocity(self):
        x, y = ball_ahead(state_with(ball=[400, 300], vel=[200, 100]), 0.05)
        self.assertAlmostEqual(x, 410)
        self.assertAlmostEqual(y, 305)

    def test_bounces_off_the_wall(self):
        _, y = ball_ahead(state_with(ball=[400, R + 1], vel=[0, -200]), 0.05)
        self.assertGreaterEqual(y, R)

    def test_is_capped(self):
        x, _ = ball_ahead(state_with(ball=[400, 300], vel=[100, 0]), 10)
        self.assertAlmostEqual(x, 400 + 100 * MAX_AHEAD)

    def test_never_runs_past_a_goal(self):
        state = state_with(ball=[5, 300], vel=[-300, 0], paddles=[100, 300])
        self.assertEqual(ball_ahead(state, 0.05), [5, 300])

    def test_does_not_modify_the_state(self):
        state = state_with(ball=[400, 300], vel=[200, 100])
        ball_ahead(state, 0.05)
        self.assertEqual(state["ball"], [400, 300])


class TestMeow(unittest.TestCase):
    def test_synthetic_meow_is_a_short_wav(self):
        import wave
        with wave.open(client.synth_meow()) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (1, 2))
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 0.45, places=2)


if __name__ == "__main__":
    unittest.main()
