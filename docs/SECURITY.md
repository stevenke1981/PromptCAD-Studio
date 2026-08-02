# Security 與 Threat Model

## 受保護資產

- LLM API Key、Token 與供應商憑證。
- 使用者提示詞、尺寸、客戶零件與輸出檔。
- Renderer 主機、CPU／記憶體與檔案系統。

## 信任邊界

- 使用者 Prompt 與手動 DSL：不可信。
- 使用者上傳圖片／PDF／DXF、檔名、MIME、EXIF、DPI、頁面內容與 Feature Tree：不可信。
- LLM 回應：不可信，即使 Structured Outputs 驗證成功也只代表格式正確。
- PromptCAD 的固定 backend registry 與來源編譯器：受信任程式碼。
- CadQuery／Build123d／OpenSCAD／OpenCascade：高複雜度原生幾何核心，必須限制資源。
- FreeCAD／Fusion 360／SOLIDWORKS host：伺服器外部邊界；PromptCAD 只輸出受控腳本，不自動執行。

## 主要威脅

- 提示詞注入要求執行 Python、Shell、讀取憑證或寫入任意路徑。
- LLM 回傳惡意欄位、超大尺寸、超高孔數、NaN／Infinity 或退化幾何。
- CAD kernel 被複雜布林操作耗盡資源或觸發原生崩潰。
- 惡意 backend ID 嘗試注入模組路徑、執行檔、參數、環境或動態 plugin。
- Renderer 產生超大、格式偽裝、symlink 或逃出 staging 的 artifact。
- 下載端點路徑穿越。
- 公開部署沒有認證、TLS、速率限制或租戶隔離。
- 使用者誤把 AI 草案當作可直接加工的正式工程圖。
- 壓縮圖片解碼造成記憶體／CPU DoS，或錯誤校準產生比例看似合理但實際錯誤的 CAD。
- 惡意、加密、截斷或超多頁 PDF 嘗試耗盡 native PDF renderer，或利用錯誤頁面／透視假設產生看似合理的 CAD。
- 惡意 DXF 以 blocks、外部參照、非有限座標、3D OCS、極大量實體或退化圓弧耗盡解析器與驗證資源。

## 現有控制

