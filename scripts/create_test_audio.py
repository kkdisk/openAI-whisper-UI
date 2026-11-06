"""以 Windows 內建語音生成中英混合比較素材；不使用雲端或新增套件。"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import wave
from pathlib import Path

CONTENT = [
    (
        "zh-TW",
        "這是一份人工編寫的合成測試錄音，不是真實會議。今天要比較語音轉錄模型，"
        "檢查中文、英文、數字、單位與否定句。請忠實保留原話，不要自行補上沒有說"
        "出的決議。以下的姓名、日期與任務都是測試情境，不代表任何真實承諾。",
    ),
    (
        "en-US",
        "This is a synthetic recording for speech recognition tests. Please kee"
        "p the original meaning, technical terms, numbers, and units. This reco"
        "rding does not contain a real business decision or an actual customer "
        "commitment.",
    ),
    (
        "zh-TW",
        "第一個議題是軟體介面。請檢查登入畫面、資料匯入和錯誤訊息。系統必須在本"
        "機離線執行，不得把錄音或逐字稿傳到外部伺服器。只有一個任務可以同時執行"
        "，取消後要保留已完成的文字。重新整理頁面不應該讓背景工作消失。",
    ),
    (
        "en-US",
        "The API request uses JSON, UTF-8 encoding, and an HTTP status code. A "
        "timeout is different from a permission error. Please verify the CPU th"
        "read count, GPU memory usage, and the model revision before starting t"
        "he next test.",
    ),
    (
        "zh-TW",
        "第二個議題是測試數據。第一組設定是一點五伏特、兩百五十毫安培，第二組設"
        "定是三點三伏特、一百二十毫安培。這兩組條件不同，不能直接把結果混在一起"
        "。請同時記錄平均值、最大值、最小值與標準差，並檢查小數點和單位有沒有寫"
        "錯。",
    ),
    (
        "en-US",
        "Set the voltage to 1.5 volts and the current to 250 milliamps. The res"
        "istance is 12 ohms. The sample rate is 16 kilohertz. Compare version 2"
        ".0 with version 2.1, and do not confuse milliamps with amps or millise"
        "conds with seconds.",
    ),
    (
        "zh-TW",
        "第三個議題是機構與溫控。請確認孔盤的水平度、滑軌的穩定性，以及抽液之後"
        "的殘留量。溫度設定為攝氏三十七度，允許誤差正負零點五度。現在提出的是改"
        "善建議，還沒有正式通過。不要因為有人提出方法，就推定這個人已經答應負責"
        "。",
    ),
    (
        "en-US",
        "Check the sensor, connector, heat sink, and power supply. The target t"
        "emperature is 37 degrees Celsius, with a tolerance of plus or minus 0."
        "5 degrees. This is a proposal for review. It is not an approved change"
        " to the production specification.",
    ),
    (
        "zh-TW",
        "第四個議題是品質分析。請保留原始量測資料，再計算良率與製程能力。異常值"
        "需要回查來源，不可以只為了讓圖表好看就刪除。若規格尚未設定，請標示待確"
        "認，不要自動判定合格。測試型號是甲一二三，批次編號是二零二六一一零六。",
    ),
    (
        "en-US",
        "Review the CSV file, SQL query, and PDF report. Check the CPK calculat"
        "ion and the pass or fail criteria. The test identifiers are GLT-123 an"
        "d IQC-2026. Preserve these identifiers exactly when possible, and flag"
        " uncertain words instead of inventing a replacement.",
    ),
    (
        "zh-TW",
        "第五個議題是待辦事項。林工程師說，我會在下週五之前完成第一輪測試。陳工"
        "程師說，我可以協助檢查資料，但目前不能承諾交付日期。王經理則提出，是否"
        "先增加兩組樣本。這三句分別是承諾、有限度協助與提案，整理時必須清楚區分"
        "。",
    ),
    (
        "en-US",
        "I will complete the first test by next Friday. I can help review the d"
        "ata, but I cannot confirm the delivery date yet. Could we add two more"
        " samples? Please separate a firm commitment, an offer of assistance, a"
        "nd an open question in the meeting notes.",
    ),
    (
        "zh-TW",
        "第六個議題是模型比較。所有模型使用相同的測試音訊、語言、提示詞與解碼參"
        "數。請先比較小型與中型模型，再評估速度優先和品質優先的選項。模型比較時"
        "一次只執行一個任務，不要同時跑其他大量運算，避免背景負載讓測試結果失去"
        "可比性。",
    ),
    (
        "en-US",
        "Compare small, medium, large-v3-turbo, and large-v3 using the same aud"
        "io. Record the elapsed time and memory usage. A faster result is not a"
        "utomatically more accurate. Speaker diarization is a separate task, an"
        "d a speaker label is not a verified person's name.",
    ),
    (
        "zh-TW",
        "最後請檢查長句是否完整，是否漏掉否定詞，是否重複產生原音沒有的內容。本"
        "段只會說一次以下句子：目前只有一台設備可以測試。若辨識結果出現連續重複"
        "，請回聽原音再判斷。合成測試通過之後，仍需要另外使用真實多人會議錄音驗"
        "證。",
    ),
    (
        "en-US",
        "This is the end of the synthetic test. Save the original transcript an"
        "d the formatted transcript separately. Compare both with the reference"
        " text, document the errors, and keep unresolved items marked for revie"
        "w. Thank you for completing this local, offline evaluation.",
    ),
]

SYNTHESIS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $installed = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled })
    $voices = @{}
    foreach ($culture in @('zh-TW', 'en-US')) {
        $voice = $installed |
            Where-Object { $_.VoiceInfo.Culture.Name -eq $culture } |
            Select-Object -First 1
        if (-not $voice) { throw "Missing installed voice: $culture" }
        $voices[$culture] = $voice.VoiceInfo.Name
    }
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $synth.Rate = [int]$data.rate
    for ($i = 0; $i -lt $data.segments.Count; $i++) {
        $segment = $data.segments[$i]
        $synth.SelectVoice($voices[$segment.language])
        $path = Join-Path $data.directory ('{0:D2}.wav' -f $i)
        $synth.SetOutputToWaveFile($path, $format)
        $synth.Speak($segment.text)
        $synth.SetOutputToNull()
    }
    $voices | ConvertTo-Json -Compress
} finally {
    $synth.Dispose()
}
"""


