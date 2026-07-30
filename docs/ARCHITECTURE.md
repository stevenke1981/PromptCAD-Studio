# Architecture

```text
Prompt
  ├─ RuleBasedPlanner
  └─ OpenAICompatiblePlanner
          ↓
LLMIntent / normalized intent
          ↓
CadDocument 1.0
(Pydantic, extra=forbid, units=mm, bounded lists/numbers)
          ↓
DesignValidator
  ├─ valid ───→ EngineeringDrawingPdf + CadQueryCompiler + OpenScadCompiler → Renderer
  └─ invalid ─→ 保存 DSL／原始碼／預覽，但 validation-blocked
          ↓
Job manifest + downloadable artifacts
```

## 為何加入 DSL

直接讓模型輸出並執行 CAD Python 會帶來任意程式碼執行、不可重現、套件版本漂移與幾何失敗風險。DSL 將能力限制在明確的幾何白名單；模型只負責理解需求，Pydantic 負責型別與範圍，編譯器負責確定性實作。

同一份 `spec.json` 可以：

- 經 Web UI 修改後重新生成。
- 透過 CLI `promptcad render spec.json` 重現。
- 放入 Git 進行設計審查與差異比較。
- 由不同 renderer 產生可比較的輸出。

## 坐標系

- 單位固定為毫米。
- 原點位於零件 XY 中心、底面 Z=0。
- 板件、圓柱、圓環與盒體沿 +Z 建立。
- 孔位置使用全域 `(x, y, z)`。
- `axis=z`：孔在 XY 平面定位；正面入口位於 +Z 面。
- `axis=x`：孔在 YZ 平面定位；正面入口位於 +X 面。
- `axis=y`：孔在 XZ 平面定位；正面入口位於 +Y 面。
- 盲孔、沉孔與沉頭孔由正向外表面向零件內部切除。

## Renderer 選擇

`PROMPTCAD_RENDER_BACKEND`：

- `auto`：CadQuery → OpenSCAD → source-only。
- `cadquery`：優先 CadQuery；失敗時依 `ALLOW_SOURCE_FALLBACK` 決定是否降級。
- `openscad`：可輸出 STL，但不支援 STEP／DXF，且 fallback 不套用 fillet/chamfer。
- `source_only`：只產生可重現來源與預覽。

Renderer 只執行系統編譯器生成的固定程式，使用 `shell=false`、固定命令列、封閉工作目錄及 timeout。

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
model.scad
preview.svg
drawing.pdf（不依賴 CAD kernel 的三視圖工程草圖）
model.step / model.stl / model.dxf / model.svg（依環境與請求）
render-warnings.json（CadQuery 執行時）
manifest.json
```

manifest 記錄 planner、renderer、請求格式、狀態、警告及產物，不把 API Key 寫入檔案。

## 擴充新特徵

1. 在 `app/models/cad.py` 加入新的 base 或 feature 型別與 schema 限制。
2. 在 `compiler.py` 與 `openscad.py` 實作確定性編譯。
3. 在 `validator.py` 加入邊界、材料深度與特徵互動檢查。
4. 更新 `LLMIntent`、system prompt 與 rule parser。
5. 新增語意、原始碼與 API 回歸測試。
6. 更新 DSL 文件與範例。

## 生產化建議

- 將 renderer 拆成無對外網路的獨立 worker 與工作佇列。
- 每個工作使用容器、非 root 使用者、唯讀根檔案系統、seccomp、CPU／記憶體／檔案大小／時間限制。
- 使用 PostgreSQL 與物件儲存取代單機目錄，並加入租戶與配額。
- 對 prompt、schema、compiler、kernel 與輸出保存版本 provenance。
- 增加 BREP 有效性、最小壁厚、干涉、可加工性、公差堆疊與材料規則。
- 對複雜／退化幾何進行 fuzzing 與 renderer 崩潰隔離。
