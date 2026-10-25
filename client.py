"""Pong client: draws the game state with pygame (see PLAN.md).

    python client.py                local game, two players on one keyboard: W/S (left), Up/Down (right)
    python client.py NAME [HOST]    networked: join server.py on HOST (default localhost), W/S or Up/Down

Add --plain to either for the flat look without effects.py (glow, particles, shake),
e.g. for experiments where only the network should cost time. Add --log to a networked
client to write CSV logs to logs/ (see telemetry.py).

Networked, the first two names to join play left and right, later ones watch. Names
must differ: the server tells players apart by name. Messages only flow when something
happens (see server.py): your paddle is yours alone and never waits for the network,
and when the ball reaches your paddle, your client decides hit or miss on the spot and
tells the server. Between those moments both clients compute the ball's path themselves;
the opponent's call arrives a moment later, and until then the client predicts it from
where the opponent's paddle is heading. The ping to the server shows bottom left.

A cat meows on every paddle hit: assets/meow.wav when present, else a synthetic one.

The window is sized to the screen and runs at the display's refresh rate
(vsync); the local game steps with the real frame time. Cat sprites are optional:
assets/cat_left.png and assets/cat_right.png are drawn over the paddles when present.
"""
import io
import json
import math
import queue
import sys
import threading
import time
import wave
from array import array
from collections import deque
from pathlib import Path

import pygame
import zmq

import effects
import game
import telemetry
from game import BALL_SIZE, HEIGHT, PADDLE_H, PADDLE_W, PADDLE_X, WIDTH

MAX_FPS = 240  # vsync normally sets the pace; this only caps it when vsync is off
MAX_DT = 1 / 30  # a stalled frame must not move the ball through a paddle
ASSETS = Path(__file__).parent / "assets"
BG, FG, DIM = (20, 20, 28), (235, 235, 235), (90, 90, 100)

# networked only
STATE_PORT, INPUT_PORT = 5555, 5556  # server.py's PUB and ROUTER
PING_EVERY = 0.2  # s; pings keep the clock in sync and double as the heartbeat
CLOCK_SAMPLES = 15  # recent pings; the clock trusts the one with the shortest round trip
GLIDE_RATE = 12  # per second: how fast the ball glides onto a corrected path
GLIDE_MAX = 120  # units; a bigger correction (a serve, a goal) jumps instead
PADDLE_GLIDE_MAX = PADDLE_H  # units; an opponent paddle further off than this jumps (e.g. after a reconnect)


class View:
    """Field units to screen pixels, with fonts and cat sprites prepared for that scale."""

    def __init__(self, scale):
        self.scale = scale
        self.offset = (0, 0)  # in pixels; effects.py shakes the field by moving this
        self.fonts = pygame.font.Font(None, round(72 * scale)), pygame.font.Font(None, round(28 * scale))
        self.title = pygame.font.Font(None, round(180 * scale))
        self.cats = load_cats(PADDLE_H * scale)
        self.shade = pygame.Surface((round(WIDTH * scale), round(HEIGHT * scale)), pygame.SRCALPHA)
        self.shade.fill((*BG, 235))

    def point(self, x, y):
        return x * self.scale + self.offset[0], y * self.scale + self.offset[1]

    def box(self, cx, cy, w, h):
        rect = pygame.FRect(0, 0, w * self.scale, h * self.scale)
        rect.center = self.point(cx, cy)
        return rect


def fit_scale():
    """Largest scale at which the field still fits comfortably on the main display."""
    w, h = pygame.display.get_desktop_sizes()[0]
    return min(0.9 * w / WIDTH, 0.85 * h / HEIGHT)


def load_cats(height):
    """One sprite per side, scaled once to the paddle height. None where the PNG is missing."""
    cats = []
    for side in ("left", "right"):
        path = ASSETS / f"cat_{side}.png"
        if path.exists():
            img = pygame.image.load(path).convert_alpha()
            cats.append(pygame.transform.smoothscale_by(img, height / img.get_height()))
        else:
            cats.append(None)
    return cats


