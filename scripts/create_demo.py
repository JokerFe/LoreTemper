"""Generate an explicit synthetic settings recording for real-model smoke tests."""

from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
folder = root / "work" / "demo"
folder.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
for i in range(3):
    im = Image.new("RGB", (1280, 720), "#f5f7f1")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1280, 80), fill="#214b39")
    d.text((40, 22), "LOCAL SERVICE SETTINGS — SYNTHETIC TEST", font=font, fill="white")
    d.rounded_rectangle((280, 150, 1050, 620), radius=15, fill="white", outline="#d1ddca", width=2)
    d.text((330, 195), "Network settings", font=font, fill="#29472e")
    d.text((330, 265), "Port", font=small, fill="#586b4e")
    d.rounded_rectangle((330, 310, 780, 375), radius=8, outline="#8aa477", width=2)
    d.text((350, 325), "3000" if i == 0 else "8080", font=font, fill="#243d27")
    d.rounded_rectangle((330, 420, 510, 477), radius=8, fill="#24593c")
    d.text((365, 432), "Save", font=small, fill="white")
    if i == 2:
        d.rounded_rectangle((330, 515, 930, 571), radius=8, fill="#e4f1dc")
        d.text((350, 528), "Settings saved successfully", font=small, fill="#356b37")
    d.text((30, 675), f"TEST FIXTURE | STATE {i + 1} | No real user content", font=small, fill="#8d9d7a")
    im.save(folder / f"{i}.png")
listing = folder / "frames.txt"
listing.write_text("".join(f"file '{i}.png'\nduration 3\n" for i in range(3)) + "file '2.png'\n")
subprocess.run(
    [
        "/opt/homebrew/bin/ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-r",
        "10",
        "-pix_fmt",
        "yuv420p",
        str(folder / "synthetic-tutorial.mp4"),
    ],
    check=True,
)
print(folder / "synthetic-tutorial.mp4")
