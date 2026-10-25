"""Headless bot: plays one networked match without a window, for experiments (see PLAN.md).

    python bot.py NAME [HOST] [--miss SIGMA]

Built on client.Link, so it speaks exactly the protocol a real client does: clock sync,
key changes, its own hit or miss calls. It moves its paddle to where the ball will meet it,
but aims off by a random amount per shot (normally distributed, SIGMA units, default 40),
so it misses sometimes and a match ends. It presses Space whenever a ball waits and quits
once the match is over.

Two bots against server.py make a bot-vs-bot match:
    python server.py & python bot.py left & python bot.py right
"""
import random
import sys
import time

import game
from client import Link
from game import HEIGHT

FPS = 120  # decisions per second, like a client on a 120 Hz display
DEADBAND = 4  # units; this close to the target the paddle stops, so it doesn't twitch
READY_EVERY = 0.5  # s; how often to press Space again while a ball waits for us


def choose_dir(state, side, aim_off):
    """The key the bot holds: toward where the ball will meet its paddle (plus aim_off),
    or back to the centre while the ball isn't heading its way."""
    y = state["paddles"][side]
    meet = game.meet(state["ball"], state["vel"])
    target = meet[2][1] + aim_off if meet and meet[1] == side else HEIGHT / 2
    if abs(target - y) <= DEADBAND:
        return 0
    return 1 if target > y else -1


def play(name, host, sigma):
    link = Link(name, host)
    d, ready, aim, last_ready = 0, False, (None, 0.0), 0.0
    try:
        while True:
            state = link.update(d, ready)  # sends what we decided last frame
            ready, now = False, time.perf_counter()
            if state and name in state["players"]:
                side = state["players"].index(name)
                if state["phase"] == "over":
                    winner = state["players"][game.winner(state)]
                    print(f"{name}: match over, {winner} won {state['score'][0]}-{state['score'][1]}")
                    return
                if state["phase"] == "serve" and not state["ready"][side] and now - last_ready > READY_EVERY:
                    ready, last_ready = True, now
                seg = link.rally.seg["id"]
                if aim[0] != seg:  # a new shot: pick how far off this one is aimed
                    aim = (seg, random.gauss(0, sigma))
                d = choose_dir(state, side, aim[1])
            time.sleep(1 / FPS)
    finally:
        link.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    sigma = 40.0
    if "--miss" in args:
        i = args.index("--miss")
        sigma = float(args[i + 1])
        del args[i:i + 2]
    if len(args) not in (1, 2):
        print(__doc__)
        sys.exit(1)
    try:
        play(args[0], args[1] if len(args) == 2 else "localhost", sigma)
    except KeyboardInterrupt:
        pass
