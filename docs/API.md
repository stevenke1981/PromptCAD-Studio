# REST API

基底路徑為 `/api/v1`。設定 `PROMPTCAD_API_TOKEN` 後，所有本章端點都需要 Bearer Token 或 `X-API-Key`。

## 狀態語意

- `completed`：請求的 CAD 核心輸出已執行完成。
- `source_only`：沒有可用 CAD 核心、指定 `render=false`，或設定為 source-only；DSL、原始碼與預覽仍可用。
- `failed`：設計驗證失敗或 renderer 發生不可恢復錯誤。
- `renderer_used=validation-blocked`：DSL 可解析，但存在幾何 error，因此沒有執行 CadQuery／OpenSCAD。

## `GET /health`

```json
{"status": "ok", "version": "0.2.0"}
```

## `GET /capabilities`

回傳規劃器、特徵、格式、圖片分析、CadQuery／OpenSCAD 可用狀態及目前設定。

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
  "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
  "render": true
}
```

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
  "formats": ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
  "render": true
}
```

## `POST /image-analysis`

以 `multipart/form-data` 上傳校準正俯視 PNG/JPEG。此端點只分析，不建立工作或執行 CAD kernel。

欄位：

- `image`：PNG/JPEG，實際格式由解碼器判定，不信任檔名或 MIME。
- `known_length_mm`：外框最長邊的實際毫米尺寸。
- `thickness_mm`：使用者量測或指定的零件厚度。

```bash
curl -X POST http://localhost:8000/api/v1/image-analysis \
  -F "image=@plate-top-view.png" \
  -F "known_length_mm=100" \
  -F "thickness_mm=5"
```

成功回傳校準端點、比例、外框、圓孔、信心、可編輯 `feature_tree`、`proposed_spec` 與 validation。`convertible=false` 表示外框或幾何不足以安全轉換；呼叫端不可直接製造。

## `POST /image-feature-tree-to-spec`

將人工編輯後的 Feature Tree 重新轉成受控 `CadDocument`。只接受矩形 sketch、extrude、圓 sketch 與成對 through cut，不接受程式碼或自由命令。請將 `/image-analysis` 回傳的完整 `analysis` 原樣帶回；可修改的部分只有獨立的 `feature_tree`。伺服器會驗證分析簽章，防止偽造圖片雜湊、校準或偵測來源。

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
- `413`：圖片 multipart request 超過伺服器的前置 body 上限。
- `404`：工作或檔案不存在，或識別碼／檔名不符合安全格式。
- `422`：Prompt、DSL、圖片、校準、Feature Tree、格式或 LLM 規劃結果不符合 schema。
- HTTP `200` 且 manifest `status=failed`：工作已被記錄，但驗證閘門阻止渲染；請查看 `validation.issues`。
