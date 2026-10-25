"""Pong client: draws the game state with pygame (see PLAN.md).

    python client.py                local game, two players on one keyboard: W/S (left), Up/Down (right)
    python client.py NAME [HOST]    networked: join server.py on HOST (default localhost), W/S or Up/Down

Networked, the client sends its input every frame and draws the newest state from
the server; the first two names to join play left and right, later ones watch.
Names must differ: the server tells players apart by name. To feel smooth between
server states, it moves your own paddle as soon as you press a key and moves the
ball forward from its last velocity.

A cat meows on every paddle hit: assets/meow.wav when present, else a synthetic one.

The window is sized to the screen and runs at the display's refresh rate
(vsync); the local game steps with the real frame time. Cat sprites are optional:
assets/cat_left.png and assets/cat_right.png are drawn over the paddles when present.
"""
import io
import math
import sys
import time
import wave
from array import array
from pathlib import Path

import pygame
import zmq

import game
from game import BALL_SIZE, HEIGHT, PADDLE_H, PADDLE_SPEED, PADDLE_W, PADDLE_X, WIDTH

MAX_FPS = 240  # vsync normally sets the pace; this only caps it when vsync is off
MAX_DT = 1 / 30  # a stalled frame must not move the ball through a paddle
ASSETS = Path(__file__).parent / "assets"
BG, FG, DIM = (20, 20, 28), (235, 235, 235), (90, 90, 100)

# smoothing between server states (networked only)
SETTLE = 0.1  # s without input before your paddle eases back to where the server has it
CORRECTION = 10  # per second: how fast it eases back
SNAP = PADDLE_H / 2  # further off than this, jump straight to the server's position
MAX_AHEAD = 0.1  # s; the ball is never moved forward further than this past the last state


class View:
    """Field units to screen pixels, with fonts and cat sprites prepared for that scale."""

    def __init__(self, scale):
        self.scale = scale
        self.fonts = pygame.font.Font(None, round(72 * scale)), pygame.font.Font(None, round(28 * scale))
        self.title = pygame.font.Font(None, round(180 * scale))
        self.cats = load_cats(PADDLE_H * scale)
        self.shade = pygame.Surface((round(WIDTH * scale), round(HEIGHT * scale)), pygame.SRCALPHA)
        self.shade.fill((*BG, 235))

    def point(self, x, y):
        return x * self.scale, y * self.scale

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


def predict_paddle(y, server_y, d, idle, dt):
    """Your own paddle: moves with your input at once, like game.step will move it on the
    server a moment later. Once you have let go for SETTLE seconds it eases back to the
    server's position, and it jumps there if it is ever more than SNAP off."""
    y = game.clamp(y + d * PADDLE_SPEED * dt, PADDLE_H / 2, HEIGHT - PADDLE_H / 2)
    if abs(server_y - y) > SNAP:
        return server_y
    if idle >= SETTLE:
        y += (server_y - y) * min(1, CORRECTION * dt)
    return y


def ball_ahead(state, age):
    """The ball moved forward `age` seconds from its last velocity, bouncing as in game.step.
    Never past a goal: scoring is the server's call, so then it stays where the state has it."""
    if age <= 0:
        return state["ball"]
    ahead = game.step(state, 0, 0, min(age, MAX_AHEAD))
    return ahead["ball"] if ahead["score"] == state["score"] else state["ball"]


def draw(screen, state, view, me=None):
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


def start_screen(screen, view, status, hint):
    screen.fill(BG)
    text(screen, view, view.title, "PONG", FG, 200)
    for cat, x in zip(view.cats, (WIDTH / 2 - 230, WIDTH / 2 + 230)):
        if cat:
            screen.blit(cat, cat.get_rect(center=view.point(x, 200)))
    text(screen, view, view.fonts[1], status, FG, 360)
    text(screen, view, view.fonts[1], hint, DIM, 410)


def end_screen(screen, view, state, players, hint):
    """Drawn over the final state of the match."""
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


def local():
    screen, view = open_window("Pong")
    meow = load_meow()
    names = ["W / S", "Up / Down"]
    state, playing = game.new_state(), False
    clock = pygame.time.Clock()
    while (pressed := read_events()) is not None:
        dt = min(clock.tick(MAX_FPS) / 1000, MAX_DT)
        if pygame.K_SPACE in pressed and (not playing or game.winner(state) is not None):
            state, playing = game.new_state(), True
        if not playing:
            start_screen(screen, view, "Press Space to start", "Left: W / S     Right: Up / Down     Esc: quit")
        else:
            keys = pygame.key.get_pressed()
            hits = state["hits"]
            state = game.step(state, keys[pygame.K_s] - keys[pygame.K_w],
                              keys[pygame.K_DOWN] - keys[pygame.K_UP], dt)
            if meow and state["hits"] > hits:
                meow.play()
            draw(screen, {**state, "players": names}, view)
            if game.winner(state) is not None:
                end_screen(screen, view, state, names, "Space for a rematch     Esc to quit")
        pygame.display.flip()


def networked(name, host):
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.CONFLATE, 1)  # keep only the newest state; must be set before connect
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(f"tcp://{host}:5555")
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.IDENTITY, name.encode())
    dealer.setsockopt(zmq.IMMEDIATE, 1)  # don't queue inputs while the server is unreachable
    dealer.connect(f"tcp://{host}:5556")

    screen, view = open_window(f"Pong: {name}")
    meow = load_meow()
    controls = "W / S or Up / Down to move     Esc to quit"
    clock, seq = pygame.time.Clock(), 0
    state, received, my_y, idle = None, 0.0, None, 0.0
    try:
        while read_events() is not None:
            dt = min(clock.tick(MAX_FPS) / 1000, MAX_DT)
            keys = pygame.key.get_pressed()
            d = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
            seq += 1
            try:
                dealer.send_json({"seq": seq, "ts": time.time(), "dir": d}, zmq.NOBLOCK)
            except zmq.Again:  # not connected (yet); stale inputs are worthless, so drop it
                pass
            if sub.poll(0):
                new = sub.recv_json()
                if meow and state and new["hits"] > state["hits"]:
                    meow.play()
                state, received = new, time.perf_counter()

            if state is None:
                start_screen(screen, view, f"Connecting to {host}...", controls)
            elif len(state["players"]) < 2:
                status = "You play left. Waiting for an opponent..." if state["players"] == [name] else "Joining..."
                start_screen(screen, view, status, controls)
            else:
                players = state["players"]
                side = players.index(name) if name in players else None
                over = game.winner(state) is not None
                shown = dict(state)
                if not over:
                    shown["ball"] = ball_ahead(state, time.perf_counter() - received)
                if side is not None and not over:
                    server_y = state["paddles"][side]
                    idle = 0.0 if d else idle + dt
                    my_y = predict_paddle(server_y if my_y is None else my_y, server_y, d, idle, dt)
                    shown["paddles"] = list(state["paddles"])
                    shown["paddles"][side] = my_y
                else:
                    my_y = None
                draw(screen, shown, view, me=name)
                if over:
                    end_screen(screen, view, state, players, "Esc to quit")
                elif side is None:
                    banner(screen, view, "Watching: two players are already in")
            pygame.display.flip()
    finally:
        ctx.destroy(linger=0)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            local()
        elif len(sys.argv) in (2, 3):
            networked(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "localhost")
        else:
            print(__doc__)
    except KeyboardInterrupt:
        pass
    pygame.quit()
