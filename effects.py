"""Eye candy for client.py: glow, trails, particles, shake and animated screens.

Everything here is drawn from the states the client already has; it never changes
the game. Events are found by comparing each state with the one before it, the same
way the meow works: `hits` went up is a paddle hit, `score` changed is a goal, and a
flipped vertical velocity without either is a wall bounce. Turn it all off with
`python client.py --plain`.

Glows and particles are blurred sprites rendered once at startup and added onto the
frame (BLEND_ADD), so the per-frame cost stays a few hundred small blits.
"""
import math
import random
import time

import pygame

import game
from game import BALL_SIZE, BALL_SPEED, HEIGHT, MAX_SPEED, PADDLE_H, PADDLE_W, PADDLE_X, WIDTH

BG = (20, 20, 28)
FG = (235, 235, 235)
DIM = (90, 90, 100)
SIDES = ((80, 220, 255), (255, 90, 200))  # left cyan, right magenta
HEAT = ((255, 255, 255), (255, 230, 140), (255, 150, 50), (255, 70, 60))  # ball colour from slow to fast
WALL, DUST = (200, 200, 255), (120, 140, 255)
CONFETTI = ((255, 90, 200), (80, 220, 255), (255, 220, 90), (140, 255, 140), (255, 255, 255))
LEVELS = 16  # brightness steps for additive sprites
MAX_PARTICLES = 600
R = BALL_SIZE / 2


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(round(lerp(a, b, t)) for a, b in zip(c1, c2))


def heat_colour(t):
    """Colour along HEAT for t in 0..1."""
    t = min(max(t, 0), 1) * (len(HEAT) - 1)
    i = min(int(t), len(HEAT) - 2)
    return mix(HEAT[i], HEAT[i + 1], t - i)


def glow(size, colour, blur, shape="circle", border_radius=0):
    """A blurred shape on black, meant to be added onto the frame. Sizes in pixels.

    Blur time grows fast with the radius (a 160 px glow took 1.5 s), so wide glows are
    blurred at a fraction of their size and scaled up; a blur has no detail to lose."""
    f = max(1.0, blur / 8)
    w, h = max(1, round(size[0] / f)), max(1, round(size[1] / f))
    blur = max(1, round(blur / f))
    pad = blur * 3
    surf = pygame.Surface((w + 2 * pad, h + 2 * pad))
    rect = pygame.Rect(pad, pad, w, h)
    if shape == "circle":
        pygame.draw.ellipse(surf, colour, rect)
    else:
        pygame.draw.rect(surf, colour, rect, border_radius=round(border_radius / f))
    surf = pygame.transform.gaussian_blur(surf, blur)
    return pygame.transform.smoothscale_by(surf, f) if f > 1 else surf


def text_glow(font, line, colour, blur):
    """A blurred copy of rendered text on black, in the letters' own shape."""
    f = max(1.0, blur / 8)
    label = font.render(line, True, colour, (0, 0, 0)).convert()  # rendered with a background it is 8-bit
    label = pygame.transform.smoothscale_by(label, 1 / f)
    blur = max(1, round(blur / f))
    pad = blur * 3
    surf = pygame.Surface((label.get_width() + 2 * pad, label.get_height() + 2 * pad))
    surf.blit(label, (pad, pad))
    return pygame.transform.smoothscale_by(pygame.transform.gaussian_blur(surf, blur), f)


def levels(surf):
    """LEVELS copies of an additive sprite from faint to full brightness."""
    out = []
    for i in range(1, LEVELS + 1):
        copy = surf.copy()
        k = round(255 * i / LEVELS)
        copy.fill((k, k, k), special_flags=pygame.BLEND_MULT)
        out.append(copy)
    return out


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "age", "colour", "size", "drag", "gravity")

    def __init__(self, x, y, vx, vy, life, colour, size=1, drag=3.0, gravity=0.0):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life, self.age, self.colour, self.size = life, 0.0, colour, size
        self.drag, self.gravity = drag, gravity


class Confetti:
    __slots__ = ("x", "y", "vx", "vy", "spin", "phase", "colour", "size")

    def __init__(self):
        self.x, self.y = random.uniform(0, WIDTH), random.uniform(-60, -10)
        self.vx, self.vy = random.uniform(-40, 40), random.uniform(60, 160)
        self.spin, self.phase = random.uniform(4, 12), random.uniform(0, math.tau)
        self.colour, self.size = random.choice(CONFETTI), random.uniform(5, 10)


