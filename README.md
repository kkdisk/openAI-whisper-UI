# 本機離線會議逐字稿

Windows 本機 Web UI，使用 faster-whisper CPU INT8 辨識中文及英文專有名詞。主要輸出帶時間戳 TXT 與附 AI 整理指示的 Markdown，另可下載 SRT。音檔及文字不會由本工具上傳外部服務。

## 日常使用

完成下方「首次安裝／重建環境」、備妥 `.venv` 與 `small` 模型後，可雙擊 `start.bat`，或在 PowerShell 執行（模型與虛擬環境不包含在 Git 倉庫內）：

```powershell
.\start.bat
```

服務就緒後會開啟 `http://127.0.0.1:8000`。如果瀏覽器未自動開啟，手動輸入此網址即可。

1. 選擇 MP4、MP3、WAV 或 M4A，一次一個檔案。
2. 選填會議名稱、日期、已知與會者與專有名詞；語言預設中文、模型 small、4 執行緒。
3. 開始轉錄。上傳進度與轉錄進度分開顯示；載入模型／解碼時不顯示虛構百分比。
4. 完成後下載「會議整理用 MD」，自行交給 AI 整理。TXT 保留時間戳，原始 TXT 保留繁體轉換前文字。

檔案上限 4 GiB、音軌上限 120 分鐘，使用第一條音軌。這些限制可在 `app/config.py` 調整；媒體編碼是否可解碼仍需實際檢查。專有名詞用來提示模型，不會強制替換；完整提示超過 200 tokens 會顯示縮短提示，字數不等於 token 數。

關閉或重新整理瀏覽器不會停止轉錄。停止服務請在啟動視窗按 **Ctrl+C**，通常需數秒清理；再次使用可重新啟動。8000 埠被其他服務占用時會報錯，不會把其他服務當成本工具。

若服務已在執行，直接開啟 `http://127.0.0.1:8000` 使用即可，不必重複執行啟動檔。`start.bat` 使用無 BOM 的 ASCII 與 CRLF 換行，Git 已設定批次檔保留 CRLF，避免 CMD 將編碼或換行解讀成指令。

### 環境修復

若出現 `ModuleNotFoundError`（例如 `av.about`），可能是已安裝套件內的檔案缺失。一般 `uv sync` 可能認為版本已安裝而略過，需完整重裝。**先停止轉錄服務並確認工作程序退出**，避免 Windows 鎖住正在使用的 DLL，再執行：

```powershell
uv sync --locked --group dev --reinstall
.\start.bat
```

若本機保留本專案已下載的乾淨 `.uv-pristine` 快取，可改為離線重裝：

```powershell
uv sync --locked --offline --group dev --reinstall --cache-dir .uv-pristine --link-mode copy
```

不要在長檔轉錄中執行重裝。已有部分卸載失敗時，也應先確保程序停止，再重裝完整環境；不能只依安裝版本紀錄認定檔案完整。

## 首次安裝／重建環境（需要網路）

需要 Windows、Python **3.10.x**（目前驗證 3.10.11）及 uv。以專案目錄為工作目錄：

```powershell
uv sync --locked --group dev
.\.venv\Scripts\python.exe scripts\prepare_models.py --model small
```

首次模型準備會解析並記錄 repo commit，使用該固定 revision 下載至 `models/small`，驗證權重、tokenizer 與套件內 VAD，再寫入 `ready.json`。可指定已知 revision 重建同一模型：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_models.py --model small --revision 536b0662742c02347bc0e980a01041f333bce120
```

需要其他模型時將 `--model` 改成 `base` 或 `medium`。模型準備期間請停止轉錄服務；不要同時更新正在使用的模型。日常 `start.bat` 不會執行安裝、更新或模型下載。

使用 PyAV 附帶的 FFmpeg 函式庫，無需安裝全域 FFmpeg。若原生 DLL 載入失敗，請檢查 64 位元 Python 與 Windows Microsoft Visual C++ 執行期；不要以忽略 DLL 錯誤或自動下載模型作為回退。

## 搬到完全離線的電腦

不建議直接複製 `.venv`，因為其中路徑可能綁定原電腦。先在相容的 Windows／Python 3.10 電腦準備 wheelhouse：

```powershell
uv export --locked --no-dev --format requirements-txt --output-file temp\requirements.offline.txt
python -m pip download --only-binary=:all: -r temp\requirements.offline.txt -d temp\wheelhouse
```

將程式碼、requirements.offline.txt、wheelhouse、完整 `models/small`（含 ready.json）及已備妥的 Python／uv 安裝工具複製到目標電腦。目標電腦建立自己的環境：

```powershell
uv venv --python 3.10
uv pip install --python .venv\Scripts\python.exe --no-index --find-links wheelhouse -r requirements.offline.txt
.\start.bat
```

此搬移流程尚須在目標電腦驗證；Windows VAD ONNX／onnxruntime DLL 來自套件，不在 Whisper 模型資料夾。少任何資產時應明確報錯，不連網補下載。

## 資料保存、取消與限制

- 原始來源檔案不會修改。上傳副本只放在 `data/jobs/<id>/media.upload`，任務結束後刪除。
- metadata 與逐段 JSONL 由主程序保存，再提供頁面顯示；重新啟動後可從歷史清單讀取。
- 取消先協作停止，必要時終止獨立工作程序。保留已保存片段，尚未傳回的內容可能遺失；不完整檔名有 `.partial`。
- 意外關閉服務後，下次啟動會清理殘留媒體、將活動任務標為 interrupted。恢復的是已保存結果，不會接續推論。
- 歷史結果保留到使用者在頁面確認刪除。`data/` 可能包含敏感會議資料，請依自己的保存需求清理；模型與資料均排除版本控制。
- 繁體轉換、VAD 與專有名詞提示不能保證辨識正確；英文型號、數字、否定詞及決議需人工核對。
- 第一版不辨識說話者、不產生摘要、不自動呼叫外部 AI，也不保證 60 分鐘錄音的處理時間。
- SRT 允許零長度片段；若有非法時間只阻止 SRT 匯出，不影響 TXT／MD 保留內容。

## 開發與驗證

```powershell
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
node --check app/static/app.js

# Windows 程序驗證
uv run --no-sync pytest -m windows

# 需要本機 small 模型與語音檔；不自行下載
$env:WHISPER_TEST_AUDIO = 'D:\path\sample.wav'
uv run --no-sync pytest --real-model
```

一般測試封鎖外部連線，允許 Windows asyncio 所需的 loopback socket。真實模型測試以 PyAV 產生 MP4／AAC，再清空 PATH 執行辨識。沒有素材／模型會標記 skip，不能算通過真實辨識驗收。使用者語音與測試輸出放在忽略的 `temp/`，不提交錄音內容。

目前驗證結果、未完成項目及執行證據見 [tasks/validation.md](tasks/validation.md)；任務追蹤見 [tasks/todo.md](tasks/todo.md)。
