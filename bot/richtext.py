"""Telegram message entities -> HTML (so a message an admin typed with bold/italic/links keeps its formatting)."""
import html

_TAGS = {"bold": ("<b>", "</b>"), "italic": ("<i>", "</i>"), "underline": ("<u>", "</u>"),
         "strikethrough": ("<s>", "</s>"), "code": ("<code>", "</code>"), "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
         "blockquote": ("<blockquote>", "</blockquote>")}


def to_html(text, entities):
    """Entity offsets/lengths are in UTF-16 code units, so walk the text char by char counting units."""
    if not text:
        return ""
    opens, closes = {}, {}
    bounds, pos = set(), 0
    for ch in text:
        bounds.add(pos)
        pos += 2 if ord(ch) > 0xFFFF else 1
    bounds.add(pos)
    for e in entities or []:
        if e.get("offset") not in bounds or e.get("offset", 0) + e.get("length", 0) not in bounds:
            continue  # points into the middle of an emoji: ignore
        t = e.get("type")
        if t == "pre":
            o, c = "<pre>", "</pre>"
        elif t == "text_link" and str(e.get("url", "")).startswith(("http://", "https://", "tg://")):
            o, c = f'<a href="{html.escape(e["url"], quote=True)}">', "</a>"
        elif t in _TAGS:
            o, c = _TAGS[t]
        else:
            continue
        a, b = e["offset"], e["offset"] + e["length"]
        opens.setdefault(a, []).append((b, o))
        closes.setdefault(b, []).append(c)
    out, pos = [], 0
    for ch in text:
        for c in reversed(closes.pop(pos, [])):
            out.append(c)
        for _end, o in sorted(opens.pop(pos, []), key=lambda x: -x[0]):
            out.append(o)
        out.append(html.escape(ch, quote=False))
        pos += 2 if ord(ch) > 0xFFFF else 1
    for c in reversed(closes.pop(pos, [])):
        out.append(c)
    return "".join(out)
