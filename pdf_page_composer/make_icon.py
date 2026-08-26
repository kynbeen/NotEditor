from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def build_icon(output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 큰 투명 캔버스에서 그린 뒤 축소해 작업표시줄의 작은 크기에서도 가장자리가 매끈하게 보인다.
    render_size = 1024
    image = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = render_size / 256

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in values)

    draw.rounded_rectangle(
        box((28, 24, 228, 232)), radius=round(42 * scale), fill="#6366F1"
    )
    draw.rounded_rectangle(
        box((63, 48, 180, 198)), radius=round(13 * scale), fill="#FFFFFF"
    )
    fold = tuple(
        (round(x * scale), round(y * scale))
        for x, y in ((148, 48), (180, 80), (148, 80))
    )
    draw.polygon(fold, fill="#C7D2FE")
    draw.rounded_rectangle(
        box((82, 102, 161, 114)), radius=round(6 * scale), fill="#6366F1"
    )
    draw.rounded_rectangle(
        box((82, 130, 161, 142)), radius=round(6 * scale), fill="#A5B4FC"
    )
    draw.rounded_rectangle(
        box((82, 158, 138, 170)), radius=round(6 * scale), fill="#A5B4FC"
    )
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(
        output_path,
        format="ICO",
        sizes=[(256, 256), (64, 64), (32, 32), (16, 16)],
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    build_icon(root / "assets" / "icon.ico")


if __name__ == "__main__":
    main()