def render(directory: Path, rate: int) -> dict:
    payload = {
        "directory": str(directory),
        "rate": rate,
        "segments": [{"language": language, "text": text} for language, text in CONTENT],
    }
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SYNTHESIS],
        input=json.dumps(payload).encode("ascii"),
        capture_output=True,
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return json.loads(result.stdout.decode("utf-8-sig"))


def read_blocks(directory: Path) -> list[bytes]:
    blocks = []
    for index in range(len(CONTENT)):
        with wave.open(str(directory / f"{index:02d}.wav"), "rb") as handle:
            if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (
                1,
                2,
                16000,
            ):
                raise ValueError("合成音訊格式不符合 16 kHz／單聲道／16 bit。")
            blocks.append(handle.readframes(handle.getnframes()))
    return blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "temp")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    stem = "mixed_zh_en_5min"
    wav_path = args.output / f"{stem}.wav"
    if wav_path.exists():
        parser.error(f"檔案已存在，不覆蓋：{wav_path}")
    target_frames = 300 * 16000
    rate = 0
    with tempfile.TemporaryDirectory(prefix="tts-", dir=args.output) as temporary:
        directory = Path(temporary).resolve()
        for _ in range(5):
            voices = render(directory, rate)
            blocks = read_blocks(directory)
            spoken_frames = sum(len(block) // 2 for block in blocks)
            spoken_sec = spoken_frames / 16000
            print(f"語音速度 {rate}，合成區塊共 {spoken_sec:.1f} 秒", flush=True)
            if 245 <= spoken_sec <= 290:
                break
            rate += 1 if spoken_sec > 290 else -1
        else:
            raise RuntimeError("目前語音引擎無法將內容調整到適當長度，沒有輸出截斷音訊。")
    gap_frames, remainder = divmod(target_frames - spoken_frames, len(blocks) + 1)
    segments = []
    position = 0
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * gap_frames)
        position += gap_frames
        for index, (block, (language, text)) in enumerate(zip(blocks, CONTENT, strict=True), 1):
            start = position / 16000
            output.writeframes(block)
            position += len(block) // 2
            segments.append(
                {
                    "index": index,
                    "language": language,
                    "voice": voices[language],
                    "block_start_sec": start,
                    "block_end_sec": position / 16000,
                    "text": text,
                }
            )
            output.writeframes(b"\0\0" * gap_frames)
            position += gap_frames
        output.writeframes(b"\0\0" * remainder)
    manifest = {
        "synthetic": True,
        "duration_sec": 300,
        "sample_rate": 16000,
        "channels": 1,
        "sample_width_bytes": 2,
        "speech_rate": rate,
        "tts_blocks_sec": spoken_sec,
        "gap_sec": gap_frames / 16000,
        "sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest(),
        "notice": (
            "合成中英語音，不代表真實會議品質；"
            "區塊時間不是語音活動／字詞精準標註。數字讀法需人工核對。"
        ),
        "segments": segments,
    }
    (args.output / f"{stem}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reference = ["合成中英測試音檔參考文字（非真實會議）", manifest["notice"], ""]
    for segment in segments:
        reference.append(
            f"[{segment['block_start_sec']:.3f}–{segment['block_end_sec']:.3f}] "
            f"{segment['language']}\n{segment['text']}\n"
        )
    (args.output / f"{stem}.reference.txt").write_text("\n".join(reference), encoding="utf-8")
    print(f"完成：{wav_path}（300 秒）")


if __name__ == "__main__":
    main()
