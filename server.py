"""Authoritative Pong server (see PLAN.md).

    python server.py

Runs game.py in a fixed-timestep 120 Hz loop. Each tick it applies the latest input
from each player and publishes the state on PUB :5555. Inputs arrive on ROUTER :5556,
where the first two client identities become the left and right player.

Every ball waits in the centre until both players have pressed Space: the first serve,
the serve after each point and a rematch. Inputs double as heartbeats, so a player
who sends nothing for TIMEOUT seconds is dropped and the match starts over.
"""
import time

import zmq

import game

TICK_HZ = 120
DT = 1 / TICK_HZ
MAX_LAG = 0.25  # if the loop falls this far behind (e.g. the laptop slept), skip ahead instead of catching up
TIMEOUT = 1.0  # s without any input before a player counts as gone; clients send one every frame


class Match:
    """Who is playing and which phase the match is in. No sockets, so it can be unit tested."""

    def __init__(self):
        self.players, self.dirs, self.seen, self.ready = [], {}, {}, set()
        self.reset()

    def reset(self):
        self.state = game.new_state()
        self.hold()

    def hold(self):
        """Keep the ball in the centre until both players are ready; it then leaves with serve_vel."""
        self.serve_vel, self.state = self.state["vel"], {**self.state, "vel": [0, 0]}
        self.ready.clear()

    @property
    def phase(self):
        if len(self.players) < 2:
            return "waiting"
        if game.winner(self.state) is not None:
            return "over"
        return "serve" if self.serve_vel is not None else "playing"

    def on_input(self, name, msg, now):
        if name not in self.players:
            if len(self.players) == 2:
                return  # a third client only watches
            self.players.append(name)
            print(f"joined: {name} ({'left' if len(self.players) == 1 else 'right'})")
        if not isinstance(msg, dict):
            msg = {}
        self.seen[name] = now
        self.dirs[name] = clamp_dir(msg.get("dir", 0))
        if msg.get("ready") is True and self.phase in ("serve", "over"):
            self.ready.add(name)

    def tick(self, now):
        for name in [p for p in self.players if now - self.seen[p] > TIMEOUT]:
            self.players.remove(name)
            del self.dirs[name], self.seen[name]
            print(f"left: {name} (no input for {TIMEOUT:g} s)")
            self.reset()  # a fresh match once two players are in again

        phase = self.phase
        if phase in ("serve", "over") and self.ready.issuperset(self.players):
            if phase == "over":
                self.reset()  # rematch: the Space presses that started it also serve the first ball
            self.state = {**self.state, "vel": self.serve_vel}
            self.serve_vel, phase = None, "playing"
            self.ready.clear()

        if phase not in ("serve", "playing"):
            return
        moved = game.step(self.state, *(self.dirs[p] for p in self.players), DT)
        if phase == "serve":
            self.state = {**self.state, "paddles": moved["paddles"]}  # paddles move, the ball waits
            return
        before, self.state = self.state["score"], moved
        if self.state["score"] != before:
            print(f"score {self.state['score'][0]}-{self.state['score'][1]}")
            if (side := game.winner(self.state)) is not None:
                print(f"{self.players[side]} wins")
            else:
                self.hold()  # game.step already put the ball in the centre, aimed at the player who conceded

    def message(self):
        """The game fields of the state message; the loop adds seq and ts."""
        return {"players": self.players, "phase": self.phase,
                "ready": [p in self.ready for p in self.players], **self.state}


def serve():
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind("tcp://*:5555")
    router = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.ROUTER_HANDOVER, 1)  # a client reconnecting under the same name takes over its old slot
    router.bind("tcp://*:5556")
    try:
        run(pub, router)
    finally:
        ctx.destroy(linger=0)  # don't wait to deliver queued states on exit


def run(pub, router):
    match, seq = Match(), 0
    print(f"serving at {TICK_HZ} Hz on ports 5555 (state) and 5556 (inputs)")
    next_tick = time.perf_counter()
    while True:
        now = time.perf_counter()
        while router.poll(0):
            frames = router.recv_multipart()
            try:
                ident, raw = frames
                name, msg = ident.decode(), zmq.utils.jsonapi.loads(raw)
            except ValueError:  # wrong frame count, bad UTF-8 or bad JSON: drop it
                continue
            match.on_input(name, msg, now)
        match.tick(now)

        seq += 1
        pub.send_json({"seq": seq, "ts": time.time(), **match.message()})

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
