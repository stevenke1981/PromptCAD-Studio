# Project Status — v0.7.0

## 可用功能

- Prompt → 本地規則／OpenAI-compatible LLM → CadDocument 1.0／1.1／1.2。
- 標準件 CAD Agent：NEMA17 自動辨識、來源追蹤、參數覆寫與完整支架輸出。
- 校準圖片／草圖／PDF 轉 CAD：安全解碼、指定 PDF 頁面、可選四點透視校正、矩形或任意閉合折線輪廓、圓孔、可編輯 Feature Tree、人工確認及完整輸出。
- 受限 DXF 轉 3D：line／exact three-point arc 閉合輪廓、圓孔／孔陣列、對稱判斷、CENTER 半剖面旋轉、一致四角圓角／倒角、可編輯 Feature Tree、人工確認及完整輸出。
- Web UI、REST API、CLI。
- 可編輯 JSON 後重新驗證與輸出。
- Plate、cylinder、ring、L bracket、open enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- 開放式外殼 ±X／±Y 矩形側面切口。
- X／Y／Z 軸孔、fillet、chamfer。
- CadQuery STEP／STL／DXF／SVG 與 OpenSCAD STL fallback。
- 關閉式 `CadBackend` registry 與能力合約 1.0；API、CLI、Web 可選擇固定 backend ID。
- 六種確定性來源：CadQuery、Build123d、FreeCAD Python、OpenSCAD、Fusion 360 adapter、SOLIDWORKS adapter。
- CadQuery／OpenSCAD local runner 保持相容；Build123d 是 opt-in runner；FreeCAD／Fusion 360／SOLIDWORKS 不由伺服器執行。
- `promptcad capabilities` 與 API planner／backend capability reporting。
- `backend-report.json`、spec／artifact SHA-256、backend diagnostics、fallback chain 與逐格式結果。
- 不依賴 CAD kernel 的 A4 三視圖工程草圖 PDF。
- 幾何驗證閘門、Token、下載路徑保護與工作 ZIP。
- 一般 JSON body、Feature Tree、圖片、DXF 與 renderer 的 body／併發／timeout／輸出上限。
- SQLite durable queue、獨立 Worker、原子 claim、lease／heartbeat、有限重試、服務重啟復原與 cooperative cancellation。
- REST HTTP 202、CLI queue commands 與 Web 背景模式、reload recovery、離線重連及取消。
- Hardened Docker worker profile：無外網、唯讀 root、capability drop、no-new-privileges 與 CPU／memory／PID 上限。

## 本封裝已驗證

