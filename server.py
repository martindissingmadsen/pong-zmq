"""Pong server: the referee between two clients (see PLAN.md).

    python server.py

The server doesn't simulate the ball tick by tick. The ball's flight between two paddle
contacts is a segment: where it started (p), its velocity (v) and when (t, server time).
Every machine computes the same path from it with game.fly. When the ball reaches a
paddle, the client defending that side decides hit or miss with its own paddle, which it
sees without any network delay, and sends that one decision. The server checks it, works
out the next segment (or the goal) and broadcasts a snapshot. Clients also send a message
when their up/down key changes, so the other side can work out where that paddle is.

So messages only flow when something happens: key changes, contacts, Space presses, plus
pings five times a second (clock sync, and the heartbeat that detects a lost player).

Every ball still waits in the centre until both players have pressed Space: the first
serve, the serve after each point and a rematch. Ports: PUB :5555 for snapshots, ROUTER
:5556 for client messages and ping replies.

Every run logs to logs/server-<date>-<time>.csv, one row per event (LOG_FIELDS); `t` is server
time. recv: a client message (its seq, its ts, which kinds it carried). snapshot: one sent (its
seq, ts and size in bytes). join, leave. hit and miss: the defender's call, with how many seconds
after the contact it arrived. over: the winner and the final score.
"""
import csv
import math
import os
import random
import time
from pathlib import Path

import zmq

import game
from game import HEIGHT, PADDLE_H, WIDTH

TIMEOUT = 1.0  # s without any message before a player counts as gone; clients ping 5 times a second
SERVE_DELAY = 0.15  # s between both pressing Space and the ball leaving, so every client hears it in time
SNAPSHOT_EVERY = 1.0  # s; also sent on every change, this only catches up late joiners
POLL_MS = 20  # how often the loop wakes up to check timeouts when nothing arrives
PADDLE_CLOCK_SLACK = 0.5  # s; a paddle change may say it happened at most this long ago
CENTRE = [WIDTH / 2, HEIGHT / 2]
LOG_DIR = Path(os.environ.get("PONG_LOG_DIR", Path(__file__).parent / "logs"))  # experiments point this elsewhere
LOG_FIELDS = ("t", "event", "player", "seq", "ts", "value")
KINDS = ("ping", "paddle", "hit", "miss", "ready")  # what a client message can carry


