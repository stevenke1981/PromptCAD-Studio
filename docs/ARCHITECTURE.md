# Architecture

```text
Prompt
  ├─ StandardAwarePlanner → versioned standards catalog
  ├─ RuleBasedPlanner
  └─ OpenAICompatiblePlanner
          ↓
LLMIntent / normalized intent ───────────────┐
                                             │
Image (PNG/JPEG)
  → bounded Pillow decode
  → OpenCV rectangle/circle extraction
  → calibrated ImageAnalysis 1.0
  → editable Feature Tree ──────────────────┤
                                             ↓
DXF
  → request/body + entity budgets
  → one-shot ezdxf worker
  → normalized line/three-point-arc profile + circles
  → editable Feature Tree ──────────────────┤
                                             ↓
CadDocument 1.0 / 1.1
(Pydantic, extra=forbid, units=mm, bounded lists/numbers)
          ↓
DesignValidator
  ├─ valid ───→ EngineeringDrawingPdf + closed CadBackend registry
  │               ├─ six deterministic source compilers / adapters
  │               └─ selected local runner: CadQuery / Build123d / OpenSCAD
  └─ invalid ─→ 保存 DSL／原始碼／預覽，但 validation-blocked
          ↓
backend-report.json + Job manifest + downloadable artifacts
```

## 為何加入 DSL

直接讓模型輸出並執行 CAD Python 會帶來任意程式碼執行、不可重現、套件版本漂移與幾何失敗風險。DSL 將能力限制在明確的幾何白名單；模型只負責理解需求，Pydantic 負責型別與範圍，編譯器負責確定性實作。

同一份 `spec.json` 可以：

- 經 Web UI 修改後重新生成。
- 透過 CLI `promptcad render spec.json` 重現。
- 放入 Git 進行設計審查與差異比較。
- 由不同 renderer 產生可比較的輸出。

## Image-to-CAD ingestion

圖片不進入 prompt planner，也不會產生或執行任意 Python。`ImageFeatureExtractor` 先以 Pillow 解碼及正規化 EXIF 方向，再把受限像素陣列交給 OpenCV。輸出是有型別、有限值、固定 operation 白名單的 Feature Tree；只有高信心矩形外框與圓孔能轉成 `CadDocument`。

單一已知最長邊提供等比例校準，座標轉換後以外框中心作 CAD `(0, 0)`。厚度必須由使用者提供。所有影像結果預設 `review_required=true`，Web 和 CLI 都需要明確確認才進入 renderer。

上傳資料不保存到工作目錄；原始檔名、MIME、DPI 與 EXIF 尺寸不作為幾何或路徑依據。

## DXF-to-CAD ingestion

DXF 也不進入 prompt planner。父程序先限制 multipart bytes 與併發，將內容寫入伺服器擁有的暫存路徑，再以 `shell=false` 啟動一次性 worker。worker 使用直接鎖定的 `ezdxf`，只讀取 modelspace 的 LINE、ARC、CIRCLE、closed LWPOLYLINE 與 2D POLYLINE；父程序負責 timeout、終止與暫存檔清理。

支援範圍正規化為 `CadDocument 1.1` 的 line／three-point-arc `profile_extrusion`，圓孔轉為 Z 軸 through hole。Feature Tree 可編輯，但伺服器會以 HMAC 驗證 DXF SHA-256、單位、解析器版本、實體統計與原始幾何，再重新執行 DesignValidator。原始 DXF 與使用者檔名不進入 job、artifact 或 ZIP。

## 坐標系

- 單位固定為毫米。
- 原點位於零件 XY 中心、底面 Z=0。
- 板件、圓柱、圓環與盒體沿 +Z 建立。
- 孔位置使用全域 `(x, y, z)`。
- `axis=z`：孔在 XY 平面定位；正面入口位於 +Z 面。
- `axis=x`：孔在 YZ 平面定位；正面入口位於 +X 面。
- `axis=y`：孔在 XZ 平面定位；正面入口位於 +Y 面。
- 盲孔、沉孔與沉頭孔由正向外表面向零件內部切除。

## CadBackend 能力合約 1.0

`BackendRegistry` 是建立於 server startup 的固定、唯讀 allowlist。外部請求只能選擇 `auto`、`source_only` 或已註冊的短 ID；不能提供 import path、class name、executable、arguments、environment 或 plugin metadata。

每個 backend 宣告：

- compiler 與 contract version。
- `local_process`、`host_application` 或 `none` 執行類型。
- CadDocument schema、base、feature 與 export format 支援。
- server render formats、runtime availability 與 semantic fidelity。
- 固定來源檔名。