- 180 個 pytest 測試通過，涵蓋 Phase 1–6 既有與新增流程；`app` 覆蓋率 82%。
- Python 3.12.13 的 uv 鎖定環境可重現安裝。
- Ruff 全專案檢查通過（自動排除本機 `.venv` 與生成產物）。
- Python `compileall` 通過。
- Web JavaScript `node --check` 通過。
- Editable package 安裝與 `promptcad --help` 通過。
- Source-only 端到端 smoke generation 通過。
- Web 實際操作「提示詞 → 規則規劃器 → 預覽與下載」通過。
- 中文提示詞 → 矩形側面切口 DSL → CadQuery STEP／STL／DXF／SVG 通過。
- 校準 PNG → 100×60×5 mm 板件與四孔 Feature Tree → CadQuery STEP／STL／DXF／SVG／PDF 通過。
- 影像 STEP 回讀為 1 個實體、100×60×5 mm bounding box、4 個圓柱面。
- 圖片工作 manifest 保留 `image-feature-tree` planner、校準 provenance 與確認後 Feature Tree；JPEG 與透視梯形安全回歸測試通過。
- 多頁 PDF 第 1 頁可安全光柵化為矩形 Feature Tree；任意三角形輪廓產生 `profile_extrusion`；四角梯形預設阻擋、明確啟用後才執行透視校正。
- Web 實際操作 PDF 與自由輪廓上傳皆通過，預覽與可編輯 Feature Tree 可見，Console errors 為 0。
- 線／圓弧四孔 DXF → `profile_extrusion` → CadQuery STEP／STL／DXF／SVG／PDF 通過。
- CENTER 半剖面 DXF → `profile_revolution` → CadDocument 1.2，四種幾何來源與實際 CadQuery STEP 匯出／回讀通過。
- 三孔線性陣列、四孔圓周陣列及一致四角 fillet／chamfer 均保留可編輯 Feature Tree 語意，並在受控 DSL 邊界展開或編譯。
- DXF STEP 回讀為 1 個有效實體、120×40×6 mm bounding box、4 個 Ø5 mm through holes；兩端 R20 圓弧保持解析幾何。
- Web 實際操作「DXF 上傳 → 特徵樹 → 人工確認 → 14 個下載入口」通過，無 Console 錯誤；延遲回應不會覆寫較新的上傳分析。
- 鎖定執行環境的 pip-audit 無已知漏洞。
- Backend registry 拒絕 path／module／executable 型 ID；六個 source compilers 對共用 DSL fixture 產生確定且語法有效的來源。
- Prompt injection payload 只保留為 JSON 資料，沒有生成 `system`／`open` 等任意呼叫。
- Fusion 360 Web 選擇已由 CadQuery 產生並封裝有效 `model.step` 與 `model.fusion360.py`，manifest／backend report 記錄 `fusion360 → cadquery`，且桌面 host 不在伺服器執行。
- Renderer 缺少要求檔案、輸出簽章錯誤、超量輸出或 exact 特徵失敗時均 fail closed；執行期 fallback 會在完成後更新 provenance。
- CadQuery v0.5.0 工作保留完整六來源、`backend-report.json`、逐格式結果與每個 artifact SHA-256。
- Web 背景工作已實際通過 queue → reload recovery → cancel，以及 queue → worker → manifest → 12 個下載入口；完成流程無 Console 錯誤。
- CLI `async-generate` → `promptcad-worker --once` → `queue-status` 已實際完成並取得 result URL。
- Phase 6 審查的 lease ownership、取消 manifest、401／404／離線 UI 與 prompt 上限競態均已修正；最終零剩餘 findings。
- Build123d 0.11.1 已在獨立 venv 實際產生有效 80×40×5 mm STEP，回讀包含兩個半徑 3.3 mm 圓柱孔面。

## 封裝環境限制

本機 CadQuery 2.8.0 已以 OpenCascade 實際產生及回讀既有 STEP／STL／DXF／SVG。Build123d 0.11.1 也已在另一個專用 venv 驗收；兩者依賴衝突的 OCP distributions，不能共存於同一環境。Docker、OpenSCAD executable、Conda 與 FreeCAD host runtime 仍未在本機執行；Fusion 360／SOLIDWORKS adapters 也未在授權桌面 CAD host 做端到端驗收。

Hardened Compose profile 已把 renderer 移入無外網、唯讀 root、資源受限的獨立 worker；本機沒有 Docker executable，所以只完成 YAML 靜態驗證，Docker 主機啟動仍是 `MANUAL_REQUIRED`。正式多租戶部署仍需平台核准的 seccomp／AppArmor、TLS、租戶授權、配額與 retention。

## 下一階段

- 擴充標準件目錄至 NEMA23、常用軸承與連接器。
- 2D 草圖 DSL 後續：尺寸約束、多視圖配對與歧義候選。
- 多孔群的非規則 pattern／mirror。
- 正式工程圖、尺寸、公差、BOM 與標題欄。
- BREP validity、最小壁厚、製程規則與干涉檢查。
- 正式工程圖的尺寸約束、基準、公差、表面處理、BOM、revision 與審核／核准生命週期。
- 多節點外部 queue、PostgreSQL／物件儲存、租戶公平排程、配額與 retention。
- 在實際 FreeCAD host 與已授權 Fusion 360／SOLIDWORKS workstation 執行人工端到端 adapter 驗收。
- 圖片線／圓弧輪廓、遮擋／反光處理、比例尺自動辨識與多視角尺度推定。
