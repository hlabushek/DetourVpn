"""Render the Android launcher icons from apps/client/android/icon-source/app-icon.webp.

Needs Pillow (not a project dependency): ``python scripts/make_app_icon.py``. Writes the adaptive
icon (foreground PNGs + XML) and legacy PNGs under apps/client/android/app/src/main/res.
"""

from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps" / "client" / "android" / "icon-source" / "app-icon.webp"
RES = ROOT / "apps" / "client" / "android" / "app" / "src" / "main" / "res"
FOREGROUND_SIZES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
LEGACY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
# Share of the adaptive 108dp canvas the icon square may fill (the visible safe zone is 66dp).
FOREGROUND_SCALE = 0.84


def cropped_square() -> Image.Image:
    image = Image.open(SOURCE).convert("RGBA")
    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
    box = ImageChops.difference(image, white).convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()
    if box is None:
        raise SystemExit("icon_source_empty")
    square = image.crop(box)
    pixels = square.load()
    for y in range(square.height):  # key out the white corners outside the rounded square
        for x in range(square.width):
            r, g, b, _ = pixels[x, y]
            if r > 225 and g > 225 and b > 225:
                pixels[x, y] = (255, 255, 255, 0)
    return square


def main() -> None:
    square = cropped_square()
    background = square.getpixel((square.width // 2, 8))[:3]
    hex_color = "#%02X%02X%02X" % background
    for density, size in FOREGROUND_SIZES.items():
        directory = RES / f"mipmap-{density}"
        directory.mkdir(parents=True, exist_ok=True)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        inner = round(size * FOREGROUND_SCALE)
        art = square.resize((inner, inner), Image.LANCZOS)
        canvas.paste(art, ((size - inner) // 2, (size - inner) // 2), art)
        canvas.save(directory / "ic_launcher_foreground.png")
    for density, size in LEGACY_SIZES.items():
        square.resize((size, size), Image.LANCZOS).save(RES / f"mipmap-{density}" / "ic_launcher.png")
    (RES / "mipmap-anydpi-v26").mkdir(exist_ok=True)
    (RES / "mipmap-anydpi-v26" / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        "</adaptive-icon>\n",
        encoding="utf-8",
        newline="\n",
    )
    (RES / "values").mkdir(exist_ok=True)
    (RES / "values" / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <color name="ic_launcher_background">{hex_color}</color>\n</resources>\n',
        encoding="utf-8",
        newline="\n",
    )
    print("icons written, background", hex_color)


if __name__ == "__main__":
    main()
