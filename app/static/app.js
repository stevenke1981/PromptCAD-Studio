const $ = (selector) => document.querySelector(selector);
const promptEl = $('#prompt');
const plannerEl = $('#planner');
const backendEl = $('#backend');
const backendNoteEl = $('#backend-note');
const tokenEl = $('#token');
const generateBtn = $('#generate');
const statusEl = $('#status');
const previewEl = $('#preview');
const previewEmpty = $('#preview-empty');
const downloadsEl = $('#downloads');
const warningsEl = $('#warnings');
const specEl = $('#spec');
const partName = $('#part-name');
const badge = $('#badge');
const regenerateBtn = $('#regenerate');
const analyzeImageBtn = $('#analyze-image');
const imageFileEl = $('#image-file');
const knownLengthEl = $('#known-length');
const imageThicknessEl = $('#image-thickness');
const featureTreePanel = $('#feature-tree-panel');
const featureTreeEl = $('#feature-tree');
const generateFeatureTreeBtn = $('#generate-feature-tree');
const featureTreeSummary = $('#feature-tree-summary');
const analyzeDxfBtn = $('#analyze-dxf');
const dxfFileEl = $('#dxf-file');
const dxfThicknessEl = $('#dxf-thickness');
const dxfUnitsEl = $('#dxf-units');
let activePreviewUrl = null;
let activeImageHash = null;
let activeImageAnalysis = null;
let activeDxfAnalysis = null;
let activeFeatureSource = null;
let analysisRequestVersion = 0;

function authHeaders(includeJson = true) {
  const out = {};
  if (includeJson) out['Content-Type'] = 'application/json';
  if (tokenEl.value.trim()) out.Authorization = `Bearer ${tokenEl.value.trim()}`;
  return out;
}

function formats() {
  return [...document.querySelectorAll('.checks input:checked:not(:disabled)')].map((el) => el.value);
}

let backendCapabilities = [];

function applyBackendCapability() {
  const backend = backendEl.value;
  const kernelFormats = new Set(['step', 'stl', 'dxf', 'svg']);
  const capability = backendCapabilities.find((item) => item.backend_id === backend);
  const serverFormats = new Set(capability?.server_render_formats || []);
  if (capability?.execution_kind === 'host_application') serverFormats.add('step');
  for (const input of document.querySelectorAll('.checks input')) {
    if (!kernelFormats.has(input.value)) continue;
    input.disabled = Boolean(
      capability && !serverFormats.has(input.value),
    );
  }
  if (backend === 'auto') {
    backendNoteEl.textContent = '自動選擇可用且相容的本機 CAD 核心。';
  } else if (!capability) {
    backendNoteEl.textContent = '尚未取得後端能力資訊。';
  } else if (capability.runtime_available) {
    backendNoteEl.textContent =
      `${capability.display_name} 可在伺服器執行；格式：${capability.server_render_formats.join(', ') || '來源'}`;
  } else if (capability.execution_kind === 'host_application') {
    backendNoteEl.textContent =
      `${capability.display_name} adapter 將連同伺服器產生的已驗證 STEP 打包；桌面 CAD 不會在伺服器啟動。`;
  } else {
    backendNoteEl.textContent =
      `${capability.display_name} 僅輸出受控來源／adapter；不會在伺服器啟動桌面 CAD。`;
  }
}

async function loadCapabilities() {
  try {
    const data = await api('/api/v1/capabilities');
    backendCapabilities = data.backends || [];
    applyBackendCapability();
  } catch (error) {
    backendNoteEl.textContent = `後端能力讀取失敗：${error.message}`;
  }
}

function setStatus(text, error = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('error', error);
}

function setBadge(text, kind) {
  badge.textContent = text;
  badge.className = `badge ${kind}`;
}

