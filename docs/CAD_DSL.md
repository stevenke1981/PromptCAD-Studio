# CadDocument 1.0 DSL

`spec.json` 是 PromptCAD Studio 的可編輯中介格式。所有尺寸均為毫米，所有 model 都禁止未知欄位。

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
  "fillets": [],
  "chamfers": [],
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

## Fillet / Chamfer

```json
{"kind": "fillet", "radius": 5, "selector": "vertical"}
```

```json
{"kind": "chamfer", "distance": 2, "selector": "vertical"}
```

selector：`all`、`vertical`、`top`、`bottom`。

## 驗證與重建

```bash
promptcad validate spec.json
promptcad render spec.json
```

或 POST 到 `/api/v1/validate` 與 `/api/v1/generate-from-spec`。

即使 schema 合法，孔仍可能落在零件外、與其他孔重疊或深度超過材料；這些由 DesignValidator 檢查，error 會阻止 renderer。
