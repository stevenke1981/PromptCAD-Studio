# CadDocument 1.0 / 1.1 DSL

`spec.json` 是 PromptCAD Studio 的可編輯中介格式。所有尺寸均為毫米，所有 model 都禁止未知欄位。

一般參數化零件使用 1.0；DXF 自由閉合輪廓使用 1.1。

## 最小板件

```json
{
  "schema_version": "1.0",
  "name": "simple-plate",
  "source_prompt": "manual",
  "unit": "mm",
  "material": "aluminum",
  "base": {"kind": "plate", "length": 100, "width": 60, "thickness": 8},
  "holes": [],
  "cutouts": [],
  "fillets": [],
  "chamfers": [],
  "standards": [],
  "assumptions": [],
  "notes": [],
  "planner": {"planner": "manual", "confidence": 1, "review_required": false}
}
```

## Base

### Plate

```json
{"kind": "plate", "length": 120, "width": 60, "thickness": 10}
```

### Cylinder

```json
{"kind": "cylinder", "diameter": 30, "height": 50}
```

### Ring

```json
{"kind": "ring", "outer_diameter": 30, "inner_diameter": 15, "height": 5}
```

### L bracket

```json
{"kind": "l_bracket", "width": 80, "depth": 50, "vertical_height": 60, "thickness": 4}
```

### Open enclosure

```json
{"kind": "enclosure", "length": 100, "width": 70, "height": 30, "wall_thickness": 2}
```

### Profile extrusion（schema 1.1）

自由 2D 外框由連續、閉合的 line 與 three-point arc 組成，再沿 +Z 拉伸：

```json
{
  "schema_version": "1.1",
  "base": {
    "kind": "profile_extrusion",
    "thickness": 6,
    "outer": {
      "segments": [
        {"kind": "line", "start": {"x": 0, "y": 0}, "end": {"x": 80, "y": 0}},
        {
          "kind": "arc",
          "start": {"x": 80, "y": 0},
          "mid": {"x": 90, "y": 10},
          "end": {"x": 80, "y": 20}
        },
        {"kind": "line", "start": {"x": 80, "y": 20}, "end": {"x": 0, "y": 20}},
        {
          "kind": "arc",
          "start": {"x": 0, "y": 20},
          "mid": {"x": -10, "y": 10},
          "end": {"x": 0, "y": 0}
        }
      ]
    }
  }
}
```

驗證器會拒絕不連續、未閉合、退化、自交、自接觸、零面積、過度取樣、X／Y 軸側向孔或 Z 軸孔落在輪廓外的幾何。CadQuery 保留精確三點圓弧；OpenSCAD 只在安全上限內能維持 0.5 mm 弦長精度時使用折線近似，否則 validation 會阻止輸出。

## Hole

通孔：

```json
{
  "kind": "hole",
  "x": 40,
  "y": 20,
  "z": 0,
  "axis": "z",
  "diameter": 6.6,
  "hole_type": "clearance",
  "depth": null,
  "thread": "M6",
  "counterbore_diameter": null,
  "counterbore_depth": null,
  "countersink_diameter": null,
  "countersink_angle": null
}
```

盲孔需要 `depth`；沉孔需要 `counterbore_diameter` 與 `counterbore_depth`；沉頭孔需要 `countersink_diameter` 與 `countersink_angle`。

可用 `hole_type`：

- `through`
- `blind`
- `clearance`
- `tapped`
- `counterbore`
- `countersink`

可用 `axis`：`x`、`y`、`z`。盲孔與頭部特徵一律從該軸正向外表面向內建立。

## Rectangular side cutout

矩形側面開口目前適用於 `enclosure`：

```json
{
  "kind": "rectangular_cutout",
  "face": "positive_y",
  "x": 25,
  "y": 0,
  "z": 9,
  "width": 14,
  "height": 8
}
```

`face` 可用 `positive_x`、`negative_x`、`positive_y`、`negative_y`。`width` 是所選側面的水平方向尺寸，`height` 是 Z 軸垂直尺寸；`x`、`y`、`z` 表示開口中心。切口會自動貫穿外殼壁厚。

## Fillet / Chamfer

```json
{"kind": "fillet", "radius": 5, "selector": "vertical"}
```

```json
{"kind": "chamfer", "distance": 2, "selector": "vertical"}
```

selector：`all`、`vertical`、`top`、`bottom`。

## Standard provenance

標準件 CAD Agent 會把採用的外部尺寸來源保存在 `standards`：

```json
{
  "key": "nema17-face",
  "revision": "2026-07-nanotec-st4118",
  "source_label": "Nanotec ST4118 NEMA 17 product overview mechanical drawing",
  "source_url": "https://www.nanotec.com/fileadmin/files/Baureihenuebersichten/Schrittmotoren/Product_Overview_ST4118.pdf"
}
```

## 驗證與重建

```bash
promptcad validate spec.json
promptcad render spec.json
```

或 POST 到 `/api/v1/validate` 與 `/api/v1/generate-from-spec`。

即使 schema 合法，孔或側面開口仍可能落在零件外，孔也可能互相重疊或深度超過材料；這些由 DesignValidator 檢查，error 會阻止 renderer。