def synth_meow(rate=22050, seconds=0.45):
    """A synthetic meow as WAV bytes: a harmonic tone whose pitch rises and falls while
    its bright "ee" fades into an "ow". Stand-in until we record our own cats."""
    samples, phase, n = array("h"), 0.0, int(rate * seconds)
    for i in range(n):
        t = i / n
        f0 = 420 + 380 * math.sin(math.pi * t ** 0.7) - 60 * t  # pitch: up, then down
        formant = 2400 - 1600 * t  # vowel: ee -> ow
        phase += 2 * math.pi * f0 / rate
        weights = [math.exp(-((k * f0 - formant) / 600) ** 2) + 0.25 / k for k in range(1, 13)]
        value = sum(w * math.sin(k * phase) for k, w in enumerate(weights, 1)) / sum(weights)
        envelope = min(1, t / 0.08) * min(1, (1 - t) / 0.35)
        samples.append(round(0.6 * 32767 * envelope * value))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    out.seek(0)
    return out


def load_meow():
    """assets/meow.wav if we have one, else a synthetic meow. None without an audio device."""
    path = ASSETS / "meow.wav"
    try:
        return pygame.mixer.Sound(path if path.exists() else synth_meow())
    except pygame.error:
        return None


class Clock:
    """Server time, from pings: the offset measured over the fastest recent round trip,
    because a slow round trip leaves the most doubt about when the server answered."""

    def __init__(self):
        self.samples = deque(maxlen=CLOCK_SAMPLES)
        self.offset = self.rtt = None

    def pong(self, sent, server_t, received):
        self.rtt = received - sent
        self.samples.append((self.rtt, server_t - (sent + received) / 2))
        self.offset = min(self.samples)[1]


def path(seg, t):
    """Where a ball segment is at server time t: still at its start until then."""
    return game.fly(seg["p"], seg["v"], max(0.0, t - seg["t"]))


