# PromptCAD Studio

PromptCAD Studio 將中文或英文提示詞轉成**可驗證、可重現、可編輯的參數化 CAD**：

```text
提示詞
  → 本地規則／OpenAI-compatible LLM 規劃器
  → 受控 CadDocument 1.0 DSL
  → 幾何與製造前驗證
  → CadQuery／OpenSCAD 編譯器
  → STEP、STL、DXF、SVG、Python、SCAD、JSON
```

目前版本是可直接執行的 MVP，適合固定板、法蘭／墊圈、圓柱、L 型支架與開口外殼。系統不會直接執行模型產生的 Python；LLM 只能填寫有型別、尺寸上限與特徵白名單的 CAD DSL，再由確定性編譯器建立幾何。

## 已包含

- 中文／英文提示詞本地解析，沒有 API Key 也能使用。
- OpenAI-compatible LLM 規劃器，支援 `json_schema`、`json_object` 與純提示 JSON 模式。
- 可編輯 `spec.json`：在 Web UI 修改尺寸後重新驗證與輸出。
- 基礎幾何：板件、圓柱、圓環、L 型支架、開口外殼。
- 孔特徵：通孔、盲孔、間隙孔、攻牙底孔近似、沉孔、沉頭孔，以及 X／Y／Z 軸方向。
- 邊特徵：圓角與倒角。
- 螺紋尺寸輔助：常見 M2～M12 一般間隙孔與攻牙底孔表；其他尺寸使用明確標示的比例近似。
- 驗證閘門：孔超出輪廓、孔重疊、盲孔／沉孔深度、沉頭深度、圓環內孔、邊距、壁厚、圓角與倒角。
- 無效設計會保存 DSL 與原始碼供修正，但**不會送入 CAD 核心渲染**。
- Web UI、REST API、CLI、Docker Compose、Conda 環境、測試、CI 與完整範例。
- 每次工作保存 manifest、DSL、驗證報告、CadQuery、OpenSCAD、預覽及實際輸出。

## 最快啟動：Docker

Docker 環境內含 CadQuery 2.8.0，可實際產生 STEP、STL、DXF 與 SVG。

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

### 僅開發 DSL、API 與原始碼

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

未安裝 CadQuery／OpenSCAD 時，系統進入 `source_only`：仍會產生 `spec.json`、`validation.json`、`model.py`、`model.scad` 與 `preview.svg`，但不會偽造 STEP／STL／DXF。

## 使用方式

### Web 編輯流程

1. 輸入「長 120、寬 60、厚 10，四角 M6 通孔，R5」。
2. 系統產生 CAD DSL、驗證報告與預覽。
3. 展開「可編輯 CAD DSL / JSON」，修改長寬、孔徑、孔位或特徵。
4. 按「套用 JSON 並重新輸出」。
5. 下載個別檔案或工作 ZIP。

### CLI

```bash
promptcad generate "畫一個長120、寬60、厚10的固定板，四角M6孔，R5" --planner rule
promptcad validate examples/generated/plate-four-holes/spec.json
promptcad render examples/generated/plate-four-holes/spec.json
promptcad doctor
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
    "formats": ["step", "stl", "dxf", "svg", "py", "scad", "json"],
    "render": true
  }'
```

修改後的 DSL 重新輸出：

```bash
curl -X POST http://localhost:8000/api/v1/generate-from-spec \
  -H "Content-Type: application/json" \
  --data-binary @request-with-spec.json
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
```

更穩定的寫法、軸向與孔位格式見 [`docs/PROMPT_GUIDE.md`](docs/PROMPT_GUIDE.md)。

## 輸出檔

| 檔案 | 用途 |
|---|---|
| `spec.json` | 可編輯、可版本控制的 CAD DSL |
| `validation.json` | errors、warnings、info 與 review 狀態 |
| `model.py` | 可獨立執行的 CadQuery 模型 |
| `model.scad` | OpenSCAD fallback 模型 |
| `preview.svg` | 不依賴 CAD 核心的快速工程預覽 |
| `model.step` | 實體 CAD 交換檔，需要 CadQuery |
| `model.stl` | 3D 列印網格，需要 CadQuery 或 OpenSCAD |
| `model.dxf` | 水平截面 2D DXF，需要 CadQuery |
| `model.svg` | CadQuery 投影 SVG |
| `manifest.json` | 工作狀態、產物與 renderer provenance |

## 重要限制

1. **M6、M8 等標示不等於完整實體螺紋。** 目前建立攻牙底孔／間隙孔的圓柱幾何，螺紋規格保留在 DSL 與驗證資訊中。
2. DXF 是零件水平截面，不是含尺寸、公差、基準、表面處理與圖框的正式工程圖。
3. 規則解析器偏向常見單一零件與單一孔群；複雜多段輪廓、多孔群、裝配與自由曲面應使用 LLM 規劃器或直接編輯 DSL。
4. `preview.svg` 是快速預覽，不是 CAD kernel 的 BREP 渲染結果。
5. AI 推論與預設值都會要求 review；投入 CNC、雷切、射出或承載用途前，必須由合格工程人員覆核材料、公差、強度與製程。

## 測試與品質檢查

```bash
uv run pytest
uv run python -m compileall -q app scripts tests
uv run ruff check .
node --check app/static/app.js
uv run python scripts/smoke_test.py
```

目前測試涵蓋規則解析、M 制孔徑、盲孔、沉頭孔、X/Y/Z 軸幾何原始碼、驗證閘門、可編輯 DSL、API Token、檔案下載與 ZIP。

## 專案結構

```text
app/
  api/                 REST 路由
  core/                設定、Token 與路徑安全
  models/              CadDocument DSL 與 API 模型
  services/
    planners/          本地規則與 OpenAI-compatible LLM
    compiler.py        CadQuery 確定性編譯器
    openscad.py        OpenSCAD 編譯器
    renderer.py        CAD 核心選擇與受限 subprocess
    validator.py       幾何／製造前驗證閘門
  static/              無前端建置步驟的 Web UI
docs/                  API、架構、DSL、提示詞與安全說明
examples/generated/    可直接修改及重新輸出的完整範例
scripts/               doctor 與 smoke test
tests/                 單元、API 與安全回歸測試
generated/             執行時工作目錄
```

詳細資料：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/CAD_DSL.md`](docs/CAD_DSL.md)
- [`docs/API.md`](docs/API.md)
- [`docs/SECURITY.md`](docs/SECURITY.md)
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
