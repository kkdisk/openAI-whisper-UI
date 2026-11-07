# GPU 版本開發交接

> **2026-11-07 v0.5.0 接續更新**：M7 第一輪已實作 auto／cpu／cuda、GPU 精度及編號、共用裝置政策、限時離線診斷、首段前乾淨程序 CPU 回退、任務／匯出紀錄與比較工具。實機確認 RTX 2060 6 GB／驅動 621.01，已安裝可選 `gpu` 執行庫；small／medium／large-v3-turbo 已按本文 revision 準備，三模型九組 CPU／CUDA 短語音比較均完成。small 真實 GPU 任務、反覆暫停／繼續及暫停取消保稿已驗證。使用者回報可以運作，授權 commit／push／新建 v0.5.0 tag，既有 tag 不移動。最新證據與待辦見 [validation](validation.md) 最末節及 [todo](todo.md)；下方為開發前交接快照，其「GPU 尚未實作／新機未知」不再代表目前版本。H20 尚未開始，長錄音／人工品質、完整 large-v3 及剩餘故障矩陣仍待完成。

更新日期：2026-11-07（Asia/Taipei）。此文件將對話中的專案知識保存於 Git，供另一台電腦及下一次 AI 開發工作接續。硬體／相依版本是本次快照，換機後必須重新確認。

## 1. 從這裡接續

使用者已確認 v0.4.0 人工驗收 OK，下一個目標是製作自動選擇 GPU／CPU 的版本。**GPU 目前尚未實作。** 本次只保存知識、規劃與驗收文件，未安裝 CUDA、修改引擎或變更已接受的版面。

- 遠端：`https://github.com/kkdisk/openAI-whisper-UI.git`，分支 `main`。
- 功能基線：`80df32b79f7b16ae604fd076e73cd8f89d90a8b8`；annotated tag `v0.4.0` 指向此提交。前置人工編輯與規劃提交為 `143ee5d`。
- 交接文件在後續文件提交中；換機請取得最新 `main`，只 checkout `v0.4.0` 會漏掉本文件與交付後人工接受紀錄。
- 先讀本文件、[人工輔助規劃](human-assisted-transcription.md)、[驗證紀錄](validation.md)，再檢查現有程式；[todo](todo.md) 的未勾選項不等於功能完全未寫，可能是尚有實機驗收。
- 本次只要求 commit／push `main`；不新增或移動標籤，專案版本維持 0.4.0，不建立 GitHub Release。

## 2. 已完成與尚未完成

| 項目 | 狀態與邊界 |
|---|---|
| 本機離線 CPU 辨識 | faster-whisper／CTranslate2，CPU INT8；主程序保存結果、Windows 子程序推論 |
| 逐段預覽 | SSE 加分頁補查、穩定片段 ID；重新整理可恢復已保存結果 |
| 人工修訂 | 改字、新增、刪除／復原、還原原文、自動儲存、修訂匯出；原始 TXT 不變 |
| H10 暫停／繼續 | 合作式暫停，工作程序確認後才算暫停；可反覆繼續、取消，保護修訂與終態 |
| 驗證 | 發行前 `pytest --real-model -q` 為 94 passed、無略過；Ruff、JS 語法、鎖檔與差異檢查通過，保留 1 項既有 TestClient 棄用警告 |
| 人工接受 | 使用者明確回報 OK；附圖呈現已完成及修訂已儲存。未逐項列出操作案例，不能推論 GPU／資源或準確率已驗證 |
| H10 尚缺 | 真實模型暫停等待時間及 CPU／RAM 量測；因此 H10 整項仍未勾選 |
| H20～H50 | 回聽校稿狀態、修訂歷程／定稿、局部重辨識、服務關閉後續跑尚未實作 |
| M7／M8／M9 | GPU、自動裝置選擇、完整 large-v3、說話者分離／姓名／歸屬仍未完成 |

已記錄的人工截圖：來源 00:05:53、總耗時／活動 00:04:49、暫停 00:00:00。這是使用者接受的畫面狀態，不能當作 GPU 基準或暫停延遲測量。

## 3. 原開發機與相依快照