function deactivateFeatureTree() {
  activeImageHash = null;
  activeImageAnalysis = null;
  activeDxfAnalysis = null;
  activeFeatureSource = null;
  featureTreeEl.value = '[]';
  featureTreePanel.hidden = true;
  featureTreeSummary.textContent = '特徵樹';
  generateFeatureTreeBtn.disabled = true;
}

function clearFeatureTreeState() {
  analysisRequestVersion += 1;
  analyzeImageBtn.disabled = false;
  analyzeDxfBtn.disabled = false;
  deactivateFeatureTree();
  if (activePreviewUrl) {
    URL.revokeObjectURL(activePreviewUrl);
    activePreviewUrl = null;
  }
  previewEl.removeAttribute('src');
  specEl.value = '{}';
  partName.textContent = '尚未生成';
  downloadsEl.innerHTML = '';
  warningsEl.innerHTML = '';
  previewEl.hidden = true;
  previewEmpty.hidden = false;
  previewEmpty.textContent = '生成後會顯示工程預覽';
}

async function api(url, options = {}) {
  const includeJson = !(options.body instanceof FormData);
  const response = await fetch(url, {
    ...options,
    headers: {...authHeaders(includeJson), ...(options.headers || {})},
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      // Non-JSON error bodies fall back to the HTTP status.
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.json();
}

async function fetchBlob(url) {
  const response = await fetch(url, {headers: authHeaders(false)});
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.blob();
}

async function showPreview(artifact) {
  if (!artifact) {
    previewEl.hidden = true;
    previewEmpty.hidden = false;
    return;
  }

  try {
    const blob = await fetchBlob(artifact.url);
    if (activePreviewUrl) URL.revokeObjectURL(activePreviewUrl);
    activePreviewUrl = URL.createObjectURL(blob);
    previewEl.src = activePreviewUrl;
    previewEl.hidden = false;
    previewEmpty.hidden = true;
  } catch (error) {
    previewEl.hidden = true;
    previewEmpty.hidden = false;
    previewEmpty.textContent = `預覽載入失敗：${error.message}`;
  }
}

async function downloadArtifact(url, filename) {
  const blob = await fetchBlob(url);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function addDownload(url, label, filename) {
  const link = document.createElement('a');
  link.href = url;
  link.textContent = label;
  link.download = filename;
  link.addEventListener('click', async (event) => {
    if (!tokenEl.value.trim()) return;
    event.preventDefault();
    try {
      setStatus(`正在下載 ${filename}…`);
      await downloadArtifact(url, filename);
      setStatus(`已下載 ${filename}`);
    } catch (error) {
      setStatus(`下載失敗：${error.message}`, true);
    }
  });
  downloadsEl.appendChild(link);
}

function showImageAnalysis(data) {
  partName.textContent = data.proposed_spec?.name || '影像特徵候選';
  specEl.value = data.proposed_spec ? JSON.stringify(data.proposed_spec, null, 2) : '{}';
  featureTreeEl.value = JSON.stringify(data.feature_tree, null, 2);
  activeImageHash = data.image_sha256;
  activeImageAnalysis = data;
  activeDxfAnalysis = null;
  activeFeatureSource = 'image';
  featureTreePanel.hidden = false;
  featureTreeSummary.textContent = '影像特徵樹';
  setBadge(data.convertible ? 'REVIEW' : 'BLOCKED', data.convertible ? 'warn' : 'fail');

  warningsEl.innerHTML = '';
  const messages = [
    ...data.warnings,
    ...(data.validation?.issues || []).map((issue) => issue.message),
  ];
  for (const text of [...new Set(messages)]) {
    const item = document.createElement('div');
    item.className = 'warning';
    item.textContent = text;
    warningsEl.appendChild(item);
  }
  downloadsEl.innerHTML = '';

  if (data.preview_svg) {
    if (activePreviewUrl) URL.revokeObjectURL(activePreviewUrl);
    activePreviewUrl = URL.createObjectURL(
      new Blob([data.preview_svg], {type: 'image/svg+xml'}),
    );
    previewEl.src = activePreviewUrl;
    previewEl.hidden = false;
    previewEmpty.hidden = true;
  } else {
    previewEl.hidden = true;
    previewEmpty.hidden = false;
    previewEmpty.textContent = '目前只能顯示候選特徵，請調整圖片或校準資料。';
  }
  generateFeatureTreeBtn.disabled = !data.convertible;
}

function showDxfAnalysis(data) {
  partName.textContent = data.proposed_spec?.name || 'DXF 特徵候選';
  specEl.value = data.proposed_spec ? JSON.stringify(data.proposed_spec, null, 2) : '{}';
  featureTreeEl.value = JSON.stringify(data.feature_tree, null, 2);
  activeImageHash = null;
  activeImageAnalysis = null;
  activeDxfAnalysis = data;
  activeFeatureSource = 'dxf';
  featureTreePanel.hidden = false;
  featureTreeSummary.textContent = 'DXF 特徵樹';
  setBadge(data.convertible ? 'REVIEW' : 'BLOCKED', data.convertible ? 'warn' : 'fail');

  warningsEl.innerHTML = '';
  const messages = [
    ...data.warnings,
    ...(data.validation?.issues || []).map((issue) => issue.message),
  ];
  for (const text of [...new Set(messages)]) {
    const item = document.createElement('div');
    item.className = 'warning';
    item.textContent = text;
    warningsEl.appendChild(item);
  }
  downloadsEl.innerHTML = '';

  if (data.preview_svg) {
    if (activePreviewUrl) URL.revokeObjectURL(activePreviewUrl);
    activePreviewUrl = URL.createObjectURL(
      new Blob([data.preview_svg], {type: 'image/svg+xml'}),
    );
    previewEl.src = activePreviewUrl;
    previewEl.hidden = false;
    previewEmpty.hidden = true;
  } else {
    previewEl.hidden = true;
    previewEmpty.hidden = false;
    previewEmpty.textContent = '目前無法建立候選預覽，請檢查 DXF 幾何與單位。';
  }
  generateFeatureTreeBtn.disabled = !data.convertible;
}

function showManifest(data) {
  partName.textContent = data.spec.name;
  deactivateFeatureTree();
  specEl.value = JSON.stringify(data.spec, null, 2);
  setBadge(
    data.validation.review_required ? 'REVIEW' : data.status.toUpperCase(),
    data.status === 'failed' ? 'fail' : data.validation.review_required ? 'warn' : 'ok',
  );

  const previewArtifact =
    data.artifacts.find((item) => item.filename === 'model.svg') ||
    data.artifacts.find((item) => item.filename === 'preview.svg');
  void showPreview(previewArtifact);

  warningsEl.innerHTML = '';
  const messages = [
    ...data.warnings,
    ...data.spec.assumptions,
    ...data.spec.standards.map(
      (standard) => `標準來源：${standard.key} ${standard.revision} — ${standard.source_url}`,
    ),
    ...data.validation.issues
      .filter((issue) => issue.severity !== 'info')
      .map((issue) => issue.message),
  ];
  for (const text of [...new Set(messages)]) {
    const item = document.createElement('div');
    item.className = 'warning';
    item.textContent = text;
    warningsEl.appendChild(item);
  }

  downloadsEl.innerHTML = '';
  for (const artifact of data.artifacts) {
    addDownload(artifact.url, artifact.filename, artifact.filename);
  }
  addDownload(
    `/api/v1/jobs/${data.job_id}/bundle.zip`,
    '全部打包 ZIP',
    `promptcad-${data.job_id}.zip`,
  );
}

generateBtn.addEventListener('click', async () => {
  const prompt = promptEl.value.trim();
  if (prompt.length < 3) {
    setStatus('請先輸入零件描述。', true);
    return;
  }
  if (!formats().length) {
    setStatus('請至少選擇一種輸出格式。', true);
    return;
  }

  generateBtn.disabled = true;
  setStatus('正在解析、驗證並建立 CAD…');
  setBadge('RUNNING', 'neutral');
  try {
    const data = await api('/api/v1/generate', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        planner: plannerEl.value,
        backend: backendEl.value,
        formats: formats(),
        render: true,
      }),
    });
    showManifest(data);
    setStatus(`完成：${data.renderer_used} / ${data.status}`);
    await loadJobs();
  } catch (error) {
    setStatus(error.message, true);
    setBadge('FAILED', 'fail');
  } finally {
    generateBtn.disabled = false;
  }
});

