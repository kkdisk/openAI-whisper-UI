# 實作與驗證紀錄

> 日期：2026-11-06。本文件是當次執行快照，不是背景任務的即時狀態；最新狀態以本機 UI 歷史清單或 data/jobs metadata 為準。

## 已完成的本機實作

- Python 專案、uv.lock、原生依賴、模型準備腳本與 small 本機模型。
- 中文／英文術語轉錄、VAD、提示 token 檢查、原始／繁體文字與 TXT／MD／SRT 匯出。
- Windows spawn／Job Object、協作與強制取消、JSONL／原子 metadata、歷史恢復及刪除。
- JSON 建任務、原始串流上傳、大小檢查、期限管理、SSE／分頁補查、本機 session／CSRF。
- 原生單頁 UI、start.bat、README，以及終態後讀取既有結果的 export_job.py。

這些功能已寫入程式，不代表所有場景均通過驗收。尤其瀏覽器操作、串流競爭及真實長檔仍有下列限制。

## 環境與版本

| 項目 | 實際結果 |
|---|---|
| Python／uv | 3.10.11／0.12.5 |
| faster-whisper／CTranslate2 | 1.2.1／4.8.5 |
| PyAV／onnxruntime | 16.1.0／1.23.2 |
| OpenCC Python | 0.1.7 |
| FastAPI／uvicorn | 0.162.0／0.54.0 |
| 模型 | Systran/faster-whisper-small |
| 模型 revision | 536b0662742c02347bc0e980a01041f333bce120 |
| CPU 執行類型 | 原生載入確認支援 int8 |
| 初始可用磁碟 | D 槽約 69.8 GiB（安裝前快照，之後會變動） |

已以乾淨快取重裝鎖定依賴，再執行 `uv sync --locked --offline --group dev --cache-dir .uv-pristine --link-mode copy`，鎖檔／環境檢查成功。

## 自動驗證

| 檢查 | 結果與範圍 |
|---|---|
| pytest --real-model | 40 passed，17.72 秒；包含合成語音真實模型測試 |
| 一般 pytest | 真實模型預設 skip；不把 skip 當辨識驗收 |
| ruff check | 通過 |
| ruff format --check | 通過 |
| node --check app/static/app.js | 通過；僅語法，不等同瀏覽器操作驗收 |
| Windows 程序測試 | spawn 成功、阻塞程序強制取消、工作程序崩潰、父程序綁定前後被強制結束均有覆蓋 |
| 任務／資料 | 獨占、上傳取消保留位置、歷史恢復、截斷 JSONL、非法時間保存、metadata 替換短暫拒絕重試 |
| API | 存取防護、大小不符清理、早期拒絕不讀媒體、歷史／匯出／刪除、終態 SSE、JSON 大小限制 |

測試有 1 個第三方警告：Starlette 提示 httpx TestClient 將淘汰；目前鎖定組合仍全部通過。後續更新需重新確認相容性，不能以增加套件掩蓋警告。

一般測試限制外部 socket 連線，保留 Windows asyncio 必需的 loopback 連線。這與實體斷網的人工驗證不同。

## 真實模型短檔驗證

- 使用 Windows 內建中文語音合成非敏感的 10.8 秒測試語音，包含中文、API 與版本數字。
- PyAV 轉成 MP4／AAC；移除 PATH 並禁止外部連線後，small＋CPU INT8＋4 執行緒產出 3 段非空文字。
- Whisper、tokenizer 與套件 VAD 載入／推論成功，不依賴全域 FFmpeg。
- 實際 HTTP 服務也跑通「建任務 → PUT 上傳 → 工作子程序 → 完成 → TXT／MD／SRT 下載」；三種檔案均保留英文術語。
- 終態後來源副本已刪除，export_job.py 另行讀取已保存資料匯出成功。
- 素材及輸出位於忽略的 temp／data，不納入 Git。合成語音不能代表真實會議辨識品質。

## 使用者長錄音：R01

使用者提供的本機 M4A，約 **108 分 7 秒**，AAC／48 kHz、約 52.4 MiB。原檔名稱與逐字稿只保留在本機，不寫入此報告。

