from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    size = 256
    image = Image.new("RGBA", (size, size), "#101827")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 24, 228, 232), radius=42, fill="#6366F1")
    draw.rounded_rectangle((63, 48, 180, 198), radius=13, fill="#FFFFFF")
    draw.polygon(((148, 48), (180, 80), (148, 80)), fill="#C7D2FE")
    draw.rounded_rectangle((82, 102, 161, 114), radius=6, fill="#6366F1")
    draw.rounded_rectangle((82, 130, 161, 142), radius=6, fill="#A5B4FC")
    draw.rounded_rectangle((82, 158, 138, 170), radius=6, fill="#A5B4FC")
    image.save(assets / "icon.ico", format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])


if __name__ == "__main__":
    main()
