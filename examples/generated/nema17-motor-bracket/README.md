# 第二階段：NEMA17 標準件 CAD Agent

輸入：

```text
畫一個可固定 NEMA17 馬達的支架
```

Agent 自動完成：

- 60 × 50 × 50 mm、3 mm 厚鋁製 L 支架。
- 31 mm 方形孔距、4 × Ø3.4 mm M3 間隙孔。
- Ø22.5 mm 馬達定位凸台／軸心通孔。
- 四個 Ø4.5 mm M4 底板安裝孔。
- 在 `spec.json.standards` 保存原廠馬達圖面與支架厚度來源。
- STEP、STL、DXF、SVG、PDF、CadQuery、OpenSCAD 與 JSON。

3 mm 是常見支架厚度，不是 NEMA 強制規格；可在提示詞使用 `板厚5mm` 等語句覆寫。