- 首次完整工作在已保存約 5 分鐘、166 段時出現 storage_or_ipc 錯誤；已保存片段與 partial 匯出均保留。
- 當時診斷不足，**不能證實根因**。已補上不含逐字稿的例外類別／程式位置／errno 診斷，以及 Windows metadata 原子替換遇短暫 PermissionError 的有界重試，並加入回歸測試。
- 重跑快照：已保存約 **12 分 15 秒、543 段**，仍 transcribing，未出現新的錯誤或診斷警告；已超過先前失敗位置，但尚未跑完整份，不能視為修正已經長檔驗收。
- 此次工作同時有開發與測試負載，不當作專用效能 benchmark；未做 medium／4／6／8 執行緒比較，也未完成峰值 RAM 量測。
- 已啟動本機等候匯出程序，任務終止後寫入 `data/exports/<job_id>/`，包括原始 TXT、顯示 TXT、MD、SRT；非成功終態使用 partial 標記。若等候程序被關閉，可手動重新執行。

```powershell
# 僅讀取既有任務；不更動狀態、不啟動推論
.\.venv\Scripts\python.exe scripts\export_job.py <job_id> --wait
```

最新任務 ID 可在本機 UI 查詢；開發時的快照另存於忽略的 `temp/real-job-id.txt`。服務持續運作於 `http://127.0.0.1:8000`，目前工作完成前不要重啟或更新模型。

## 尚未完成的驗收

- 真實長檔完整完成、末尾文字／時間軸與記憶體目標。
- 代表性錄音的多模型／執行緒效能比較及人工術語、型號、否定詞、決議核對。
- 實際斷網、真實模型各階段取消，以及直接關閉命令視窗的人工驗證。
- 上傳閒置／總期限、寫入失敗、取消競爭、IPC 背壓與 SSE 斷線重連期間的完整故障矩陣。
- 瀏覽器實際操作：Windows 自動操作請求逾時，內建瀏覽器 access host 不可用；尚未完成視覺與點擊流程驗收，不能用 HTTP 測試替代。
- 離線 wheelhouse 搬移到另一台電腦；README 已列出流程，但尚未在目標機實測。

## Git 邊界

Git 初始化時，本機 main 追蹤 `https://github.com/kkdisk/openAI-whisper-UI.git` 的 origin/main，基礎為遠端 `421c1b0` 初始 README 提交；當時保留本機 README，尚未 commit／push 程式。後續交付狀態以 Git refs 為準。

已確認 .venv、模型、temp、data、套件快取均被 .gitignore 排除。遠端倉庫為公開，交付僅包含程式、測試、規格、鎖檔與說明文件，不傳送錄音、逐字稿或模型。

## 啟動問題修復補記

- 使用者回報 CMD 路徑／亂碼錯誤與 `av.about` 缺失。start.bat 原為 LF；已改成無 BOM ASCII＋CRLF、setlocal／pushd、引號化環境變數、依賴預檢與正確退出碼，並加入 `.gitattributes` 防止換行再次漂移。
- 實際發現 .venv 的套件內容不完整，包含缺少的 av.about；部分套件安裝紀錄仍存在，因此一般 sync 不足以修復。缺檔成因尚未證實。
- 背景工作鎖住原生 DLL，直接強制重裝被 Windows 拒絕。另建同鎖檔的乾淨暫存環境，逐檔比對補回缺失內容，原生相同檔案不覆寫；重新安裝不被工作程序鎖住的開發工具與 HTTP 客戶端，補回 PyAV console 入口。
- 原有不同內容的檔案另存於忽略的 temp/environment-repair-backup。轉錄工作持續執行，沒有為此次修復停止或重啟服務。
- 修復後全部已安裝 distribution 的 RECORD 路徑檢查無缺檔；FastAPI、uvicorn、faster-whisper、PyAV／av.about、OpenCC、onnxruntime 與 VAD 載入／推論通過。
- CMD 從不同工作目錄可正確進入 app.main，收到預期的 8000 埠占用提示；不因此停止既有服務。新增兩項啟動回歸測試（編碼／換行與含中文空白路徑），一般測試為 **41 passed、1 skipped**；ruff 檢查及格式檢查通過。這次沒有重跑 opt-in 真實模型測試。
- README 補上停止服務後才完整重裝的修復步驟；啟動檔不自動安裝或聯網。

## v0.1.0 交付前檢查

- 版本依據：pyproject.toml 的 0.1.0；預定 annotated tag 為 v0.1.0，分支為 main，遠端為上述 origin。
- 修復後完整執行 `pytest --real-model -q`：**42 passed**，18.58 秒；Ruff 檢查／格式檢查、JavaScript 語法與 Git 差異檢查通過。第三方 TestClient 淘汰警告仍有 1 項，未影響測試結果。
- 使用者截圖已確認本機頁面、轉錄進度及逐字稿預覽正常呈現；尚未因此推定所有取消／下載／恢復操作均通過人工接受。
- R01 長錄音在交付前快照仍持續轉錄，已超過原失敗位置且無新錯誤；完整長檔、峰值記憶體、模型效能比較與人工辨識品質仍維持待驗證，不以本次 tag 代替這些驗收。
- 沒有前端編譯或 EXE 打包產物；此版以 Python 原始碼、鎖檔與啟動腳本交付。commit／tag 推送不等同 GitHub Release 或 CI 通過。