- 所有 DSL model 使用 `extra=forbid`。
- 數值上下限、固定毫米單位、孔／特徵數上限與固定輸出格式。
- LLM 只能填寫 `LLMIntent`，不能提供程式碼、命令、模組或路徑。
- 關閉式 registry 只接受固定短 ID；沒有 request-driven import、plugin discovery、executable、arguments 或 environment。
- 六個來源編譯器只把 schema 驗證後的 DSL 數值映射到固定模板；prompt injection 只能存在於 JSON 資料，不能變成 AST call。
- 任何設計 validation error 都會標記 `validation-blocked`，不啟動 CAD 核心。
- `auto` 只會執行 CadQuery／OpenSCAD；Build123d 必須 opt-in。FreeCAD、Fusion 360 與 SOLIDWORKS 永不由 server runner 執行。
- Fusion 360／SOLIDWORKS adapter 只讀 sibling `model.step`，不讀環境變數、不啟動 subprocess、不匯入 PromptCAD 內部模組；工作包的 STEP 由可用 exact runtime 在伺服器端先驗證產生，host adapter 本身不在伺服器執行。
- subprocess 使用參數陣列、`shell=false`、關閉 stdin、固定 cwd、私有 HOME／TEMP、環境 allowlist、執行期間有界 stdout／stderr、程序樹 timeout 與固定併發槽。
- Renderer 在私有 staging 檢查 symlink、resolved path、缺檔、單檔／總大小、warning 結構及 STEP／STL／DXF／SVG signature，通過後才原子提升；hash 採分塊串流。
- 宣告 exact 的 Build123d／FreeCAD 特徵失敗時停止，不會以 warnings 靜默略過 fillet／chamfer。
- 一般 generation JSON、Feature Tree、圖片與 DXF 都有 multipart／JSON 解析前 body 上限與併發限制。
- job ID、檔名與解析後路徑皆驗證，禁止跨工作目錄下載。
- `backend-report.json` 保存 contract、capability、spec／source SHA-256、diagnostics 與 fallback chain；manifest 保存每個 artifact SHA-256 與逐格式結果。
- 可選 Bearer／X-API-Key，使用 constant-time compare。
- `.env`、執行輸出、cache 與憑證不進版本控制或 Docker build context。
- API 回應與 manifest 不保存 LLM API Key。
- 圖片路由在 multipart 解析前套用 ASGI request-body 上限；整個請求在解析與讀取前先取得固定併發槽，避免暫存磁碟與等待佇列無界成長。
- 圖片只允許實際解碼為單 frame PNG/JPEG；在 EXIF transpose／像素載入前先驗證 header 尺寸與總像素，並限制壓縮 bytes、單邊尺寸與分析時間。PDF 必須具有 `%PDF-` 簽章，並限制總頁數、指定頁碼、光柵單邊與總像素。
- pypdfium2 的文件開啟、頁面渲染與 native handle teardown 全部在同一 process-wide lock 下序列化；錯誤、加密、截斷或不安全 PDF 只回傳受控分析錯誤。
- 分析使用固定大小 executor；即使呼叫端逾時，工作完成前仍占用原槽位，不會累積無界背景原生執行緒。
- 解碼後套用 EXIF orientation 並轉成受控灰階陣列；不信任檔名、MIME、DPI 或 EXIF 尺寸。
- 原始圖片不寫入 job、artifact 或 ZIP；分析只保存 SHA-256 provenance。
- 校準保存實際距離、像素端點與 `mm_per_pixel`；角度、對邊平行度與對邊長度不符合矩形條件時不轉 CAD。
- 任意閉合折線只進入有限點數、唯一點、非零面積的 typed Feature Tree；可能是透視矩形的未校正凸四邊形預設阻擋，透視校正必須由使用者明確啟用且仍標記人工覆核。
- 伺服器以 HMAC 綁定圖片 SHA-256、尺寸、校準、偵測結果與分析版本；Feature Tree round-trip 及生成時都會驗證 provenance。
- DXF 路由在 multipart 解析前限制 body 與併發；直接鎖定 `ezdxf`，且只允許有限 modelspace 實體、單一閉合 2D 外框、有限圓孔與明確單位。
- DXF 解析在一次性 subprocess 執行，使用 `shell=false`、固定 cwd、移除應用 secrets 的環境 allowlist、timeout、固定 stdout 上限及系統暫存區中由父程序擁有的路徑；父程序在成功、錯誤或 timeout 後清理檔案。
- LINE／ARC 正規化保留 exact three-point arc；OpenSCAD／validation 的取樣數另有硬上限，避免極大半徑造成 CPU／記憶體 DoS。
- 原始 DXF、上傳檔名與未列入 manifest 的檔案不進入 artifact 或 ZIP；DXF provenance 以 HMAC 綁定來源雜湊、單位、解析器版本、實體統計與原始幾何。
- Feature Tree 使用 operation 與參數白名單、有限值和父子關係驗證，再經既有 `DesignValidator` 才能渲染。
- Docker Compose 預設只綁定 `127.0.0.1`；公開部署必須另行設定認證、TLS、速率限制及 OS sandbox。
- Durable queue 使用固定 SQLite schema、參數化查詢、32 位 hex ID、原子 claim、lease／heartbeat、有限重試與 pending 容量；payload 進入 Worker 後再次以 Pydantic 驗證。
- Running cancellation 是 cooperative boundary；Renderer 會終止 Windows Job Object／POSIX process group，而 queued cancellation 不會啟動工作。
- Hardened Compose profile 強制 API source-only，將 CAD runner 移入無外網 worker；API／worker 使用唯讀 root、capability drop、`no-new-privileges` 與 CPU／memory／PID 限制。

## 部署要求

公開部署至少應增加：

- TLS 與反向代理。
- 強制 API Token 或真正的使用者認證。
- 每 IP／使用者速率限制、工作數與輸出大小配額。
- 使用 hardened worker profile，並由部署平台核准、鎖定 seccomp／AppArmor 或等效政策；高風險環境應提升為每工作獨立 sandbox。
- 工作目錄生命週期與自動刪除政策。
- 依租戶隔離資料與下載授權。
- 依賴與容器漏洞掃描、鎖版及 SBOM。

## 製造圖完整性邊界

- `ManufacturingDrawingSpec` 綁定 canonical CadDocument SHA-256；原始 drawing spec、draft PDF、目前 spec/PDF 與每版 review snapshot 讀取時重新驗證。
- 狀態轉換以 `expected_version`、per-job lock 及跨程序 create-once claim 序列化。claim 有 300 秒 lease；只有沒有 commit snapshot 的過期版本才會清除該版本限定孤兒檔後重試。
- ZIP 只包含 manifest 及通過完整性驗證的製造歷程，不掃描工作目錄，不包含 rogue、暫存或 claim 檔。
- Reviewer 名稱是自我聲明 metadata；產品不將它表示成身分證明、法律電子簽章或 PKI 簽章。

## 尚未完成

- 每工作獨立 sandbox、客製 seccomp／AppArmor policy 與租戶級 cgroup 配額；目前為服務級 hardened container。
- 多租戶帳號、細粒度授權、審計資料庫與速率限制。
- CAD kernel 惡意／退化 BREP fuzzing。
- 多節點外部 queue、租戶公平排程、dead-letter queue 與 retention／garbage collection。
- 完整 ASME Y14.5／ISO GPS、PKI-backed 簽章、供應商身分與正式審計資料庫。
- FreeCAD host runtime 尚未實際執行；Fusion 360／SOLIDWORKS host adapters 也尚未在授權桌面環境做端到端驗收。

## 安全回報

請勿在公開 issue 貼出 API Key、客戶 CAD 或可利用的主機資訊。安全回報流程見根目錄 `SECURITY.md`。