class Match:
    """Who is playing, the score and the ball's current segment. No sockets, so it can be unit tested."""

    def __init__(self, now=0.0, log=None):
        self.log = log or (lambda *row: None)  # called with one LOG_FIELDS row per event
        self.players, self.seen, self.ready = [], {}, set()
        self.paddles = {}  # name -> {"y", "dir", "t"}: position at server time t, then moving with dir
        self.seg_id = 0  # never reset, so clients can always tell a newer segment from an older one
        self.changed = True
        self.reset(now)

    def reset(self, now):
        self.score, self.hits = [0, 0], 0
        self.hold(random.choice((-1, 1)), now)

    def hold(self, toward, now):
        """Keep the ball in the centre until both players are ready; it then leaves toward `toward`."""
        self.serve_vel = game.serve({}, toward)["vel"]
        self.new_segment(CENTRE, [0, 0], now)
        self.ready.clear()

    def new_segment(self, p, v, t):
        self.seg_id += 1
        self.seg = {"id": self.seg_id, "p": list(p), "v": list(v), "t": t}
        self.changed = True

    @property
    def phase(self):
        if len(self.players) < 2:
            return "waiting"
        if game.winner({"score": self.score}) is not None:
            return "over"
        return "serve" if self.serve_vel is not None else "playing"

    def on_message(self, name, msg, now):
        """Handle one client message. Returns the reply for that client (a pong), if any."""
        if not isinstance(msg, dict):
            return None
        reply = {"pong": msg["ping"], "t": now} if number(msg.get("ping")) else None
        if name not in self.players:
            if len(self.players) == 2:
                return reply  # a third client only watches, but still needs the clock
            self.players.append(name)
            self.paddles[name] = {"y": HEIGHT / 2, "dir": 0, "t": now}
            self.changed = True
            side = "left" if len(self.players) == 1 else "right"
            print(f"joined: {name} ({side})")
            self.log(now, "join", name, "", "", side)
        self.seen[name] = now
        side = self.players.index(name)

        if isinstance(paddle := msg.get("paddle"), dict) and number(paddle.get("y")):
            t = paddle["t"] if number(paddle.get("t")) else now
            half = PADDLE_H / 2
            old, new = self.paddles[name], {"y": game.clamp(paddle["y"], half, HEIGHT - half),
                                            "dir": clamp_dir(paddle.get("dir", 0)),
                                            "t": game.clamp(t, now - PADDLE_CLOCK_SLACK, now)}
            self.paddles[name] = new
            # clients resend their paddle with every ping; only a real change is worth a snapshot
            if new["dir"] != old["dir"] or abs(game.paddle_at(new, now) - game.paddle_at(old, now)) > 1:
                self.changed = True
        if msg.get("ready") is True and self.phase in ("serve", "over") and name not in self.ready:
            self.ready.add(name)
            self.changed = True
        if isinstance(hit := msg.get("hit"), dict):
            self.on_contact(side, hit.get("id"), hit.get("paddle"), now)
        if isinstance(miss := msg.get("miss"), dict):
            self.on_contact(side, miss.get("id"), None, now)
        return reply

    def on_contact(self, side, seg_id, paddle_y, now):
        """The defending client's call on the current segment: a hit with its paddle at paddle_y, or a miss."""
        if self.phase != "playing" or seg_id != self.seg["id"]:
            return  # out of play, or about a segment that is already over
        dt, target, pos, vel = game.meet(self.seg["p"], self.seg["v"])
        if target != side:
            return  # only the side the ball is heading for gets to decide
        half = PADDLE_H / 2
        after = game.bounce(side, pos[1], game.clamp(paddle_y, half, HEIGHT - half), vel) if number(paddle_y) else None
        contact = self.seg["t"] + dt
        self.log(now, "hit" if after else "miss", self.players[side], "", "", round(now - contact, 6))
        if after:
            self.hits += 1
            self.new_segment(pos, after, contact)
            return
        scorer = 1 - side
        self.score = [s + (i == scorer) for i, s in enumerate(self.score)]
        print(f"score {self.score[0]}-{self.score[1]}")
        if game.winner({"score": self.score}) is not None:
            print(f"{self.players[scorer]} wins")
            self.log(now, "over", self.players[scorer], "", "", f"{self.score[0]}-{self.score[1]}")
            self.serve_vel = None
            self.new_segment(CENTRE, [0, 0], now)
            self.ready.clear()
        else:
            self.hold(-1 if side == 0 else 1, now)  # serve to the player who conceded

    def tick(self, now):
        for name in [p for p in self.players if now - self.seen[p] > TIMEOUT]:
            self.players.remove(name)
            del self.seen[name], self.paddles[name]
            print(f"left: {name} (nothing heard for {TIMEOUT:g} s)")
            self.log(now, "leave", name, "", "", "timeout")
            self.reset(now)  # a fresh match once two players are in again

        phase = self.phase
        if phase in ("serve", "over") and self.ready.issuperset(self.players):
            if phase == "over":
                self.reset(now)  # rematch: the Space presses that started it also serve the first ball
            self.new_segment(CENTRE, self.serve_vel, now + SERVE_DELAY)
            self.serve_vel = None
            self.ready.clear()

    def message(self):
        """The snapshot; the loop adds seq and ts. Complete on its own, so clients only need the newest."""
        return {"players": self.players, "phase": self.phase, "ready": [p in self.ready for p in self.players],
                "score": self.score, "hits": self.hits, "seg": self.seg,
                "paddles": [self.paddles[p] for p in self.players]}


def serve():
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind("tcp://*:5555")
    router = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.ROUTER_HANDOVER, 1)  # a client reconnecting under the same name takes over its old slot
    router.bind("tcp://*:5556")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / time.strftime("server-%Y%m%d-%H%M%S.csv")
    try:
        with open(path, "w", newline="", buffering=1) as f:  # line-buffered: a crash loses at most one row
            writer = csv.writer(f)
            writer.writerow(LOG_FIELDS)
            print(f"logging to {path}")
            run(pub, router, lambda *row: writer.writerow(row))
    finally:
        ctx.destroy(linger=0)  # don't wait to deliver queued snapshots on exit


def run(pub, router, log):
    match, seq, next_snapshot = Match(time.perf_counter(), log), 0, 0.0
    print("serving on ports 5555 (snapshots) and 5556 (client messages)")
    while True:
        router.poll(POLL_MS)
        while router.poll(0):
            frames = router.recv_multipart()
            now = time.perf_counter()  # per message, so pongs carry the time it really arrived
            try:
                ident, raw = frames
                name, msg = ident.decode(), zmq.utils.jsonapi.loads(raw)
            except ValueError:  # wrong frame count, bad UTF-8 or bad JSON: drop it
                log(now, "bad", "", "", "", "unreadable message")
                continue
            if isinstance(msg, dict):
                log(now, "recv", name, msg["seq"] if number(msg.get("seq")) else "",
                    msg["ts"] if number(msg.get("ts")) else "", "+".join(k for k in KINDS if k in msg))
            if reply := match.on_message(name, msg, now):
                router.send_multipart([ident, zmq.utils.jsonapi.dumps(reply)])
        now = time.perf_counter()
        match.tick(now)
        if match.changed or now >= next_snapshot:
            seq += 1
            ts = time.time()
            raw = zmq.utils.jsonapi.dumps({"seq": seq, "ts": ts, "t": time.perf_counter(), **match.message()})
            pub.send(raw)
            log(now, "snapshot", "", seq, ts, len(raw))
            match.changed, next_snapshot = False, now + SNAPSHOT_EVERY


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def clamp_dir(d):
    """Directions come from the network, so only -1, 0 and 1 get through."""
    return (d > 0) - (d < 0) if number(d) else 0


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        pass