analyzeImageBtn.addEventListener('click', async () => {
  const file = imageFileEl.files[0];
  const knownLength = Number(knownLengthEl.value);
  const thickness = Number(imageThicknessEl.value);
  if (!file) {
    setStatus('請先選擇 PNG 或 JPEG 圖片。', true);
    return;
  }
  if (!(knownLength > 0) || !(thickness > 0)) {
    setStatus('校準長度與厚度必須大於 0。', true);
    return;
  }

  clearFeatureTreeState();
  const requestVersion = analysisRequestVersion;
  const body = new FormData();
  body.append('image', file);
  body.append('known_length_mm', String(knownLength));
  body.append('thickness_mm', String(thickness));
  analyzeImageBtn.disabled = true;
  setStatus('正在安全解碼、校準並擷取輪廓與圓孔…');
  setBadge('ANALYZING', 'neutral');
  try {
    const data = await api('/api/v1/image-analysis', {method: 'POST', body});
    if (requestVersion !== analysisRequestVersion) return;
    showImageAnalysis(data);
    setStatus(
      data.convertible
        ? '擷取完成：請檢查 Feature Tree，確認後再輸出 CAD。'
        : '擷取完成，但幾何信心不足，已停止自動轉換。',
      !data.convertible,
    );
  } catch (error) {
    if (requestVersion !== analysisRequestVersion) return;
    clearFeatureTreeState();
    setStatus(`圖片分析失敗：${error.message}`, true);
    setBadge('FAILED', 'fail');
  } finally {
    if (requestVersion === analysisRequestVersion) analyzeImageBtn.disabled = false;
  }
});