class Rally:
    """The networked game as this client sees it between the server's snapshots. No sockets:
    Link feeds it snapshots and server time, and sends whatever ends up in `outbox`."""

    def __init__(self, name):
        self.name = name
        self.snap = self.seg = self.t = None
        self.provisional = False  # the segment is our own call or prediction, not yet from the server
        self.decided = None  # id of the last segment whose paddle contact was handled
        self.mine = {"y": HEIGHT / 2, "dir": 0, "t": 0.0}  # our paddle's last key change, as we send it
        self.glide = [0.0, 0.0]  # display offset that fades out after a correction
        self.paddle_glide = [0.0, 0.0]  # the same for each opponent paddle when its key change arrives
        self.outbox = []
        self.pending = {}  # segment id -> a contact we called or predicted, until the server answers
        self.events = []  # rows for telemetry: ("contacts", server time, ...)

    def side(self):
        players = self.snap["players"]
        return players.index(self.name) if self.name in players else None

    def receive(self, snap, t, arrived=None):
        """A snapshot from the server. t is the server time now, or None before the clock is synced;
        `arrived` is when it actually arrived (server time), if known, for the contact log."""
        old, self.snap = self.snap, snap
        if old is not None and t is not None and old["players"] == snap["players"]:
            # an opponent's key change arrives a network delay late: glide there instead of jumping
            for i, rec in enumerate(snap["paddles"]):
                gap = self.paddle_glide[i] + game.paddle_at(old["paddles"][i], t) - game.paddle_at(rec, t)
                self.paddle_glide[i] = gap if abs(gap) < PADDLE_GLIDE_MAX else 0.0
        else:
            self.paddle_glide = [0.0, 0.0]
        seg, correction = snap["seg"], 0.0
        if self.seg is None or seg["id"] > self.seg["id"] or (seg["id"] == self.seg["id"] and self.provisional):
            correction = self._switch(seg, t, provisional=False)
        for seg_id in [i for i in self.pending if i < seg["id"]]:  # the server has moved past these
            c = self.pending.pop(seg_id)
            outcome = "hit" if snap["score"] == c["score"] else "goal"
            answer = arrived if arrived is not None else t
            self.events.append(("contacts", t, seg_id, c["side"], int(c["mine"]), c["call"],
                                1000 * (c["noticed"] - c["contact"]),
                                None if answer is None else 1000 * (answer - c["contact"]),
                                outcome, int((c["call"] == "hit") == (outcome == "hit")),
                                correction if outcome == "hit" else None))  # after a goal the ball is reset anyway

    def _switch(self, seg, t, provisional):
        """Follow a new segment, gliding from where the ball is drawn now unless that is far off.
        Returns how far the path we followed was from the new one right now, in units."""
        error = 0.0
        if self.seg is not None and t is not None:
            gap = [a - b for a, b in zip(self._ball(t)[0], path(seg, t)[0])]
            self.glide = gap if math.hypot(*gap) < GLIDE_MAX else [0.0, 0.0]
            error = math.dist(path(self.seg, t)[0], path(seg, t)[0])  # leaving out any glide still on screen
        self.seg, self.provisional = seg, provisional
        return error

    def _ball(self, t):
        (x, y), vel = path(self.seg, t)
        return [x + self.glide[0], y + self.glide[1]], vel

    def _paddle(self, i, t):
        """Where paddle i is at time t. Ours from our own last key change, the others from theirs."""
        return game.paddle_at(self.mine if i == self.side() else self.snap["paddles"][i], t)

    def _paddle_shown(self, i, t):
        """Paddle i as drawn: an opponent's includes what is left of its glide."""
        if i == self.side():
            return self._paddle(i, t)
        half = PADDLE_H / 2
        return game.clamp(self._paddle(i, t) + self.paddle_glide[i], half, HEIGHT - half)

    def _contact(self, t, side):
        """Once the ball reaches a paddle: decide hit or miss for our own side, predict it for the other."""
        seg = self.seg
        if self.decided == seg["id"] or seg["v"][0] == 0:
            return
        dt, target, pos, vel = game.meet(seg["p"], seg["v"])
        when = seg["t"] + dt
        if t < when:
            return
        self.decided = seg["id"]
        mine = target == side
        paddle = self._paddle(target, when)  # at the moment of contact, not this (later) frame
        after = game.bounce(target, pos[1], paddle, vel)
        self.pending[seg["id"]] = {"side": target, "mine": mine, "call": "hit" if after else "miss",
                                   "contact": when, "noticed": t, "score": list(self.snap["score"])}
        if mine:
            self.outbox.append({"hit": {"id": seg["id"], "paddle": paddle}} if after else {"miss": {"id": seg["id"]}})
        if after:
            self._switch({"id": seg["id"] + 1, "p": pos, "v": after, "t": when}, t, provisional=True)

    def frame(self, t, d, ready=False):
        """Advance to server time t with your key direction d; returns the state to draw."""
        dt = 0.0 if self.t is None else max(0.0, t - self.t)  # real time: everything here is exact for any dt
        self.t = t
        snap, side = self.snap, self.side()
        if snap["phase"] == "playing":
            self._contact(t, side)  # before a key change this frame, which only counts from now on
        if side is not None:
            if ready and snap["phase"] in ("serve", "over"):
                self.outbox.append({"ready": True})
            if d != self.mine["dir"]:
                # computed from the key change and the clock, exactly as the other machines compute it
                self.mine = {"y": game.paddle_at(self.mine, t), "dir": d, "t": t}
                self.outbox.append({"paddle": self.mine})

        fade = math.exp(-GLIDE_RATE * dt)
        self.glide = [g * fade for g in self.glide]
        self.paddle_glide = [g * fade for g in self.paddle_glide]
        ball, vel = self._ball(t)
        paddles = [self._paddle_shown(i, t) for i in range(len(snap["players"]))]
        return {"players": snap["players"], "phase": snap["phase"], "ready": snap["ready"],
                "score": snap["score"], "hits": snap["hits"] + self.provisional,
                "ball": ball, "vel": vel, "paddles": paddles}


