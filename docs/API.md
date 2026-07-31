# REST API

基底路徑為 `/api/v1`。設定 `PROMPTCAD_API_TOKEN` 後，所有本章端點都需要 Bearer Token 或 `X-API-Key`。

## 狀態語意

- `completed`：請求的 CAD 核心輸出已執行完成。
- `source_only`：沒有可用 CAD 核心、指定 `render=false`，或設定為 source-only；DSL、原始碼與預覽仍可用。
- `failed`：設計驗證失敗或 renderer 發生不可恢復錯誤。
- `renderer_used=validation-blocked`：DSL 可解析，但存在幾何 error，因此沒有執行 CadQuery／OpenSCAD。

## `GET /health`

```json
{"status": "ok", "version": "0.7.0"}
```

## `GET /capabilities`

回傳規劃器、特徵、格式、圖片／DXF 分析、目前設定，以及完整 `CadBackend` 能力合約 1.0：

- `backends[]`：固定 backend ID、compiler／contract version、execution kind、schema／feature／format 支援、runtime 狀態及 semantic fidelity。
- `schema_versions` 與 `base_features`：包含 CadDocument 1.2 與 `profile_revolution`。
- `dxf_operations`：`auto`、`extrude`、`revolve`。
- `planner_capabilities[]`：planner ID、版本、可用狀態、輸入類型與描述。
- `async_queue_available` 與 `async_job_kinds`：durable queue 是否可用及目前接受的 `prompt`／`spec` 工作。

CLI 可取得同一份機器可讀資訊：

```bash
promptcad capabilities
```

後端 registry 是伺服器擁有的關閉 allowlist；API 不接受模組路徑、執行檔、命令參數或外掛設定。

## `POST /plan`

只產生與驗證 CAD DSL，不建立工作目錄，也不啟動幾何核心。

```json
{
  "prompt": "做一個外徑30、內徑15、厚5的墊圈",
  "planner": "rule"
}
```

`planner` 可為 `auto`、`agent`、`rule`、`llm`。`auto` 遇到已支援的標準件（目前為 NEMA17 馬達面）會優先路由至標準件 CAD Agent。

## `POST /generate`

由提示詞建立 DSL、驗證、原始碼、預覽及可用的 CAD 輸出。

```json
{
  "prompt": "長120寬60厚10的固定板，四角M6孔，R5",
  "planner": "auto",
  "backend": "cadquery",
  "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
  "render": true
}
```

`backend` 的允許值：

- `auto`：CadQuery → OpenSCAD → source-only。
- `cadquery`：本機 CadQuery runner；可輸出 STEP／STL／DXF／SVG。
- `build123d`：選用且必須明確指定；runtime 可用時輸出 STEP／STL，否則 source-only。
- `openscad`：本機 OpenSCAD runner；只輸出 STL，且 fillet／chamfer 會 fail closed。
- `freecad`：只輸出 `model.freecad.py`，伺服器不執行。
- `fusion360`、`solidworks`：伺服器不執行 host adapter；`render=true` 時會以可用的 CadQuery／Build123d exact runtime 產生並封裝 adapter 所需的 `model.step`，否則明確降為 source-only。
- `source_only`：不啟動 CAD runtime。

若明確後端不可用且 `PROMPTCAD_ALLOW_SOURCE_FALLBACK=true`，工作會安全降級並在 diagnostics、fallback chain 與逐格式結果中說明；設為 `false` 時請求回傳 `422`。

## `POST /generate-from-spec`

將 Web 或版本控制中修改過的 `CadDocument` 重新驗證及輸出。`spec` 必須是完整 DSL；未知欄位會被拒絕。

```json
{
  "spec": {
    "schema_version": "1.0",
    "name": "edited-plate",
    "source_prompt": "manual edit",
    "unit": "mm",
    "material": "aluminum",
    "base": {"kind": "plate", "length": 140, "width": 60, "thickness": 10},
    "holes": [],
    "cutouts": [],
    "fillets": [],
    "chamfers": [],
    "assumptions": [],
    "notes": [],
    "planner": {"planner": "manual", "confidence": 1, "review_required": false}
  },
  "backend": "source_only",
  "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
  "render": true
}
```

所有正式生成路徑都接受相同 `backend` 欄位：`/generate`、`/generate-from-spec`、`/generate-from-image-feature-tree` 與 `/generate-from-dxf-feature-tree`。

## 非同步產生與取消

`POST /async/generate` 接受與 `/generate` 相同的 body；`POST /async/generate-from-spec` 接受與 `/generate-from-spec` 相同的 body。成功加入 durable queue 時回傳 HTTP `202`：

```json
{
  "queue_job_id": "32-character-lowercase-hex-id",
  "kind": "prompt",
  "status": "queued",
  "created_at": "2026-07-31T12:00:00Z",
  "updated_at": "2026-07-31T12:00:00Z",
  "attempts": 0,
  "cancellation_requested": false,
  "result_job_id": null,
  "result_url": null,
  "error": null
}
```