imageFileEl.addEventListener('change', clearFeatureTreeState);

analyzeDxfBtn.addEventListener('click', async () => {
  const file = dxfFileEl.files[0];
  const thickness = Number(dxfThicknessEl.value);
  if (!file) {
    setStatus('請先選擇 DXF 檔案。', true);
    return;
  }
  if (!(thickness > 0)) {
    setStatus('零件厚度必須大於 0。', true);
    return;
  }

  clearFeatureTreeState();
  const requestVersion = analysisRequestVersion;
  const body = new FormData();
  body.append('dxf', file);
  body.append('thickness_mm', String(thickness));
  body.append('unit_override', dxfUnitsEl.value);
  analyzeDxfBtn.disabled = true;
  setStatus('正在安全讀取 DXF、解析輪廓並建立特徵樹…');
  setBadge('ANALYZING', 'neutral');
  try {
    const data = await api('/api/v1/dxf-analysis', {method: 'POST', body});
    if (requestVersion !== analysisRequestVersion) return;
    showDxfAnalysis(data);
    setStatus(
      data.convertible
        ? 'DXF 分析完成：請檢查特徵樹，確認後再輸出 CAD。'
        : 'DXF 分析完成，但幾何無法安全轉換，已停止輸出。',
      !data.convertible,
    );
  } catch (error) {
    if (requestVersion !== analysisRequestVersion) return;
    clearFeatureTreeState();
    setStatus(`DXF 分析失敗：${error.message}`, true);
    setBadge('FAILED', 'fail');
  } finally {
    if (requestVersion === analysisRequestVersion) analyzeDxfBtn.disabled = false;
  }
});

dxfFileEl.addEventListener('change', clearFeatureTreeState);