class Link:
    """The sockets, owned by a small thread: snapshots in on a SUB, everything else through a DEALER.

    The thread sends the pings itself and timestamps each pong and snapshot the moment it
    arrives. Done in the game loop, they would wait for the next frame, which on one machine
    turned a sub-millisecond round trip into about 8 ms at 120 fps, and pulled the clock 4 ms
    late. With `log_dir`, everything measured goes to CSV files (see telemetry.py)."""

    def __init__(self, name, host, log_dir=None):
        self.ctx = zmq.Context()
        self.pipe = self.ctx.socket(zmq.PAIR)  # game loop -> thread; wakes the thread at once
        address = f"inproc://link-{id(self)}"
        self.pipe.bind(address)
        self.snapshots = queue.SimpleQueue()  # thread -> game loop: (snapshot, arrival time)
        self.clock, self.rally = Clock(), Rally(name)  # the thread feeds the clock; the game loop reads it
        self.seq = 0  # messages sent so far, counted by the thread
        self.traffic = [0, 0, 0, 0]  # messages out, messages in, bytes out, bytes in, counted by the thread
        self.paddle = None  # our paddle as of the last frame, resent with every ping; None while watching
        self.recorder = telemetry.Recorder(log_dir, name) if log_dir else None
        self.last_frame = None
        self.thread = threading.Thread(target=self._talk, args=(name, host, address), daemon=True)
        self.thread.start()

    def _talk(self, name, host, address):
        """The thread: forwards the game loop's messages, pings on time, timestamps what arrives."""
        pipe = self.ctx.socket(zmq.PAIR)
        pipe.connect(address)
        sub = self.ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.CONFLATE, 1)  # every snapshot is complete, so only the newest matters
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.connect(f"tcp://{host}:{STATE_PORT}")
        dealer = self.ctx.socket(zmq.DEALER)
        dealer.setsockopt(zmq.IDENTITY, name.encode())
        dealer.setsockopt(zmq.IMMEDIATE, 1)  # don't queue messages while the server is unreachable
        dealer.connect(f"tcp://{host}:{INPUT_PORT}")
        poller = zmq.Poller()
        for socket in (pipe, sub, dealer):
            poller.register(socket, zmq.POLLIN)
        recorder, last_seq = self.recorder, None

        def send(msg):
            self.seq += 1
            raw = json.dumps({"seq": self.seq, "ts": time.time(), **msg}).encode()
            try:
                dealer.send(raw, zmq.NOBLOCK)
                self.traffic[0] += 1
                self.traffic[2] += len(raw)
            except zmq.Again:  # not connected (yet); a stale message is worthless, so drop it
                pass

        def arrived(raw):
            """Timestamp first, before parsing; returns (message, arrival time, our server-time estimate)."""
            received = time.perf_counter()
            self.traffic[1] += 1
            self.traffic[3] += len(raw)
            offset = self.clock.offset
            return json.loads(raw), received, None if offset is None else received + offset

        next_ping = time.perf_counter()
        try:
            while True:
                if time.perf_counter() >= next_ping:
                    # the paddle rides along, so a missed or stale key change (a reconnect) heals itself
                    paddle = self.paddle
                    send({"ping": time.perf_counter(), **({"paddle": paddle} if paddle else {})})
                    next_ping = time.perf_counter() + PING_EVERY
                wait = max(0.0, next_ping - time.perf_counter())
                ready = dict(poller.poll(wait * 1000))
                if dealer in ready:
                    while dealer.poll(0):
                        msg, received, _ = arrived(dealer.recv())
                        if "pong" in msg:
                            self.clock.pong(msg["pong"], msg["t"], received)
                            if recorder:
                                sample = msg["t"] - (msg["pong"] + received) / 2
                                recorder.row("pings", received + self.clock.offset, 1000 * self.clock.rtt,
                                             1000 * sample, 1000 * self.clock.offset)
                if sub in ready:
                    while sub.poll(0):
                        raw = sub.recv()
                        snap, received, server_t = arrived(raw)
                        self.snapshots.put((snap, received))
                        if recorder:
                            skipped = None if last_seq is None else snap["seq"] - last_seq - 1
                            latency = None if server_t is None or "t" not in snap else 1000 * (server_t - snap["t"])
                            recorder.row("snapshots", server_t, snap["seq"], skipped, len(raw), latency,
                                         snap["phase"], *snap["score"], snap["hits"], snap["seg"]["id"])
                        last_seq = snap["seq"]
                if pipe in ready:
                    while pipe.poll(0):
                        raw = pipe.recv()
                        if not raw:  # close() asks the thread to stop
                            return
                        send(json.loads(raw))
        finally:
            for socket in (dealer, sub, pipe):
                socket.close(linger=0)

    def send(self, msg):
        self.pipe.send_json(msg)

    def update(self, d, ready=False):
        """One frame: take in the newest snapshot, advance the rally, log.
        Returns the state to draw, or None until the clock is synced and a snapshot has arrived."""
        now = time.perf_counter()
        interval, self.last_frame = (None if self.last_frame is None else now - self.last_frame), now
        newest = None
        while True:
            try:
                newest = self.snapshots.get_nowait()
            except queue.Empty:
                break
        t = None if self.clock.offset is None else time.perf_counter() + self.clock.offset
        if newest:
            snap, received = newest
            self.rally.receive(snap, t, None if t is None else received + self.clock.offset)
        if self.recorder:
            self.recorder.frame(interval, t, tuple(self.traffic), self.clock)
        if t is None or self.rally.snap is None:
            return None
        shown = self.rally.frame(t, d, ready)
        mine = self.rally.mine
        self.paddle = None if self.rally.side() is None else {
            "y": game.paddle_at(mine, t), "dir": mine["dir"], "t": t}  # same motion, fresh start time
        for msg in self.rally.outbox:
            self.send(msg)
        self.rally.outbox.clear()
        if self.recorder:
            for kind, server_t, *values in self.rally.events:
                self.recorder.row(kind, server_t, *values)
        self.rally.events.clear()
        return shown

    def close(self):
        self.pipe.send(b"")
        self.thread.join(timeout=1)
        self.ctx.destroy(linger=0)
        if self.recorder:
            self.recorder.close()


