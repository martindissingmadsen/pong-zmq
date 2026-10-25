"""Authoritative Pong server (Phase 2, see PLAN.md).

    python server.py

Runs game.py in a fixed-timestep 120 Hz loop. Each tick it applies the latest input
from each player and publishes the state on PUB :5555. Inputs arrive on ROUTER :5556,
where the first two client identities become the left and right player. The ball
waits in the centre until both have joined.
"""
import time

import zmq

import game

TICK_HZ = 120
DT = 1 / TICK_HZ
MAX_LAG = 0.25  # if the loop falls this far behind (e.g. the laptop slept), skip ahead instead of catching up


def serve():
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind("tcp://*:5555")
    router = ctx.socket(zmq.ROUTER)
    router.bind("tcp://*:5556")
    try:
        run(pub, router)
    finally:
        ctx.destroy(linger=0)  # don't wait to deliver queued states on exit


def run(pub, router):
    players, dirs, seq = [], {}, 0
    state = game.new_state()
    print(f"serving at {TICK_HZ} Hz on ports 5555 (state) and 5556 (inputs)")
    next_tick = time.perf_counter()
    while True:
        while router.poll(0):
            frames = router.recv_multipart()
            try:
                ident, raw = frames
                name, msg = ident.decode(), zmq.utils.jsonapi.loads(raw)
            except ValueError:  # wrong frame count, bad UTF-8 or bad JSON: drop it
                continue
            if name not in players and len(players) < 2:
                players.append(name)
                print(f"joined: {name} ({'left' if len(players) == 1 else 'right'})")
            if name in players:
                dirs[name] = clamp_dir(msg.get("dir", 0) if isinstance(msg, dict) else 0)

        if len(players) == 2:
            before = state["score"]
            state = game.step(state, dirs.get(players[0], 0), dirs.get(players[1], 0), DT)
            if state["score"] != before:
                print(f"score {state['score'][0]}-{state['score'][1]}")
                if (side := game.winner(state)) is not None:
                    print(f"{players[side]} wins")

        seq += 1
        pub.send_json({"seq": seq, "ts": time.time(), "players": players, **state})

        next_tick += DT
        delay = next_tick - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        elif delay < -MAX_LAG:
            next_tick = time.perf_counter()


def clamp_dir(d):
    """Inputs come from the network, so only -1, 0 and 1 get through to game.step."""
    return (d > 0) - (d < 0) if isinstance(d, (int, float)) else 0


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        pass
