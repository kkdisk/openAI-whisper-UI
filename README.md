# 本機離線會議逐字稿

Windows 本機 Web UI，使用 faster-whisper CPU INT8 或 NVIDIA GPU 辨識中文及英文專有名詞。主要輸出帶時間戳 TXT 與附 AI 整理指示的 Markdown，另可下載 SRT。音檔及文字不會由本工具上傳外部服務。

目前版本：**v0.5.1**。本次更新 todo 完成度與 M5 使用者整階段接受紀錄，功能沿用 v0.5.0 的 GPU 自動／手動選擇、診斷及首段前故障回退；RTX 2060 上 small／medium／large-v3-turbo 短語音 CPU、GPU INT8／FP16 與 FP16 均已通過。使用者已確認可運作並接受 M5，逐項資源量測數據未提供；M6／M7 的進一步品質、模型比較與剩餘故障矩陣仍獨立追蹤。保留文字修訂與 H10 暫停／繼續；關閉服務後續跑及說話者分離尚未實作。

## 日常使用

GPU 工作區進度、環境與驗收邊界見 [GPU 開發交接](tasks/gpu-development-handoff.md) 及 [驗證紀錄](tasks/validation.md)。

### GPU 環境及裝置選擇

先停止服務，再為 Windows NVIDIA 電腦安裝鎖定的可選執行庫；CPU 電腦仍可只執行一般 `uv sync --locked --group dev`。

```powershell
uv sync --locked --group dev --extra gpu
.\.venv\Scripts\python.exe scripts\diagnose_gpu.py
# 使用 30 秒內、包含實際語音的本機檔案，完整消費辨識結果：
.\.venv\Scripts\python.exe scripts\diagnose_gpu.py --audio path\to\speech.wav
```

