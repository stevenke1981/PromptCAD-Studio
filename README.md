# PromptCAD Studio

PromptCAD Studio 將中文或英文提示詞轉成**可驗證、可重現、可編輯的參數化 CAD**：

```text
提示詞
  → 標準件 CAD Agent／本地規則／OpenAI-compatible LLM 規劃器
圖片／草圖／PDF
  → 安全解碼／頁面選擇／已知長度校準／可選透視校正／輪廓與圓孔擷取／可編輯 Feature Tree
DXF
  → 隔離解析／線與三點圓弧輪廓／圓孔／對稱判斷／可編輯 Feature Tree
  → 受控 CadDocument 1.0／1.1 DSL
  → 幾何與製造前驗證
  → 關閉式 CadBackend registry／能力合約 1.0
  → CadQuery／Build123d／FreeCAD Python／OpenSCAD／Fusion 360／SOLIDWORKS 原始碼
  → STEP、STL、DXF、SVG、工程圖 PDF、Python、SCAD、JSON
```

目前版本 v0.7.0 是可直接執行的 CAD Agent 平台，適合固定板、法蘭／墊圈、圓柱、L 型支架與開口外殼，也可將校準圖片、草圖、指定 PDF 頁面或受限 DXF 轉成可編輯 Feature Tree。系統不會直接執行模型產生的 Python；LLM 只能填寫有型別、尺寸上限與特徵白名單的 CAD DSL，再由伺服器擁有的確定性編譯器建立幾何來源。

## 已包含

- 中文／英文提示詞本地解析，沒有 API Key 也能使用。
- 標準件 CAD Agent：辨識 NEMA17 並帶入有來源、可覆寫的馬達面尺寸與支架參數。
- 校準圖片／PDF 轉 CAD：PNG/JPEG 或指定 PDF 頁面可擷取矩形、任意閉合折線輪廓與圓孔；矩形照片可明確啟用四點透視校正，並產生信心分數、可編輯 Feature Tree、CAD DSL 與完整工程輸出。
- 受限 DXF 工程圖轉 3D：閉合 LINE／ARC／LWPOLYLINE／2D POLYLINE 外框、CIRCLE 圓孔、CENTER 旋轉軸、線性／圓周孔陣列及一致四角圓角／倒角，經人工確認後拉伸或旋轉成完整 CAD 輸出。
- OpenAI-compatible LLM 規劃器，支援 `json_schema`、`json_object` 與純提示 JSON 模式。
- 可編輯 `spec.json`：在 Web UI 修改尺寸後重新驗證與輸出。
- 基礎幾何：板件、圓柱、圓環、L 型支架、開口外殼。
- 孔特徵：通孔、盲孔、間隙孔、攻牙底孔近似、沉孔、沉頭孔，以及 X／Y／Z 軸方向。
- 外殼特徵：可在 ±X／±Y 側面建立矩形開口，適合 USB、TF 卡、按鍵與通風窗口。
- 邊特徵：圓角與倒角。
- 工程圖：不依賴 CAD kernel 的 A4 橫式三視圖 PDF，包含外形尺寸與標題欄。
- 螺紋尺寸輔助：常見 M2～M12 一般間隙孔與攻牙底孔表；其他尺寸使用明確標示的比例近似。
- 驗證閘門：孔超出輪廓、孔重疊、側面開口越界、盲孔／沉孔深度、沉頭深度、圓環內孔、邊距、壁厚、圓角與倒角。
- 無效設計會保存 DSL 與原始碼供修正，但**不會送入 CAD 核心渲染**。
- 關閉式多後端 registry：請求只能選擇固定短 ID，不能提供 Python 模組、執行檔、命令列、環境變數或外掛 metadata。
- `CadBackend` 能力合約 1.0：公開 schema、特徵、輸出格式、執行類型、runtime 狀態與語意 fidelity。
- 六個確定性來源編譯器／adapter：CadQuery、Build123d、FreeCAD Python、OpenSCAD、Fusion 360 與 SOLIDWORKS。
- API、CLI 與 Web 都可選擇後端；每個工作記錄 fallback chain、逐格式結果、spec SHA-256、artifact SHA-256 與 `backend-report.json`。
- SQLite durable queue、獨立 `promptcad-worker`、原子 claim、lease／heartbeat、有限重試、取消與服務重啟後續傳。
- REST、CLI 與 Web 背景模式；網頁會保存進行中的 queue ID，重新開啟後繼續輪詢並取得最終 manifest。
- Docker Compose worker 與 hardened override：worker 無外網、唯讀根檔案系統、移除 Linux capabilities、`no-new-privileges` 及 CPU／記憶體／程序數限制。
- `auto` 只會執行 CadQuery → OpenSCAD → source-only；Build123d 必須明確選擇，FreeCAD／Fusion 360／SOLIDWORKS 永不由伺服器自動執行。選擇 Fusion 360／SOLIDWORKS 且 `render=true` 時，工作包會以可用的 exact 本機核心附帶已驗證的中性 STEP。
- Web UI、REST API、CLI、Docker Compose、Conda 環境、測試、CI 與完整範例。
- 每次工作保存 manifest、DSL、驗證報告、六種後端來源、能力報告、預覽及實際輸出。

