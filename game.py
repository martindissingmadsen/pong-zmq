"""Pong game logic: pure functions on a state dict, no networking or graphics.

Implements the "Game interface" contract in PLAN.md. The state has exactly the game
fields of the state message; the server adds seq, ts and players:
    {"ball": [x, y], "vel": [vx, vy], "paddles": [yL, yR], "score": [l, r], "hits": n}

Positions are centres in game units, velocities in units per second.
"""
import math
import random

WIDTH, HEIGHT = 800, 600
PADDLE_W, PADDLE_H = 20, 100
PADDLE_X = (30, 770)  # centre x of the left and right paddle
PADDLE_SPEED = 400
BALL_SIZE = 12  # the ball's hitbox is a square of this side
BALL_SPEED = 300  # speed at each serve
SPEEDUP = 1.05  # per paddle hit
MAX_SPEED = 700
WIN_SCORE = 11

MAX_SERVE_ANGLE = math.radians(45)  # off the horizontal
MAX_BOUNCE = math.radians(60)  # off the horizontal, when the ball hits the paddle's very end
R = BALL_SIZE / 2


def new_state():
    state = {"paddles": [HEIGHT / 2, HEIGHT / 2], "score": [0, 0], "hits": 0}
    return serve(state, toward=random.choice((-1, 1)))


def serve(state, toward):
    """Ball back to the centre at BALL_SPEED, heading for side `toward` (-1 left, 1 right)."""
    angle = random.uniform(-MAX_SERVE_ANGLE, MAX_SERVE_ANGLE)
    vel = [toward * BALL_SPEED * math.cos(angle), BALL_SPEED * math.sin(angle)]
    return {**state, "ball": [WIDTH / 2, HEIGHT / 2], "vel": vel}


def winner(state):
    """0 or 1 once a side has WIN_SCORE points, else None."""
    for side, points in enumerate(state["score"]):
        if points >= WIN_SCORE:
            return side
    return None


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def paddle_at(rec, t):
    """Where a paddle is at time t, from its last key change: at y at time rec["t"], then
    moving with dir. Server and clients all compute a paddle they don't control this way."""
    half = PADDLE_H / 2
    return clamp(rec["y"] + rec["dir"] * PADDLE_SPEED * max(0.0, t - rec["t"]), half, HEIGHT - half)


def face_x(side):
    """x of the paddle face the ball meets on that side."""
    return PADDLE_X[side] + (PADDLE_W / 2 if side == 0 else -PADDLE_W / 2)


def bounce(side, y_hit, paddle_y, vel):
    """The ball's velocity after meeting `side`'s paddle face at y_hit, or None for a miss.

    Straight back from the paddle's centre, up to MAX_BOUNCE off the horizontal at its ends,
    SPEEDUP faster, capped at MAX_SPEED."""
    offset = (y_hit - paddle_y) / (PADDLE_H / 2 + R)
    if abs(offset) > 1:
        return None
    speed = min(math.hypot(*vel) * SPEEDUP, MAX_SPEED)
    angle = offset * MAX_BOUNCE
    return [(1 if side == 0 else -1) * speed * math.cos(angle), speed * math.sin(angle)]


def fly(ball, vel, dt):
    """Position and velocity dt seconds on, bouncing off the top and bottom walls but not
    paddles or goals. Exact for any dt, so every machine computes the same path."""
    span = HEIGHT - 2 * R
    p = (ball[1] - R + vel[1] * dt) % (2 * span)  # distance travelled, folded back at each wall
    y, vy = (R + p, vel[1]) if p <= span else (R + 2 * span - p, -vel[1])
    return [ball[0] + vel[0] * dt, y], [vel[0], vy]


def meet(ball, vel):
    """Where a ball flying from `ball` with `vel` meets the paddle face it is heading for:
    (seconds until then, side, position, velocity there). None if it isn't moving sideways."""
    if vel[0] == 0:
        return None
    side = 0 if vel[0] < 0 else 1
    edge = ball[0] - R if side == 0 else ball[0] + R
    dt = (face_x(side) - edge) / vel[0]
    pos, v = fly(ball, vel, dt)
    return dt, side, pos, v


def step(state, dir_left, dir_right, dt):
    """Return the state dt seconds later. dir is -1 up, 0 still, 1 down. Works for any dt."""
    if winner(state) is not None:
        return state

    half = PADDLE_H / 2
    paddles = [clamp(y + d * PADDLE_SPEED * dt, half, HEIGHT - half)
               for y, d in zip(state["paddles"], (dir_left, dir_right))]

    (x0, y0), (vx, vy) = state["ball"], state["vel"]
    x, y = x0 + vx * dt, y0 + vy * dt

    # top and bottom walls: reflect the overshoot back into the field
    if y < R:
        y, vy = 2 * R - y, abs(vy)
    elif y > HEIGHT - R:
        y, vy = 2 * (HEIGHT - R) - y, -abs(vy)

    hits = state["hits"]
    side = 0 if vx < 0 else 1
    face = face_x(side)
    edge0, edge = (x0 - R, x - R) if side == 0 else (x0 + R, x + R)
    # swept test: did the ball's leading edge cross the paddle face during this step?
    if (edge0 - face) * (edge - face) <= 0 and edge0 != edge:
        t = (face - edge0) / (edge - edge0)
        y_hit = y0 + (y - y0) * t
        if after := bounce(side, y_hit, paddles[side], (vx, vy)):
            vx, vy = after
            x, y = face + (R if side == 0 else -R), y_hit
            hits += 1

    state = {**state, "ball": [x, y], "vel": [vx, vy], "paddles": paddles, "hits": hits}

    if x < -R or x > WIDTH + R:
        scorer = 1 if x < 0 else 0
        score = list(state["score"])
        score[scorer] += 1
        state = {**state, "score": score}
        if winner(state) is not None:
            return {**state, "ball": [WIDTH / 2, HEIGHT / 2], "vel": [0, 0]}
        return serve(state, toward=-1 if scorer == 1 else 1)  # serve to the player who conceded
    return state