generateFeatureTreeBtn.addEventListener('click', async () => {
  if (!activeFeatureSource) {
    setStatus('請先分析圖片或 DXF。', true);
    return;
  }
  if (!formats().length) {
    setStatus('請至少選擇一種輸出格式。', true);
    return;
  }
  let featureTree;
  try {
    featureTree = JSON.parse(featureTreeEl.value);
  } catch (error) {
    setStatus(`Feature Tree JSON 格式錯誤：${error.message}`, true);
    return;
  }

  generateFeatureTreeBtn.disabled = true;
  setStatus('正在驗證特徵樹並輸出 CAD…');
  setBadge('RUNNING', 'neutral');
  try {
    const isDxf = activeFeatureSource === 'dxf';
    const data = await api(
      isDxf
        ? '/api/v1/generate-from-dxf-feature-tree'
        : '/api/v1/generate-from-image-feature-tree',
      {
      method: 'POST',
      body: JSON.stringify({
        analysis: isDxf ? activeDxfAnalysis : activeImageAnalysis,
        feature_tree: featureTree,
        formats: formats(),
        render: true,
        backend: backendEl.value,
      }),
      },
    );
    showManifest(data);
    setStatus(`完成：${data.renderer_used} / ${data.status}`);
    await loadJobs();
  } catch (error) {
    setStatus(`特徵樹輸出失敗：${error.message}`, true);
    setBadge('FAILED', 'fail');
  } finally {
    generateFeatureTreeBtn.disabled = false;
  }
});

regenerateBtn.addEventListener('click', async () => {
  if (!formats().length) {
    setStatus('請至少選擇一種輸出格式。', true);
    return;
  }

  let spec;
  try {
    spec = JSON.parse(specEl.value);
  } catch (error) {
    setStatus(`JSON 格式錯誤：${error.message}`, true);
    return;
  }

  regenerateBtn.disabled = true;
  setStatus('正在驗證修改後的 DSL 並重新建立 CAD…');
  setBadge('RUNNING', 'neutral');
  try {
    const data = await api('/api/v1/generate-from-spec', {
      method: 'POST',
      body: JSON.stringify({
        spec,
        formats: formats(),
        render: true,
        backend: backendEl.value,
      }),
    });
    showManifest(data);
    setStatus(`完成：${data.renderer_used} / ${data.status}`);
    await loadJobs();
  } catch (error) {
    setStatus(error.message, true);
    setBadge('FAILED', 'fail');
  } finally {
    regenerateBtn.disabled = false;
  }
});

document.querySelectorAll('[data-example]').forEach((button) =>
  button.addEventListener('click', () => {
    promptEl.value = button.dataset.example;
  }),
);

async function loadJobs() {
  try {
    const jobs = await api('/api/v1/jobs');
    const root = $('#jobs');
    root.innerHTML = '';
    if (!jobs.length) {
      root.textContent = '尚無工作。';
      return;
    }
    for (const job of jobs) {
      const node = document.createElement('div');
      node.className = 'job';
      node.innerHTML = `<strong>${escapeHtml(job.name)}</strong><small>${escapeHtml(job.prompt)}</small><small>${escapeHtml(job.status)} · ${escapeHtml(job.renderer_used)}</small>`;
      node.addEventListener('click', async () => {
        try {
          const manifest = await api(`/api/v1/jobs/${job.job_id}`);
          showManifest(manifest);
          window.scrollTo({top: 0, behavior: 'smooth'});
        } catch (error) {
          setStatus(error.message, true);
        }
      });
      root.appendChild(node);
    }
  } catch (error) {
    $('#jobs').textContent = error.message;
  }
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

$('#refresh').addEventListener('click', loadJobs);
backendEl.addEventListener('change', applyBackendCapability);
tokenEl.addEventListener('change', () => {
  void loadCapabilities();
  void loadJobs();
});
window.addEventListener('beforeunload', () => {
  if (activePreviewUrl) URL.revokeObjectURL(activePreviewUrl);
});
void loadJobs();
void loadCapabilities();