## 最快啟動：Docker

Docker 環境內含 CadQuery 2.8.0，可實際產生 STEP、STL、DXF 與 SVG。Docker image 不安裝 Build123d；選擇 `build123d` 時會安全降級為來源輸出。

```bash
docker compose up --build
```

瀏覽器開啟：

- Web：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`

不設定 `.env` 也能以本地規則解析器啟動。需要 LLM 或 API Token 時再建立設定檔：

```bash
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
docker compose up --build
```

停止服務：

```bash
docker compose down
```

生成檔保存在專案的 `generated/`，重新建立容器不會消失。

公開或半公開測試環境建議使用隔離 worker 設定；API 容器只產生來源，實際 CAD kernel 只在無外網 worker 執行：

```bash
docker compose -f docker-compose.yml -f docker-compose.sandboxed.yml up --build
```

此設定仍需要部署端提供 TLS、認證、租戶配額、保留政策與平台核准的 seccomp／AppArmor 設定。

## 不使用 Docker

### 建議：uv 本機開發環境

專案鎖定檔可在 Windows、Linux 與 macOS 建立一致的 Python 3.12 開發環境：

```bash
uv sync --extra dev
uv run pytest
uv run uvicorn app.main:app --reload
```

Windows PowerShell 也可直接使用相同命令，不需要手動啟用 `.venv`。如果本機尚未安裝相容的 Python，uv 會依 `.python-version` 建立專案專用環境。

### 完整 CAD 環境

CadQuery 的 OpenCascade 相依套件較重，建議使用 Miniforge／Mambaforge：

```bash
mamba env create -f environment.yml
conda activate promptcad
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 選用 Build123d runtime

CadQuery 2.8.0 與 Build123d 0.11.1 依賴不同、互相衝突的 OCP distributions，**不可把 `[cad]` 與 `[build123d]` extras 安裝在同一個虛擬環境**。請為 Build123d 建立獨立環境：

```powershell
python -m venv .venv-build123d
.venv-build123d\Scripts\Activate.ps1
python -m pip install -e ".[build123d]"
promptcad generate "長80寬40厚5的板，中間兩個M6通孔" --backend build123d
```

CadQuery 請繼續使用 Docker、Conda，或另一個只安裝 `.[cad]` 的 venv。Build123d 是 opt-in，不會加入 `auto` fallback chain。

### 僅開發 DSL、API 與原始碼

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

未安裝相容 CAD runtime 時，系統進入 `source_only`：仍會產生 `spec.json`、`validation.json`、六種後端來源、`backend-report.json`、`preview.svg` 與 `drawing.pdf`，但不會偽造 STEP／STL／DXF。

## 使用方式

### 標準件 CAD Agent

```powershell
promptcad generate "畫一個可固定 NEMA17 馬達的支架" --planner agent
promptcad generate "NEMA17 馬達支架，板厚5mm，支架寬70mm" --planner agent
```