背景工作端點：

- `GET /async/jobs?limit=50`：依建立時間倒序列出 queue jobs。
- `GET /async/jobs/{queue_job_id}`：取得 queued／running／completed／failed／cancelled 狀態。
- `POST /async/jobs/{queue_job_id}/cancel`：queued 工作立即取消；running 工作設定 cooperative cancellation，Worker 會終止 renderer 程序樹並保存 cancelled manifest。

完成後 `result_job_id` 與 `result_url` 會指向既有 `/jobs/{job_id}` manifest，因此同步與非同步輸出契約相同。Queue 使用 SQLite WAL、原子 claim、lease／heartbeat、有限重試與服務重啟復原；至少啟動一個獨立 Worker：

```bash
promptcad-worker
```

如果沒有 Worker，工作會保持 `queued`，仍可安全取消。queue 滿載時 enqueue 回傳 `429`。

## `POST /image-analysis`

以 `multipart/form-data` 上傳校準 PNG/JPEG 或 PDF。此端點只分析，不建立工作或執行 CAD kernel。

欄位：

- `image`：PNG/JPEG/PDF，實際格式由解碼器或 PDF 簽章判定，不信任檔名或 MIME。
- `known_length_mm`：外框最長邊的實際毫米尺寸。
- `thickness_mm`：使用者量測或指定的零件厚度。
- `page_index`：PDF 的零起算頁碼，預設 0；圖片只能使用 0。
- `perspective_correction`：預設 `false`；只在來源確實為矩形板的凸四角照片時啟用。

```bash
curl -X POST http://localhost:8000/api/v1/image-analysis \
  -F "image=@plate-top-view.png" \
  -F "known_length_mm=100" \
  -F "thickness_mm=5" \
  -F "page_index=0" \
  -F "perspective_correction=false"
```

成功回傳來源頁面、校準端點、比例、矩形或自由折線外框、圓孔、信心、可編輯 `feature_tree`、`proposed_spec` 與 validation。矩形使用 `CadDocument 1.0 plate`，自由輪廓使用 `CadDocument 1.1 profile_extrusion`。`convertible=false` 表示外框或幾何不足以安全轉換；呼叫端不可直接製造。

## `POST /image-feature-tree-to-spec`

將人工編輯後的 Feature Tree 重新轉成受控 `CadDocument`。只接受矩形 sketch 或有限點數的閉合折線 sketch、extrude、圓 sketch與成對 through cut，不接受程式碼或自由命令。請將 `/image-analysis` 回傳的完整 `analysis` 原樣帶回；可修改的部分只有獨立的 `feature_tree`。伺服器會驗證分析簽章，防止偽造圖片／PDF 雜湊、頁面、校準或偵測來源。

```json
{
  "analysis": {
    "analysis_version": "1.0",
    "image_sha256": "64-character-lowercase-sha256",
    "analysis_token": "server-issued-integrity-token",
    "...": "the remaining /image-analysis response fields"
  },
  "feature_tree": [
    {
      "id": "profile-01",
      "operation": "sketch_rectangle",
      "parent_id": null,
      "parameters": {"length_mm": 100, "width_mm": 60},
      "confidence": 0.99
    },
    {
      "id": "extrude-01",
      "operation": "extrude",
      "parent_id": "profile-01",
      "parameters": {"distance_mm": 5},
      "confidence": 0.99
    }
  ]
}
```

回傳與 `/plan` 相同的 `PlanResponse`。

## `POST /generate-from-image-feature-tree`

以已簽章的圖片分析結果和人工確認後的 Feature Tree 建立正式工作。Body 與上一端點相同，另可帶 `formats` 與 `render`。此路徑會再次驗證分析來源和 CAD DSL，並在工作包中保存：

- `image-analysis.json`：校準、尺寸、輪廓、圓孔、影像 SHA-256，以及 `provenance_verification=verified-before-generation`。
- `feature-tree.json`：實際用於生成的 Feature Tree。
- `spec.json`、`validation.json` 與所有可用 CAD 產物。

`manifest.planner_used` 會保留為 `image-feature-tree`，不會降級成手動 DSL 工作。

## `POST /dxf-analysis`

以 `multipart/form-data` 上傳受限 2D DXF。此端點只分析，不建立工作或執行 CAD kernel。

- `dxf`：內容以 ASCII／AutoCAD Binary DXF 簽章與 `ezdxf` 實際解析，不信任檔名或 MIME。
- `thickness_mm`：人工指定的拉伸厚度；旋轉模式保留欄位但不使用。
- `unit_override`：`auto`、`mm`、`inch` 或 `cm`；`auto` 只接受 DXF `$INSUNITS` 明確為這三種單位。
- `operation_mode`：`auto`、`extrude` 或 `revolve`。`auto` 遇到唯一水平／垂直 CENTER layer 或 linetype 時推論旋轉，否則拉伸。

