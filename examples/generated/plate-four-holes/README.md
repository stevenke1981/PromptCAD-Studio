# Plate with four M6 clearance holes

來源提示詞：

```text
鋁合金固定板，長120mm、寬60mm、厚10mm，四角 M6 通孔，孔中心離邊緣10mm，四角 R5。
```

此目錄可在沒有 CadQuery 的環境重現：

- `spec.json`：可編輯 CAD DSL。
- `validation.json`：驗證結果。
- `model.py`：CadQuery 模型；安裝 CadQuery 後可輸出 STEP/STL/DXF/SVG。
- `model.scad`：OpenSCAD 模型。
- `preview.svg`：快速預覽。
- `generate-from-spec.request.json`：可直接 POST 到 `/api/v1/generate-from-spec`。

CLI：

```bash
promptcad validate examples/generated/plate-four-holes/spec.json
promptcad render examples/generated/plate-four-holes/spec.json
```