def draw(screen, state, view, me=None, fx=None):
    if fx:
        return fx.draw(screen, state, me)
    screen.fill(BG)
    for y in range(0, HEIGHT, 30):
        pygame.draw.rect(screen, DIM, view.box(WIDTH / 2, y + 10, 4, 16))

    big, small = view.fonts
    players = state.get("players", [])  # added by the server, not part of game.py's state
    for i, (score, x) in enumerate(zip(state["score"], (WIDTH / 4, WIDTH * 3 / 4))):
        screen.blit(text := big.render(str(score), True, FG), text.get_rect(center=view.point(x, 50)))
        if i < len(players):
            mine = players[i] == me
            name = small.render(players[i] + (" (you)" if mine else ""), True, FG if mine else DIM)
            screen.blit(name, name.get_rect(center=view.point(x, 90)))

    for x, y, cat in zip(PADDLE_X, state["paddles"], view.cats):
        paddle = view.box(x, y, PADDLE_W, PADDLE_H)  # the hitbox; a cat is only drawn on top
        if cat:
            screen.blit(cat, cat.get_rect(center=paddle.center))
        else:
            pygame.draw.rect(screen, FG, paddle)

    # drawn round, but the hitbox is still the BALL_SIZE square
    pygame.draw.aacircle(screen, FG, view.point(*state["ball"]), BALL_SIZE / 2 * view.scale)


def text(screen, view, font, line, colour, y):
    label = font.render(line, True, colour)
    screen.blit(label, label.get_rect(center=view.point(WIDTH / 2, y)))


def banner(screen, view, line):
    text(screen, view, view.fonts[1], line, FG, HEIGHT * 2 / 3)


def start_screen(screen, view, status, hint, fx=None):
    if fx:
        return fx.start_screen(screen, status, hint)
    screen.fill(BG)
    text(screen, view, view.title, "PONG", FG, 200)
    for cat, x in zip(view.cats, (WIDTH / 2 - 230, WIDTH / 2 + 230)):
        if cat:
            screen.blit(cat, cat.get_rect(center=view.point(x, 200)))
    text(screen, view, view.fonts[1], status, FG, 360)
    text(screen, view, view.fonts[1], hint, DIM, 410)


def end_screen(screen, view, state, players, hint, fx=None):
    """Drawn over the final state of the match."""
    if fx:
        return fx.end_overlay(screen, state, players, hint)
    side = game.winner(state)
    screen.blit(view.shade, (0, 0))
    text(screen, view, view.fonts[0], f"{players[side]} wins", FG, 250)
    text(screen, view, view.fonts[1], f"{state['score'][0]} - {state['score'][1]}", FG, 310)
    text(screen, view, view.fonts[1], hint, DIM, 400)


