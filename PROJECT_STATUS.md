# Project Status — v0.1.0

## 可用功能

- Prompt → 本地規則／OpenAI-compatible LLM → CadDocument 1.0。
- Web UI、REST API、CLI。
- 可編輯 JSON 後重新驗證與輸出。
- Plate、cylinder、ring、L bracket、open enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- X／Y／Z 軸孔、fillet、chamfer。
- CadQuery STEP／STL／DXF／SVG 與 OpenSCAD STL fallback。
- 幾何驗證閘門、Token、下載路徑保護與工作 ZIP。

## 本封裝已驗證

- 26 個 pytest 測試通過。
- Python 3.12.13 的 uv 鎖定環境可重現安裝。
- Ruff 全專案檢查通過（自動排除本機 `.venv` 與生成產物）。
- Python `compileall` 通過。
- Web JavaScript `node --check` 通過。
- Editable package 安裝與 `promptcad --help` 通過。
- Source-only 端到端 smoke generation 通過。
- Web 實際操作「提示詞 → 規則規劃器 → 預覽與下載」通過。

## 封裝環境限制

建立此發行包的執行環境沒有 Docker、CadQuery、OpenSCAD 或 Conda，因此沒有在該環境中實際執行 OpenCascade 生成 STEP／STL／DXF。Python 3.12 source-only 路徑已完整驗收；專案的 Dockerfile 與 Conda 環境固定 CadQuery 2.8.0，在具備 Docker 的主機執行 `docker compose up --build` 才會走實際 CAD kernel 路徑。

## 下一階段

- 2D 草圖 DSL：線、圓弧、約束、拉伸、旋轉與陣列。
- 多孔群與 pattern／mirror。
- 正式工程圖、尺寸、公差、BOM 與標題欄。
- BREP validity、最小壁厚、製程規則與干涉檢查。
- 非同步 renderer worker、sandbox、配額與多租戶。
- 圖片／手繪草圖／DXF 輸入。
