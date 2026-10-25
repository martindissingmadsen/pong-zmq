"""Pong client: draws the game state with pygame (see PLAN.md).

    python client.py                local game, two players on one keyboard: W/S (left), Up/Down (right)
    python client.py NAME [HOST]    networked: join server.py on HOST (default localhost), W/S or Up/Down

Networked, the client sends its input every frame and draws the newest state from
the server; the first two names to join play left and right, later ones watch.
Names must differ: the server tells players apart by name.

The window is sized to the screen and runs at the display's refresh rate
(vsync); the local game steps with the real frame time. Cat sprites are optional:
assets/cat_left.png and assets/cat_right.png are drawn over the paddles when present.
"""
import sys
import time
from pathlib import Path

import pygame
import zmq

import game
from game import BALL_SIZE, HEIGHT, PADDLE_H, PADDLE_W, PADDLE_X, WIDTH

MAX_FPS = 240  # vsync normally sets the pace; this only caps it when vsync is off
MAX_DT = 1 / 30  # a stalled frame must not move the ball through a paddle
ASSETS = Path(__file__).parent / "assets"
BG, FG, DIM = (20, 20, 28), (235, 235, 235), (90, 90, 100)


class View:
    """Field units to screen pixels, with fonts and cat sprites prepared for that scale."""

    def __init__(self, scale):
        self.scale = scale
        self.fonts = pygame.font.Font(None, round(72 * scale)), pygame.font.Font(None, round(28 * scale))
        self.cats = load_cats(PADDLE_H * scale)

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

    if (side := game.winner(state)) is not None:
        banner(screen, view, f"{(players or ['Left', 'Right'])[side]} wins")


def banner(screen, view, text):
    label = view.fonts[1].render(text, True, FG)
    screen.blit(label, label.get_rect(center=view.point(WIDTH / 2, HEIGHT * 2 / 3)))


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


def quit_requested():
    return any(event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
               for event in pygame.event.get())


def local():
    screen, view = open_window("Pong")
    state = game.new_state()
    clock = pygame.time.Clock()
    while not quit_requested():
        dt = min(clock.tick(MAX_FPS) / 1000, MAX_DT)
        keys = pygame.key.get_pressed()
        state = game.step(state, keys[pygame.K_s] - keys[pygame.K_w],
                          keys[pygame.K_DOWN] - keys[pygame.K_UP], dt)
        draw(screen, {**state, "players": ["W / S", "Up / Down"]}, view)
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
    clock, seq, state = pygame.time.Clock(), 0, None
    try:
        while not quit_requested():
            clock.tick(MAX_FPS)
            keys = pygame.key.get_pressed()
            seq += 1
            try:
                dealer.send_json({"seq": seq, "ts": time.time(),
                                  "dir": (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])},
                                 zmq.NOBLOCK)
            except zmq.Again:  # not connected (yet); stale inputs are worthless, so drop it
                pass
            if sub.poll(0):
                state = sub.recv_json()

            if state is None:
                screen.fill(BG)
                banner(screen, view, f"Connecting to {host}...")
            else:
                draw(screen, state, view, me=name)
                if len(state["players"]) < 2:
                    banner(screen, view, "Waiting for an opponent")
                elif name not in state["players"]:
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
