# 轉錄期間校稿計畫

使用者已授權規劃及實作。沿用逐段推論、JSONL 原稿及 SSE，不改模型參數。

1. 人工修訂以獨立、原子替換的 revisions.json 保存；原稿編號及進度不變。
2. 提供修訂快照與逐項寫入 API；以版本號拒絕過期分頁，驗證原稿及新增句子的參照。
3. 已輸出段落可改字、刪除及復原；原稿前後可新增句子，新增句子可設定秒數。
4. 前端延遲自動儲存、序列化寫入、明確狀態及重試；切換任務及匯出前等待儲存。
5. 完成事件不重建編輯畫面；刷新從原稿及修訂還原。離頁提示尚未保存的內容。
6. 修訂套用於複製及 TXT/MD/SRT；原始 TXT 永遠來自原稿。未填時間的新增句在 TXT/MD 標示時間未設定，SRT 拒絕匯出。

驗證：轉錄追加和完成、恢復、刪除復原、新增排序、原始稿不變、版本衝突、儲存失敗與前端切換競態；完整 pytest、ruff、git diff --check。瀏覽器人工驗收另外記錄。

## 實作與驗證結果

- 已完成以上功能；實作位於 app/revisions.py、app/main.py、app/static/app.js 及匯出層，README 已加入操作與保存限制。
- pytest：82 passed、1 skipped（既有需指定模型的測試）；包含 Node 前端回歸，新增 5 個 API 與 6 個前端情境；frontend.test.cjs 最後單獨執行 26 passed。既有 Starlette TestClient 相容性警告仍存在。ruff 與 git diff --check 通過。
- 瀏覽器人工驗收未完成：browser access host unavailable；使用者授權並還原 Chrome 後，工具仍因無法可靠辨識網址停止。使用者截圖確認編輯欄位與按鈕顯示，未將截圖列為儲存／恢復驗收。獨立測試服務使用 127.0.0.1:8011 與 temp/live-edit-smoke/data，不操作正式資料。
- 未執行長音檔推論效能測試，辨識參數未變更；編輯功能與後續規劃已於 143ee5d 提交並推送 origin/main，遠端已核對，沒有標記版本或建立 Release。