`auto` 也會自動辨識 NEMA17。Agent 從版本化標準件目錄載入 42.3 mm 馬達面、31 mm 安裝孔距、4×M3、Ø22 定位凸台與 Ø5 軸徑，並在輸出 DSL 的 `standards` 保存來源。板厚、支架寬度、底板深度與立板高度可由提示詞覆寫。

### 圖片／草圖轉 CAD

Web UI 可上傳高對比、單一零件的 PNG/JPEG 或 PDF，選擇頁面並輸入外框最長邊實際尺寸與厚度後，系統會：

1. 移除 EXIF 方向差異並限制壓縮大小、像素及影像尺寸。
2. 對 PDF 套用頁數與像素上限後光柵化指定頁面；需要時由使用者明確啟用四角矩形透視校正。
3. 偵測矩形或任意閉合折線外框與圓孔，記錄校準端點、`mm_per_pixel`、信心及來源雜湊。
4. 產生可編輯 Feature Tree；人工確認後才轉入既有驗證與 CAD renderer。
5. 低信心、不閉合或無法安全解釋的影像只回傳候選與警告，不會直接建立製造輸出。

CLI：

```powershell
promptcad image examples/image-to-cad/plate-top-view.png `
  --known-length 100 --thickness 5 `
  --analysis-output examples/generated/image-to-cad/image-analysis.json

# PDF 頁碼採零起算；矩形照片只有明確指定時才做透視校正
promptcad image drawing.pdf --page 0 `
  --known-length 100 --thickness 5 `
  --perspective-correction

# 編輯 image-analysis.json 內的 feature_tree 後，再以同一張原圖確認輸出
promptcad image examples/image-to-cad/plate-top-view.png `
  --known-length 100 --thickness 5 `
  --feature-tree-input examples/generated/image-to-cad/image-analysis.json `
  --confirm
```

CLI 會重新分析原圖並比對 SHA-256；只有來源相符的已編輯 Feature Tree 才能輸出。最終工作包會保存校準 provenance、確認後的 Feature Tree、DSL、驗證與 CAD 產物。

完整驗收範例位於 [`examples/generated/image-to-cad`](examples/generated/image-to-cad)。

### DXF 工程圖轉 3D

Web UI 可上傳 DXF，指定單位及自動／拉伸／旋轉模式後查看解析出的閉合輪廓、圓孔陣列、CENTER 軸、對稱性與 Feature Tree。自動模式遇到唯一水平或垂直 CENTER 線時，會把半剖面正規化成半徑／Z 輪廓並建立 360° 旋轉體；否則使用指定厚度拉伸。原始 DXF 不會保存到工作或 ZIP；只有人工確認的 Feature Tree、來源雜湊、DSL 與 CAD 產物會留下。

```powershell
promptcad dxf examples/dxf-to-cad/plate-two-holes-mm.dxf `
  --thickness 6 `
  --analysis-output dxf-analysis.json

# 編輯分析 JSON 內的 feature_tree，再以同一 DXF 確認輸出
promptcad dxf examples/dxf-to-cad/plate-two-holes-mm.dxf `
  --thickness 6 `
  --feature-tree-input dxf-analysis.json `
  --confirm
```

目前會拒絕 blocks／INSERT、SPLINE、HATCH、ELLIPSE、非 WCS +Z、3D、高複雜度、多重或開放輪廓。旋轉第一切片會拒絕斜 CENTER 線、跨軸剖面、圓孔與次要完成特徵；無單位 DXF 必須用 `--units mm|inch|cm` 明確指定。CLI 可用 `--operation auto|extrude|revolve` 控制推論。

線／圓弧與四孔的完整 STEP、STL、DXF、SVG、PDF、Python、SCAD、JSON 驗收包位於 [`examples/generated/dxf-to-cad`](examples/generated/dxf-to-cad)。

### Web 編輯流程

1. 輸入「長 120、寬 60、厚 10，四角 M6 通孔，R5」。
2. 系統產生 CAD DSL、驗證報告與預覽。
3. 展開「可編輯 CAD DSL / JSON」，修改長寬、孔徑、孔位或特徵。
4. 按「套用 JSON 並重新輸出」。
5. 下載個別檔案或工作 ZIP。

