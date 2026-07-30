"""Create a deterministic calibrated top-view fixture for Phase 3 acceptance."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    output = Path(__file__).with_name("plate-top-view.png")
    image = Image.new("L", (1000, 700), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 110, 900, 590), fill=0)
    for x, y in ((260, 270), (260, 430), (740, 270), (740, 430)):
        draw.ellipse((x - 20, y - 20, x + 20, y + 20), fill=255)
    image.save(output, format="PNG", optimize=True)
    print(output)


if __name__ == "__main__":
    main()
