"""THE one home for human-like input timing and motion (fork feature).

Toggleable via ``spawn_browser(humanize=True)`` — off by default, so nothing
about the upstream behavior changes unless a caller opts in.

**Why this exists.** ``click_element`` dispatches one trusted click at an
element's exact geometric center with no preceding cursor motion, and
``type_text`` sends one key event per character separated by a single fixed
delay (see ``text_entry.type_characters``). Both are correct at the protocol
level — real, trusted, hit-tested CDP input — but neither one *moves* or
*paces* the way a human does, and that gap is a known avenue for anti-bot
systems that profile pointer trajectories or keystroke-interval distributions
(most of the 10 public detectors this fork was evaluated against do not check
this; some commercial stacks do).

**Where the numbers come from.** Every timing/motion figure below is a
quantile table derived from a real recorded human session — a front page
visit, scrolling, clicking into an article, reading, and posting a comment —
captured by a separate, privacy-respecting recorder. Its own trace carries
``"textCaptured": false`` and ``"keyValuesCaptured": false``: no page text and
no actual key VALUES were ever recorded, only event timing, pointer
coordinates and a coarse key CLASS (``printable`` / ``space`` / ``modifier`` /
``delete``). That is also this module's own discipline for the same reason
F-869/F-873/F-876/F-877 hold it elsewhere in this tree: nothing here ever
needs to know, and never carries, what was actually typed or clicked.

**What "sampling a quantile table" means and why not ``random.uniform``.**
Human timing is heavy-tailed and skewed, not flat: most keystrokes in the
source trace land 90-190ms apart, but the p95 is over a second (a pause to
think, or to glance back at the source text) and a flat ``random.uniform(min,
max)`` would put as much weight on that tail as on the common case — which is
itself a distinguishable, too-uniform signature. :func:`_sample_quantiles`
instead draws from the empirical CDF (percentile -> value points,
piecewise-linear between them), so the SHAPE recorded in the trace — the
common case dense, the tail rare — is what gets reproduced.

**What this is not.** A single Bezier curve with per-step delays drawn from a
real trace is a large improvement over an instant teleport-and-click, but it
is still a model, not a replay: it will not fool a detector that fingerprints
against real minimum-jerk human-motion statistics in detail (acceleration/
deceleration shape, correlated micro-tremor). That is future work, named
rather than silently claimed.
"""

from __future__ import annotations

import itertools
import math
import random

#: (percentile 0-100, value) points, piecewise-linear between them. Derived
#: from ~1700 recorded events (see module docstring); a class with few samples
#: (``modifier``, ``delete``) says so in its own comment.

#: Per-character keydown->keydown interval, milliseconds, by key class.
KEYSTROKE_DELAY_MS: dict[str, list[tuple[float, float]]] = {
    # n=69 — the common path, and the only one with enough samples to trust
    # the tail.
    "printable": [
        (0, 46), (25, 94), (50, 111), (75, 188), (95, 1093), (100, 1470)
    ],
    # n=10 — a bit slower on average (thumb reach), longer tail (word pause).
    "space": [
        (0, 47), (25, 95), (50, 218), (75, 876), (95, 1844), (100, 1844)
    ],
    # n=4 — thin; a Shift press ahead of a capital or punctuation character.
    "modifier": [
        (0, 32), (25, 172), (50, 469), (75, 1390), (95, 1390), (100, 1390)
    ],
    # n=4 — thin; a Backspace/Delete correction.
    "delete": [
        (0, 173), (25, 234), (50, 469), (75, 593), (95, 593), (100, 593)
    ],
}

#: Mouse micro-step pacing: consecutive ``pointermove`` samples were 14-46ms
#: apart (n=632; ~60Hz with an occasional longer gap), the unit each
#: intermediate point along a humanized path is spaced by.
MOUSE_STEP_DT_MS: list[tuple[float, float]] = [
    (0, 14), (25, 15), (50, 16), (75, 17), (95, 46), (100, 171)
]

#: Recorded distance (px) covered per micro-step (n=632) — used only to pick
#: how many intermediate points a path gets, not as a hard per-step distance.
_MOUSE_STEP_PX_MEDIAN = 3.6
_MOUSE_STEP_PX_P75 = 9.2

#: pointerdown->pointerup hold time, milliseconds. n=3 in the source trace —
#: too thin to trust the tail, so this is a min/median/max sketch rather than
#: a five-point quantile table; still far better than the 0ms this replaces.
CLICK_DWELL_MS: list[tuple[float, float]] = [(0, 61), (50, 77), (100, 94)]

