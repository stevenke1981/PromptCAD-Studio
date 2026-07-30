# QA Report — PromptCAD Studio v0.1.0

驗證日期：2026-07-30（Asia/Taipei）

## 通過項目

- `pytest -q`：40/40 通過。
- 測試覆蓋率：`app` 77%。
- `uv sync --frozen --extra dev`：Python 3.12.13 鎖定環境同步通過。
- `ruff check .`：通過。
- `python -m compileall -q app scripts tests examples/generated/plate-four-holes/model.py`：通過。
- `node --check app/static/app.js`：通過。
- `docker-compose.yml`、`environment.yml`、`environment.runtime.yml` YAML 解析：通過。
- 完整範例 `spec.json` 驗證：通過。
- source-only 提示詞 → DSL → 驗證 → 原始碼 → 預覽端到端 smoke：通過。
- 常見私鑰／API Key 模式掃描：未發現憑證。
- `pip-audit`：本輪兩次連線 PyPI 均於 TLS handshake 逾時，未能刷新漏洞資料；前一輪稽核未發現已知漏洞。
- Editable package 安裝與 `promptcad --help`：通過。
- 內建瀏覽器實際操作提示詞生成：預覽、source-only 狀態、6 個下載入口及最近工作均正確，瀏覽器 console 無錯誤。
- CadQuery 2.8.0 實際產生 ESP32 螢幕外殼 STEP／STL／DXF／SVG，STEP 回讀為單一實體，外形 94 × 58 × 22 mm，含 4 個圓柱孔面。
- Windows 相對 `PROMPTCAD_DATA_DIR` renderer 路徑回歸測試：通過。
- 中文提示詞產生 +Y 面 14 × 8 mm 矩形開口：CadQuery 完成，STEP 回讀為單一實體 94 × 58 × 22 mm，體積 22520 mm³。
- A4 橫式三視圖 `drawing.pdf`：PDF 1.4 結構、視圖標籤、外形尺寸與標題欄測試通過。
- 第一階段固定座提示詞：120 × 60 × 30 mm、兩個置中 Ø6.6 mm M6 間隙孔、R5 與全部八種輸出格式通過。
- 第二階段 NEMA17 Agent：`auto` 自動路由、2 筆來源 provenance、4×M3 馬達孔、Ø22.5 中心孔、4×M4 底板孔及參數覆寫通過；STEP 回讀為單一 60 × 50 × 53 mm 實體。
- 多代理稽核後補強：無空格中文辨識、cm/in 單位轉換、標準幾何偏離阻擋、PDF 馬達面孔投影與 Web 來源／假設顯示。

## 測試涵蓋

- 中文與英文尺寸解析。
- M2～M12 常用孔徑邏輯、中文數量字與四角排列。
- 盲孔、沉孔、沉頭孔與材料深度。
- L 型支架立板 Y 軸孔及 X/Y/Z 軸編譯。
- 圓環、外殼、圓角、孔邊界與孔重疊。
- OpenAI-compatible Structured Outputs request 與失敗 fallback。
- Web/API 可編輯 DSL 重新輸出。
- API Token、檔案下載、ZIP 與路徑穿越防護。
- 無效幾何的 validation-blocked 閘門。
- 外殼矩形側面切口的提示詞解析、面邊界、CadQuery／OpenSCAD 編譯與 API capabilities。
- Prompt injection 文字保持為 JSON 資料，不形成 Python import／system call。

## 未在封裝主機執行的項目

Docker、OpenSCAD 與 Conda 路徑未在此主機執行。CadQuery 2.8.0 的 OpenCascade 輸出路徑已直接驗證；完整 Docker 與 Conda 定義固定使用 Python 3.11 + CadQuery 2.8.0。
