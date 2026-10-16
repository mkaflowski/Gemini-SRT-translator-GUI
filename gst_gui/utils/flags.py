"""
Small flag images drawn with Pillow.

Windows has no glyphs for flag emoji (and Tk cannot draw colour emoji), so the
file list shows these images instead. Flags are simplified but recognizable.
"""
from PIL import Image, ImageDraw

W, H = 21, 14

# Simple stripe flags: ('h' | 'v', [colors]) - equal stripes
_STRIPES = {
    'PL': ('h', ['#ffffff', '#dc143c']),
    'DE': ('h', ['#000000', '#dd0000', '#ffce00']),
    'RU': ('h', ['#ffffff', '#0039a6', '#d52b1e']),
    'UA': ('h', ['#0057b7', '#ffd700']),
    'NL': ('h', ['#ae1c28', '#ffffff', '#21468b']),
    'HU': ('h', ['#ce2939', '#ffffff', '#477050']),
    'BG': ('h', ['#ffffff', '#00966e', '#d62612']),
    'EE': ('h', ['#0072ce', '#000000', '#ffffff']),
    'LT': ('h', ['#fdb913', '#006a44', '#c1272d']),
    'LV': ('h', ['#9e3039', '#9e3039', '#ffffff', '#9e3039', '#9e3039']),
    'ID': ('h', ['#ce1126', '#ffffff']),
    'SI': ('h', ['#ffffff', '#005da4', '#ed1c24']),
    'SK': ('h', ['#ffffff', '#0b4ea2', '#ee1c25']),
    'HR': ('h', ['#ff0000', '#ffffff', '#171796']),
    'RS': ('h', ['#c6363c', '#0c4076', '#ffffff']),
    'ES': ('h', ['#aa151b', '#f1bf00', '#f1bf00', '#aa151b']),
    'TH': ('h', ['#a51931', '#f4f5f8', '#2d2a4a', '#2d2a4a', '#f4f5f8', '#a51931']),
    'IN': ('h', ['#ff9933', '#ffffff', '#138808']),
    'FR': ('v', ['#0055a4', '#ffffff', '#ef4135']),
    'IT': ('v', ['#009246', '#ffffff', '#ce2b37']),
    'RO': ('v', ['#002b7f', '#fcd116', '#ce1126']),
}

# Nordic cross: (background, cross, inner cross or None)
_NORDIC = {
    'SE': ('#006aa7', '#fecc00', None),
    'NO': ('#ba0c2f', '#ffffff', '#00205b'),
    'DK': ('#c8102e', '#ffffff', None),
    'FI': ('#ffffff', '#002f6c', None),
}


def _stripes(draw, direction, colors):
    n = len(colors)
    for i, color in enumerate(colors):
        if direction == 'h':
            draw.rectangle([0, round(i * H / n), W, round((i + 1) * H / n) - 1], fill=color)
        else:
            draw.rectangle([round(i * W / n), 0, round((i + 1) * W / n) - 1, H], fill=color)


def _nordic(draw, bg, cross, inner):
    draw.rectangle([0, 0, W, H], fill=bg)
    cx, cy = 7, H // 2
    draw.rectangle([cx - 2, 0, cx + 2, H], fill=cross)
    draw.rectangle([0, cy - 2, W, cy + 2], fill=cross)
    if inner:
        draw.rectangle([cx - 1, 0, cx + 1, H], fill=inner)
        draw.rectangle([0, cy - 1, W, cy + 1], fill=inner)