## R01 完成與下一階段規劃補記

此補記取代前文 R01 仍執行中的快照。已重新讀取本機 metadata 與匯出 Markdown：狀態 completed，音軌約 108 分 7 秒，5,087 段，最後保存位置約 1:48:05，耗時 3,457.407 秒（約 57 分 37 秒），error_code 為空、warnings 為空。這證明程序跑到終態，不證明所有語音都被正確辨識；仍需原音末尾核對、資源量測與人工品質驗收。

文字檢查觀察：

- 時間戳未見倒退；片段字數中位數 4，3,400／5,087 段（約 66.8%）不超過 5 字。這是閱讀粒度統計，不是辨識錯誤率。
- 01:25:05～01:25:14 有同一文字連續 10 次，原始辨識結果即存在，不是 Markdown 或繁體轉換產生；是否為幻覺或原音重複仍須回聽。
- 共 88 段經繁體轉換改變；確認有「只有」轉成「隻有」的副作用，須與模型辨識問題分開處理。
- 跨時間區間抽樣可見語意可疑的技術詞，但未建立人工參考稿，不能宣稱 CER／WER 或猜測正確人名、型號與詞彙。
- 本紀錄只保留樣本代號、時間區間及非敏感統計，不收錄完整會議內容或原檔名稱。

使用者已同意將上述品質問題、CPU／GPU 模型比較、pyannote 分離與人工姓名對應納入規劃。SPEC 第 13 節、plan 的 M6～M9、todo 的 T60～T90 均是新規劃，沒有安裝新依賴、下載新模型、修改程式或改變 v0.1.0 的既有行為。

另依使用者要求中斷本工具的 app.main 程序，確認對應程序退出且 8000 無 listener；既有錄音與逐字稿保留。之後如重新啟動，埠與程序狀態需重新確認，本紀錄不代表即時狀態。

## Ctrl+C 正常停止修復

- 使用者回報的 traceback 發生於 Uvicorn 重送捕捉到的 SIGINT：Python 3.10 相容層使 KeyboardInterrupt 離開 server.run，並非該 traceback 證明模型轉錄失敗。
- app.main 僅捕捉 KeyboardInterrupt，正常停止顯示繁體中文訊息，finally 再確認任務管理器關閉與 socket 釋放，涵蓋 lifespan 尚未啟動就中斷的情況。其他例外不因此被隱藏。
- 新增清理呼叫回歸與獨立 Windows 子程序的真實 Uvicorn／SIGINT 整合測試：程序正常回傳 0，無 KeyboardInterrupt／Traceback。測試使用臨時資料及隨機可用埠，不操作使用者轉錄結果。
- 本輪一般測試 **43 passed、1 skipped**（真實模型 opt-in 未重跑），Ruff、格式及差異檢查通過；既有 TestClient 第三方警告仍在。
- CMD 的 `Terminate batch job (Y/N)?` 是批次中斷確認，仍可能出現，輸入 Y 即可；README 已補充，不宣稱程式能消除 CMD 自身提示。

## Medium 準備復原與 turbo 候選

- 使用者的 medium 下載已落盤，但 snapshot_download 結束階段曾出現例外；提供內容缺少最後的例外類別／訊息，原始原因尚不能確認。hf_xet 的 HTTP 回退提示本身不是錯誤。
- medium 的必要檔案已依 HF 本機來源紀錄比對完整雜湊，revision 為 08e178d48790749d25932bbc082711ddcfdfbc4f。模型、tokenizer 與 VAD 的禁止連網載入已通過，已寫入 ready.json，模型清單確認可選；沒有重新下載權重，未宣稱真實語音辨識品質通過。
- prepare_models.py 新增 --verify-only、序列下載及驗證成功後原子標記；檔案損毀、來源紀錄缺少／版本不同、載入失敗及下載失敗均有回歸覆蓋。最新一般測試 49 passed、1 skipped，Ruff／格式檢查通過；真實模型 opt-in 未重跑。
- 下一階段模型矩陣加入 large-v3-turbo。官方模型卡支持其 decoder 精簡的設計，但本機尚未準備／實測 turbo，沒有更改 CPU 預設，也未移除 medium；原始錄音與模型比較內容仍只放本機。

## Turbo 選項與五分鐘合成素材補記

