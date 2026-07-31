# Release Notes

## v0.5.0 — 2026-07-31

第六階段 durable async queue 與隔離 Worker：

- 新增 SQLite WAL queue，支援 prompt／edited spec、原子 claim、lease／heartbeat、過期復原、有限重試、容量限制及五種明確狀態。
- 新增獨立 `promptcad-worker`；queue payload 在 Worker 再次經 Pydantic 驗證，損壞 payload 只會終止該工作，不會終止 Worker。
- cooperative cancellation 貫穿 planning、validation、materialization 與 renderer loop；取消會終止完整程序樹，且 queue／manifest 不會出現 cancelled／completed 矛盾。
- 新增 HTTP `202` async generate／generate-from-spec、queue list／status／cancel REST 端點；同步 API 保持相容。
- 新增 `promptcad async-generate`、`async-render`、`queue-list`、`queue-status`、`queue-cancel` 與 `promptcad-worker --once`。
- Web 新增背景模式、狀態輪詢、取消、reload recovery、離線重連與失效 queue ID 自動清理；完成流程顯示 12 個下載入口且無 Console 錯誤。
- Docker Compose 新增 Worker service；hardened override 強制 API source-only，Worker 無外網、唯讀 root、移除 capabilities、no-new-privileges 及 CPU／memory／PID 限制。
- 修正 lease 被其他 Worker 接手時舊 Worker 二次 fail 導致程序退出、取消與 manifest 競態、async prompt 上限不一致，以及 SQLite 操作阻塞 ASGI event loop。
- 142 項測試與 81% app 覆蓋率通過；Ruff、compileall、JavaScript 語法與 YAML 靜態驗證通過；最終 code review 零剩餘 findings。
- 本機未安裝 Docker executable，因此 hardened Compose runtime 啟動標記 `MANUAL_REQUIRED`；正式部署仍需 TLS、租戶授權／配額、retention 與平台核准的 seccomp／AppArmor。

## v0.4.0 — 2026-07-31

第五階段可擴充規劃器與多 CAD 後端：

- 建立關閉式 `CadBackend` registry 與能力合約 1.0；請求只能選擇固定 ID，不能注入 module、executable、arguments、environment 或 plugin metadata。
- Web、REST API、CLI 支援 `backend`／`--backend`；新增 `promptcad capabilities` 與 planner capability reporting。
- 六種確定性來源編譯器／adapter：CadQuery、Build123d、FreeCAD Python、OpenSCAD、Fusion 360、SOLIDWORKS。
- 所有來源由 schema 驗證後的 DSL 產生；prompt injection 維持為 JSON 資料，design validation error 會阻止所有 CAD runner。
- 保留 CadQuery／OpenSCAD local runners；`auto` fallback 是 CadQuery → OpenSCAD → source-only。
- Build123d 0.11.1 為 opt-in local runner，只輸出 STEP／STL；CadQuery 與 Build123d 因 OCP distributions 衝突，必須使用不同 venv。
- FreeCAD 僅輸出 Python source，尚未在 host runtime 實際執行。Fusion 360／SOLIDWORKS host adapter 永不由伺服器執行；render 開啟時工作包會以可用的 exact 本機核心附帶已驗證 sibling STEP。
- 每個工作新增 `backend-report.json`、spec hash、source／artifact SHA-256、capability snapshot、backend diagnostics、fallback chain 與逐格式結果。
- Generation JSON body、Feature Tree、圖片、DXF 與 renderer 新增或保留解析前 body、併發、timeout、console、單檔與總輸出上限。
- 128 項測試與 81% app 覆蓋率通過，包括 registry 安全、跨後端 conformance、來源確定性、adapter 中性 STEP 前置條件、執行期 provenance、程序樹終止、缺檔 fail-closed 及 Phase 1–4 回歸。
- Renderer 以有界 stdout／stderr、程序樹 timeout、嚴格 STL／STEP／DXF／SVG 檢查及串流 SHA-256 保護輸出；exact 後端不再靜默略過 fillet／chamfer。
- Build123d 獨立環境實際驗收：有效 80×40×5 mm STEP 與兩個半徑 3.3 mm 圓柱孔面。
- 公開部署明確要求獨立、無外網、低權限、唯讀根目錄且有 cgroup/seccomp 或平台等效 OS sandbox 的 renderer worker。

## v0.3.0 — 2026-07-31

第四階段受限 DXF 工程圖轉 3D：

- ASCII／AutoCAD Binary DXF 內容辨識，支援 mm／inch／cm 與無單位手動覆寫。
- modelspace LINE、ARC、CIRCLE、closed LWPOLYLINE、2D POLYLINE 白名單；blocks、SPLINE、HATCH、ELLIPSE、3D 與多重／開放輪廓 fail closed。
- `CadDocument 1.1 profile_extrusion`，保留 line 與 exact three-point arc，圓孔轉為 Z 軸 through hole。
- 可編輯 DXF Feature Tree、對稱報告、來源與正規化幾何 SHA-256、人工確認與 HMAC provenance。
- CadQuery、OpenSCAD、SVG 與工程圖 PDF 支援自由閉合輪廓；OpenSCAD／validation 圓弧取樣有硬上限。
- Web DXF 上傳與 Feature Tree 編輯、REST 三個 DXF 端點、CLI `promptcad dxf`。
- multipart 前置 body／併發限制、一次性 parser worker、timeout、父程序暫存檔清理、實體／線段／孔與 response 上限。
- 原始 DXF 與上傳檔名不保存；ZIP 只依 manifest allowlist 打包。

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
