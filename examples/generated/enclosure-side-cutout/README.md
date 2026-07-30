# Enclosure with rectangular side cutout

這個範例由中文提示詞直接產生：

```text
做一個94x58x22mm外殼，壁厚2mm，+Y面一個14x8mm矩形開口，中心x=25mm、z=9mm
```

## 驗收尺寸

- 外殼：94 × 58 × 22 mm
- 壁厚：2 mm
- 側面：`positive_y`
- 開口：14 × 8 mm
- 開口中心：X=25 mm、Z=9 mm
- STEP：單一實體，體積 22520 mm³
- PDF：A4 橫式三視圖，正視圖標示矩形開口

## 重新驗證及輸出

```powershell
promptcad validate examples/generated/enclosure-side-cutout/spec.json
promptcad render examples/generated/enclosure-side-cutout/spec.json
```
