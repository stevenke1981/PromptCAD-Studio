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

目標：Agent 能從「NEMA17 馬達支架」等需求查找並套用有來源、可版本化的工程標準，不要求使用者輸入所有尺寸。

核心能力：

- 標準件資料庫與來源版本。
- NEMA17、常用軸承、螺絲、螺帽與連接器模板。
- 規格選擇、假設、信心與衝突提示。
- 馬達軸中心、孔距、安裝厚度與工具間隙推理。
- 標準資料與使用者尺寸分離，允許覆寫並保留 provenance。

## 第三階段：圖片／草圖轉 CAD

輸入：零件照片、手繪草圖、白板草圖、專利圖、PDF 與掃描圖。

處理：

```text
影像校正 → 線／圓／輪廓偵測 → 尺寸推估
→ Feature Tree 候選 → 人工校準尺度 → CAD DSL → CAD
```

尺寸推估必須標示信心；沒有比例尺、已知尺寸或多視角時，不把估計值當作製造尺寸。

## 第四階段：2D 工程圖轉 3D

輸入 DXF／工程圖 PDF，辨識：

- 對稱與中心線。
- 拉伸與旋轉輪廓。
- 線性／圓周陣列。
- 倒角、圓角與孔註記。
- 多視圖對應及尺寸約束。

輸出可編輯 Feature Tree、CadDocument 與 STEP；有歧義時產生多個候選並要求覆核。

## 第五階段：可擴充規劃器與多 CAD 後端

長期架構：

```text
Prompt／Image／DXF
  → Planner
  → CAD DSL／Feature Tree
  → Validator
  → CAD Compiler
  → CadQuery／Build123d／FreeCAD／OpenSCAD
  → Fusion 360／SolidWorks Adapter
```

LLM 只負責產生受控 DSL，不直接執行任意 CAD 程式碼。每個後端必須共用幾何驗證、單位、特徵語意與可追溯輸出。

## 下一個建議里程碑

第二階段先建立「NEMA17 馬達支架」單一垂直切片：版本化標準尺寸、可覆寫參數、孔距與軸心驗證、STEP/STL/DXF/PDF 輸出，以及來源與假設顯示。完成該切片後再擴充其他標準件。
