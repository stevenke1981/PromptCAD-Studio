# Security Policy

## 核心安全邊界

- LLM 只可輸出 JSON DSL；不執行模型提供的 Python、Shell、路徑或網路請求。
- CadQuery/OpenSCAD 原始碼只由白名單編譯器依數值模型產生。
- 生成子程序使用固定命令陣列，不使用 shell。
- Job ID 與下載檔名都做路徑邊界檢查。
- 提示詞、孔數、尺寸、輸出格式與執行時間均有限制。
- 對外部署時應設定 `PROMPTCAD_API_TOKEN`，並由反向代理補上 TLS、流量限制及身分驗證。

## 回報漏洞

請勿在公開 issue 放入可利用細節、API Key 或客戶 CAD。請以私密安全通報方式聯絡專案維護者。
