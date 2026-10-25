"""Unit tests for how effects.py reads events from consecutive states:  python -m unittest"""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import client  # noqa: E402
import effects  # noqa: E402
import game  # noqa: E402


def state_with(**fields):
    return {**game.new_state(), "players": ["clara", "martin"], **fields}


class TestEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.screen, cls.view = client.open_window("test")
        cls.fx = effects.FX(cls.view)

    def setUp(self):
        self.fx.reset()

    def feed(self, *states):
        for state in states:
            self.fx._events(state)

    def test_hit_by_left_paddle(self):
        self.feed(state_with(ball=[60, 300], vel=[-300, 0], hits=0), state_with(ball=[52, 300], vel=[315, 0], hits=1))
        self.assertGreater(self.fx.paddle_hit[0], 0)
        self.assertEqual(self.fx.paddle_hit[1], 0)
        self.assertEqual(len(self.fx.texts), 1)
        self.assertTrue(self.fx.particles)

    def test_hit_by_right_paddle(self):
        self.feed(state_with(ball=[740, 300], vel=[300, 0], hits=4), state_with(ball=[748, 300], vel=[-315, 0], hits=5))
        self.assertGreater(self.fx.paddle_hit[1], 0)

    def test_goal_shakes_and_pops_the_scorer(self):
        self.feed(state_with(ball=[2, 300], score=[3, 4]), state_with(ball=[400, 300], score=[3, 5]))
        self.assertGreater(self.fx.shake, 0)
        self.assertGreater(self.fx.score_pop[1], 0)
        self.assertEqual(self.fx.score_pop[0], 0)

    def test_new_match_is_not_a_goal(self):
        self.feed(state_with(score=[11, 7]), state_with(score=[0, 0]))
        self.assertEqual(self.fx.shake, 0)
        self.assertFalse(self.fx.particles)

    def test_wall_bounce_sparks(self):
        self.feed(state_with(ball=[300, 10], vel=[200, -150]), state_with(ball=[302, 8], vel=[200, 150]))
        self.assertTrue(self.fx.particles)
        self.assertEqual(self.fx.paddle_hit, [0, 0])

    def test_nothing_happens_in_free_flight(self):
        self.feed(state_with(ball=[300, 300], vel=[200, 100]), state_with(ball=[302, 301], vel=[200, 100]))
        self.assertFalse(self.fx.particles)
        self.assertEqual(self.fx.shake, 0)

    def test_draws_every_screen(self):
        state = state_with(ball=[400, 300])
        self.fx.start_screen(self.screen, "status", "hint")
        self.fx.draw(self.screen, state, me="clara")
        over = state_with(score=[11, 3], vel=[0, 0])
        self.fx.draw(self.screen, over)
        self.fx.end_overlay(self.screen, over, over["players"], "hint")
        self.assertEqual(self.view.offset, (0, 0), "shake must not leak into later drawing")

    @classmethod
    def tearDownClass(cls):
        pygame.quit()


if __name__ == "__main__":
    unittest.main()
