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
