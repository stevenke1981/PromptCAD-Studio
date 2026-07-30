# REST API

基底路徑為 `/api/v1`。設定 `PROMPTCAD_API_TOKEN` 後，所有本章端點都需要 Bearer Token 或 `X-API-Key`。

## 狀態語意

- `completed`：請求的 CAD 核心輸出已執行完成。
- `source_only`：沒有可用 CAD 核心、指定 `render=false`，或設定為 source-only；DSL、原始碼與預覽仍可用。
- `failed`：設計驗證失敗或 renderer 發生不可恢復錯誤。
- `renderer_used=validation-blocked`：DSL 可解析，但存在幾何 error，因此沒有執行 CadQuery／OpenSCAD。

## `GET /health`

```json
{"status": "ok", "version": "0.1.0"}
```

## `GET /capabilities`

回傳規劃器、特徵、格式、CadQuery／OpenSCAD 可用狀態及目前設定。

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
- `404`：工作或檔案不存在，或識別碼／檔名不符合安全格式。
- `422`：Prompt、DSL、格式或 LLM 規劃結果不符合 schema。
- HTTP `200` 且 manifest `status=failed`：工作已被記錄，但驗證閘門阻止渲染；請查看 `validation.issues`。