```bash
curl -X POST http://localhost:8000/api/v1/dxf-analysis \
  -F "dxf=@plate-two-holes-mm.dxf" \
  -F "thickness_mm=6" \
  -F "unit_override=auto" \
  -F "operation_mode=auto"
```

成功回傳來源與正規化幾何 SHA-256、解析器版本、實體統計、對稱軸、閉合 line／arc 輪廓、圓孔／孔陣列、推論操作、CENTER 軸、可忠實還原的一致四角 fillet／chamfer、可編輯 Feature Tree、preview 與 validation。旋轉剖面會正規化為半徑／Z 座標並建立 CadDocument 1.2；斜軸、跨軸、未接觸軸、含孔或完成特徵的旋轉第一切片會 fail closed。所有結果都要求人工覆核。

## `POST /dxf-feature-tree-to-spec`

Body 為 `/dxf-analysis` 的完整 `analysis` 與獨立、可編輯的 `feature_tree`。伺服器會驗證分析簽章、節點白名單與父子關係，再建立 `CadDocument 1.1 profile_extrusion` 或 `CadDocument 1.2 profile_revolution`；孔陣列在此邊界展開為既有的明確孔特徵。

## `POST /generate-from-dxf-feature-tree`

Body 與上一端點相同，另可帶 `formats` 與 `render`。確認後的工作會保存 `dxf-analysis.json`、`dxf-feature-tree.json`、DSL、驗證與 CAD 產物；不保存原始 DXF。`manifest.planner_used=dxf-feature-tree`。

## `POST /validate`

Body 是完整 `CadDocument`。回傳：

```json
{
  "valid": true,
  "review_required": false,
  "issues": []
}
```

`error` 會阻止 renderer；`warning` 要求人工覆核；`info` 用於假設與螺紋近似資訊。

## 工作與下載

- `GET /jobs`：列出最近工作。
- `GET /jobs/{job_id}`：取得 manifest。
- `GET /jobs/{job_id}/files/{filename}`：下載單一輸出。
- `GET /jobs/{job_id}/bundle.zip`：下載工作目錄中的所有輸出。

`job_id` 必須是系統產生的 32 位十六進位 ID；檔名只接受安全白名單字元，且解析後必須直接位於該工作目錄。

每個 manifest 額外包含：

- `backend_requested`、`backend_used`、`backend_contract_version`。
- `source_backends`、`backend_diagnostics`、`fallback_chain`。
- `spec_sha256` 與 `validation_version`。
- `format_results[]`：每個請求格式的 `produced`、`unavailable`、`failed` 或 `source_only`。
- `artifacts[].sha256`：下載產物的內容雜湊。

`backend-report.json` 保存能力快照、來源檔 SHA-256、spec hash、選擇結果與診斷。六個來源檔都由 schema 驗證後的受控 DSL 確定性產生；任何 design validation error 都會阻止 runner。

## 認證

```http
Authorization: Bearer YOUR_TOKEN
```

或：

```http
X-API-Key: YOUR_TOKEN
```

範例：

```bash
curl http://localhost:8000/api/v1/capabilities \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 常見錯誤

- `401`：Token 缺少或錯誤。
- `413`：圖片、DXF、Feature Tree 或一般 JSON generation request 超過對應的前置 body 上限。
- `429`：durable queue 已達 pending／running 容量上限。
- `404`：工作或檔案不存在，或識別碼／檔名不符合安全格式。
- `422`：Prompt、DSL、圖片、校準、Feature Tree、格式或 LLM 規劃結果不符合 schema。
- HTTP `200` 且 manifest `status=failed`：工作已被記錄，但驗證閘門阻止渲染；請查看 `validation.issues`。

## 資源上限

一般 plan／generate／generate-from-spec／validate 以及兩個 async enqueue 端點預設限制 2 MB body、4 個併發請求；Feature Tree 預設 1 MB／4 併發。Renderer 預設 2 個併發工作、120 秒 timeout、單檔 200 MB、總輸出 500 MB、console 100,000 字元。Queue 預設最多 100 個 pending／running 工作、300 秒 lease、2 次嘗試與 0.5 秒 idle polling。這些限制可由 `PROMPTCAD_MAX_GENERATE_BODY_BYTES`、`PROMPTCAD_GENERATE_CONCURRENCY`、`PROMPTCAD_RENDER_CONCURRENCY`、`PROMPTCAD_ASYNC_QUEUE_*` 及相關環境變數調整。

`docker-compose.sandboxed.yml` 將同步 API renderer 設為 source-only，並在無外網、唯讀根檔案系統、移除 capabilities、`no-new-privileges` 與 cgroup 資源限制的獨立 worker 執行 CAD runner。正式部署仍需 TLS、租戶授權／配額、保留政策與平台核准的 seccomp／AppArmor 或等效控制。
