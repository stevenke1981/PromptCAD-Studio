# Release Notes

## v0.1.0 — 2026-07-30

首個可執行 Prompt-to-CAD MVP：

- 受控、可編輯 CadDocument 1.0 DSL。
- 中文／英文規則解析及 OpenAI-compatible LLM planner。
- CadQuery 與 OpenSCAD 確定性編譯器。
- STEP、STL、DXF、SVG、Python、SCAD、JSON 輸出管線。
- Plate、cylinder、ring、L bracket、enclosure。
- Through、blind、clearance、tapped approximation、counterbore、countersink。
- X/Y/Z 軸孔與常見 M 制孔徑輔助。
- Web JSON 編輯與 `/generate-from-spec`。
- 驗證失敗阻止 CAD kernel 執行。
- Token-aware 預覽／下載、路徑安全、Docker、Conda、CI 與 26 項測試。
- uv 鎖定環境與 Python 3.11／3.12 CI 矩陣，讓本機與 GitHub Actions 使用同一套相依版本。
- 修正全專案 Ruff 掃描誤納入 `.venv` 的問題，並清除既有靜態檢查警告。
