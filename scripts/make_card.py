"""Render assets/card.png (1200x600) for the catalog entry. Pillow only."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = (14, 11, 36)
INK2 = (23, 19, 58)
LINE = (46, 40, 96)
BONE = (244, 240, 255)
MUTE = (169, 163, 209)
ACID = (200, 255, 61)
EMBER = (255, 106, 61)
ALARM = (255, 90, 82)
CASH = (74, 215, 168)

OUT = Path(__file__).resolve().parents[1] / "assets" / "card.png"
FONT_DIR = Path("C:/Windows/Fonts")


def font(size: int, bold: bool = False, mono: bool = False):
    candidates = (["consolab.ttf" if bold else "consola.ttf"] if mono else
                  ["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"])
    for name in candidates:
        path = FONT_DIR / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> int:
    W, H = 1200, 600
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)

    # kicker + title
    d.text((64, 56), "HERMES PLUGIN", font=font(22, bold=True), fill=ACID)
    d.text((64, 92), "counterparty", font=font(72, bold=True), fill=BONE)
    d.text((64, 182), "Hermes checks who your agent is about to pay, email or buy from.",
           font=font(28), fill=BONE)
    d.text((64, 222), "DoD 1260H, FCC Covered List, Section 889, UFLPA, BIOSECURE, OFAC, ownership chain.",
           font=font(22), fill=MUTE)

    # approval panel mock
    px, py, pw, ph = 64, 286, 1072, 232
    d.rounded_rectangle((px, py, px + pw, py + ph), radius=14, fill=INK2, outline=LINE, width=2)
    mono = font(20, mono=True)
    monob = font(20, bold=True, mono=True)
    y = py + 20
    d.text((px + 24, y), "> send_email  to=orders@hikvision.com  subject=\"PO 4471: 200 IP cameras\"", font=mono, fill=MUTE)
    y += 34
    d.text((px + 24, y), "counterparty screen for send_email: 1 flagged counterparty", font=monob, fill=ALARM)
    y += 32
    d.text((px + 24, y), "- Hikvision is on DoD Section 1260H, FCC Covered List, Section 889.", font=mono, fill=BONE)
    y += 30
    d.text((px + 24, y), "  Grant and loan funds may not buy it [2 CFR 200.216; FAR 52.204-25]", font=mono, fill=BONE)
    y += 30
    d.text((px + 24, y), "  Source: Tensorust public-records ontology, lists pulled 2026-11-06", font=mono, fill=MUTE)
    y += 40
    d.text((px + 24, y), "Tool 'send_email' requires approval  [y]es / [n]o / [a]lways", font=monob, fill=ACID)

    # footer
    d.text((64, 542), "hermes plugins install counterparty", font=font(22, mono=True), fill=CASH)
    wm = font(22, bold=True)
    label = "TENSO"
    d.text((W - 64 - d.textlength("TENSORUST", font=wm) - 160, 544), label, font=wm, fill=BONE)
    d.text((W - 64 - d.textlength("RUST", font=wm) - 160, 544), "RUST", font=wm, fill=EMBER)
    d.text((W - 64 - d.textlength("Keep the receipts.", font=font(20)), 546), "Keep the receipts.", font=font(20), fill=MUTE)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