### CLI

```bash
promptcad capabilities
promptcad generate "畫一個長120、寬60、厚10的固定板，四角M6孔，R5" --planner rule
promptcad generate "長80寬40厚5的板，中間兩個M6通孔" --backend cadquery
promptcad generate "長80寬40厚5的板，中間兩個M6通孔" --backend build123d
promptcad generate "長80寬40厚5的板" --backend freecad --no-render
promptcad generate "畫一個可固定 NEMA17 馬達的支架" --planner agent
promptcad validate examples/generated/plate-four-holes/spec.json
promptcad render examples/generated/plate-four-holes/spec.json --backend cadquery
promptcad render examples/generated/enclosure-side-cutout/spec.json
promptcad image examples/image-to-cad/plate-top-view.png --known-length 100 --thickness 5
promptcad dxf examples/dxf-to-cad/plate-two-holes-mm.dxf --thickness 6
promptcad doctor
```

背景工作不會佔住 CLI；API 與 CLI 共用 `generated/.queue/promptcad.sqlite3`：

```bash
promptcad async-generate "畫一個可固定 NEMA17 馬達的支架" --planner agent
promptcad queue-list --limit 10
promptcad queue-status QUEUE_JOB_ID
promptcad queue-cancel QUEUE_JOB_ID
promptcad-worker                     # 持續處理
promptcad-worker --once              # 最多處理一筆後離開
```

只輸出 DSL 與原始碼：

```bash
promptcad generate "外徑30、內徑15、厚5的墊圈" --no-render
```

### REST API

提示詞產生：

```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "鋁合金固定座，長120mm、寬60mm、厚10mm，四角 M6 通孔，R5",
    "planner": "rule",
    "backend": "cadquery",
    "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
    "render": true
  }'
```

`backend` 可為 `auto`、`cadquery`、`build123d`、`freecad`、`openscad`、`fusion360`、`solidworks` 或 `source_only`。Fusion 360 與 SOLIDWORKS adapter 必須在已授權桌面 CAD 主程式內執行；PromptCAD 伺服器不執行它們，但在 `render=true` 時會以 CadQuery 或 Build123d 封裝其所需的已驗證 `model.step`。

修改後的 DSL 重新輸出：

```bash
curl -X POST http://localhost:8000/api/v1/generate-from-spec \
  -H "Content-Type: application/json" \
  --data-binary @request-with-spec.json
```

背景產生回傳 HTTP `202` 與 queue ID；之後輪詢狀態或取消，完成時依 `result_url` 取得原本的 manifest：

```bash
curl -X POST http://localhost:8000/api/v1/async/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"長80寬40厚5的板","planner":"rule","backend":"auto","formats":["step","json"],"render":true}'

curl http://localhost:8000/api/v1/async/jobs/QUEUE_JOB_ID
curl -X POST http://localhost:8000/api/v1/async/jobs/QUEUE_JOB_ID/cancel
```

完整端點與狀態語意見 [`docs/API.md`](docs/API.md)。

## LLM 設定

`.env` 範例：

```dotenv
PROMPTCAD_PLANNER_MODE=auto
PROMPTCAD_LLM_BASE_URL=https://api.openai.com/v1
PROMPTCAD_LLM_API_KEY=你的金鑰
PROMPTCAD_LLM_MODEL=gpt-5.6
PROMPTCAD_LLM_STRUCTURED_MODE=json_schema
```

OpenAI-compatible 本地端點，例如 llama.cpp server：

```dotenv
PROMPTCAD_LLM_BASE_URL=http://host.docker.internal:8080/v1
PROMPTCAD_LLM_API_KEY=local
PROMPTCAD_LLM_MODEL=qwen3-coder
PROMPTCAD_LLM_STRUCTURED_MODE=json_object
```

`auto` 會在 LLM 未設定時使用本地規則；LLM 呼叫失敗時，預設也會退回本地解析器。不同供應商對 Structured Outputs 的支援不一致，必要時將模式改成 `json_object` 或 `prompt_only`。

