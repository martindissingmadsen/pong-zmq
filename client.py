"""Phase 1 front end: draws a game state with pygame (see PLAN.md).

    python client.py        two players on one keyboard: W/S (left), Up/Down (right)

The window is sized to the screen and runs at the display's refresh rate
(vsync), stepping the game with the real frame time. Cat sprites are optional:
assets/cat_left.png and assets/cat_right.png are drawn over the paddles when present.
"""
from pathlib import Path

import pygame

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


def draw(screen, state, view):
    screen.fill(BG)
    for y in range(0, HEIGHT, 30):
        pygame.draw.rect(screen, DIM, view.box(WIDTH / 2, y + 10, 4, 16))

    big, small = view.fonts
    players = state.get("players", [])  # added by the server, not part of game.py's state
    for i, (score, x) in enumerate(zip(state["score"], (WIDTH / 4, WIDTH * 3 / 4))):
        screen.blit(text := big.render(str(score), True, FG), text.get_rect(center=view.point(x, 50)))
        if i < len(players):
            name = small.render(players[i], True, DIM)
            screen.blit(name, name.get_rect(center=view.point(x, 90)))

    for x, y, cat in zip(PADDLE_X, state["paddles"], view.cats):
        paddle = view.box(x, y, PADDLE_W, PADDLE_H)  # the hitbox; a cat is only drawn on top
        if cat:
            screen.blit(cat, cat.get_rect(center=paddle.center))
        else:
            pygame.draw.rect(screen, FG, paddle)

    # drawn round, but the hitbox is still the BALL_SIZE square
    pygame.draw.aacircle(screen, FG, view.point(*state["ball"]), BALL_SIZE / 2 * view.scale)


def local():
    pygame.init()
    scale = fit_scale()
    size = round(WIDTH * scale), round(HEIGHT * scale)
    try:
        screen = pygame.display.set_mode(size, pygame.SCALED | pygame.RESIZABLE, vsync=1)
    except pygame.error:  # no vsync here; clock.tick still caps the rate
        screen = pygame.display.set_mode(size, pygame.SCALED | pygame.RESIZABLE)
    pygame.display.set_caption("Pong")
    view = View(scale)
    state = game.new_state()
    clock = pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                return
        dt = min(clock.tick(MAX_FPS) / 1000, MAX_DT)
        keys = pygame.key.get_pressed()
        state = game.step(state, keys[pygame.K_s] - keys[pygame.K_w],
                        keys[pygame.K_DOWN] - keys[pygame.K_UP], dt)
        draw(screen, {**state, "players": ["W / S", "Up / Down"]}, view)
        pygame.display.flip()


if __name__ == "__main__":
    try:
        local()
    except KeyboardInterrupt:
        pass
    pygame.quit()