| Backend | 來源檔 | Server execution | 輸出／語意 |
|---|---|---|---|
| CadQuery | `model.py` | 本機受限 subprocess | STEP／STL／DXF／SVG；exact |
| Build123d | `model.build123d.py` | 明確 opt-in 且 runtime 可用時 | STEP／STL；exact |
| FreeCAD Python | `model.freecad.py` | 不執行 | STEP／STL source contract；exact |
| OpenSCAD | `model.scad` | 本機受限 subprocess | STL；approximated |
| Fusion 360 | `model.fusion360.py` | 不執行，需授權 host app | 匯入 sibling STEP 再輸出 F3D |
| SOLIDWORKS | `model.solidworks.py` | 不執行，需授權 host app | 匯入 sibling STEP 再輸出 SLDPRT |

六種來源都從 schema 驗證過的 `CadDocument` 確定性產生；prompt 只會成為 JSON 字串資料。DesignValidator 出現 error 時不會啟動任何 CAD runner。Fusion 360／SOLIDWORKS adapter 只讀取同工作包的 `model.step`，不讀環境變數、不啟動 subprocess，也不匯入 PromptCAD 內部模組。

## Renderer 選擇與 fallback

`PROMPTCAD_RENDER_BACKEND`：

- `auto`：CadQuery → OpenSCAD → source-only。
- `cadquery`：只選 CadQuery；runtime 缺少時依 `ALLOW_SOURCE_FALLBACK` 降為 source-only。
- `build123d`：只在明確選擇且選用 runtime 可用時執行，不加入 `auto` chain。
- `openscad`：可輸出 STL，但不支援 STEP／DXF／SVG；fillet／chamfer 會在執行前 fail closed。
- `freecad`：只輸出來源。`fusion360`、`solidworks` host adapter 永不在伺服器執行；render 工作會用可用的 CadQuery／Build123d exact runtime 建立已驗證 sibling STEP。
- `source_only`：只產生可重現來源與預覽。

Renderer 只執行 server-owned compiler 從受控 DSL 生成的固定程式，使用 `shell=false`、固定命令列、私有 staging／HOME／TEMP、環境 allowlist、執行期間有界 stdout／stderr、程序樹 timeout、併發槽與 artifact 大小／格式簽章檢查。所有要求且宣告支援的輸出都必須存在、非空並通過驗證，才會從 staging 原子提升到 job；實際 fallback provenance 在執行後寫入 manifest 與 backend report。

CadQuery 2.8.0 與 Build123d 0.11.1 依賴互相衝突的 OCP distributions，必須安裝於不同 venv。Build123d 已在獨立環境實際輸出並回讀 80×40×5 mm STEP 與兩個半徑 3.3 mm 圓柱孔面；FreeCAD host runtime 尚未執行驗收。

## 驗證閘門

驗證分為 schema 與設計兩層：

1. Pydantic schema：型別、必填欄位、未知欄位、正數、最大尺寸、最大特徵數。
2. DesignValidator：邊界、有效孔徑、孔重疊、材料深度、圓環內孔、薄壁、邊距、圓角／倒角。

只要出現 `error`，renderer 就不會啟動。`warning` 不阻止輸出，但 `review_required=true`。

## 儲存模型

每個工作使用不可預測 UUID hex 目錄，包含：

```text
spec.json
validation.json
model.py
model.build123d.py
model.freecad.py
model.scad
model.fusion360.py
model.solidworks.py
backend-report.json
preview.svg
drawing.pdf（不依賴 CAD kernel 的三視圖工程草圖）
model.step / model.stl / model.dxf / model.svg（依環境與請求）
render-warnings.json（CadQuery 執行時）
manifest.json
```

`backend-report.json` 記錄 contract、能力快照、spec／source hash、後端選擇、fallback chain 與 diagnostics。manifest 記錄 planner、renderer、逐格式結果、artifact SHA-256、狀態、警告及產物，不把 API Key 寫入檔案。

`GET /capabilities` 與 `promptcad capabilities` 同時公開 backend 與 planner capability；API、CLI 與 Web 共用相同 backend ID 集合。

## 擴充新特徵

1. 在 `app/models/cad.py` 加入新的 base 或 feature 型別與 schema 限制。
2. 在 `compiler.py` 與 `openscad.py` 實作確定性編譯。
3. 在 `validator.py` 加入邊界、材料深度與特徵互動檢查。
4. 更新 `LLMIntent`、system prompt 與 rule parser。
5. 新增語意、原始碼與 API 回歸測試。
6. 更新 DSL 文件與範例。

## 生產化建議

- 將 renderer 拆成無對外網路的獨立 worker 與工作佇列。
- 每個工作使用容器、非 root 使用者、唯讀根檔案系統、seccomp、CPU／記憶體／檔案大小／時間限制。這是公開部署的必要條件，不是選配強化。
- 使用 PostgreSQL 與物件儲存取代單機目錄，並加入租戶與配額。
- 對 prompt、schema、compiler、kernel 與輸出保存版本 provenance。
- 增加 BREP 有效性、最小壁厚、干涉、可加工性、公差堆疊與材料規則。
- 對複雜／退化幾何進行 fuzzing 與 renderer 崩潰隔離。
