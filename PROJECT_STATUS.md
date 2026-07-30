# Project Status — v0.2.0

## 可用功能

- Prompt → 本地規則／OpenAI-compatible LLM → CadDocument 1.0。
- 標準件 CAD Agent：NEMA17 自動辨識、來源追蹤、參數覆寫與完整支架輸出。
- 校準圖片／草圖轉 CAD：安全上傳、矩形與圓孔擷取、可編輯 Feature Tree、人工確認及完整輸出。
- Web UI、REST API、CLI。
- 可編輯 JSON 後重新驗證與輸出。
- Plate、cylinder、ring、L bracket、open enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- 開放式外殼 ±X／±Y 矩形側面切口。
- X／Y／Z 軸孔、fillet、chamfer。
- CadQuery STEP／STL／DXF／SVG 與 OpenSCAD STL fallback。
- 不依賴 CAD kernel 的 A4 三視圖工程草圖 PDF。
- 幾何驗證閘門、Token、下載路徑保護與工作 ZIP。

## 本封裝已驗證

- 59 個 pytest 測試通過。
- `app` 測試覆蓋率 79%。
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

## 封裝環境限制

本機已安裝 CadQuery 2.8.0，並以 OpenCascade 實際產生及回讀 ESP32 螢幕外殼的 STEP／STL／DXF／SVG。Docker、OpenSCAD 與 Conda 路徑仍未在本機執行；專案的 Dockerfile 與 Conda 環境固定 CadQuery 2.8.0。

## 下一階段

- 擴充標準件目錄至 NEMA23、常用軸承與連接器。
- 2D 草圖 DSL：線、圓弧、約束、拉伸、旋轉與陣列。
- 多孔群與 pattern／mirror。
- 正式工程圖、尺寸、公差、BOM 與標題欄。
- BREP validity、最小壁厚、製程規則與干涉檢查。
- 非同步 renderer worker、sandbox、配額與多租戶。
- 受限 DXF 單一閉合輪廓／圓孔 → Feature Tree → 3D 拉伸。
- 圖片四點透視校正、任意閉合輪廓、線與圓弧。
