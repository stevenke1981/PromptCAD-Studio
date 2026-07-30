# Security 與 Threat Model

## 受保護資產

- LLM API Key、Token 與供應商憑證。
- 使用者提示詞、尺寸、客戶零件與輸出檔。
- Renderer 主機、CPU／記憶體與檔案系統。

## 信任邊界

- 使用者 Prompt 與手動 DSL：不可信。
- LLM 回應：不可信，即使 Structured Outputs 驗證成功也只代表格式正確。
- PromptCAD 編譯器：受信任程式碼。
- CadQuery／OpenSCAD／OpenCascade：高複雜度原生幾何核心，必須限制資源。

## 主要威脅

- 提示詞注入要求執行 Python、Shell、讀取憑證或寫入任意路徑。
- LLM 回傳惡意欄位、超大尺寸、超高孔數、NaN／Infinity 或退化幾何。
- CAD kernel 被複雜布林操作耗盡資源或觸發原生崩潰。
- 下載端點路徑穿越。
- 公開部署沒有認證、TLS、速率限制或租戶隔離。
- 使用者誤把 AI 草案當作可直接加工的正式工程圖。

## 現有控制

- 所有 DSL model 使用 `extra=forbid`。
- 數值上下限、固定毫米單位、孔／特徵數上限與固定輸出格式。
- LLM 只能填寫 `LLMIntent`，不能提供程式碼、命令、模組或路徑。
- 編譯器只把已驗證的數值映射到固定 CadQuery／OpenSCAD 模板。
- 任何設計 validation error 都會標記 `validation-blocked`，不啟動 CAD 核心。
- subprocess 使用參數陣列、`shell=false`、關閉 stdin、固定 cwd 與 timeout。
- job ID、檔名與解析後路徑皆驗證，禁止跨工作目錄下載。
- 可選 Bearer／X-API-Key，使用 constant-time compare。
- `.env`、執行輸出、cache 與憑證不進版本控制或 Docker build context。
- API 回應與 manifest 不保存 LLM API Key。

## 部署要求

公開部署至少應增加：

- TLS 與反向代理。
- 強制 API Token 或真正的使用者認證。
- 每 IP／使用者速率限制、工作數與輸出大小配額。
- Renderer 獨立容器、無外網、非 root、唯讀根目錄、cgroup 與 seccomp。
- 工作目錄生命週期與自動刪除政策。
- 依租戶隔離資料與下載授權。
- 依賴與容器漏洞掃描、鎖版及 SBOM。

## 尚未完成

- OS 級 sandbox、seccomp 與每工作 cgroup 配額。
- 多租戶帳號、細粒度授權、審計資料庫與速率限制。
- CAD kernel 惡意／退化 BREP fuzzing。
- 工作佇列與 renderer worker 網路隔離。
- 正式工程圖、公差與製造簽核流程。

## 安全回報

請勿在公開 issue 貼出 API Key、客戶 CAD 或可利用的主機資訊。安全回報流程見根目錄 `SECURITY.md`。
