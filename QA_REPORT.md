# QA Report — PromptCAD Studio v0.1.0

驗證日期：2026-07-30（Asia/Taipei）

## 通過項目

- `pytest -q`：26/26 通過。
- 測試覆蓋率：`app` 74%。
- `uv sync --frozen --extra dev`：Python 3.12.13 鎖定環境同步通過。
- `ruff check .`：通過。
- `python -m compileall -q app scripts tests examples/generated/plate-four-holes/model.py`：通過。
- `node --check app/static/app.js`：通過。
- `docker-compose.yml`、`environment.yml`、`environment.runtime.yml` YAML 解析：通過。
- 完整範例 `spec.json` 驗證：通過。
- source-only 提示詞 → DSL → 驗證 → 原始碼 → 預覽端到端 smoke：通過。
- 常見私鑰／API Key 模式掃描：未發現憑證。
- `pip-audit`：第三方套件未發現已知漏洞（本地套件本身不在 PyPI，依工具規則略過）。
- Editable package 安裝與 `promptcad --help`：通過。
- 內建瀏覽器實際操作提示詞生成：預覽、source-only 狀態、6 個下載入口及最近工作均正確，瀏覽器 console 無錯誤。

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
- Prompt injection 文字保持為 JSON 資料，不形成 Python import／system call。

## 未在封裝主機執行的項目

封裝主機沒有 Docker、CadQuery、OpenSCAD 或 Conda，因此未在此主機實際產生 STEP／STL／DXF。完整 Docker 與 Conda 定義固定使用 Python 3.11 + CadQuery 2.8.0；請在具備 Docker 的主機執行 `docker compose up --build` 驗證 OpenCascade 路徑。
