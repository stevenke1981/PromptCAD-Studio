# 第一階段 MVP：鋁合金固定座

輸入提示詞：

```text
幫我畫一個鋁合金固定座，長120，寬60，高30，中間兩個 M6 孔，四角 R5
```

PromptCAD 解析結果：

- 鋁合金固定座：120 × 60 × 30 mm
- 兩個 M6 一般間隙孔：Ø6.6 mm
- 孔中心：(-20, 0)、(20, 0) mm
- 垂直邊圓角：R5

本目錄包含 STEP、STL、DXF、SVG、CadQuery、OpenSCAD、DSL、驗證報告及 `drawing.pdf` 三視圖工程草圖。

## 重新驗證及輸出

```powershell
promptcad validate examples/generated/mvp-mounting-block/spec.json
promptcad render examples/generated/mvp-mounting-block/spec.json
```
