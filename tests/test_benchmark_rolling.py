"""分段對照腳本中不需模型的部分；子程序以替身取代。"""

import json
import wave

import numpy as np
import pytest

from app.transcriber import decode_media
from scripts import benchmark_rolling as bench


def test_parse_run():
    assert bench.parse_run("whole") == {"name": "whole", "mode": "whole", "block_sec": None}
    assert bench.parse_run("rolling:600")["block_sec"] == 600
    for bad in ("rolling", "rolling:0", "chunk:600"):
        with pytest.raises(ValueError):
            bench.parse_run(bad)


def test_with_last():
    assert list(bench.with_last([])) == []
    assert list(bench.with_last("ab")) == [("a", False), ("b", True)]


def rows(*items):
    return [{"start_sec": s, "end_sec": s + 1, "text": t} for s, t in items]


def test_text_metrics():
    data = rows((0, "你 好"), (1, "你 好"), (2, ""), (3, ""), (4, "再見"))
    assert bench.char_count(data) == 6
    assert bench.consecutive_repeats(data) == 1  # 空字串不算


def test_worst_windows_ranks_differences():
    base = rows((5, "相同內容"), (65, "第二分鐘的內容"))
    other = rows((5, "相同內容"), (65, "完全不一樣"))
    worst = bench.worst_windows(base, other, 120)
    assert worst[0]["start_sec"] == 60 and worst[0]["ratio"] < 1
    assert worst[-1]["ratio"] == 1


def test_linear_slope():
    assert bench.linear_slope([1, 2]) is None
    assert bench.linear_slope([100, 110, 120, 130]) == pytest.approx(10)


def test_repeat_is_sample_exact(tmp_path):
    source = tmp_path / "src.wav"
    rng = np.random.default_rng(1)
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(rng.integers(-9000, 9000, 44100 * 3).astype("<i2").tobytes())
    output = tmp_path / "x3.wav"
    bench.main(["repeat", str(source), "--times", "3", "--output", str(output)])
    once = decode_media(source, 60)
    assert np.array_equal(decode_media(output, 60), np.tile(once, 3))
    with pytest.raises(SystemExit):
        bench.main(["repeat", str(source), "--times", "2", "--output", str(output)])


def fake_child(output, name, mode, segments, blocks):
    (output / f"{name}.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in segments), encoding="utf-8"
    )
    result = {
        "name": name,
        "mode": mode,
        "total_sec": 10,
        "transcribe_sec": 8,
        "duration_sec": 130,
        "peak_working_set_mib": 900,
        "peak_private_mib": 1000,
        "segments": len(segments),
        "blocks": blocks,
        "timestamp_adjustments": [],
        "locked_language": "zh",
    }
    (output / f"{name}.result.json").write_text(json.dumps(result), encoding="utf-8")


def test_run_spawns_children_and_reports(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    output = tmp_path / "out"
    calls = []

    def fake_run(command):
        config = json.loads(open(command[-1], encoding="utf-8").read())
        calls.append(config)
        seg = lambda s, t, b=0: {"start_sec": s, "end_sec": s + 2, "text": t, "block": b}  # noqa: E731
        if config["mode"] == "whole":
            fake_child(output, "whole", "whole", [seg(50, "跨界前"), seg(62, "跨界後")], [{}])
        elif config["name"] == "rolling-60":
            fake_child(
                output,
                "rolling-60",
                "rolling",
                [seg(50, "跨界前"), seg(61, "跨界後", 1)],
                [
                    {"index": 0, "offset_sec": 0, "forced_cut": True, "private_mib": 800},
                    {"index": 1, "offset_sec": 60.5, "forced_cut": False, "private_mib": 810},
                ],
            )
        else:
            return 3
        return 0

    monkeypatch.setattr(bench, "spawn", fake_run)
    bench.main(
        ["run", str(audio), "--output", str(output), "--runs", "whole", "rolling:60", "rolling:90"]
    )
    assert [c["name"] for c in calls] == ["whole", "rolling-60", "rolling-90"]
    assert calls[1]["block_sec"] == 60 and calls[1]["search_sec"] == 30
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["audio_sha256"]) == 64
    assert (output / "rolling-90.failed").read_text() == "3"
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "| rolling-90 | 失敗或未完成" in report
    assert "00:01:00（強制切點）" in report
    assert "跨界後 ←切點後" in report
    assert "字數相對整段" in report
    with pytest.raises(FileExistsError):
        bench.main(["run", str(audio), "--output", str(output), "--runs", "whole"])