- turbo 原先只在規劃中，現已接入共享模型型別、UI 模型清單、API 請求及準備來源；未下載者仍反灰，不自動改用其他模型。
- 本機 large-v3-turbo 來源 mobiuslabsgmbh/faster-whisper-large-v3-turbo，revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf。下載、必要檔案雜湊與 CPU INT8／tokenizer／VAD 禁止連網載入通過，ready.json 已寫入；未做模型品質或 GPU 效能驗收。
- 模型清單／schema 一致性與 API 未備妥拒絕、備妥後接受已有回歸測試。既有 small／medium 設定與歷史結果保留；運行中的服務需要重啟才能載入新的後端清單。
- 依使用者要求產生 temp/mixed_zh_en_5min.wav，固定 300 秒、16 kHz、單聲道、16-bit PCM，9,600,044 bytes；峰值絕對振幅 30,855、削波樣本數 0。16 個中英交替區塊使用 Microsoft Hanhan Desktop／Microsoft Zira Desktop，語音速度 2，合成區塊共約 289.51 秒，區塊間隙約 0.62 秒。
- 同名 .reference.txt 與 .json 包含參考內容、區塊時間及 SHA-256；檔案雜湊及內容一致性已核對。此為合成朗讀，不是真實會議，也不作字詞時間或自然說話者分離的人工標註。
- scripts/create_test_audio.py 可重建素材，已存在 WAV 時拒絕覆蓋；不依賴雲端或新增語音套件。輸出只放忽略的 temp，不納入公開倉庫。

## 審查修正驗證（2026-11-06）

- 上傳原始串流的 close／flush 例外會轉成 upload_failed；仍執行 end_upload 及任務收尾。隔離回歸涵蓋單獨關閉失敗及寫入／關閉同時失敗，確認 receiving 清除、任務 failed、媒體副本刪除且可建立下一個任務。
- 完成任務恢復時核對 JSONL 完整片段數與原 metadata，檔案遺失、空檔、完整行減少／增加、尾端不完整均標 interrupted 並提示；保留可讀片段與 partial 匯出。合法零片段仍可 completed；未完成任務允許 JSONL 先於 metadata 落盤。
- 前端維持單一預覽 SSE，另在存在活動任務時每 2 秒查詢活動槽位。Node VM 情境測試涵蓋切換至歷史結果後背景完成、不覆蓋目前預覽、過期回應不覆蓋新任務、暫時離線可重試；尚未作實際瀏覽器人工接受。
- 新任務記錄 s2t-conservative-v1：以 OpenCC 詞組結果作候選，只替換 STCharacters 中非繁體候選的簡體專用字，保留共用字及原始 raw_text。回歸涵蓋「是只有」、皇后／干涉／里程、開發／頭髮及英文型號。共用字不猜選，可能保留簡體慣用寫法；不自動重轉歷史逐字稿。
- 全部一般測試 **66 passed、1 skipped**；其中 pytest 呼叫 Node，另有 3 個前端情境通過。Ruff、Python 格式、JavaScript 語法與 git diff --check 通過。既有 Starlette TestClient 的第三方棄用警告仍在。
- 真實模型 opt-in 及五分鐘中英音檔本輪未重跑；這些修正不代表多語辨識品質問題已解決，也未 commit／tag／push。

## v0.2.0 提交前檢查（2026-11-06）

- 版本同步 pyproject.toml、uv.lock 及 README；新增 annotated tag v0.2.0，保留 v0.1.0 不移動。來源為 main，origin 為 https://github.com/kkdisk/openAI-whisper-UI.git。
- 全部測試含明確啟用的本機 small 真實模型／短 MP4 離線轉錄：**67 passed**，沒有略過；既有 TestClient 第三方棄用警告 1 項。Node 的 3 個前端情境由 pytest 執行。
- Ruff、Python 格式、JavaScript 語法與差異檢查通過。模型、音檔、轉錄結果及快取仍在忽略目錄，不納入提交。
- 本版交付的是原始碼及文件，沒有打包 EXE、建立 GitHub Release 或執行 CI；未重跑五分鐘音檔品質比較，瀏覽器人工操作及 GPU／說話者分離驗收仍未完成。

## 中英交替模式及參數比較（v0.2.0 之後的開發變更）

- UI 新增實驗性 mixed 模式（每個解碼視窗重新偵測語言），進階設定可關閉 condition_on_previous_text。預設仍為 zh／前文開啟；舊資料缺少新欄位時依原設定讀取。任務保存及 TXT／MD 記載設定，原文及既有結果不重寫。
- scripts/benchmark_transcription.py 使用相同核心選項建構函式，依序執行 baseline（開頭偵測＋前文）、multilingual（分段偵測＋前文）、independent（分段偵測＋無前文）；必要模型須已準備，不下載或覆蓋結果。
- 固定五分鐘合成 WAV，SHA-256 fd615b45b1c7e897f191829c21a234778eec51ab0b30cfce55e48862f27ab962。turbo revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf，CPU INT8、8 執行緒、beam 5、同一初始提示及預設 VAD；共用已載入模型及音訊，每組一次。

