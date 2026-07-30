# Release Notes

## v0.2.0 — 2026-07-31

第三階段校準圖片／草圖轉 CAD：

- 正俯視 PNG/JPEG 安全解碼、EXIF orientation 正規化與 bytes／像素／尺寸／timeout／併發限制。
- 已知最長邊毫米校準、旋轉矩形外框與圓孔擷取、信心分數及 SHA-256 provenance。
- 有型別且可編輯的 Feature Tree：rectangle sketch、extrude、circle sketch、through cut。
- Web 圖片上傳、Feature Tree 編輯與人工確認輸出。
- REST `/image-analysis`、`/image-feature-tree-to-spec` 與 `/generate-from-image-feature-tree`。
- CLI `promptcad image`，支援分析 JSON、`--feature-tree-input` 編輯回送與 `--confirm` 全格式生成。
- 非矩形與不完整 Feature Tree 會停止自動 CAD 轉換。
- 前置 multipart body 限制、固定分析 worker capacity、來源 HMAC 驗證與預設 loopback 部署。
- OpenCV headless、Pillow、multipart 依賴鎖定及 Docker／Conda runtime 同步。
- 59 項測試、79% app 覆蓋率、無已知 Python 相依漏洞。
- 真實驗收：校準 PNG 產出一個 100×60×5 mm STEP 實體及四個孔。

## v0.1.0 — 2026-07-30

首個可執行 Prompt-to-CAD MVP：

- 第二階段標準件 CAD Agent：NEMA17 自動路由、版本化來源、參數覆寫與支架生成。
- 受控、可編輯 CadDocument 1.0 DSL。
- 中文／英文規則解析及 OpenAI-compatible LLM planner。
- CadQuery 與 OpenSCAD 確定性編譯器。
- STEP、STL、DXF、SVG、Python、SCAD、JSON 輸出管線。
- A4 橫式三視圖工程草圖 PDF，可在 source-only 模式輸出。
- Plate、cylinder、ring、L bracket、enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- 開放式外殼 ±X／±Y 矩形側面切口，支援中文／英文提示詞、DSL、驗證、預覽與雙編譯器。
- X/Y/Z 軸孔與常見 M 制孔徑輔助。
- Web JSON 編輯與 `/generate-from-spec`。
- 驗證失敗阻止 CAD kernel 執行。
- Token-aware 預覽／下載、路徑安全、Docker、Conda、CI 與 42 項測試。
- uv 鎖定環境與 Python 3.11／3.12 CI 矩陣，讓本機與 GitHub Actions 使用同一套相依版本。
- 修正全專案 Ruff 掃描誤納入 `.venv` 的問題，並清除既有靜態檢查警告。
- 修正 Windows 使用相對資料目錄時 renderer 重複拼接工作路徑的問題。