## API 保護

設定：

```dotenv
PROMPTCAD_API_TOKEN=請換成長且隨機的字串
```

之後所有 `/api/v1/*` 端點都要帶以下其中一種標頭：

```http
Authorization: Bearer YOUR_TOKEN
X-API-Key: YOUR_TOKEN
```

Web UI 的 Token 欄位會連同預覽、單檔下載及 ZIP 下載一起使用。

## 支援的提示詞

```text
畫一個鋁合金固定板，長120、寬60、厚10，四角 M6 通孔，孔中心離邊10，四角 R5。
做一個長80、寬40、厚10的板，中央一個直徑5、深6的盲孔。
做一個墊圈，外徑30、內徑15、厚5。
做一個 L 型支架，寬80、深50、高60、厚4，立板兩個 M5 孔。
Create a 100x60x10 mm plate with two 5 mm blind holes 6 mm deep.
做一個 100×70×30 mm 的開口盒，壁厚2mm，底部四角3mm通孔。
做一個94x58x22mm外殼，壁厚2mm，+Y面一個14x8mm矩形開口，中心x=25mm、z=9mm。
```

更穩定的寫法、軸向與孔位格式見 [`docs/PROMPT_GUIDE.md`](docs/PROMPT_GUIDE.md)。

## 輸出檔

| 檔案 | 用途 |
|---|---|
| `spec.json` | 可編輯、可版本控制的 CAD DSL |
| `validation.json` | errors、warnings、info 與 review 狀態 |
| `image-analysis.json` | 圖片尺寸、校準、偵測結果與已驗證來源 |
| `feature-tree.json` | 人工確認後、實際用於生成 CAD 的 Feature Tree |
| `dxf-analysis.json` | DXF 單位、來源雜湊、實體統計、對稱性與已驗證 provenance |
| `dxf-feature-tree.json` | 人工確認後、實際用於 DXF 轉 3D 的 Feature Tree |
| `model.py` | 可獨立執行的 CadQuery 模型 |
| `model.build123d.py` | Build123d 模型；只在明確選擇且獨立 runtime 可用時由伺服器執行 |
| `model.freecad.py` | FreeCAD Python 來源；伺服器不執行 |
| `model.scad` | OpenSCAD fallback 模型 |
| `model.fusion360.py` | Fusion 360 host adapter；匯入同工作包的 `model.step` |
| `model.solidworks.py` | SOLIDWORKS host adapter；匯入同工作包的 `model.step` |
| `backend-report.json` | 合約版本、能力快照、來源 SHA-256、spec hash、診斷與 fallback chain |
| `preview.svg` | 不依賴 CAD 核心的快速工程預覽 |
| `drawing.pdf` | A4 橫式三視圖工程草圖，不依賴 CAD 核心 |
| `model.step` | 實體 CAD 交換檔，需要 CadQuery |
| `model.stl` | 3D 列印網格，需要 CadQuery 或 OpenSCAD |
| `model.dxf` | 水平截面 2D DXF，需要 CadQuery |
| `model.svg` | CadQuery 投影 SVG |
| `manifest.json` | 工作狀態、後端選擇、逐格式結果、artifact SHA-256 與 renderer provenance |

## 重要限制

