const $ = (selector) => document.querySelector(selector);
const promptEl = $('#prompt');
const plannerEl = $('#planner');
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
let activePreviewUrl = null;

function authHeaders(includeJson = true) {
  const out = {};
  if (includeJson) out['Content-Type'] = 'application/json';
  if (tokenEl.value.trim()) out.Authorization = `Bearer ${tokenEl.value.trim()}`;
  return out;
}

function formats() {
  return [...document.querySelectorAll('.checks input:checked')].map((el) => el.value);
}

function setStatus(text, error = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('error', error);
}

function setBadge(text, kind) {
  badge.textContent = text;
  badge.className = `badge ${kind}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {...authHeaders(true), ...(options.headers || {})},
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

function showManifest(data) {
  partName.textContent = data.spec.name;
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
      body: JSON.stringify({spec, formats: formats(), render: true}),
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
tokenEl.addEventListener('change', loadJobs);
window.addEventListener('beforeunload', () => {
  if (activePreviewUrl) URL.revokeObjectURL(activePreviewUrl);
});
void loadJobs();
