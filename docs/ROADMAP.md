# PromptCAD Studio 路線圖

## 第一階段：Prompt-to-CAD MVP

狀態：可用 Baseline。

```text
自然語言
  → 結構化 CadDocument DSL
  → 幾何驗證
  → CadQuery／OpenSCAD
  → STEP／STL／DXF／SVG／工程圖 PDF
```

目前支援板件、圓柱、圓環、L 型支架、開放式外殼、孔特徵、矩形側面切口、圓角與倒角。工程圖 PDF 已提供 A4 三視圖、外形尺寸及標題欄；正式製造圖仍需公差、基準、表面處理及完整尺寸鏈。

## 第二階段：標準件 CAD Agent

狀態：NEMA17 完整垂直切片已完成；標準件目錄可繼續擴充。

目標：Agent 能從「NEMA17 馬達支架」等需求查找並套用有來源、可版本化的工程標準，不要求使用者輸入所有尺寸。

核心能力：

- 標準件資料庫與來源版本。
- NEMA17、常用軸承、螺絲、螺帽與連接器模板。
- 規格選擇、假設、信心與衝突提示。
- 馬達軸中心、孔距、安裝厚度與工具間隙推理。
- 標準資料與使用者尺寸分離，允許覆寫並保留 provenance。

目前 NEMA17 切片已具備：獨立 `standard-agent` planner、自動路由、版本化來源、31 mm 馬達孔距、M3 安裝孔、Ø22.5 mm 中心通孔、可覆寫支架參數、幾何驗證與全部輸出格式。

## 第三階段：圖片／草圖轉 CAD

狀態：校準正俯視矩形板與圓孔的完整垂直切片已完成。

輸入：零件照片、手繪草圖、白板草圖、專利圖、PDF 與掃描圖。

處理：

```text
影像校正 → 線／圓／輪廓偵測 → 尺寸推估
→ Feature Tree 候選 → 人工校準尺度 → CAD DSL → CAD
```

尺寸推估必須標示信心；沒有比例尺、已知尺寸或多視角時，不把估計值當作製造尺寸。

目前切片已具備：PNG/JPEG 安全解碼、外框最長邊校準、旋轉矩形與圓孔擷取、信心分數、影像 SHA-256、可編輯 Feature Tree、人工確認、CAD DSL、STEP/STL/DXF/SVG/PDF 輸出，以及非矩形阻擋。後續再擴充四點透視校正、任意閉合輪廓、線／圓弧與真實照片驗證集。

## 第四階段：2D 工程圖轉 3D

狀態：受限 DXF 單一閉合輪廓 + 圓孔 → 拉伸 3D 垂直切片已完成。

輸入 DXF／工程圖 PDF，辨識：

- 對稱與中心線。
- 拉伸與旋轉輪廓。
- 線性／圓周陣列。
- 倒角、圓角與孔註記。
- 多視圖對應及尺寸約束。

輸出可編輯 Feature Tree、CadDocument 與 STEP；有歧義時產生多個候選並要求覆核。

目前切片已具備：ASCII／binary DXF 安全辨識、mm／inch／cm 單位、LINE／ARC／closed LWPOLYLINE／2D POLYLINE 外框、CIRCLE 圓孔、線與三點圓弧正規化、對稱報告、可編輯 Feature Tree、CadDocument 1.1、CadQuery／OpenSCAD／SVG／PDF 輸出、一次性解析 worker、來源 HMAC 與人工確認。下一步擴充工程圖 PDF、多視圖、旋轉、陣列、倒角、圓角與尺寸約束。

## 第五階段：可擴充規劃器與多 CAD 後端

狀態：能力合約與多後端來源垂直切片已完成。

```text
Prompt／Image／DXF
  → Planner
  → CAD DSL／Feature Tree
  → Validator
  → closed CadBackend registry / contract 1.0
  → CadQuery／Build123d／FreeCAD Python／OpenSCAD source
  → Fusion 360／SOLIDWORKS host adapter
```

LLM 只負責產生受控 DSL，不直接執行任意 CAD 程式碼。每個後端必須共用幾何驗證、單位、特徵語意與可追溯輸出。

已完成：

- 固定 backend ID allowlist、能力合約 1.0 與跨後端 conformance suite。
- API、CLI、Web 後端選擇，以及 `promptcad capabilities`／planner capabilities。
- 六個確定性來源編譯器／adapter；所有來源只承接 schema 驗證後的 DSL。
- CadQuery／OpenSCAD 既有 local runner；Build123d 是 opt-in local runner。
- FreeCAD source-only；Fusion 360／SOLIDWORKS host adapters 永不在伺服器執行。
- `backend-report.json`、spec／artifact SHA-256、逐格式結果、diagnostics 與 fallback chain。
- JSON body／generation／renderer 併發與輸出上限。
- 獨立 Build123d venv 實際驗收有效 80×40×5 mm STEP 與兩個半徑 3.3 mm 圓柱孔面。

## 第六階段：非同步與 OS-sandboxed Worker

狀態：單機 durable queue 與 hardened Docker 垂直切片已完成。

```text
Web／REST／CLI enqueue
  → SQLite WAL durable queue
  → atomic claim + lease + heartbeat + bounded retry
  → isolated promptcad-worker
  → cooperative cancellation + renderer process-tree termination
  → existing manifest／artifact contract
```

已完成：

- prompt／edited spec 兩種背景工作，HTTP `202`、查詢、列表與取消端點。
- `promptcad async-generate`／`async-render`／`queue-*` 與獨立 `promptcad-worker`。
- Web 背景模式、可見 queue 狀態、取消按鈕及 reload 後續傳。
- SQLite 原子 claim、worker lease／heartbeat、過期復原、有限重試與 queue 容量。
- cooperative cancellation 會越過 planning／validation／materialization 邊界並終止 renderer 程序樹。
- Docker Compose worker 與 hardened override：無外網、唯讀 root、capability drop、no-new-privileges、CPU／memory／PID 上限；API 在此 profile 強制 source-only。

## 下一個建議里程碑

補齊可製造工程圖：尺寸約束、基準、公差、表面處理、BOM 與簽核狀態；同時擴充 2D 工程圖的旋轉、陣列、多視圖與倒角／圓角推理。正式多租戶服務另需 PostgreSQL／外部 queue、租戶配額、retention、速率限制與平台核准的 seccomp／AppArmor policy。FreeCAD／Fusion 360／SOLIDWORKS host runtime 仍需各自授權環境的人工端到端驗收。