| 參數組 | 推論耗時 | 輸出片段 | 完整英文段落（共 8 段） |
|---|---:|---:|---|
| baseline | 575.42 秒 | 54 | 0 |
| multilingual | 299.21 秒 | 41 | 3（承諾／協助／提問、模型比較、結尾） |
| independent | 181.82 秒 | 64 | 4（API、品質報告、模型比較、結尾），首段另僅保留尾句 |

- 「完整英文段落」為對照腳本文字的內容保留檢查，容許標點、數字／型號表記差異；不是 CER／WER 或語音人工驗收。三組都未通過完整性檢查，後兩組亦有中文段落遺漏及姓名／術語誤辨；不能只看英文增加或單次速度決定預設。
- 基準組仍有重複與英文被錯誤中文取代。結果與使用者先前的單次輸出有差異，未固定採樣隨機種子、前景／背景及系統負載；基準組期間曾執行一般測試與 VAD 診斷，故時間僅作探索紀錄，不宣稱參數帶來固定加速倍數。
- 以相同預設 VAD 對照全部 16 個腳本區塊，區塊音訊均完整保留；本例的英文遺漏不是 VAD 整段刪除所致。此結論限本測試檔。
- 本機 temp/quality-20261106/ 保存 manifest、各組完整 JSONL／摘要 JSON／TXT、vad-coverage.json 及 comparison.md。對照報告按時間重疊摘錄，非精準對齊；全部留在忽略目錄。
- 一般測試 72 passed、1 skipped（本機真實模型短 MP4 opt-in 本輪未重跑；三組完整合成檔推論另如上）。Node 4 個情境通過；包含新設定的 API 保存／恢復／匯出、實際引擎傳參及舊設定相容。Ruff／格式／JavaScript 語法通過；新 UI 瀏覽器人工驗收尚未完成。未 commit／tag／push。

## 原速及放慢語速比較

- 依使用者同意，以相同合成檔前 70 秒比較原速、0.9、0.85 倍速，包含 2 段中文及 2 段英文。固定 turbo revision、CPU INT8／8 執行緒、beam 5、auto（開頭）、前文開啟、中文初始提示與預設 VAD，各組重設 CT2 隨機種子 42，每組一次。
- 新增 scripts/benchmark_speed.py，以 PyAV 的 atempo 保留音高，不另安裝系統 FFmpeg；保存 PCM WAV、原文、參數及音訊指紋，以 processed_sec * speed + clip_start_sec 名義換算時間戳。音訊拉伸及模型時間戳誤差仍在，並非精準對齊。

| 語速 | 音訊長度 | 推論耗時 | 首段英文 | API 英文 |
|---|---:|---:|---|---|
| 1.0 | 70.00 秒 | 55.47 秒 | 遺漏 | 保留 |
| 0.9 | 77.74 秒 | 156.23 秒 | 遺漏 | 錯誤混雜文字 |
| 0.85 | 82.31 秒 | 105.10 秒 | 遺漏 | 保留 |

- 本段未見放慢改善：0.9 反而退步，0.85 未恢復首段英文，中文新增「否定句→否定據」。不更改 UI 原速預設、不加入自動放慢；不將單次結果推論為所有音檔的結論。
- 三份 WAV 皆為 16 kHz、單聲道、16-bit PCM，削波樣本數 0。變速回歸以 440 Hz 正弦波確認長度拉長且音高仍約 440 Hz，原速樣本不改動；3 項新增測試通過。
- 全部一般測試最新 **75 passed、1 skipped**，保留既有 TestClient 第三方警告；本機真實模型短 MP4 opt-in 本輪未重跑，三組真實模型短檔推論另如上。原速組期間執行過短小變速測試，耗時僅記錄，非正式效能基準。
- 本機 temp/speed-20261106/ 包含三份 WAV／JSONL／TXT、摘要、manifest 及 comparison.md。產品辨識流程沒有加入變速；原始檔及既有結果保留，沒有 commit／tag／push。

## R02 新朗讀樣本與提示比較

