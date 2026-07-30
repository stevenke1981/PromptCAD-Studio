# ESP32-2432S028R 螢幕外殼

PromptCAD Studio 產生的開放式桌上外殼，適用於常見 ESP32-2432S028R（CYD）2.8 吋觸控螢幕模組。

## 設計尺寸

- 模組參考外形：86 × 50 mm
- 外殼：94 × 58 × 22 mm
- 內腔：90 × 54 mm
- 壁厚／底厚：2 mm
- 安裝孔：4 × Ø3.2 mm
- 孔中心距：70 × 34 mm
- 材料建議：PETG 或 ABS

## 檔案

- `spec.json`：可在 PromptCAD Web UI 或 CLI 重新驗證、修改。
- `validation.json`：PromptCAD 幾何驗證報告。
- `preview.svg`：俯視工程預覽。
- `drawing.pdf`：A4 橫式三視圖工程草圖。
- `model.step`：可編輯的 STEP 實體模型。
- `model.stl`：可直接匯入切片軟體的網格模型。
- `model.dxf`：外殼水平截面。
- `model.svg`：CadQuery 實體投影。
- `model.py`：CadQuery 參數化模型。
- `model.scad`：OpenSCAD fallback 模型。

## 重新輸出

```powershell
promptcad validate examples/generated/esp32-display-enclosure/spec.json
promptcad render examples/generated/esp32-display-enclosure/spec.json
```

## 製作前確認

ESP32-2432S028R 有不同 PCB／USB 接頭版次。列印前請以游標卡尺核對模組外形、孔距、最高元件和 USB／TF 卡位置。此版先提供可直接安裝螢幕模組的開放式底座；側面接頭切口應依手上實板加入。