1. **M6、M8 等標示不等於完整實體螺紋。** 目前建立攻牙底孔／間隙孔的圓柱幾何，螺紋規格保留在 DSL 與驗證資訊中。
2. DXF 是零件水平截面；`drawing.pdf` 是含外形尺寸與標題欄的三視圖草圖，尚不含完整公差、基準、表面處理與製造註記。
3. 規則解析器偏向常見單一零件、單一孔群與單一矩形側面開口；複雜多段輪廓、多孔群、裝配與自由曲面應使用 LLM 規劃器或直接編輯 DSL。
4. `preview.svg` 是快速預覽，不是 CAD kernel 的 BREP 渲染結果。
5. AI 推論與預設值都會要求 review；投入 CNC、雷切、射出或承載用途前，必須由合格工程人員覆核材料、公差、強度與製程。
6. 圖片流程支援校準後的高對比矩形、自由閉合折線、圓孔、指定 PDF 頁面與明確啟用的矩形透視校正；厚度仍必須輸入。反光、遮擋、多零件、無比例或線／圓弧輪廓仍需人工處理。
7. DXF 垂直切片支援 modelspace 中一個閉合 2D 外框的拉伸或 CENTER 半剖面旋轉、Z 軸圓孔與規則孔陣列；只會把可忠實還原的全域一致四角圓角／倒角轉成完成特徵。側向孔、工程圖 PDF、多視圖配對、尺寸註記與局部完成特徵仍屬後續工作。過大而無法在安全取樣上限內維持 0.5 mm 弦長精度的圓弧會停止轉換。
8. FreeCAD 來源已通過語法、確定性與 conformance 測試，但本機尚未在 FreeCAD host runtime 實際執行；不得把 source export 解讀為 host runtime 驗收。
9. Fusion 360／SOLIDWORKS 沒有伺服器端執行能力，也沒有授權桌面主程式的端到端驗收；adapter 只處理同工作包的中性 STEP。
10. `docker-compose.sandboxed.yml` 提供可執行的單機 OS 隔離基線，但不是完整多租戶平台；正式公開部署仍需 TLS、帳號授權、租戶配額、保留政策、集中式 queue／database，以及平台核准的 seccomp／AppArmor 或等效政策。本機沒有 Docker executable，因此此 Compose profile 仍需在 Docker 主機做最終啟動驗收。

## 測試與品質檢查

```bash
uv run pytest
uv run python -m compileall -q app scripts tests
uv run ruff check .
node --check app/static/app.js
uv run python scripts/smoke_test.py
```

目前 180 項測試涵蓋規則解析、NEMA17 Agent、M 制孔徑、盲孔、沉頭孔、矩形側面開口、X/Y/Z 軸幾何原始碼、圖片／多頁 PDF 安全解碼與校準、透視校正、自由輪廓、DXF 格式／單位／2D／複雜度防線、CENTER 旋轉、孔陣列、圓角／倒角推論、四種旋轉來源編譯、Feature Tree 編輯、驗證閘門、API Token、檔案下載與 ZIP、六後端 conformance，以及 queue 原子 claim、lease 復原、損壞 payload、取消競態、程序樹終止、REST／CLI／Worker 背景流程。

Build123d 已在獨立環境實際驗收：有效的 80×40×5 mm STEP，包含兩個半徑 3.3 mm 的圓柱孔面。CadQuery 與 Build123d 的 OCP 相依衝突，因此此結果來自分離的 venv。

## 專案結構

```text
app/
  api/                 REST 路由
  core/                設定、Token 與路徑安全
  models/              CadDocument DSL 與 API 模型
  services/
    backends.py         關閉式能力 registry 與後端選擇
    build123d_compiler.py / freecad_compiler.py / external_adapters.py
    planners/          本地規則與 OpenAI-compatible LLM
    image_analysis.py  校準影像、特徵擷取與 Feature Tree 轉換
    dxf_analysis.py    受限 DXF 解析、正規化與 Feature Tree 轉換
    compiler.py        CadQuery 確定性編譯器
    openscad.py        OpenSCAD 編譯器
    renderer.py        CAD 核心選擇與受限 subprocess
    validator.py       幾何／製造前驗證閘門
  workers/             一次性 DXF 分析程序
  static/              無前端建置步驟的 Web UI
docs/                  API、架構、DSL、提示詞與安全說明
examples/generated/    可直接修改及重新輸出的完整範例
scripts/               doctor 與 smoke test
tests/                 單元、API 與安全回歸測試
generated/             執行時工作目錄
```

詳細資料：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/ROADMAP.md`](docs/ROADMAP.md)
- [`docs/CAD_DSL.md`](docs/CAD_DSL.md)
- [`docs/API.md`](docs/API.md)
- [`docs/SECURITY.md`](docs/SECURITY.md)
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