def test_run_validates_arguments(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(SystemExit):
        bench.main(
            ["run", str(audio), "--output", str(tmp_path / "o1"), "--runs", "whole", "whole"]
        )
    with pytest.raises(SystemExit):
        bench.main(["run", str(audio), "--output", str(tmp_path / "o2"), "--runs", "rolling:20"])
    assert not (tmp_path / "o2").exists()


class FakeWhisper:
    """記錄每次 transcribe 的參數；每 5 秒一段，並刻意輸出一段越過塊尾的片段。"""

    calls = []

    def __init__(self, *args, **kwargs):
        self.model = type("M", (), {"compute_type": "int8_float32"})()
        self.hf_tokenizer = type(
            "T", (), {"encode": lambda self, text, **kw: type("E", (), {"ids": [1] * len(text)})()}
        )()

    def transcribe(self, audio, **options):
        FakeWhisper.calls.append(options)
        length = len(audio) / 16000
        segments = [
            type("S", (), {"start": s, "end": s + 2, "text": f" 第{int(s)}秒"})()
            for s in np.arange(0, length - 2, 5)
        ]
        segments.append(type("S", (), {"start": length + 1, "end": length + 3, "text": " 越界"})())
        info = type("I", (), {"language": "zh", "duration_after_vad": length})()
        return iter(segments), info


@pytest.mark.parametrize("mode", ["whole", "rolling"])
def test_child_with_fake_model(tmp_path, monkeypatch, mode):
    import faster_whisper

    FakeWhisper.calls = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisper)
    monkeypatch.setattr("app.models.model_path", lambda *args: tmp_path)
    audio = tmp_path / "a.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(np.zeros(16000 * 50, dtype="<i2").tobytes())
    spec = bench.parse_run("whole" if mode == "whole" else "rolling:20")
    config = {
        "audio": str(audio),
        "model": "small",
        "threads": 4,
        "device": "cpu",
        "compute_type": "auto",
        "device_index": 0,
        "language": "auto",
        "condition_on_previous_text": True,
        "terms": "GLT-123",
        "seed": None,
        "search_sec": 5,
        "min_gap_sec": 0.3,
        "prev_context_tokens": 50,
        "sample_sec": 0.2,
        **spec,
    }
    path = tmp_path / f"{spec['name']}.config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    bench.child(path)
    result = json.loads((tmp_path / f"{spec['name']}.result.json").read_text(encoding="utf-8"))
    rows = bench.read_rows(tmp_path / f"{spec['name']}.jsonl")
    assert result["duration_sec"] == pytest.approx(50)
    assert result["segments"] == len(rows)
    if mode == "whole":
        assert len(FakeWhisper.calls) == 1
        return
    # 靜音 → 無語音間隙，視窗中點切；50 秒 / 20 秒塊 → 3 塊
    offsets = [b["offset_sec"] for b in result["blocks"]]
    assert len(offsets) == 3 and offsets[0] == 0
    # 非最後一塊的越界片段被調整為零長度於下一塊起點；最後一塊不裁切
    for b, following in zip(result["blocks"], offsets[1:], strict=False):
        edge = [r for r in rows if r["block"] == b["index"] and r["text"] == "越界"]
        assert edge[0]["start_sec"] == edge[0]["end_sec"] == pytest.approx(following)
    last = [r for r in rows if r["text"] == "越界"][-1]
    assert last["end_sec"] > result["duration_sec"]
    assert len(result["timestamp_adjustments"]) == 2
    assert all(r["start_sec"] <= r["end_sec"] for r in rows)
    # auto：首塊有語音即鎖定；第二塊起帶 token 形式的前文提示
    assert FakeWhisper.calls[0]["language"] is None
    assert FakeWhisper.calls[1]["language"] == "zh"
    assert isinstance(FakeWhisper.calls[0]["initial_prompt"], str)
    assert isinstance(FakeWhisper.calls[1]["initial_prompt"], list)
    assert result["peak_private_mib"] and result["memory_samples"]
