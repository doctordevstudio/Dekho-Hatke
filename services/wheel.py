"""Spin-wheel animation: renders a real spinning-wheel GIF that decelerates and lands on the winning amount."""
import io
import random

from PIL import Image, ImageDraw, ImageFont

PALETTE = [(138, 92, 255), (255, 79, 163), (61, 224, 255), (200, 255, 61),
           (255, 200, 61), (255, 90, 110), (61, 255, 162), (108, 123, 255)]
INK = (11, 11, 22)
SIZE = 320
SEGMENTS = 8


def _font(px):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def pick_values(amount, lo, hi, n=SEGMENTS, decimals=False):
    """`n` wheel labels (the winning amount is one of them). Returns (values, winning_index)."""
    ints = float(lo) == int(lo) and float(hi) == int(hi) and not decimals and float(amount) == int(amount)
    pool = set()
    tries = 0
    while len(pool) < n - 1 and tries < 200:
        tries += 1
        v = random.randint(int(lo), int(hi)) if ints else round(random.uniform(float(lo), float(hi)), 2)
        if v != amount:
            pool.add(v)
    values = list(pool)
    while len(values) < n - 1:  # tiny ranges: pad with repeats
        values.append(random.choice(values) if values else amount)
    values.append(amount)
    random.shuffle(values)
    return values, values.index(amount)


def _label(v):
    return f"{int(v)}" if float(v) == int(v) else f"{float(v):.2f}".rstrip("0").rstrip(".")


def _base_wheel(values, px):
    S = px * 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    n = len(values)
    step = 360 / n
    box = (8, 8, S - 8, S - 8)
    for i in range(n):
        d.pieslice(box, i * step - 90, (i + 1) * step - 90, fill=PALETTE[i % len(PALETTE)] + (255,),
                   outline=(255, 255, 255, 255), width=4)
    font = _font(int(S * 0.095))
    cx = cy = S / 2
    import math
    for i, v in enumerate(values):
        theta = i * step + step / 2
        txt = Image.new("RGBA", (int(S * 0.3), int(S * 0.14)), (0, 0, 0, 0))
        ImageDraw.Draw(txt).text((txt.width / 2, txt.height / 2), _label(v), font=font, fill=INK + (255,), anchor="mm")
        txt = txt.rotate(90 - theta, expand=True, resample=Image.BICUBIC)
        a = math.radians(theta - 90)
        r = S * 0.34
        img.alpha_composite(txt, (int(cx + r * math.cos(a) - txt.width / 2), int(cy + r * math.sin(a) - txt.height / 2)))
    ring = ImageDraw.Draw(img)
    ring.ellipse(box, outline=(255, 210, 80, 255), width=10)
    return img.resize((px, px), Image.LANCZOS)


def render_gif(values, win_index, size=SIZE, frames=32):
    """Return GIF bytes of the wheel spinning and stopping with the winning segment under the pointer."""
    n = len(values)
    step = 360 / n
    wheel_px = int(size * 0.88)
    base = _base_wheel(values, wheel_px)
    theta_win = win_index * step + step / 2 + random.uniform(-step * 0.3, step * 0.3)
    total = (-theta_win) % 360 + 360 * 5

    def canvas(angle):
        c = Image.new("RGBA", (size, size), INK + (255,))
        d = ImageDraw.Draw(c)
        d.ellipse((6, 6, size - 6, size - 6), fill=(24, 20, 44, 255))
        w = base.rotate(-angle, resample=Image.BICUBIC)
        c.alpha_composite(w, ((size - wheel_px) // 2, (size - wheel_px) // 2 + 6))
        mid = size // 2
        d.ellipse((mid - 20, mid - 14, mid + 20, mid + 26), fill=(255, 255, 255, 255))
        d.ellipse((mid - 12, mid - 6, mid + 12, mid + 18), fill=PALETTE[1] + (255,))
        d.polygon([(mid - 16, 2), (mid + 16, 2), (mid, 38)], fill=(255, 255, 255, 255), outline=INK + (255,))
        return c.convert("RGB")

    imgs = []
    for i in range(frames):
        t = i / (frames - 1)
        imgs.append(canvas(total * (1 - (1 - t) ** 3)))
    pal = imgs[-1].quantize(colors=96, method=Image.MEDIANCUT)
    q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
    durations = [60] * (frames - 1) + [2500]
    buf = io.BytesIO()
    q[0].save(buf, format="GIF", save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=False)
    return buf.getvalue()