def _special(draw, country):
    if country == 'GB':
        draw.rectangle([0, 0, W, H], fill='#012169')
        draw.line([0, 0, W, H], fill='#ffffff', width=3)
        draw.line([0, H, W, 0], fill='#ffffff', width=3)
        draw.line([0, 0, W, H], fill='#c8102e', width=1)
        draw.line([0, H, W, 0], fill='#c8102e', width=1)
        draw.rectangle([W // 2 - 2, 0, W // 2 + 2, H], fill='#ffffff')
        draw.rectangle([0, H // 2 - 2, W, H // 2 + 2], fill='#ffffff')
        draw.rectangle([W // 2 - 1, 0, W // 2 + 1, H], fill='#c8102e')
        draw.rectangle([0, H // 2 - 1, W, H // 2 + 1], fill='#c8102e')
    elif country == 'JP':
        draw.rectangle([0, 0, W, H], fill='#ffffff')
        draw.ellipse([W // 2 - 4, H // 2 - 4, W // 2 + 4, H // 2 + 4], fill='#bc002d')
    elif country == 'KR':
        draw.rectangle([0, 0, W, H], fill='#ffffff')
        box = [W // 2 - 4, H // 2 - 4, W // 2 + 4, H // 2 + 4]
        draw.pieslice(box, 180, 360, fill='#cd2e3a')
        draw.pieslice(box, 0, 180, fill='#0047a0')
        for x in (2, W - 5):
            draw.rectangle([x, 2, x + 2, 4], fill='#000000')
            draw.rectangle([x, H - 5, x + 2, H - 3], fill='#000000')
    elif country in ('CN', 'VN'):
        draw.rectangle([0, 0, W, H], fill='#de2910' if country == 'CN' else '#da251d')
        center = (5, 4) if country == 'CN' else (W // 2, H // 2)
        _star(draw, center, 3, '#ffde00')
    elif country == 'TR':
        draw.rectangle([0, 0, W, H], fill='#e30a17')
        draw.ellipse([4, 3, 12, 11], fill='#ffffff')
        draw.ellipse([6, 4, 13, 10], fill='#e30a17')
        _star(draw, (15, 7), 2, '#ffffff')
    elif country == 'IL':
        draw.rectangle([0, 0, W, H], fill='#ffffff')
        draw.rectangle([0, 1, W, 2], fill='#0038b8')
        draw.rectangle([0, H - 3, W, H - 2], fill='#0038b8')
        cx, cy = W // 2, H // 2
        draw.polygon([(cx, cy - 4), (cx + 3, cy + 2), (cx - 3, cy + 2)], outline='#0038b8')
        draw.polygon([(cx, cy + 4), (cx + 3, cy - 2), (cx - 3, cy - 2)], outline='#0038b8')
    elif country == 'GR':
        _stripes(draw, 'h', ['#0d5eaf', '#ffffff'] * 4 + ['#0d5eaf'])
        draw.rectangle([0, 0, 7, 7], fill='#0d5eaf')
        draw.rectangle([3, 0, 4, 7], fill='#ffffff')
        draw.rectangle([0, 3, 7, 4], fill='#ffffff')
    elif country == 'CZ':
        _stripes(draw, 'h', ['#ffffff', '#d7141a'])
        draw.polygon([(0, 0), (10, H // 2), (0, H)], fill='#11457e')
    elif country == 'PT':
        draw.rectangle([0, 0, W, H], fill='#ff0000')
        draw.rectangle([0, 0, 8, H], fill='#006600')
        draw.ellipse([5, H // 2 - 3, 11, H // 2 + 3], fill='#ffcc00')
    elif country == 'SA':
        draw.rectangle([0, 0, W, H], fill='#006c35')
        draw.rectangle([5, 9, W - 5, 10], fill='#ffffff')
        draw.rectangle([5, 4, W - 5, 6], fill='#ffffff')
    else:
        return False
    return True


def _star(draw, center, r, color):
    import math
    cx, cy = center
    points = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.45
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=color)


def flag_image(country):
    """PIL image of the flag for an ISO 3166 country code, or None if unknown."""
    country = (country or '').upper()
    img = Image.new('RGB', (W, H), '#808080')
    draw = ImageDraw.Draw(img)
    if country in _STRIPES:
        _stripes(draw, *_STRIPES[country])
        if country == 'IN':
            draw.ellipse([W // 2 - 2, H // 2 - 2, W // 2 + 2, H // 2 + 2], outline='#000080')
    elif country in _NORDIC:
        _nordic(draw, *_NORDIC[country])
    elif not _special(draw, country):
        return None
    # thin border so white flags stay visible on light/dark rows
    draw.rectangle([0, 0, W - 1, H - 1], outline='#555555')
    return img