#: Bounds on how many intermediate ``mouseMoved`` points a humanized path
#: gets, regardless of distance — enough to look continuous, few enough that
#: a full-page-width move does not cost dozens of CDP round trips.
_MIN_PATH_POINTS = 3
_MAX_PATH_POINTS = 20

#: Below this distance (px) a move is answered as a single point with no
#: delay — the source trace shows no intermediate samples at that scale.
_STRAIGHT_LINE_PX = 2.0


def _sample_quantiles(points: list[tuple[float, float]]) -> float:
    """Draw one value from the empirical CDF *points* (percentile, value).

    ``random``, not ``secrets``: this is motion/timing pacing, not a security
    boundary, so a non-cryptographic PRNG is the right and faster tool.
    """
    p = random.uniform(0, 100)  # noqa: S311
    for (p_lo, v_lo), (p_hi, v_hi) in itertools.pairwise(points):
        if p_lo <= p <= p_hi:
            if p_hi == p_lo:
                return v_lo
            frac = (p - p_lo) / (p_hi - p_lo)
            return v_lo + frac * (v_hi - v_lo)
    return points[-1][1]


def sample_keystroke_delay(key_class: str = "printable") -> float:
    """Seconds to wait before the *next* key, sampled for *key_class*.

    An unrecognized class (there are only four) falls back to ``printable``,
    the common case, rather than raising — this is pacing, not validation.
    """
    table = KEYSTROKE_DELAY_MS.get(key_class, KEYSTROKE_DELAY_MS["printable"])
    return _sample_quantiles(table) / 1000.0


def sample_click_dwell() -> float:
    """Seconds to hold the mouse button down before releasing it."""
    return _sample_quantiles(CLICK_DWELL_MS) / 1000.0


#: Per-tab last known pointer position, so a path has somewhere real to start
#: from instead of teleporting from nowhere. Keyed by ``id(tab)`` on
#: ``element_resolution``'s precedent (a ``Tab`` is unhashable — ``Connection``
#: defines ``__eq__`` without ``__hash__``). Unbounded for the life of the
#: process; a few floats per tab is not worth a weakref-finalizer here.
_last_pointer: dict[int, tuple[float, float]] = {}


def remember_pointer(tab: object, point: tuple[float, float]) -> None:
    """Record where the (humanized) pointer last was on *tab*."""
    _last_pointer[id(tab)] = point


def last_pointer(tab: object, fallback: tuple[float, float]) -> tuple[float, float]:
    """Where the pointer was last seen on *tab*, or *fallback* if never."""
    return _last_pointer.get(id(tab), fallback)


def natural_mouse_path(
    start: tuple[float, float], end: tuple[float, float]
) -> list[tuple[float, float, float]]:
    """A curved path from *start* to *end* as ``(x, y, delay_before_point)``.

    One quadratic Bezier control point, offset perpendicular to the straight
    line by a modest, distance-proportional random fraction — a real
    hand-to-target movement curves rather than travelling a straight ray. The
    point COUNT scales with distance (more points for a longer move, the same
    density the source trace's own ~60Hz sampling shows) and is clamped so a
    full-viewport move costs at most :data:`_MAX_PATH_POINTS` CDP round trips.
    Each point's delay is drawn from :data:`MOUSE_STEP_DT_MS`, independent of
    the curve shape — pacing and path are two different recorded facts.

    A move under :data:`_STRAIGHT_LINE_PX` answers a single point with no
    delay: at that distance the recorded trace shows no intermediate samples
    at all.
    """
    x0, y0 = start
    x1, y1 = end
    distance = math.hypot(x1 - x0, y1 - y0)
    if distance < _STRAIGHT_LINE_PX:
        return [(x1, y1, 0.0)]

    step_target_px = (_MOUSE_STEP_PX_MEDIAN + _MOUSE_STEP_PX_P75) / 2
    steps = int(
        min(_MAX_PATH_POINTS, max(_MIN_PATH_POINTS, round(distance / step_target_px)))
    )

    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1.0
    perp_x, perp_y = -dy / length, dx / length
    # random, not secrets: path shape, not a security boundary (see
    # _sample_quantiles).
    side = random.choice((-1, 1))  # noqa: S311
    offset_frac = random.uniform(0.05, 0.18)  # noqa: S311
    ctrl_x = (x0 + x1) / 2 + perp_x * distance * offset_frac * side
    ctrl_y = (y0 + y1) / 2 + perp_y * distance * offset_frac * side

    path: list[tuple[float, float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * ctrl_x + t**2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * ctrl_y + t**2 * y1
        delay = _sample_quantiles(MOUSE_STEP_DT_MS) / 1000.0
        path.append((bx, by, delay))
    # The curve's own t=1 point already lands exactly on (x1, y1); no snap needed.
    return path
