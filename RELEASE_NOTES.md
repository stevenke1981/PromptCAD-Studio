# Release Notes

## v0.1.0 — 2026-07-30

首個可執行 Prompt-to-CAD MVP：

- 第二階段標準件 CAD Agent：NEMA17 自動路由、版本化來源、參數覆寫與支架生成。
- 受控、可編輯 CadDocument 1.0 DSL。
- 中文／英文規則解析及 OpenAI-compatible LLM planner。
- CadQuery 與 OpenSCAD 確定性編譯器。
- STEP、STL、DXF、SVG、Python、SCAD、JSON 輸出管線。
- A4 橫式三視圖工程草圖 PDF，可在 source-only 模式輸出。
- Plate、cylinder、ring、L bracket、enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- 開放式外殼 ±X／±Y 矩形側面切口，支援中文／英文提示詞、DSL、驗證、預覽與雙編譯器。
- X/Y/Z 軸孔與常見 M 制孔徑輔助。
- Web JSON 編輯與 `/generate-from-spec`。
- 驗證失敗阻止 CAD kernel 執行。
- Token-aware 預覽／下載、路徑安全、Docker、Conda、CI 與 42 項測試。
- uv 鎖定環境與 Python 3.11／3.12 CI 矩陣，讓本機與 GitHub Actions 使用同一套相依版本。
- 修正全專案 Ruff 掃描誤納入 `.venv` 的問題，並清除既有靜態檢查警告。
- 修正 Windows 使用相對資料目錄時 renderer 重複拼接工作路徑的問題。