- 使用者提供 R02 MP3，確認是朗讀提供的週報、內容大致相同。音軌 232.656 秒、48 kHz 雙聲道、5,584,175 bytes；依既有流程解碼為 16 kHz 單聲道。本機新建測試副本及 SHA-256 紀錄，未改動來源、未上傳。
- 候選對照文字不是逐字聽打標準稿，不計 CER／WER、不驗證報導事實，亦不把稿外語句直接當作幻覺。完整候選稿、來源指紋、原文與對照只保存於忽略的 temp/r02-input/、temp/r02-prompt-comparison/。
- 固定 turbo revision、CPU INT8／8 執行緒、beam 5、auto（開頭）、前文開啟、預設 VAD及 seed 42，每組一次，僅改 initial_prompt。未使用完整對照稿作提示。新增 no_prompt／terms_only CLI、--terms、--seed，保留原有三組預設比較；各組仍檢查 200 tokens 上限。

| 提示策略 | 推論耗時 | 片段數 | 最後輸出位置 |
|---|---:|---:|---:|
| 中文引導 | 234.03 秒 | 48 | 235.38 秒 |
| 無提示 | 215.18 秒 | 89 | 231.48 秒 |
| 僅英文術語 | 284.77 秒 | 102 | 231.48 秒 |

- 觀察：無提示及僅術語組保留「低於百萬分之一／特異性高於 99%」；中文引導組則出現 1.000.000／99.000，與候選稿不一致。兩組仍未可靠辨識 AVITI Dx、MAESTRO；僅術語組更將一處原本正確的 CLIA 變為 Celia。沒有全面勝出的設定，維持產品預設。
- 日期、Q30／百分比、部分中文術語及尾端皆列入回聽清單。中文引導組兩次重複尾句，部分時間戳超出音軌約 2.72 秒；保留原文，不直接刪除或裁切。不以單次耗時、片段數或個別術語出現當作準確率。
- 最新一般測試 **77 passed、1 skipped**；保留既有 TestClient 第三方警告。本機真實模型短 MP4 opt-in 本輪未重跑，另有上述三組完整 R02 真實模型推論。新增測試確認提示比較只改提示、不修改共用選項，且空術語清單被拒絕。未 commit／tag／push。

## R02 medium／turbo 比較（2026-11-07）

- 依使用者要求，補跑 medium 的中文引導與無提示兩組，沿用前輪 turbo 對應兩組。逐項核對音檔 SHA-256、時長、seed、CPU 執行緒、device／compute_type、引擎版本及兩種提示策略的完整選項一致；只有模型及固定 revision 不同。比較指紋存於本機 comparison-manifest.json。
- medium revision 08e178d48790749d25932bbc082711ddcfdfbc4f；turbo revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf。各組 CPU INT8／8 執行緒、beam 5、auto（開頭）、前文開啟、預設 VAD、seed 42，每組一次。

| 模型 | 中文引導推論耗時 | 無提示推論耗時 |
|---|---:|---:|
| turbo（沿用前輪） | 234.03 秒 | 215.18 秒 |
| medium（本輪） | 431.73 秒 | 314.41 秒 |

- 耗時不含載入與解碼，測試時段、背景負載及溫度未控制，僅為參考，不宣稱固定加速倍數。片段數不是準確率。
- medium 兩組在「全外顯子、12 億、百萬分之一、分析特異性高於 99%」的文字表記較接近候選稿，但 turbo 無提示組亦保留後兩項數值。兩個 medium 組的 Oncodetect 成為 On-Code Tech／On-Code-Tech，實驗室句出現 CLCLIA，未見專名全面改善。
- AVITI Dx 四組皆為 AVT-DX，MAESTRO 四組皆為 Mastro；日期、Q30 等仍須回聽。FDA 否定句四組保留。turbo 中文引導尾端超界／重複問題仍列出，沒有直接刪除原文。
- 完整四組原始輸出、選段對照及結論存於 temp/r02-model-comparison/comparison.md。另產生 5 段原速 16 kHz 單聲道 WAV，附原音起訖時間，供回聽日期、產品名、規格、數值與否定句；全部留在忽略目錄。候選稿不是逐字人工標準，未計 CER／WER，未回聽接受。
- 此單一樣本沒有足夠依據將 medium 設成預設。後續可測每視窗術語提示或短語音區塊，但不是本輪已驗證改善。本輪無產品程式或依賴修改，未重跑一般測試；完成的是兩組 medium 真實音檔推論及四組結果一致性核對。未 commit／tag／push。

## v0.3.0 提交前檢查（2026-11-07）

