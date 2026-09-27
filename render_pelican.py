"""Render the pelican animations and pack them for the firmware.

Three scenes, cycled on the device with the KEY button:
  idle - everyday default: kick off the ground, coast, weave near and far.
  ride - "即将重置！使劲蹬！": a big pelican pedalling a kid's bike flat out.
  rest - "已重置！歇一歇…":   collapsed on its back, bike upside down beside it.

Frames are drawn at 4x and box-filtered down to 240x240 for clean edges,
then stored as RGB565 run-length data in include/pelican_frames.h.
Previews go to preview/ (a GIF and a contact sheet per scene).

Run: uv run --with pillow python render_pelican.py
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

S = 4                 # supersampling factor
SIZE = 240
N = 12                # frames per loop, both scenes
RIDE_MS, REST_MS = 70, 110
FONT_CANDIDATES = [
    Path.home() / 'Library/Fonts/Alibaba-PuHuiTi-Heavy.ttf',
    Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
    Path('/System/Library/Fonts/STHeiti Medium.ttc'),
]

# Warm palette: the panel's white point is cool, so everything leans warm.
PAPER = (250, 228, 185)
SUN = (247, 207, 148)
DUST = (236, 196, 140)
STEAM = (205, 170, 125)
INK = (40, 30, 36)
WHITE = (255, 252, 240)
BELLY_SHADE = (238, 226, 212)
ORANGE = (255, 135, 0)
POUCH = (255, 184, 70)
POUCH_CREASE = (235, 150, 40)
LEG = (255, 140, 20)
LEG_FAR = (205, 100, 10)
RED = (225, 45, 25)
TEAL = (0, 138, 136)
SWEAT = (110, 190, 240)
BLUSH = (255, 160, 140)

OUTLINE = 2.5
GEAR = 2              # wheel turns per pedal turn; 8 spokes -> reads as forward roll
# A big bird on a kid's bike: tiny wheels, training wheel, safety flag.
WHEEL_R = 16
REAR, FRONT, BB = (84, 212), (152, 212), (116, 214)
GROUND_Y = REAR[1] + WHEEL_R
TRAINER = (62, GROUND_Y - 6)
CRANK = 7
THIGH = SHIN = 34     # far too long for this bike, so the knees stick out
BAR_END = (156, 160)


def bez(*ctrl, n=20):
    pts = []
    for i in range(n + 1):
        t = i / n
        p = list(ctrl)
        while len(p) > 1:
            p = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                 for a, b in zip(p, p[1:])]
        pts.append(p[0])
    return pts


def path(start, *segments):
    """Chain of cubic segments, each (ctrl1, ctrl2, end)."""
    pts, cur = [start], start
    for c1, c2, end in segments:
        pts += bez(cur, c1, c2, end)[1:]
        cur = end
    return pts


def shift(pts, dx=0, dy=0):
    return [(x + dx, y + dy) for x, y in pts]


def rotator(pivot, degrees, k=1.0, to=None):
    """Rotate about pivot, scale by k, and move pivot to `to`."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    tx, ty = to or pivot

    def tf(p):
        dx, dy = p[0] - pivot[0], p[1] - pivot[1]
        return tx + k * (dx * c - dy * s), ty + k * (dx * s + dy * c)
    return tf


class Pen:
    """Drawing helpers in 240-space, optionally through a rotate/scale transform."""

    def __init__(self, draw, tf=None, k=1.0):
        self.d = draw
        self.tf = tf or (lambda p: p)
        self.k = k

    def _map(self, pts):
        return [(x * S, y * S) for x, y in map(self.tf, pts)]

    def stroke(self, pts, color, w):
        sp = self._map(pts)
        w *= self.k
        self.d.line(sp, fill=color, width=round(w * S), joint='curve')
        r = w * S / 2
        for x, y in (sp[0], sp[-1]):
            self.d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def fill(self, pts, color):
        self.d.polygon(self._map(pts), fill=color)

    def shape(self, pts, color, w=OUTLINE):
        self.fill(pts, color)
        self.stroke(pts + [pts[0]], INK, w)

    def disc(self, c, r, color):
        (x, y), = self._map([c])
        R = r * self.k * S
        self.d.ellipse((x - R, y - R, x + R, y + R), fill=color)

    def ring(self, c, r, color, w):
        (x, y), = self._map([c])
        R = r * self.k * S
        self.d.ellipse((x - R, y - R, x + R, y + R), outline=color,
                       width=round(w * self.k * S))

    def dot(self, c, r, color, w=OUTLINE):
        self.disc(c, r + w / 2, INK)
        self.disc(c, r - w / 2, color)

    def tube(self, pts, color, w):
        self.stroke(pts, INK, w + 2 * OUTLINE)
        self.stroke(pts, color, w)