class FX:
    def __init__(self, view):
        self.view = view
        s = view.scale
        self.size = round(WIDTH * s), round(HEIGHT * s)
        self.background = self._background()
        self.sprites = {}  # (colour, size) -> brightness levels of a glow dot
        self.ball_glows = [levels(glow((round(60 * s),) * 2, heat_colour(i / 7), round(10 * s))) for i in range(8)]
        self.paddle_glows = [glow((round((PADDLE_W + 16) * s), round((PADDLE_H + 16) * s)), c, round(12 * s),
                                  "rect", round(10 * s)) for c in SIDES]
        self.meows = [view.fonts[1].render("meow!", True, c) for c in SIDES]
        self.title_glows = levels(text_glow(view.title, "PONG", SIDES[0], round(16 * s)))
        self.scores = {}  # (side, score) -> rendered number
        for colour in (*SIDES, FG, WALL):  # every particle sprite up front, so none is made mid-rally
            for size in range(2, 11):
                self.dot(colour, size)
        for i in range(8):
            self.dot(heat_colour(i / 7), 12), self.dot(heat_colour(i / 7), 5)
        for colour in SIDES:
            self.dot(colour, 30), self.dot(colour, 160)
        self.dot(DUST, 4)
        self.dust = [[random.uniform(0, WIDTH), random.uniform(0, HEIGHT), random.uniform(6, 20)] for _ in range(70)]
        self.reset()

    def reset(self):
        self.prev = None
        self.last = time.perf_counter()
        self.t = self.dt = 0.0
        self.particles, self.rings, self.texts, self.confetti = [], [], [], []
        self.trail = []
        self.shake = self.flash = 0.0
        self.flash_colour = FG
        self.paddle_hit = [0.0, 0.0]  # squash and flash timers per side
        self.score_pop = [0.0, 0.0]
        self.ball_pop = 0.0
        self.line_pulse = 0.0
        self.paddle_trail = [[], []]

    # --- prepared once -------------------------------------------------------------------------

    def _background(self):
        """Gradient, faint grid, scanlines and a vignette, all baked into one opaque surface."""
        w, h = self.size
        s = self.view.scale
        bg = pygame.Surface((w, h))
        for y in range(h):
            bg.fill(mix((12, 12, 22), (26, 18, 36), y / h), (0, y, w, 1))
        for x in range(0, WIDTH + 1, 40):
            pygame.draw.line(bg, (30, 30, 48), (x * s, 0), (x * s, h))
        for y in range(0, HEIGHT + 1, 40):
            pygame.draw.line(bg, (30, 30, 48), (0, y * s), (w, y * s))
        for y in range(0, h, 3):
            bg.fill((225, 225, 225), (0, y, w, 1), special_flags=pygame.BLEND_MULT)
        small = pygame.Surface((64, 48), pygame.SRCALPHA)
        for y in range(48):
            for x in range(64):
                d = math.hypot((x - 31.5) / 32, (y - 23.5) / 24)
                small.set_at((x, y), (0, 0, 0, round(min(1, max(0, d - 0.55) * 1.6) * 200)))
        bg.blit(pygame.transform.smoothscale(small, (w, h)), (0, 0))
        return bg

    def dot(self, colour, size):
        """Brightness levels of a glow dot `size` field units across, made on first use."""
        key = (colour, size)
        if key not in self.sprites:
            px = max(2, round(size * self.view.scale))
            self.sprites[key] = levels(glow((px, px), colour, max(1, px // 2)))
        return self.sprites[key]

    # --- events --------------------------------------------------------------------------------

    def _events(self, state):
        prev = self.prev
        self.prev = state
        if prev is None:
            return
        (x, y), (vx, vy) = state["ball"], state["vel"]
        if sum(state["score"]) == sum(prev["score"]) + 1:
            scorer = 0 if state["score"][0] > prev["score"][0] else 1
            self._goal(scorer, prev["ball"])
        elif state["score"] != prev["score"]:  # a new match
            self.trail.clear()
        elif state["hits"] > prev["hits"]:
            self._hit(0 if vx > 0 else 1, x, y, (vx, vy))
        elif vy * prev["vel"][1] < 0 and min(y, HEIGHT - y) < 60:
            self._wall(x, R if y < HEIGHT / 2 else HEIGHT - R, 1 if y < HEIGHT / 2 else -1)

    def _burst(self, x, y, n, colour, speed, direction=None, spread=math.pi, life=0.5, size=6, gravity=0.0):
        for _ in range(n):
            angle = (direction if direction is not None else 0) + random.uniform(-spread, spread)
            v = speed * random.uniform(0.3, 1)
            self.particles.append(Particle(x, y, v * math.cos(angle), v * math.sin(angle),
                                           life * random.uniform(0.6, 1.2), colour,
                                           size * random.uniform(0.6, 1.3), gravity=gravity))
        del self.particles[:-MAX_PARTICLES]

    def _hit(self, side, x, y, vel):
        colour = SIDES[side]
        self._burst(x, y, 28, colour, 420, math.atan2(vel[1], vel[0]), 0.9)
        self._burst(x, y, 10, FG, 250, math.atan2(vel[1], vel[0]), 1.4, life=0.3, size=4)
        self.rings.append([x, y, 0.0, 0.35, colour, 70])
        self.texts.append([PADDLE_X[side] + (75 if side == 0 else -75), y - 45, 0.0, side])
        self.paddle_hit[side] = 0.18
        self.ball_pop = 0.15
        self.line_pulse = 0.3
        self.shake = max(self.shake, 0.08)

    def _goal(self, scorer, ball):
        x = min(max(ball[0], 0), WIDTH)
        colour = SIDES[scorer]
        inward = 0 if x < WIDTH / 2 else math.pi
        self._burst(x, ball[1], 90, colour, 650, inward, 1.3, life=0.9, size=8)
        self._burst(x, ball[1], 30, FG, 400, inward, 1.5, life=0.6, size=5)
        self.rings.append([x, ball[1], 0.0, 0.6, colour, 220])
        self.rings.append([WIDTH / 2, HEIGHT / 2, -0.25, 0.5, FG, 60])  # the serve, a moment later
        self.score_pop[scorer] = 0.45
        self.shake, self.flash, self.flash_colour = 0.4, 0.2, colour
        self.trail.clear()

    def _wall(self, x, y, down):
        self._burst(x, y, 10, WALL, 220, math.pi / 2 * down, 1.0, life=0.3, size=4)

    # --- per frame -----------------------------------------------------------------------------

    def _advance(self):
        now = time.perf_counter()
        dt = min(now - self.last, 0.05)
        self.last = now
        self.t += dt
        self.dt = dt
        for p in self.particles:
            p.age += dt
            p.vx -= p.vx * p.drag * dt
            p.vy += (p.gravity - p.vy * p.drag) * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
        self.particles = [p for p in self.particles if p.age < p.life]
        for ring in self.rings:
            ring[2] += dt
        self.rings = [r for r in self.rings if r[2] < r[3]]
        for text in self.texts:
            text[2] += dt
        self.texts = [t for t in self.texts if t[2] < 0.8]
        self.shake, self.flash, self.ball_pop, self.line_pulse = (
            max(0.0, v - dt) for v in (self.shake, self.flash, self.ball_pop, self.line_pulse))
        self.paddle_hit = [max(0.0, v - dt) for v in self.paddle_hit]
        self.score_pop = [max(0.0, v - dt) for v in self.score_pop]
        return dt

    def _add(self, screen, sprites, k, x, y):
        """Add a sprite centred on field point (x, y) at brightness k (0..1)."""
        if k <= 0:
            return
        surf = sprites[min(LEVELS - 1, int(k * LEVELS))]
        screen.blit(surf, surf.get_rect(center=self.view.point(x, y)), special_flags=pygame.BLEND_ADD)

    def draw(self, screen, state, me=None):
        """A whole game frame, replacing client.draw."""
        dt = self._advance()
        self._events(state)
        view, s = self.view, self.view.scale
        players = state.get("players", [])
        (bx, by), (vx, vy) = state["ball"], state["vel"]
        speed = math.hypot(vx, vy)
        heat = (speed - BALL_SPEED) / (MAX_SPEED - BALL_SPEED)
        step = min(7, max(0, round(heat * 7)))  # the ball's colour in 8 steps, so its sprites are made once
        over = game.winner(state) is not None
        if not over:
            self.confetti.clear()

        if self.shake > 0:
            amount = 14 * self.shake
            view.offset = (random.uniform(-amount, amount) * s, random.uniform(-amount, amount) * s)
        screen.fill(BG)
        screen.blit(self.background, view.offset)

        # centre line, brighter just after a hit
        line = mix(DIM, FG, self.line_pulse / 0.3 * 0.6)
        for y in range(0, HEIGHT, 30):
            pygame.draw.rect(screen, line, view.box(WIDTH / 2, y + 10, 4, 16), border_radius=round(2 * s))

        # scores pop when they change, names under them
        big, small = view.fonts
        for side, (score, x) in enumerate(zip(state["score"], (WIDTH / 4, WIDTH * 3 / 4))):
            key = (side, score)
            if key not in self.scores:
                self.scores[key] = big.render(str(score), True, mix(SIDES[side], FG, 0.5))
            label = self.scores[key]
            pop = self.score_pop[side] / 0.45
            if pop > 0:
                label = pygame.transform.smoothscale_by(label, 1 + 0.8 * pop * pop)
            screen.blit(label, label.get_rect(center=view.point(x, 50)))
            if side < len(players):
                mine = players[side] == me
                name = small.render(players[side] + (" (you)" if mine else ""), True, FG if mine else DIM)
                screen.blit(name, name.get_rect(center=view.point(x, 90)))

        # paddles: afterimages when moving, a glow, then the paddle (or cat), squashed and lit on a hit
        for side, (x, y, cat) in enumerate(zip(PADDLE_X, state["paddles"], view.cats)):
            history = self.paddle_trail[side]
            history.append(y)
            del history[:-8]
            for i, ghost in enumerate(history[:-1:2]):
                if abs(ghost - y) > 3:
                    pygame.draw.rect(screen, mix(BG, SIDES[side], 0.12 + 0.05 * i),
                                     view.box(x, ghost, PADDLE_W, PADDLE_H), border_radius=round(6 * s))
            hit = self.paddle_hit[side] / 0.18
            glow_surf = self.paddle_glows[side]
            screen.blit(glow_surf, glow_surf.get_rect(center=view.point(x, y)), special_flags=pygame.BLEND_ADD)
            w, h = PADDLE_W * (1 + 0.6 * hit), PADDLE_H * (1 - 0.12 * hit)
            if cat:
                sprite = pygame.transform.smoothscale_by(cat, (1 + 0.25 * hit, 1 - 0.12 * hit)) if hit else cat
                screen.blit(sprite, sprite.get_rect(center=view.point(x, y)))
            else:
                pygame.draw.rect(screen, mix(SIDES[side], FG, 0.35 + 0.65 * hit), view.box(x, y, w, h),
                                 border_radius=round(6 * s))

        # ball trail, longer and hotter as it speeds up; embers when it is fast
        if self.trail and math.dist(self.trail[-1], (bx, by)) > 100:
            self.trail.clear()  # it was served from the centre: don't draw a streak across the field
        if not over:
            self.trail.append((bx, by))
        del self.trail[:-round(10 + 14 * max(0, heat))]
        colour = heat_colour(step / 7)
        trail_dot = self.dot(colour, 12)
        for i, (tx, ty) in enumerate(self.trail[:-1]):
            self._add(screen, trail_dot, 0.6 * (i + 1) / len(self.trail), tx, ty)
        if heat > 0.4 and not over and random.random() < heat * 60 * dt:
            self.particles.append(Particle(bx, by, -vx * 0.1 + random.uniform(-40, 40), -vy * 0.1 + random.uniform(-40, 40),
                                           0.4, colour, 5, drag=2))

        for p in self.particles:
            self._add(screen, self.dot(p.colour, round(p.size)), 1 - p.age / p.life, p.x, p.y)

        for x, y, age, life, ring_colour, radius in self.rings:
            if age < 0:
                continue
            k = age / life
            pygame.draw.aacircle(screen, mix(BG, ring_colour, (1 - k) * 0.9), view.point(x, y),
                                 radius * (0.2 + 0.8 * math.sqrt(k)) * s, max(1, round(3 * s * (1 - k))))

        # the ball itself: glow, then a core that pops on a hit
        self._add(screen, self.ball_glows[step], 0.8 + 0.2 * math.sin(self.t * 12), bx, by)
        pygame.draw.aacircle(screen, mix(colour, FG, 0.5), view.point(bx, by), R * (1 + 0.7 * self.ball_pop / 0.15) * s)

        for x, y, age, side in self.texts:
            label = self.meows[side]
            label.set_alpha(round(255 * (1 - age / 0.8)))
            screen.blit(label, label.get_rect(center=view.point(x, y - 50 * age)))

        if self.flash > 0:
            k = (self.flash / 0.2) ** 2 * 0.25
            screen.fill(tuple(round(c * k) for c in self.flash_colour), special_flags=pygame.BLEND_ADD)
        view.offset = (0, 0)

    # --- screens -------------------------------------------------------------------------------

    def start_screen(self, screen, status, hint):
        dt = self._advance()
        view, s = self.view, self.view.scale
        screen.blit(self.background, (0, 0))
        dust = self.dot(DUST, 4)
        for d in self.dust:
            d[1] -= d[2] * dt
            if d[1] < 0:
                d[0], d[1] = random.uniform(0, WIDTH), HEIGHT
            self._add(screen, dust, 0.35 + 0.25 * math.sin(self.t * 2 + d[0]), d[0], d[1])

        bob = 6 * math.sin(self.t * 2)
        self._add(screen, self.title_glows, 0.6 + 0.3 * math.sin(self.t * 3), WIDTH / 2, 200 + bob)
        title = view.title.render("PONG", True, FG)
        screen.blit(title, title.get_rect(center=view.point(WIDTH / 2, 200 + bob)))
        for side, (cat, x) in enumerate(zip(view.cats, (WIDTH / 2 - 270, WIDTH / 2 + 270))):
            y = 200 + 8 * math.sin(self.t * 2.5 + side * math.pi)
            if cat:
                screen.blit(cat, cat.get_rect(center=view.point(x, y)))
            else:
                self._add(screen, self.dot(SIDES[side], 30), 0.8, x, y)
                pygame.draw.rect(screen, mix(SIDES[side], FG, 0.35), view.box(x, y, PADDLE_W, PADDLE_H),
                                 border_radius=round(6 * s))

        for line, colour, y in ((status, FG, 360), (hint, DIM, 410)):
            label = view.fonts[1].render(line, True, colour)
            screen.blit(label, label.get_rect(center=view.point(WIDTH / 2, y)))
        self.prev = None  # the next game frame starts without a stale state to compare with

    def end_overlay(self, screen, state, players, hint):
        """Drawn over the last game frame: shade, winner in their colour, confetti."""
        view = self.view
        side = game.winner(state)
        screen.blit(view.shade, (0, 0))
        if len(self.confetti) < 260:
            self.confetti += [Confetti() for _ in range(4)]
        dt = self.dt  # draw() has just advanced the clock for this frame
        for c in self.confetti:
            c.x += c.vx * dt
            c.y += c.vy * dt
            c.phase += c.spin * dt
            if c.y > HEIGHT + 20:
                c.x, c.y = random.uniform(0, WIDTH), random.uniform(-40, -10)
            w = max(1.0, abs(math.cos(c.phase)) * c.size)
            pygame.draw.rect(screen, c.colour, view.box(c.x, c.y, w, c.size * 0.6))

        winner = view.fonts[0].render(f"{players[side]} wins", True, SIDES[side])
        self._add(screen, self.dot(SIDES[side], 160), 0.5 + 0.2 * math.sin(self.t * 4), WIDTH / 2, 250)
        screen.blit(winner, winner.get_rect(center=view.point(WIDTH / 2, 250)))
        for line, colour, y in ((f"{state['score'][0]} - {state['score'][1]}", FG, 310), (hint, DIM, 400)):
            label = view.fonts[1].render(line, True, colour)
            screen.blit(label, label.get_rect(center=view.point(WIDTH / 2, y)))