- 同步 pyproject.toml、uv.lock 及 README 至 0.3.0；新建 annotated tag v0.3.0，分支 main，既有 v0.1.0／v0.2.0 不移動。提交範圍為實驗辨識選項、比較工具、回歸測試及文件，不包含 temp、models、data 或環境快取。
- 完整測試含本機 small／短 MP4 離線轉錄 **78 passed，無略過**；既有 TestClient 第三方棄用警告 1 項。Ruff、Python 格式、JavaScript 語法、git diff --check 與 uv lock --check --offline 均通過。
- 保留原有預設。中英交替／前文及提示策略等實驗未證明整體準確率提升，合成及 R02 結果均記錄限制；新 UI 瀏覽器人工接受仍待完成。
- 本次交付原始碼與文件，未打包 EXE、未建立 GitHub Release 或執行遠端 CI。模型、音檔、完整對照及私人逐字稿保留於本機忽略目錄。

## 視覺改版（v0.3.0 之後的開發變更）

- 僅調整本機 HTML／CSS：精簡頂部工具列、白色面板與淺灰背景、青綠主色、280px 桌面設定欄、分組欄位、16px 桌面逐字稿、時間標籤、主要 MD／次要匯出按鈕及空白狀態。桌面設定表單可在欄內捲動，開始按鈕保持可見；手機維持單欄頁面捲動。保留全部控制項 ID、預設及既有 JavaScript／API 行為。
- 字型沿用系統字型，圖示為本機 HTML 的 SVG；沒有新增外部字型、CDN、前端依賴或待實作功能的假按鈕。未新增搜尋、回聽、校對或說話者功能。
- 以獨立的 localhost:8766 測試服務、臨時任務目錄、合成 UI 文字及獨立無頭 Chrome 設定檔檢查，不讀寫既有 data/jobs，不啟動真實模型。此為自動化瀏覽器檢查，不等同使用者人工接受。
- 1440×1000、1366×768、390×844 無水平溢出；桌面開始按鈕位於可視範圍，進階執行緒欄位可由鍵盤聚焦並捲至可見。手機逐字稿為 15px，桌面 16px。瀏覽器確認使用本機微軟正黑體／Segoe UI。
- 隔離頁面通過歷史切換、MD 真正下載、空任務畫面、原始 File PUT 上傳＋Windows spawn 模擬工作完成，以及取消待上傳任務；瀏覽器 Runtime 例外數為 0。截圖與結果存於忽略的 temp/ui-review/，未取代真實錄音／錯誤／長檔／推論取消人工驗收。
- 一般測試 77 passed、1 skipped，保留既有 TestClient 第三方警告；真實模型 opt-in 本輪未重跑。前端既有情境測試包含在 pytest；HTML 26 個 ID 均唯一。未 commit／tag／push。

## 深色模式

- 右上角提供淺色／深色／跟隨系統，預設跟隨系統。theme.js 在樣式表前套用已保存的偏好，系統外觀即時變更僅影響跟隨系統模式；手動選擇優先。儲存於目前網站的瀏覽器 localStorage，沒有新增伺服器設定或變更轉錄資料。
- 全部元件色彩集中為主題變數，涵蓋原生表單 color-scheme、逐字稿、歷史、進度、警告、檔案選擇及按鈕。文字與填色同步切換，避免背景動畫造成短暫低對比；手機保留顯示模式控制，窄版省略離線徽章以避免溢出。
- 新增 5 個 Node 主題情境：載入前套用與重整保存、即時跟隨系統／手動優先、阻擋儲存、無效偏好回復、分頁變更與清除。納入既有 pytest 前端入口；一般測試 77 passed、1 skipped，Node 共 9 個情境，第三方棄用警告仍在。真實模型 opt-in 本輪未重跑。
- 獨立無頭 Chrome／隔離模擬資料確認系統深色起始、即時切換、手動選擇優先、重新整理保存、跨分頁同步及封鎖 localStorage 時仍可操作，Runtime 例外數為 0。1440px 桌面、1366×768 筆電、390px 及 320px 手機皆無水平溢出。
- 實測深色正文／面板對比約 13.87:1、提示 7.95:1、主按鈕 7.88:1；淺色對應約 17.85:1、4.76:1、5.47:1。這是選定元件的文字對比檢查，不宣稱完成全面無障礙驗收。深色空白與警告畫面亦已截圖檢查。
- 截圖與檢查結果僅在忽略的 temp/theme-review/；未讀寫使用者原有任務。產品真人視覺接受仍待確認；尚未 commit／tag／push。

## 清除逐字稿工作區