def knee(hip, foot, l1, l2):
    dx, dy = foot[0] - hip[0], foot[1] - hip[1]
    d = min(math.hypot(dx, dy), l1 + l2 - 0.01)
    a = math.atan2(dy, dx)
    b = math.acos(max(-1, min(1, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    return hip[0] + l1 * math.cos(a - b), hip[1] + l1 * math.sin(a - b)


def load_font(px):
    for f in FONT_CANDIDATES:
        if f.exists():
            return ImageFont.truetype(str(f), px)
    raise SystemExit('no CJK font found')


FONT_SMALL = load_font(15 * S)
FONT_MID = load_font(26 * S)
FONT_BIG = load_font(44 * S)
# Hand-picked shake so it looks nervous rather than random noise.
SHAKE = [(0, 0, -6), (1.5, -1, -4), (-1, 1, -7), (1, 1.5, -5), (-1.5, -0.5, -4), (0.5, -1.5, -7),
         (-0.5, 1, -5), (1.5, 0.5, -6), (-1, -1, -4), (0, 1.5, -7), (1, -0.5, -5), (-1.5, 0.5, -6)]


# ---------------------------------------------------------------- shared parts

def draw_ground(pen, scroll):
    pen.stroke([(-4, GROUND_Y), (244, GROUND_Y)], INK, OUTLINE)
    for i in range(4):
        x = (i * 84 - scroll) % 336 - 40
        pen.stroke([(x, GROUND_Y + 6), (x + 18, GROUND_Y + 6)], INK, 2)
        px = (i * 84 + 50 - scroll) % 336 - 40
        pen.disc((px, GROUND_Y + 10), 1.4, INK)


def draw_wheel(pen, c, r, angle, spokes=8):
    pen.ring(c, r - 2, INK, 4)
    for k in range(spokes):
        a = angle + k * 2 * math.pi / spokes
        pen.stroke([c, (c[0] + (r - 4) * math.cos(a), c[1] + (r - 4) * math.sin(a))], INK, 1.1)
    pen.dot(c, 2.4, TEAL, 1.8)


def draw_bike(pen, trainer_angle):
    seat_top, head_top, head_bot = (102, 186), (142, 184), (144, 196)
    pen.tube([REAR, (TRAINER[0] + 2, TRAINER[1] - 2)], INK, 1.5)
    draw_wheel(pen, TRAINER, 6, trainer_angle, spokes=3)
    pen.stroke([(BB[0], BB[1] - 5), (REAR[0], REAR[1] - 3)], INK, 1.2)
    pen.stroke([(BB[0], BB[1] + 5), (REAR[0], REAR[1] + 3)], INK, 1.2)
    tubes = [
        [BB, seat_top],
        [BB, (144, 190)],
        [BB, REAR],
        [(103, 192), REAR],
        bez(head_bot, (147, 202), (150, 206), FRONT, n=8),
    ]
    for t in tubes:
        pen.stroke(t, INK, 4 + 2 * OUTLINE)
    pen.stroke([head_top, head_bot], INK, 6 + 2 * OUTLINE)
    for t in tubes:
        pen.stroke(t, TEAL, 4)
    pen.stroke([head_top, head_bot], TEAL, 6)
    # Tall kid-bike riser bars.
    pen.tube([head_top, (140, 164), BAR_END], INK, 2)
    pen.shape([(92, 184), (112, 183), (113, 188), (95, 191)], INK, 1.5)


def draw_pedal(pen, p):
    pen.stroke([(p[0] - 5, p[1] + 4.5), (p[0] + 5, p[1] + 4.5)], INK, 3)


def draw_head(pen, hx, hy, sag, swing, eye, spin=0.0):
    """Pouch, bill and face around head centre (hx, hy), drawn facing right.

    The head disc itself is part of the body silhouette and drawn by the caller;
    pass a rotated pen to tip the whole face over.
    """
    pouch = path((hx + 10, hy + 3),
                 ((hx + 28, hy + 8), (hx + 46, hy + 11), (hx + 58, hy + 11)),
                 ((hx + 52 + swing, hy + 34 + sag), (hx + 22 + swing, hy + 40 + sag),
                  (hx + 11, hy + 12)))
    pen.shape(pouch, POUCH)
    pen.stroke(bez((hx + 20 + swing * 0.5, hy + 24 + sag * 0.6), (hx + 30 + swing, hy + 30 + sag),
                   (hx + 42 + swing, hy + 26 + sag * 0.8), n=8), POUCH_CREASE, 1.4)
    bill = path((hx + 8, hy - 7), ((hx + 28, hy - 4), (hx + 48, hy + 2), (hx + 62, hy + 7)),
                ((hx + 67, hy + 9), (hx + 65, hy + 14), (hx + 58, hy + 11)),
                ((hx + 40, hy + 8), (hx + 22, hy + 5), (hx + 9, hy + 5)))
    pen.shape(bill, ORANGE)
    pen.disc((hx - 3, hy + 8), 3.4, BLUSH)
    if eye == 'strain':
        # Bulging eye, pinprick pupil staring straight ahead.
        pen.dot((hx + 2, hy - 3), 7, WHITE, 2)
        pen.disc((hx + 6.5, hy - 3.5), 1.7, INK)
        pen.stroke(bez((hx - 6, hy - 13), (hx, hy - 15), (hx + 8, hy - 13), n=6), INK, 2.4)
    elif eye == 'chill':
        # Heavy-lidded, perfectly unbothered.
        pen.dot((hx + 2, hy - 3), 6, WHITE, 2)
        pen.disc((hx + 5, hy - 1.5), 2, INK)
        lid = [(hx + 2 + 7 * math.cos(a), hy - 3 + 7 * math.sin(a))
               for a in [math.pi + i * math.pi / 12 for i in range(13)]]
        pen.fill(lid, WHITE)
        pen.stroke([(hx - 5.5, hy - 2.5), (hx + 9.5, hy - 2.5)], INK, 2)
    else:
        # Dizzy spiral, slowly turning.
        pen.dot((hx + 2, hy - 3), 7, WHITE, 2)
        swirl = [(hx + 2 + (0.4 + 0.24 * i) * math.cos(spin + i * 0.42),
                  hy - 3 + (0.4 + 0.24 * i) * math.sin(spin + i * 0.42)) for i in range(24)]
        pen.stroke(swirl, INK, 1.3)


def text_layer(text, font, color, shadow):
    box = font.getbbox(text)
    w, h = box[2] + 6 * S, box[3] + 6 * S
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if shadow:
        d.text((3 * S, 3 * S), text, font=font, fill=INK)
    d.text((0, 0), text, font=font, fill=color)
    return layer


def draw_notice(img, text):
    """The deadpan system-notice line in the top-left corner."""
    small = text_layer(text, FONT_SMALL, INK, False)
    img.paste(small, (10 * S, 7 * S), small)


def new_canvas():
    img = Image.new('RGB', (SIZE * S, SIZE * S), PAPER)
    return img, ImageDraw.Draw(img)


# ---------------------------------------------------------------- ride scene

def draw_leg(pen, hip, pedal, color, toes=1, scale=1.0):
    k = knee(hip, pedal, THIGH * scale, SHIN * scale)
    pen.tube([hip, k, pedal], color, 4)
    foot = [(pedal[0] - 4 * toes, pedal[1] - 2.5), (pedal[0] + 11 * toes, pedal[1] + 0.5),
            (pedal[0] + 10 * toes, pedal[1] + 3), (pedal[0] - 3 * toes, pedal[1] + 3)]
    pen.shape(foot, color, 2)


def draw_flag(pen, th):
    base, top = (90, 196), (32, 96)
    pen.stroke([base, top], INK, 1.6)
    wave = [3.5 * math.sin(2 * th + k * 1.3) for k in range(3)]
    flag = [top, (top[0] - 13, top[1] + 3 + wave[0]), (top[0] - 26, top[1] + 7 + wave[1]),
            (top[0] - 14, top[1] + 10 + wave[2]), (top[0] - 2, top[1] + 14)]
    pen.shape(flag, ORANGE, 1.8)


def draw_rider(pen, f, th, bob, head_bob, sag, swing, mood='strain'):

    body = shift(path(
        (142, 118),
        ((124, 98), (72, 96), (50, 122)),
        ((40, 142), (52, 178), (84, 184)),
        ((118, 190), (150, 174), (152, 146)),
        ((153, 132), (150, 122), (142, 118)),
    ), dy=bob)
    head = (164, 97 + head_bob)
    neck = bez((140, 126 + bob), (156, 118 + bob), (152, 108 + head_bob), head, n=16)

    tail = shift([(56, 116), (34, 110), (46, 120), (30, 126), (48, 128), (52, 136)], dy=bob)
    pen.shape(tail, WHITE)
    flutter = 2.0 * math.sin(th * 2 + 1)
    for dy, length in ((-10, 18), (-5, 15), (0, 11)):
        pen.stroke(bez((head[0] - 8, head[1] + dy),
                       (head[0] - 14, head[1] + dy - 3 - flutter),
                       (head[0] - 8 - length, head[1] + dy - 2 + flutter), n=8),
                   INK, 2.2)

    # Silhouette in ink first, then fills, so body, neck and head merge.
    pen.fill(body, INK)
    pen.stroke(body + [body[0]], INK, 2 * OUTLINE)
    pen.stroke(neck, INK, 13 + 2 * OUTLINE)
    pen.disc(head, 14 + OUTLINE, INK)
    pen.fill(body, WHITE)
    pen.stroke(neck, WHITE, 13)
    pen.disc(head, 14, WHITE)
    pen.stroke(shift(bez((78, 178), (102, 186), (132, 180), (146, 162), n=12), dy=bob),
               BELLY_SHADE, 3)

    # Wing stretched down to the low grips.
    wy = bob * 0.6
    wing = shift(path(
        (100, 120),
        ((122, 112), (146, 138), (156, 154)),
        ((161, 160), (158, 166), (151, 164)),
        ((132, 158), (110, 148), (98, 138)),
        ((90, 132), (92, 124), (100, 120)),
    ), dy=wy)
    pen.shape(wing, WHITE)
    tip = shift(path((142, 138), ((150, 146), (157, 152), (158, 159)),
                     ((159, 165), (152, 166), (145, 162)),
                     ((138, 156), (136, 146), (142, 138))), dy=wy)
    pen.shape(tip, INK, 1)
    for y0 in (132, 139):
        pen.stroke(shift(bez((106, y0 - 4), (116, y0 - 3), (126, y0 + 2), n=6), dy=wy), INK, 1.6)

    hx, hy = head
    draw_head(pen, hx, hy, sag, swing, mood)
    if mood != 'strain':
        return head

    for offset in (0, 2, 4):
        age = (f + offset) % 6
        x = hx - 18 - age * 8
        y = hy - 12 - age * 1.5 + age * age * 1.4
        pen.shape([(x + 3.5, y - 3), (x - 3, y - 4), (x - 4.5, y), (x - 2, y + 3.5),
                   (x + 2, y + 3)], SWEAT, 1.4)
    return head


def draw_yell(img, f):
    dx, dy, angle = SHAKE[f]
    big = text_layer('使劲蹬！', FONT_BIG, RED, True).rotate(-angle, resample=Image.BICUBIC, expand=True)
    x = (SIZE * S - big.width) // 2 + round((6 + dx) * S)
    img.paste(big, (x, round((5 + dy) * S)), big)


def ghost(img, alpha, draw_fn):
    """Draw onto a see-through layer and lay it over img at the given opacity."""
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw_fn(Pen(ImageDraw.Draw(layer)))
    mask = layer.getchannel('A').point(lambda v: round(v * alpha))
    img.paste(layer.convert('RGB'), (0, 0), mask)


def render_ride(f, leg_speed=4):
    """Everything moves at cruising pace except the legs, which spin leg_speed
    times faster and leave fading copies behind so the spin reads as a blur."""
    th = 2 * math.pi * f / N
    th_leg = th * leg_speed
    img, d = new_canvas()
    pen = Pen(d)
    pen.disc((104, 150), 72, SUN)
    # Speed lines streaking past behind the rider.
    for y, x0, length in ((104, 30, 26), (132, 150, 18), (160, 260, 30), (190, 380, 20)):
        x = (x0 - f * 40) % 480 - 120
        pen.stroke([(x, y), (x + length, y)], INK, 2)
    draw_ground(pen, f * 21)
    for offset in (0, 2, 4):
        age = (f + offset) % 6
        c = (TRAINER[0] - 10 - age * 7, GROUND_Y - 3 - age * 2)
        pen.dot(c, 2.5 + age * 1.2, DUST, 1.6)

    def crank(a, side):
        return BB[0] + side * CRANK * math.cos(a), BB[1] + side * CRANK * math.sin(a)

    bob = 2.0 * math.sin(2 * th)
    far_hip, near_hip = (104, 176 + bob), (110, 178 + bob)
    step = 2 * math.pi * leg_speed / N
    trail = [(th_leg - step * j / 4, a) for j, a in ((3, 0.25), (2, 0.4), (1, 0.55))] \
        if leg_speed > 1 else []

    def far_leg(pn, a):
        p = crank(a, -1)
        pn.tube([BB, p], INK, 2)
        draw_leg(pn, far_hip, p, LEG_FAR)
        draw_pedal(pn, p)

    def near_leg(pn, a):
        p = crank(a, 1)
        draw_leg(pn, near_hip, p, LEG)
        draw_pedal(pn, p)

    draw_flag(pen, th)
    for a, alpha in trail:
        ghost(img, alpha, lambda pn, a=a: far_leg(pn, a))
    far_leg(pen, th_leg)
    for c in (REAR, FRONT):
        draw_wheel(pen, c, WHEEL_R, GEAR * th)
    draw_bike(pen, GEAR * th * 2.6)
    pen.dot(BB, 5, WHITE, 1.8)
    pen.tube([BB, crank(th_leg, 1)], INK, 2)
    draw_rider(pen, f, th, bob, 3.0 * math.sin(2 * th - 0.9),
               6.0 * math.sin(2 * th - 1.8), 4.0 * math.sin(2 * th - 2.4))
    for a, alpha in trail:
        ghost(img, alpha, lambda pn, a=a: near_leg(pn, a))
    near_leg(pen, th_leg)
    draw_notice(img, '即将重置！')
    draw_yell(img, f)
    return img.reduce(S)


# ---------------------------------------------------------------- rest scene

# Flipped so the bar end and saddle both rest on the ground; front wheel in the air.
BIKE_K = 0.7
BIKE_FLIP = rotator(BAR_END, 206.6, k=BIKE_K, to=(156, GROUND_Y - 6))
# The collapsed bird is drawn a touch larger than the rider to fill the frame.
BIRD_K = 1.1
BIRD_GROW = rotator((80, GROUND_Y), 0, k=BIRD_K, to=(84, GROUND_Y))


def draw_flipped_bike(d, f):
    pen = Pen(d, BIKE_FLIP, BIKE_K)
    draw_wheel(pen, REAR, WHEEL_R, 0.2)
    # 90° per loop is two spoke gaps, so the idle spin loops seamlessly.
    draw_wheel(pen, FRONT, WHEEL_R, f * math.pi / 2 / N)
    draw_bike(pen, 0.5)
    pen.dot(BB, 5, WHITE, 1.8)
    for a in (0.9, 0.9 + math.pi):
        p = (BB[0] + CRANK * math.cos(a), BB[1] + CRANK * math.sin(a))
        pen.tube([BB, p], INK, 2)
        draw_pedal(pen, p)


def draw_flat_leg(pen, hip, knee_pt, foot, color):
    pen.tube([hip, knee_pt, foot], color, 4)
    fx, fy = foot
    pen.shape([(fx - 3, fy + 2), (fx + 3, fy - 9), (fx + 8, fy - 7), (fx + 5, fy + 3)], color, 2)


def draw_collapsed(d, pen, f, th):
    breath = 0.5 - 0.5 * math.cos(th)          # 0 exhaled .. 1 inhaled
    b = 3.0 * breath
    twitch = (2.5, -3) if f in (8, 9) else (0, 0)

    # Legs up in the air, limp; drawn first so the belly covers the hips.
    draw_flat_leg(pen, (72, 190 - b), (62, 164), (70, 146), LEG_FAR)
    draw_flat_leg(pen, (92, 190 - b), (104, 166),
                  (96 + twitch[0], 150 + twitch[1]), LEG)

    body = path((38, 214),
                ((40, 192), (62, 180 - b), (84, 180 - b)),
                ((108, 180 - b), (126, 194), (128, 212)),
                ((128, 227), (40, 228), (38, 214)))
    head = (24, 212)
    neck = bez((44, 208), (38, 206), (32, 210), head, n=8)
    pen.shape([(122, 204), (144, 198), (134, 208), (148, 213), (126, 219)], WHITE)
    pen.fill(body, INK)
    pen.stroke(body + [body[0]], INK, 2 * OUTLINE)
    pen.stroke(neck, INK, 13 + 2 * OUTLINE)
    pen.disc(head, 13 + OUTLINE, INK)
    pen.fill(body, WHITE)
    pen.stroke(neck, WHITE, 13)
    pen.disc(head, 13, WHITE)
    pen.stroke(bez((50, 222), (70, 226), (100, 226), (120, 220), n=10), BELLY_SHADE, 3)

    # Near wing flung out along the ground, reaching for the bike.
    wing = path((86, 208), ((104, 204), (128, 212), (146, 222)),
                ((150, 226), (146, 228), (140, 228)),
                ((120, 228), (100, 226), (86, 220)),
                ((80, 216), (80, 210), (86, 208)))
    pen.shape(wing, WHITE)
    tip = path((132, 216), ((140, 219), (146, 222), (147, 225)),
               ((148, 228), (140, 228), (134, 227)),
               ((128, 224), (128, 218), (132, 216)))
    pen.shape(tip, INK, 1)

    # Head tipped back, bill pointing at the sky, pouch puffing with each pant.
    tip_back = rotator(head, -90)
    face = Pen(d, lambda p: pen.tf(tip_back(p)), pen.k)
    draw_head(face, head[0], head[1], sag=-8 + 7 * breath, swing=0, eye='dizzy',
              spin=-th)

    # Steam coming off the belly.
    for i, x0 in enumerate((66, 86, 106)):
        age = (f / N + i / 3) % 1
        if age > 0.85:
            continue
        y0 = 172 - b - age * 34
        wisp = [(x0 + 2.5 * math.sin(k * 1.2 + age * 6), y0 - k * 2.2) for k in range(6)]
        pen.stroke(wisp, STEAM, 1.8)


def draw_sigh(img, f, th):
    """歇一歇… sagging a little more with every character."""
    breath = 0.5 - 0.5 * math.cos(th)
    x = 24 * S
    for i, c in enumerate('歇一歇…'):
        layer = text_layer(c, FONT_BIG, TEAL, True).rotate(-4 * i, resample=Image.BICUBIC,
                                                            expand=True)
        y = round((20 + i * 7 + breath * (1 + i * 0.6)) * S)
        img.paste(layer, (x, y), layer)
        x += round(FONT_BIG.getlength(c)) - 2 * S


def render_rest(f):
    th = 2 * math.pi * f / N
    img, d = new_canvas()
    pen = Pen(d)
    pen.disc((112, 176), 64, SUN)
    draw_ground(pen, 0)
    draw_flipped_bike(d, f)
    draw_collapsed(d, Pen(d, BIRD_GROW, BIRD_K), f, th)
    draw_notice(img, '已重置！')
    draw_sigh(img, f, th)
    return img.reduce(S)


# ---------------------------------------------------------------- idle scene

IDLE_N = 40           # one near/far weave, two kick-and-glide strides
IDLE_MS = 75
HORIZON = 150
GROUND_FAR = (243, 216, 168)
SHADOW = (226, 190, 138)
KICK = IDLE_N // 2


def idle_speed(f):
    """Road speed: a shove, then a long lazy coast."""
    u = (f % KICK) / KICK
    shove = math.sin(math.pi * (u - 0.05) / 0.3) if 0.05 <= u <= 0.35 else 0.0
    return 0.7 + 0.8 * shove


IDLE_DIST = [0.0]
for _f in range(IDLE_N):
    IDLE_DIST.append(IDLE_DIST[-1] + idle_speed(_f))


def idle_travel(f):
    """0..1 share of the loop's distance covered by frame f."""
    return IDLE_DIST[f] / IDLE_DIST[-1]


TRAIL_ANGLE = math.radians(167)   # legs streaming out behind, slightly downhill
FAR_LEG = 0.92                    # far leg foreshortened: smaller, further away


def idle_feet(u, hip, leg_len, ground_y):
    """Where a foot is during one kick stride, u in 0..1."""
    hx = hip[0]
    plant, push_end = (hx + 18, ground_y), (hx - 28, ground_y)
    # Fully straight: the foot sits at the full leg length from the hip.
    reach = leg_len - 0.05
    trail = (hx + reach * math.cos(TRAIL_ANGLE), hip[1] + reach * math.sin(TRAIL_ANGLE))
    if u < 0.3:                                     # planted, shoving back
        t = u / 0.3
        return plant[0] + (push_end[0] - plant[0]) * t, plant[1]
    if u < 0.42:                                    # follow through, up and back
        t = (u - 0.3) / 0.12
        x = push_end[0] + (trail[0] - push_end[0]) * t
        y = push_end[1] + (trail[1] - push_end[1]) * t - 4 * math.sin(math.pi * t)
        return x, y
    if u < 0.88:                                    # coasting, legs out straight
        t = (u - 0.42) / 0.46
        a = TRAIL_ANGLE + 0.025 * math.sin(2 * math.pi * t * 2)
        return hx + reach * math.cos(a), hip[1] + reach * math.sin(a)
    t = (u - 0.88) / 0.12                           # swing down for the next shove
    return trail[0] + (plant[0] - trail[0]) * t, trail[1] + (plant[1] - trail[1]) * t


def draw_note(pen, c):
    x, y = c
    pen.stroke([(x + 2.6, y), (x + 2.6, y - 11), (x + 7, y - 8)], INK, 1.6)
    pen.disc((x, y + 0.5), 3, INK)


def draw_idle_background(pen, f):
    pen.disc((46, HORIZON + 4), 30, SUN)
    # Far-off clouds: no parallax at that distance, so they just sit there.
    for cx, cy, w in ((40, 66, 0.85), (206, 50, 0.7)):
        puffs = [(-18, 4, 9), (-6, -3, 12), (9, -1, 10), (20, 5, 7)]
        for dx, dy, r in puffs:
            pen.disc((cx + dx * w, cy + dy * w), r * w + 1.6, INK)
        for dx, dy, r in puffs:
            pen.disc((cx + dx * w, cy + dy * w), r * w, WHITE)
        # Flat bottom: cut the puffs off and rule a base line.
        pen.fill([(cx - 32 * w, cy + 7 * w), (cx + 32 * w, cy + 7 * w),
                  (cx + 32 * w, cy + 16 * w), (cx - 32 * w, cy + 16 * w)], PAPER)
        pen.stroke([(cx - 25.5 * w, cy + 7 * w), (cx + 25.5 * w, cy + 7 * w)], INK, 1.6)
    pen.fill([(-4, HORIZON), (244, HORIZON), (244, 244), (-4, 244)], GROUND_FAR)
    pen.stroke([(-4, HORIZON), (244, HORIZON)], INK, 2)
    # Grass tufts in rows: far rows dense and slow, near rows sparse and fast.
    # Each row moves exactly one spacing per loop, so it wraps seamlessly.
    for y, spacing, size in ((160, 40, 2), (176, 64, 3), (198, 104, 4), (232, 160, 5.5)):
        off = idle_travel(f) * spacing
        x = -off
        while x < 250:
            pen.stroke([(x - size, y - size), (x, y), (x + size * 0.4, y - size * 1.4)], INK, 1.2 + size * 0.15)
            x += spacing


def render_idle(f):
    t = f / IDLE_N
    u = (f % KICK) / KICK
    img, d = new_canvas()
    base = Pen(d)
    draw_idle_background(base, f)

    # Weaving towards and away from the camera: scale, ground line and lean.
    k = 0.85 + 0.15 * math.cos(2 * math.pi * t)
    contact_y = 214 + 14 * math.cos(2 * math.pi * t)
    cx = 120 + 8 * math.sin(2 * math.pi * t)
    lean = -4 * math.sin(2 * math.pi * t)
    shadow = [(cx + 58 * k * math.cos(a), contact_y + 4 * k * math.sin(a))
              for a in [i * math.pi / 24 for i in range(48)]]
    base.fill(shadow, SHADOW)
    pen = Pen(d, rotator((118, GROUND_Y), lean, k=k, to=(cx, contact_y)), k)

    wheel = idle_travel(f) * 9 * math.pi / 3        # 540° per loop, 6 spokes
    shove = idle_speed(f) - 0.7
    bob = -1.5 * shove + 0.6 * math.sin(2 * math.pi * t * 4)
    th = 2 * math.pi * t * 2

    far_hip, near_hip = (104, 176 + bob), (110, 178 + bob)
    near_foot = idle_feet(u, near_hip, THIGH + SHIN, GROUND_Y - 3)
    # The far leg lags a little, so the two stay readable when they overlap.
    far_foot = idle_feet((u - 0.06) % 1, far_hip, (THIGH + SHIN) * FAR_LEG, GROUND_Y - 6)

    draw_flag(pen, th / 2)
    pedal_far = (BB[0] - CRANK, BB[1])
    pen.tube([BB, pedal_far], INK, 2)
    draw_pedal(pen, pedal_far)
    draw_leg(pen, far_hip, far_foot, LEG_FAR, toes=1 if far_foot[0] > far_hip[0] - 40 else -1,
             scale=FAR_LEG)
    for c in (REAR, FRONT):
        draw_wheel(pen, c, WHEEL_R, wheel, spokes=6)
    draw_bike(pen, wheel * 2.6)
    pen.dot(BB, 5, WHITE, 1.8)
    pedal_near = (BB[0] + CRANK, BB[1])
    pen.tube([BB, pedal_near], INK, 2)
    draw_pedal(pen, pedal_near)
    hx, hy = draw_rider(pen, f, th, bob, 1.5 * math.sin(th - 0.6) - shove,
                        2.5 * math.sin(th - 1.2), 2 * math.sin(th - 1.8), mood='chill')
    draw_leg(pen, near_hip, near_foot, LEG, toes=1 if near_foot[0] > near_hip[0] - 40 else -1)

    # Whistling while coasting.
    if 0.4 <= u < 0.95:
        a = (u - 0.4) / 0.55
        draw_note(pen, (hx + 14 - a * 10, hy - 20 - a * 22 + 2 * math.sin(a * 9)))
    # Calm, centred, stating the obvious.
    title = text_layer('日常划水中', FONT_MID, INK, False)
    img.paste(title, ((SIZE * S - title.width) // 2 + 3 * S, 9 * S), title)
    return img.reduce(S)


# ---------------------------------------------------------------- overlays

BANNER_W, BANNER_H = 176, 62
HOLD_HINT = '松手确认 · 按住 10 秒配网'
BANNERS = [
    ('banner_demo', '演示模式', '短按切换动画'),
    ('banner_live', '正式模式', '跟随重置状态'),
    ('banner_to_live', '切到正式模式', HOLD_HINT),
    ('banner_to_demo', '切到演示模式', HOLD_HINT),
    ('banner_to_setup', '进入配网', '松手确认'),
]
STATUS_W, STATUS_H = 232, 22
STATUS_Y = 176
SSID_BOX = (30, 72, 210, 98)          # the firmware prints the hotspot name inside
STATUSES = [
    ('status_wait', '等待手机连接热点…', INK),
    ('status_phone', '手机已连上，请在页面里选 Wi-Fi', INK),
    ('status_connecting', '正在连接，请稍候…', INK),
    ('status_fail', '连接失败，请检查密码再试', RED),
    ('status_ok', '连好了！马上进入正式模式', TEAL),
]


def render_banner(title, subtitle):
    img = Image.new('RGB', (BANNER_W * S, BANNER_H * S), PAPER)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, BANNER_W * S - 1, BANNER_H * S - 1), radius=12 * S, fill=INK)
    d.rounded_rectangle((3 * S, 3 * S, (BANNER_W - 3) * S, (BANNER_H - 3) * S), radius=10 * S,
                        outline=PAPER, width=round(1.5 * S))
    big, small = load_font(24 * S), load_font(13 * S)
    for text, font, y, color in ((title, big, 8, WHITE), (subtitle, small, 40, POUCH)):
        w = font.getlength(text)
        d.text(((BANNER_W * S - w) / 2, y * S), text, font=font, fill=color)
    return img.reduce(S)


def render_status(text, color):
    img = Image.new('RGB', (STATUS_W * S, STATUS_H * S), PAPER)
    font = load_font(14 * S)
    d = ImageDraw.Draw(img)
    d.text(((STATUS_W * S - font.getlength(text)) / 2, 2 * S), text, font=font, fill=color)
    return img.reduce(S)


def render_setup():
    """Static part of the Wi-Fi setup screen."""
    img, d = new_canvas()
    pen = Pen(d)
    title = load_font(28 * S)
    body, small = load_font(15 * S), load_font(13 * S)
    d.text(((SIZE * S - title.getlength('配网模式')) / 2, 8 * S), '配网模式', font=title, fill=INK)

    def step(n, y, text, extra=None):
        pen.dot((24, y + 9), 8, TEAL, 1.6)
        num = load_font(12 * S)
        d.text((24 * S - num.getlength(str(n)) / 2, (y + 2) * S), str(n), font=num, fill=WHITE)
        d.text((38 * S, y * S), text, font=body, fill=INK)
        if extra:
            d.text((38 * S, (y + 20) * S), extra[0], font=small, fill=INK)
            d.text((38 * S + small.getlength(extra[0]), (y + 20) * S), extra[1], font=small, fill=TEAL)

    step(1, 48, '手机连上这个 Wi-Fi')
    x0, y0, x1, y1 = SSID_BOX
    d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=8 * S, fill=WHITE,
                        outline=INK, width=round(2 * S))
    step(2, 106, '会自动弹出配网页面', ('没弹出就打开 ', '192.168.4.1'))
    step(3, 150, '选 Wi-Fi，输入密码')

    # A pelican peeking in from the corner, unimpressed.
    head = (30, 218)
    pen.stroke([(head[0] - 4, 250), head], INK, 13 + 2 * OUTLINE)
    pen.disc(head, 14 + OUTLINE, INK)
    pen.stroke([(head[0] - 4, 250), head], WHITE, 13)
    pen.disc(head, 14, WHITE)
    draw_head(pen, head[0], head[1], sag=-6, swing=0, eye='chill')
    d.text(((SIZE - 12) * S - small.getlength('短按退出'), 218 * S), '短按退出', font=small, fill=INK)
    return img.reduce(S)


def overlays():
    """(name, image, keyed): keyed overlays treat PAPER as transparent."""
    items = [(name, render_banner(t, sub), True) for name, t, sub in BANNERS]
    items += [(name, render_status(text, color), False) for name, text, color in STATUSES]
    items.append(('setup_screen', render_setup(), False))
    return items


# ---------------------------------------------------------------- export

SCENES = [('idle', render_idle, IDLE_MS, IDLE_N), ('ride', render_ride, RIDE_MS, N),
          ('rest', render_rest, REST_MS, N)]


def rgb565(p):
    r, g, b = p
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)


def rle(img):
    """(run, colorHi, colorLo) triplets in row-major order, runs 1..255."""
    out, prev, run = bytearray(), None, 0
    for c in map(rgb565, img.get_flattened_data()):
        if c == prev and run < 255:
            run += 1
            continue
        if prev is not None:
            out += bytes((run, prev >> 8, prev & 0xff))
        prev, run = c, 1
    out += bytes((run, prev >> 8, prev & 0xff))
    return out


def save_previews(name, frames, ms):
    preview = Path('preview')
    preview.mkdir(exist_ok=True)
    frames[0].save(preview / f'{name}.gif', save_all=True, append_images=frames[1:],
                   duration=ms, loop=0)
    step = max(1, len(frames) // 12)
    sheet = Image.new('RGB', (SIZE * 4, SIZE * 3))
    for i, fr in enumerate(frames[::step][:12]):
        sheet.paste(fr, (i % 4 * SIZE, i // 4 * SIZE))
    sheet.save(preview / f'{name}_sheet.png')
    frames[0].resize((SIZE * 3, SIZE * 3), Image.NEAREST).save(preview / f'{name}_x3.png')


def main():
    with open('include/pelican_frames.h', 'w') as out:
        out.write('// Generated by render_pelican.py. Do not edit.\n#pragma once\n#include <stdint.h>\n\n'
                  'struct PelicanScene {\n'
                  '    const uint8_t* const* frames;\n'
                  '    const uint32_t* sizes;\n'
                  '    uint8_t count;\n'
                  '    uint16_t frameMs;\n'
                  '};\n\n')
        for name, render, ms, count in SCENES:
            frames = [render(f) for f in range(count)]
            save_previews(name, frames, ms)
            blobs = [rle(fr) for fr in frames]
            for i, blob in enumerate(blobs):
                out.write(f'const uint8_t {name}_frame_{i}[] = {{\n')
                for o in range(0, len(blob), 24):
                    out.write('  ' + ','.join(f'0x{v:02x}' for v in blob[o:o + 24]) + ',\n')
                out.write('};\n')
            out.write(f'const uint8_t* const {name}_frames[] = {{'
                      + ','.join(f'{name}_frame_{i}' for i in range(count)) + '};\n')
            out.write(f'const uint32_t {name}_sizes[] = {{'
                      + ','.join(str(len(b)) for b in blobs) + '};\n\n')
            print(f'{name}: {sum(map(len, blobs))} bytes')
        out.write('const PelicanScene PELICAN_SCENES[] = {\n'
                  + ''.join(f'    {{{name}_frames, {name}_sizes, {count}, {ms}}},\n'
                            for name, _, ms, count in SCENES)
                  + '};\n'
                  f'constexpr int PELICAN_SCENE_COUNT = {len(SCENES)};\n'
                  + ''.join(f'constexpr uint8_t SCENE_{name.upper()} = {i};\n'
                            for i, (name, *_) in enumerate(SCENES)))
        out.write('\n// Still images drawn over or instead of the animation. Keyed ones skip\n'
                  '// PAPER pixels so the scene shows through around rounded corners.\n'
                  'struct PelicanImage {\n'
                  '    const uint8_t* data;\n'
                  '    uint32_t size;\n'
                  '    uint16_t w, h;\n'
                  '    bool keyed;\n'
                  '};\n'
                  f'constexpr int STATUS_Y = {STATUS_Y};\n'
                  f'constexpr int SSID_BOX_Y0 = {SSID_BOX[1]};\nconstexpr int SSID_BOX_Y1 = {SSID_BOX[3]};\n')
        for name, img, keyed in overlays():
            img.save(Path('preview') / f'{name}.png')
            blob = rle(img)
            out.write(f'const uint8_t {name}_data[] = {{\n')
            for o in range(0, len(blob), 24):
                out.write('  ' + ','.join(f'0x{v:02x}' for v in blob[o:o + 24]) + ',\n')
            out.write(f'}};\nconst PelicanImage {name.upper()} = '
                      f'{{{name}_data, {len(blob)}, {img.width}, {img.height}, {str(keyed).lower()}}};\n')


def write_docs_assets():
    """Images for the user manual in docs/, copied from the fresh previews."""
    import shutil
    src, dst = Path('preview'), Path('docs/img')
    dst.mkdir(parents=True, exist_ok=True)
    for name in ('idle.gif', 'ride.gif', 'rest.gif', 'banner_demo.png', 'banner_live.png',
                 'banner_to_live.png', 'banner_to_demo.png', 'banner_to_setup.png'):
        shutil.copy(src / name, dst / name)
    # The setup screen as the device shows it, with an example hotspot name.
    mono = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 17)
    for status in ('status_wait', 'status_phone', 'status_connecting', 'status_fail', 'status_ok'):
        shot = Image.open(src / 'setup_screen.png').convert('RGB')
        shot.paste(Image.open(src / f'{status}.png').convert('RGB'), ((SIZE - STATUS_W) // 2, STATUS_Y))
        d = ImageDraw.Draw(shot)
        name = 'Pelican-A1B2'
        d.text(((SIZE - d.textlength(name, font=mono)) / 2, (SSID_BOX[1] + SSID_BOX[3]) / 2 - 10),
               name, font=mono, fill=INK)
        shot.save(dst / f'setup_{status[7:]}.png')


if __name__ == '__main__':
    main()
    write_docs_assets()