- 原開發機唯讀查得 Intel Iris Xe Graphics，CTranslate2 CUDA device count 為 0；沒有 NVIDIA GPU 驗收證據。不能將原機偵測結果沿用到新機。
- 新機的 GPU 型號、顯存及驅動尚未提供；既有規劃提到 RTX 2060 6 GB 是條件式測試對象，**不是已確認的新機規格**。
- Python 限定 `>=3.10,<3.11`，既有驗證為 3.10.11；以 pyproject.toml／uv.lock 為準，不在準備 GPU 時順便全面升級 Python 或套件。
- 本機套件：faster-whisper 1.2.1、ctranslate2 4.8.5、av 16.1.0、onnxruntime 1.23.2。
- 目前 PyAV 負責解碼，無需全域 FFmpeg；VAD 使用套件內資產及 onnxruntime。GPU 辨識不代表解碼／VAD 都會移到 GPU。
- 現有 ASR 不需要先安裝整套 PyTorch／WhisperX／pyannote 才能做 CUDA；說話者分離是後續獨立工作。

2026-11-07 查閱的官方資料：

- [faster-whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu)：目前說明為 cuBLAS／CUDA 12 與 cuDNN 9；Windows 安裝須按官方來源及實際 DLL 載入驗證，不照抄 README 中標示 Linux only 的 pip 安裝段。
- [faster-whisper usage](https://github.com/SYSTRAN/faster-whisper#faster-whisper)：CUDA 範例有 `float16` 與 `int8_float16`；segments 為延遲執行的 generator，只建立模型或取得 generator 不代表推論成功。
- [CTranslate2 quantization](https://opennmt.net/CTranslate2/quantization.html)：可於載入選擇精度，硬體可能發生隱含型別轉換；需查詢支援精度並記錄實際運算設定。
- [CTranslate2 installation](https://opennmt.net/CTranslate2/installation.html) 的 cuDNN 描述與上述 faster-whisper 文件曾有差異，不能混搭假定可用。以選定版本的依賴、DLL 與實際語音推論建立本專案相容矩陣，換機時重新核對。

以上是技術來源快照，不是新機已安裝或已通過驗收的聲明。

## 4. 下一階段：優先 M7 自動 GPU，再接 H20

此順序依使用者最新意圖調整，優先於先前「H10→H20→H30」的單一路線；完整人工輔助規劃仍保留。

1. **建立環境診斷（T70.2／T70.3 基礎）**：辨識 NVIDIA 裝置、驅動、顯存、DLL 與支援精度；獨立子程序執行有限時的載入／短音訊推論，故障不拖垮 UI。分開報告未偵測裝置、驅動／DLL 不符、精度不支援與顯存不足。
2. **加入 auto／cpu／cuda（T70.2／T70.4）**：保留手動選擇，裝置與精度決策集中在共用模組，UI、工作程序、診斷與比較工具不要各自猜測。選定值及實際值存入任務／匯出資訊；舊任務沒有裝置欄位時按原 CPU 行為讀取。
3. **同音檔比較（T70.5／T70.7）**：先 small 驗證完整 CPU／CUDA 流程，再測 medium／large-v3-turbo 的 int8_float16 與 FP16；依硬體支援與可用顯存決定測試組合。完整 large-v3 尚不在 UI 白名單，不能假設已支援。
4. **GPU 任務生命週期**：驗證 H10 暫停／繼續、取消、父程序死亡、模型失敗／OOM、SSE 重連、人工修訂與匯出。暫停仍占 VRAM，不宣稱卸載模型或釋放顯存。
5. **再接 H20 回聽校稿**：先來源核對、句段播放器、待確認／已確認，之後 H30 歷程與定稿；H40 局部重辨識及 H50 斷點續跑另做可行性驗證。

### 自動模式的建議規則

- 有 CUDA 裝置不等於 GPU 可用：須驗證模型載入與真正的短語音推論，實際消費 generator。純靜音可能被 VAD 跳過，不能作唯一 GPU smoke test。
- auto 在產生正式片段前，對可分類的 CUDA 環境／資源失敗可回退 CPU，明確記錄 fallback_reason；缺模型、提示超限、檔案損壞等不是 GPU 回退理由。受損 CUDA 執行環境宜換乾淨工作程序，不在不確定上下文中直接重用。
- 明確選 cuda 時失敗即回報；不靜默改 CPU、不降模型。
- 一旦正式片段已提交，執行期 CUDA 失敗保留部分稿與人工修訂，不自動從頭重跑或拼接不同裝置結果。
- 第一輪保留目前逐段迭代及單一活動任務；批次推論另測，避免同時改變速度、VAD、片段切分與暫停行為。
- GPU 預設精度／模型由實測決定；不承諾特定倍速，也不把成功執行當成文字品質改善。

### 驗收矩陣

- 無 NVIDIA／DLL 缺失／不支援精度、auto 回退與明確 cuda 失敗；明確 cpu 必須不初始化 CUDA。
- 多張卡時記錄實際 device index，選擇策略可解釋；診斷結果是時間點快照，啟動任務時仍需重新處理資源競爭。
- 載入及首段前失敗、首段後 OOM、子程序崩潰、取消／完成／暫停競爭；部分稿與人工修改不得遺失。
- CPU／GPU 用同來源指紋、模型 revision、語言、beam、VAD、前文與提示；活動耗時、載入／解碼、RAM／VRAM 峰值分開記錄。檢查中文、英文術語、數字、否定句、尾端及重複。
- GPU 測試採明確 opt-in；CPU-only 機的 mock／單元測試只算邏輯驗證，不算 GPU 實測。
- 新機先短樣本，再代表性長錄音；H10 真實模型暫停等待與資源量測可在此補齊。

## 5. 程式入口與必須保留的行為

| 路徑 | 用途／注意事項 |
|---|---|
| app/transcriber.py | 目前固定 cpu／int8；辨識前離線載入模型及 VAD。controlled_segments 在取下一筆前檢查合作暫停 |
| app/process_control.py | Windows Job Object、啟動握手、取消及 CooperativeControl；暫停不能阻止取消／父程序死亡清理 |
| app/jobs.py | 唯一任務持有者、原子 metadata、JSONL 保存、控制確認與計時；不持鎖等待工作程序 |
| app/schemas.py／config.py | 任務設定、狀態及模型白名單；GPU 欄位需相容舊任務 |
| app/models.py／scripts/prepare_models.py | 固定來源／revision、離線驗證與 ready.json；服務啟動不自動下載模型 |
| app/revisions.py | revisions.json 獨立保存人工改動、版本衝突拒絕、原稿與新增句穩定鍵 |
| app/main.py／app/static/app.js | 本機來源／CSRF、API／SSE；世代及控制序號保護過期回應，不搶編輯焦點 |
| app/formatter.py | 原稿／修訂匯出；人工新增句無時間不能匯出 SRT |
| tests/test_pause.py | 控制時序、計時、取消、恢復與真實 Windows 子程序；GPU 版須沿用這些保障 |
| scripts/benchmark_transcription.py | 現有比較工具仍是 CPU 路徑，不能直接當成 GPU benchmark；裝置選擇應與正式流程共用 |

execution_state 為 running／pause_requested／paused／resume_requested；工作程序以 control_seq 確認，終態／取消優先。status 仍是任務階段，不把 paused 插入原本的階段排序。

活動時間是總經過時間減去已確認暫停，包含模型載入與解碼，不是純 GPU kernel 時間。已保存位置是原稿進度，不是可靠的推論 checkpoint；關閉服務不能接續。

## 6. 語音品質實驗知識

- 保留模型 raw_text、保守簡轉繁文字與人工修訂，不以全域同音字替換取代辨識品質改善。
- mixed 分段偵測與 condition_on_previous_text 已可選；既有樣本英文仍有漏辨，不能宣稱切換設定已改善全部內容。預設沒有因實驗而改變。
- 放慢至 0.9／0.85 倍的既有短樣本實驗未證實完整性改善，未套入 UI。
- R02 約 232.656 秒朗讀樣本，在 turbo／CPU INT8／8 threads／beam 5／auto／前文開啟／相同 seed 下比較提示。中文引導 234.03 秒、無提示 215.18 秒、僅術語 284.77 秒；只計推論且每組一次，不是正式效能基準。
- R02 術語提示未可靠解決後段專名；數字、日期、專名仍須回聽。中文引導尾端有疑似重複及超出音軌的時間戳；保留原文，不自動裁切或刪除。
- R02 候選對照稿不是逐字人工標準稿，所以沒有 CER／WER；片段數不代表準確率。medium／turbo 已有四組差異及局部回聽，結論見 validation.md，尚不足以改預設。
- 完整轉錄及來源目前位於忽略的 temp／data；本交接保留方法與結論，不將私人錄音及逐字稿提交 Git。

## 7. 換機：Git 會帶什麼、不會帶什麼

Git 會攜帶程式、鎖檔、測試與本交接；`.gitignore` 排除 `.venv/`、`models/`、`data/`、`temp/`、`.uv-cache/`、`.uv-pristine/` 及日誌。

| 需另行攜帶的資料 | 建議處理 |
|---|---|
| models/small、medium、large-v3-turbo | 可選擇另行複製完整模型目錄（含 ready.json、tokenizer、詞彙及下載來源 .cache）或固定 revision 重新下載；新機再驗證 |
| data/jobs/<id> | 若要保留歷史，先停止服務，備份完整任務目錄；metadata、segments.jsonl、revisions.json 一起帶，不只複製匯出 TXT |
| 原始來源錄音 | app 完成後清除上傳副本，必須另存原音；目前沒有來源指紋核對／歷史播放器／續跑 |
| temp/fixtures/meeting.wav | 既有 opt-in 真實模型測試素材；缺少時測試可能略過，要如實記錄 |
| temp/mixed_zh_en_5min.wav 及 reference.txt | 固定合成語音比較素材；可用 create_test_audio.py 依其說明重新產生，重建後記錄新指紋，不假定逐位元相同 |
| temp/r02-input、r02-prompt-comparison、r02-model-comparison、quality-20261106、speed-20261106 | 按需要私下備份，包含來源錄音／參考稿／manifest／原文；不要整個 temp 打包，裡面還有測試瀏覽器 profiles |

本機已準備模型 revision 快照：

| 模型 | repo | revision |
|---|---|---|
| small | Systran/faster-whisper-small | 536b0662742c02347bc0e980a01041f333bce120 |
| medium | Systran/faster-whisper-medium | 08e178d48790749d25932bbc082711ddcfdfbc4f |
| large-v3-turbo | mobiuslabsgmbh/faster-whisper-large-v3-turbo | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf |

R02 來源 MP3 SHA-256：`0bac42b2c330e28e9ac335c174af8d5194da4d0d89416613aa22c67d403019b7`，可供私下搬移後核對；不將檔名當來源一致證據。

## 8. 新機開始操作（Windows PowerShell）

先備妥 Git、uv 與 Python 3.10；新機預計仍走 Windows。若是 Linux，需重新驗證／替換 Windows 程序保護，不能假定直接相容。

```powershell
git clone https://github.com/kkdisk/openAI-whisper-UI.git Whisper
Set-Location Whisper
git status --short
git log -3 --oneline
uv sync --locked --group dev
```

已有 checkout 時改為核對狀態後 `git pull --ff-only origin main`；有本機修改先保留，不 reset 或 clean。不要直接搬舊 `.venv`，虛擬環境及原生 DLL 可能綁定舊機。

先唯讀診斷，指令失敗本身也要記錄，不立即重裝驅動：

```powershell
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv
.\.venv\Scripts\python.exe -c "import ctranslate2 as c; print('CUDA count:', c.get_cuda_device_count())"
.\.venv\Scripts\python.exe -c "import ctranslate2 as c; print(c.get_supported_compute_types('cuda', 0))"
```

第三個指令只在裝置 0 可用時執行；它不是模型推論測試。記錄實際套件版本，再確認 CUDA／cuDNN 對應；任何環境修改應在服務停止後進行，保留可重建的 CPU 基線。

複製模型後，可用以下離線來源／資產與 CPU runtime 驗證；這個命令**不代表 GPU 驗證**：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_models.py --model small --verify-only
```

若未攜帶模型且允許聯網，固定既有版本準備 small：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_models.py --model small --revision 536b0662742c02347bc0e980a01041f333bce120
```

先跑現有回歸；真實模型測試缺 fixture 或模型時會 skip，必須查看結果，不能宣稱零略過：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
uv lock --check --offline
git diff --check
# 備妥原本的 fixture 或設定 WHISPER_TEST_AUDIO 後：
.\.venv\Scripts\python.exe -m pytest --real-model -q
```

現有 GPU 診斷／選擇功能尚不存在，不能把 start.bat 正常啟動或上述 CPU 測試通過當成 GPU 已完成。

## 9. 給下一次 AI 工作的接續文字

> 請先讀 tasks/gpu-development-handoff.md、tasks/human-assisted-transcription.md、tasks/plan.md、tasks/todo.md 與 tasks/validation.md，確認目前 Git 與實作。v0.4.0 人工驗收已由使用者確認，GPU 尚未實作。這台是接續開發的 GPU 電腦，請先唯讀確認實際顯卡、顯存、驅動與套件，再依 M7 提出當機可驗證的診斷／auto-cpu-cuda 方案並進行授權範圍內的實作。保留離線、原稿／人工修訂、H10 暫停確認及取消清理；無裝置／DLL／OOM 要可解釋，不覆蓋部分稿或默默重跑。先 small，再 medium／turbo；不以 CPU mock 代替 GPU 實測，不更動既有標籤。若缺少忽略目錄下的模型／音檔，先清楚列出，依本文固定 revision 準備或請使用者提供私下素材。GPU 完成後再接 H20 回聽校稿。