- 次要按鈕只清空目前預覽、標題、進度、時間與訊息，並停用複製／匯出；保留歷史、左側選檔／設定及主題。只在已選取終止結果、無已知活動任務、沒有建立／上傳請求待完成時開放；禁用原因可見。不送刪除／取消請求，不釋放後端活動槽位，不增加確認對話框。
- sessionStorage 僅保存本分頁的空白意圖；重新整理保持空白，歷史選取或建立新任務解除。儲存受阻時仍可清除，但重新整理可回到原有最近任務還原行為；沒有保存逐字稿內容。已發出的下載可完成，不將較晚返回的錯誤寫回新畫面。
- 清除會遞增檢視世代、關閉預覽 SSE、清空片段及待補齊片段。分頁回應、SSE、初始 latest 成功／失敗、取消、複製與匯出錯誤都檢查世代，避免清除後回填。刪除目前歷史結果亦共用畫面重設，避免殘留舊進度。
- Node 前端 20 個情境通過，另有 5 個主題情境；pytest 仍為 77 passed、1 skipped（前端由同一 pytest 入口執行），既有第三方警告保留。涵蓋 4 種終態、空白／活動／新建請求待完成、保存欄位、無後端變更、舊非同步結果、重新整理、儲存受阻及歷史重開。真實模型 opt-in 本輪未重跑。
- 獨立 Chrome 與本機模擬任務驗證：清除保留 2 筆歷史及選檔／標題／術語／執行緒／深色偏好；截住 MD 請求後清除，既有下載仍能完成；同頁重整空白、歷史重開、其他活動任務／上傳中禁用、取消後清除、實際 File PUT 與 Windows spawn 模擬工作完成後正常顯示。
- 另驗證新分頁正常還原最近任務、封鎖 sessionStorage 的回退，以及 1366×768 深色桌面／390px 淺色手機／320px 深色手機無水平溢出。Runtime 例外數 0；截圖與結果在忽略的 temp/workspace-review/。測試未讀寫使用者原有 data/jobs；不等同真實錄音全流程或真人接受。
- 未新增 API、未變更模型／轉錄設定；JavaScript 語法、Ruff、格式與差異檢查通過。尚未 commit／tag／push。

## 介面／主題／清除工作區提交前檢查（2026-11-07）

- 依使用者要求 commit／push 至 main；本輪不新增或移動 tag，pyproject.toml／uv.lock 維持 0.3.0。提交範圍為上述介面、主題與工作區功能及相應測試／文件。
- 完整測試含本機 small／短 MP4 離線辨識 **78 passed、無略過**；Node 前端 20 個及主題 5 個情境由 pytest 執行。保留既有 TestClient 第三方棄用警告 1 項。
- Ruff、Python 格式、兩個前端 JavaScript 語法、git diff --check 及離線鎖檔檢查通過。瀏覽器驗證範圍及真人接受邊界依前述紀錄；不以提交推送當作 CI、GitHub Release 或完整真實會議驗收。
- 本機模型、音檔、完整逐字稿、截圖與測試瀏覽器設定檔仍在忽略目錄，不納入提交。

## v0.4.0：人工修訂與 H10 暫停／繼續交付前檢查（2026-11-07）

- 使用者授權 commit／push／tag；本次新版本為 v0.4.0，交付 refs 僅 main 與新 annotated tag v0.4.0，不移動 v0.1.0～v0.3.0。提交前遠端 main 核對為 143ee5d36847f8b2da9b1b63ff9ed78c3a174597，尚無 v0.4.0。
- 版本同步 README、pyproject.toml 與 uv.lock。前置人工修訂功能在 143ee5d；此次提交包括 H10 IPC 確認、控制序號／競爭保護、暫停取消／清理、計時與 UI 及相應測試與文件。
- `.venv/Scripts/python.exe -m pytest --real-model -q`：**94 passed、無略過**，25.80 秒。包含本機 small／短 MP4 離線辨識、Windows 真實子程序及暫停時父程序死亡清理；保留既有 Starlette TestClient 棄用警告 1 項。
- Ruff check、Ruff format --check（24 個 Python 檔）、app.js／theme.js 的 Node 語法檢查、`uv lock --check --offline --cache-dir .uv-cache` 及 `git diff --check` 通過。此專案為原始碼啟動，未產生 EXE 或新增發行安裝包。
- 真實模型測試驗證一般辨識路徑，沒有量測真實模型暫停延遲／CPU／RAM；H10 控制驗證使用相同受控迭代器的子程序替身。瀏覽器人工接受及完整長會議校稿仍待完成，H10／H.G 保持未勾選。關閉服務後續跑、H20～H50 及 GPU／說話者分離未在此版完成。
- 不包含模型、音檔、逐字稿與忽略目錄下測試產物。Git 推送與標籤不代表 CI、GitHub Release 或上述人工接受通過；此次未要求建立 GitHub Release。
