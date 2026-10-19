import subprocess
from PIL import Image, ImageDraw
from video_tutor.media import binary, probe, select_frames, extract_audio
from video_tutor.config import Settings
from video_tutor.worker import import_file
from video_tutor import db


def create_fixture(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        im = Image.new("RGB", (640, 360), "#f6f8f2")
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 640, 60), fill="#23523d")
        d.text((30, 20), "SETTINGS TUTORIAL", fill="white")
        d.text((40, 100), "PORT", fill="black")
        d.rectangle((40, 130, 300, 200), outline="gray", width=2)
        d.text((60, 150), "3000" if i == 0 else "8080", fill="black")
        if i == 2:
            d.rectangle((390, 130, 600, 195), fill="#236245")
            d.text((420, 150), "SAVED", fill="white")
        im.save(folder / f"{i}.png")
    listing = folder / "frames.txt"
    listing.write_text("".join(f"file '{i}.png'\nduration 2\n" for i in range(3)) + "file '2.png'\n")
    path = folder / "tutorial.mp4"
    subprocess.run(
        [
            binary("ffmpeg"),
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
            str(path),
        ],
        check=True,
    )
    return path


def test_actual_frame_timestamps_and_dedupe(tmp_path):
    source = create_fixture(tmp_path / "videos")
    meta = probe(source)
    assert meta["audio"] is None and meta["duration_ms"] >= 6000
    s = Settings(directories=[str(source.parent)], stable_seconds=1)
    picked = select_frames(source, tmp_path / "frames", meta, s, lambda: None, lambda a, b: None)
    assert len(picked["frames"]) >= 3
    times = [f["time_ms"] for f in picked["frames"]]
    assert times == sorted(times)
    assert any(abs(t - 2000) < 600 for t in times)
    a = import_file(source, s, stable=False)
    b = import_file(source, s, stable=False)
    assert a == b and len(db.rows("SELECT * FROM runs")) == 1


def test_offset_audio(tmp_path):
    p = tmp_path / "offset.mkv"
    subprocess.run(
        [
            binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=s=160x90:r=10:d=5",
            "-itsoffset",
            "2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s16le",
            str(p),
        ],
        check=True,
    )
    meta = probe(p)
    assert abs(meta["audio_offset_ms"] - 2000) < 100
    wav = tmp_path / "a.wav"
    assert extract_audio(p, wav, meta, lambda: None)
    import wave

    with wave.open(str(wav)) as w:
        assert abs(w.getnframes() / w.getframerate() - 2) < 0.1