def open_window(title):
    pygame.init()
    scale = fit_scale()
    size = round(WIDTH * scale), round(HEIGHT * scale)
    try:
        screen = pygame.display.set_mode(size, pygame.SCALED | pygame.RESIZABLE, vsync=1)
    except pygame.error:  # no vsync here; clock.tick still caps the rate
        screen = pygame.display.set_mode(size, pygame.SCALED | pygame.RESIZABLE)
    pygame.display.set_caption(title)
    return screen, View(scale)


def read_events():
    """Keys pressed since the last frame, or None when the window should close."""
    pressed = []
    for event in pygame.event.get():
        if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
            return None
        if event.type == pygame.KEYDOWN:
            pressed.append(event.key)
    return pressed


def local(plain=False):
    screen, view = open_window("Pong")
    fx = None if plain else effects.FX(view)
    meow = load_meow()
    names = ["W / S", "Up / Down"]
    state, playing = game.new_state(), False
    held = None  # after a goal: the serve velocity, kept while the ball waits in the centre for Space
    clock = pygame.time.Clock()
    while (pressed := read_events()) is not None:
        dt = min(clock.tick(MAX_FPS) / 1000, MAX_DT)
        if pygame.K_SPACE in pressed:
            if not playing or game.winner(state) is not None:
                state, playing, held = game.new_state(), True, None
            elif held:
                state, held = {**state, "vel": held}, None
        if not playing:
            start_screen(screen, view, "Press Space to start", "Left: W / S     Right: Up / Down     Esc: quit", fx)
        else:
            keys = pygame.key.get_pressed()
            hits, score = state["hits"], state["score"]
            state = game.step(state, keys[pygame.K_s] - keys[pygame.K_w],
                              keys[pygame.K_DOWN] - keys[pygame.K_UP], dt)
            if meow and state["hits"] > hits:
                meow.play()
            if state["score"] != score and game.winner(state) is None:
                held, state = state["vel"], {**state, "vel": [0, 0]}  # like the server: every ball waits for Space
            draw(screen, {**state, "players": names}, view, fx=fx)
            if game.winner(state) is not None:
                end_screen(screen, view, state, names, "Space for a rematch     Esc to quit", fx)
            elif held:
                banner(screen, view, "Press Space to serve")
        pygame.display.flip()


def networked(name, host, plain=False, log_dir=None):
    link = Link(name, host, log_dir)
    screen, view = open_window(f"Pong: {name}")
    fx = None if plain else effects.FX(view)
    meow = load_meow()
    controls = "W / S or Up / Down to move     Esc to quit"
    clock, hits = pygame.time.Clock(), None
    try:
        while (pressed := read_events()) is not None:
            clock.tick(MAX_FPS)
            keys = pygame.key.get_pressed()
            d = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
            state = link.update(d, pygame.K_SPACE in pressed)

            if state is None:
                start_screen(screen, view, f"Connecting to {host}...", controls, fx)
            elif len(state["players"]) < 2:
                status = "You play left. Waiting for an opponent..." if state["players"] == [name] else "Joining..."
                start_screen(screen, view, status, controls, fx)
            else:
                if meow and hits is not None and state["hits"] > hits:
                    meow.play()
                hits = state["hits"]
                players = state["players"]
                side = players.index(name) if name in players else None
                draw(screen, state, view, me=name, fx=fx)
                if game.winner(state) is not None:
                    end_screen(screen, view, state, players, "Space for a rematch     Esc to quit" if side is not None
                               else "Esc to quit", fx)
                elif side is None:
                    banner(screen, view, "Watching: two players are already in")
                elif state["phase"] == "serve":
                    banner(screen, view, "Ready! Waiting for your opponent..." if state["ready"][side]
                           else "Press Space when ready")
            if link.clock.rtt is not None:
                label = view.fonts[1].render(f"ping {link.clock.rtt * 1000:.0f} ms", True, DIM)
                screen.blit(label, label.get_rect(bottomleft=view.point(10, HEIGHT - 8)))
            pygame.display.flip()
    finally:
        link.close()


if __name__ == "__main__":
    plain, log = "--plain" in sys.argv, "--log" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        if not args:
            local(plain)
        elif len(args) in (1, 2):
            networked(args[0], args[1] if len(args) == 2 else "localhost", plain, "logs" if log else None)
        else:
            print(__doc__)
    except KeyboardInterrupt:
        pass
    pygame.quit()