GPU extra 固定 NVIDIA cuBLAS 12.6.4.1／cuDNN 9.5.1.17 的 Windows wheels；既有引擎與 Python 版本不變。程式只在 GPU 路徑註冊目前虛擬環境的 DLL，不修改系統 PATH 或驅動；日常啟動不安裝／下載。之後同步或重裝環境時仍須帶 `--extra gpu`，否則 uv 會移除可選執行庫。安裝依據為 [NVIDIA Windows cuDNN 文件](https://docs.nvidia.com/cudnn/installation/backend/windows/)，本機相容性以實際推論驗證。

UI 預設「自動（優先 GPU）」；可指定 CPU 或 NVIDIA GPU，並選 GPU INT8／FP16 或 FP16。進階設定可指定 GPU 編號，預設 0，不自動挑選空閒卡。API 缺少新欄位時仍按舊 CPU 行為讀取。裝置、實際精度與回退原因保存在任務及 TXT／MD 中；CTranslate2 在此 CPU 實測會把 INT8 以 `int8_float32` 執行。

auto 對首段保存前可分類的 CUDA 裝置／精度／DLL／顯存故障，最多換一個乾淨 CPU 工作程序；缺模型、提示超限、壞檔及未分類崩潰不回退。指定 cuda 時失敗即回報；已有片段後的 CUDA 故障保留原稿及人工修訂，不自動重跑或降模型。暫停仍占用 RAM／VRAM。

診斷預設以 60 秒逾時隔離子程序；無 `--audio` 時只證明模型載入，不能當作推論成功。可用 `--model`、`--compute-type`、`--device-index` 選擇；輸出含程序 RAM 峰值及整張卡的 VRAM 取樣峰值，後者含其他程序且可能漏掉尖峰。

比較工具亦支援 `--device cpu` 或 `--device cuda --compute-type int8_float16`（或 `float16`）及 `--device-index`，沿用相同裝置政策，保存來源 SHA-256、revision、實際精度、載入／解碼／推論時間。比較工具刻意不自動回退，失敗的 GPU 組不會冒充 CPU 組。

完成下方「首次安裝／重建環境」、備妥 `.venv` 與 `small` 模型後，可雙擊 `start.bat`，或在 PowerShell 執行（模型與虛擬環境不包含在 Git 倉庫內）：

```powershell
.\start.bat
```

服務就緒後會開啟 `http://127.0.0.1:8000`。如果瀏覽器未自動開啟，手動輸入此網址即可。

1. 選擇 MP4、MP3、WAV 或 M4A，一次一個檔案。
2. 選填會議名稱、日期、已知與會者與專有名詞；語言預設中文、模型 small、4 執行緒。
3. 開始轉錄。上傳進度與轉錄進度分開顯示；載入模型／解碼時不顯示虛構百分比。
4. 完成後下載「會議整理用 MD」，自行交給 AI 整理。TXT 保留時間戳，原始 TXT 保留繁體轉換前文字。

轉錄期間即可直接修改已出現的語句，停止輸入約 0.6 秒後自動儲存；請確認「修訂已儲存於本機」。每段提供前方／後方新增、刪除／復原及還原原文；工作區也可在開頭新增。相同位置的多個新增句依建立順序排列。新辨識結果會持續追加，完成時保留正在編輯的欄位。重新整理或從歷史開啟會還原已儲存修訂，取消或失敗後也可繼續校稿。

複製、TXT、MD 及 SRT 使用修訂內容；「原始 TXT」始終保留模型原文，包含人工刪除的段落。新增句子的開始／結束秒數可以留空，TXT／MD 會標示「時間未設定」；匯出 SRT 前必須填齊，結束大於開始。新增時間由使用者核對，不會自動對齊音訊；既有辨識時間戳不因文字修改而改變。刪除的句子仍顯示為停用欄位，方便復原，但不會出現在修訂匯出中。

儲存失敗會顯示原因，可按「重試儲存」。切換紀錄、新建任務及修訂匯出前會等待儲存；失敗時保留編輯畫面。未儲存時離頁會由瀏覽器提示，仍強制離開或關閉服務可能遺失最後修改。多分頁不會即時同步，過期版本寫入會被拒絕；遇到衝突時先手動複製未儲存文字，再重新整理。人工修改不會即時回饋模型或加快推論。

修訂另存於 `data/jobs/<id>/revisions.json`，以原子替換保存，不改寫 `segments.jsonl`；備份任務時請一併保留兩者。舊任務無修訂檔時直接顯示原稿。修訂檔損毀時明確報錯，不以原稿冒充修訂稿；仍可下載原始 TXT。原始 TXT 不等待修訂儲存，遇到儲存失敗或版本衝突時也可獨立下載，未儲存的修改仍保留在畫面中。

轉錄中可按「暫停轉錄」。畫面先顯示等待，工作程序完成目前推論並保存已產生片段後，才顯示「已暫停轉錄」；此時按鈕改為「繼續轉錄」。暫停時可繼續編輯與匯出部分稿，也可取消；模型與解碼音訊仍占用記憶體，且仍保留唯一活動槽位，無法同時開始另一份轉錄。模型載入、解碼及任務結束後不提供此操作。

暫停不是立即中斷原生運算。等待超過 15 秒會提示仍在等待確認，這是提醒門檻，不是保證或自動取消時間。重新整理／重開瀏覽器會讀回後端暫停狀態，不自動繼續；關閉服務後則只恢復已保存文字，不能接續推論。總耗時、活動時間及已確認暫停時間分開顯示，活動時間包含模型載入／解碼等非暫停時間；剩餘時間只在正常轉錄中估算，暫停期間停止估算。

工作區右上角的「清除工作區」只清空顯示，結果仍保留在本機歷史；左側選檔、設定及顯示模式都不變。沒有活動任務且目前結果已完成、失敗、取消或中斷時才能使用。清除後複製／匯出停用，可從歷史重新開啟；已開始的下載不受影響。清除不會刪除資料或釋放磁碟空間。

同分頁重新整理會保持空白，選取歷史或建立新任務後恢復預覽；一般新分頁仍會還原最近任務。若瀏覽器封鎖分頁儲存，本次清除仍有效，但重新整理可能恢復最近任務。

右上角可切換「跟隨系統／淺色模式／深色模式」，預設跟隨系統並即時回應系統外觀變更。手動選擇後會記住目前瀏覽器的偏好；更換瀏覽器或清除本站資料後回到跟隨系統。顯示模式不影響轉錄或匯出內容。

若會議有整段中文與整段英文交替，可試「中英交替（分段偵測，實驗）」；原本的「自動偵測」只從開頭判斷語言。分段偵測以模型音訊視窗運作，不保證逐句切換皆正確。若有連續重複，可在「進階辨識設定」關閉「參考前文」後重跑短樣本比較；可能影響用詞一致性。新設定會隨任務保存，TXT／MD 亦記載前文設定；舊任務維持原本設定，不自動重跑。

開發比較可執行以下指令，依序比較三組參數，輸出原文 JSONL、模型／音檔指紋與各組耗時到新目錄；不覆蓋既有資料，不下載模型。請先完成或取消 UI 中的轉錄，避免同時執行影響耗時。

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_transcription.py temp\mixed_zh_en_5min.wav --output temp\quality-comparison
```

預設使用已準備的 large-v3-turbo／CPU INT8／8 執行緒，可用 `--model`、`--threads` 與 `--profiles baseline multilingual independent` 指定。三組依序僅變更分段偵測、前文參考；共用已載入模型與音訊，耗時不含載入及解碼，不等於 UI 任務總耗時或準確率。

提示詞比較可指定 `--profiles baseline no_prompt terms_only --terms "API GLT-123" --seed 42`：依序使用既有中文引導、無提示、僅術語提示，固定其他參數。`--terms` 只用於 terms_only，不將完整參考稿當成提示；各組提示仍限制在 200 tokens。`--seed` 可在每組開始前重設 CT2 隨機種子，並不保證跨電腦或不同硬體完全一致。

也可測試保留音高的語速變化（原速、0.9、0.85 倍），預設取前 70 秒、baseline 參數，每組重設相同隨機種子。使用 PyAV 內建 atempo，不需額外安裝 FFmpeg；輸出 WAV、原文及名義換算回原檔的時間戳。這是比較工具，尚未套用於 UI：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_speed.py temp\mixed_zh_en_5min.wav --output temp\speed-comparison
```

可用 `--start`、`--duration`、`--profile` 選擇區間及固定參數組。放慢會改變音訊長度與視窗邊界，不保證辨識改善；變速及模型本身也可能使時間戳偏移。

檔案上限 4 GiB、音軌上限 120 分鐘，使用第一條音軌。這些限制可在 `app/config.py` 調整；媒體編碼是否可解碼仍需實際檢查。專有名詞用來提示模型，不會強制替換；完整提示超過 200 tokens 會顯示縮短提示，字數不等於 token 數。

關閉或重新整理瀏覽器不會停止轉錄。停止服務請在啟動視窗按 **Ctrl+C**，通常需數秒清理；再次使用可重新啟動。8000 埠被其他服務占用時會報錯，不會把其他服務當成本工具。

正常 Ctrl+C 停止會顯示「本機逐字稿服務已停止」，不印 KeyboardInterrupt traceback。CMD 仍可能顯示 `Terminate batch job (Y/N)?`，這是批次檔的中斷確認，輸入 `Y` 即可結束；不是轉錄模型失敗。

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

需要其他模型時將 `--model` 改成 `base`、`medium` 或 `large-v3-turbo`。模型準備期間請停止轉錄服務；不要同時更新正在使用的模型。日常 `start.bat` 不會執行安裝、更新或模型下載。

```powershell
.\.venv\Scripts\python.exe scripts\prepare_models.py --model large-v3-turbo
```

UI 列出所有支援模型；未備妥的模型顯示「未準備」且不可選。新增模型支援的程式更新後，需停止／重新啟動服務並重新整理頁面。turbo 目前同樣使用 CPU INT8，不代表已加入 NVIDIA GPU 模式，也沒有本機加速倍數保證；長會議前先測短片段。

如果下載進度到 100% 後發生例外，先保留 `models/`，不要刪掉重新下載。檔案及 HF 下載來源紀錄齊全時，可完全離線比對雜湊、載入模型與 VAD，再標記可用：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_models.py --model medium --verify-only
```

只在全部檢查通過後才寫入 `ready.json`；若版本不一致、缺少來源紀錄或雜湊不符，請再執行正常準備指令以重用／續傳既有下載，不能手動建立 ready.json 跳過驗證。`hf_xet` 未安裝的訊息代表改用一般 HTTP 下載，本身不是失敗原因；不必為了該提示額外安裝套件。

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
- 新任務採保守繁體轉換（`s2t-conservative-v1`）：僅轉換簡體專用字，保留「只、后、干、里」等共用字，避免誤改既有繁體；不保證所有歧義詞都轉成預期寫法。既有結果不會自動重轉，仍可下載原始 TXT。
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

### 固定五分鐘中英測試素材

Windows 已安裝繁體中文與英文語音時，可離線生成測試 WAV、對照文字與區塊時間／雜湊清單：

```powershell
.\.venv\Scripts\python.exe scripts\create_test_audio.py
```

輸出於 temp/mixed_zh_en_5min.wav、同名 .reference.txt 與 .json。音訊固定 300 秒、16 kHz／單聲道／PCM 16-bit，涵蓋術語、型號、數字、單位、否定與承諾語句；已存在的 WAV 不覆蓋。比較模型時固定使用同一音檔並記錄 SHA-256。

此素材是中文／英文兩種合成語音交替，不是自然多人會議，也不是精準說話者分離標註；數字口語讀法與參考文字需人工核對。通過合成測試不能取代 R01 等真實錄音驗收，所有音訊輸出維持排除 Git。
