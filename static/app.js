/* Global JS for Cartoon-to-Slides web UI */

// ---- SSE progress connection ----

function connectSSE(projectId) {
  const source = new EventSource(`/api/projects/${projectId}/progress`);

  source.addEventListener('progress', (e) => {
    const data = JSON.parse(e.data);
    updateStepUI(data);
    if (data.status === 'finished') {
      source.close();
      setTimeout(() => location.reload(), 800);
    }
  });

  source.onerror = () => {
    source.close();
  };
}

function updateStepUI(data) {
  const stepEl = document.getElementById(`step-${data.step}`);
  if (!stepEl) return;

  // Remove old status classes
  stepEl.classList.remove('step-pending', 'step-running', 'step-done', 'step-error');

  const statusMap = {
    running: 'step-running',
    done: 'step-done',
    error: 'step-error',
  };
  stepEl.classList.add(statusMap[data.status] || 'step-pending');

  const iconEl = stepEl.querySelector('.step-icon');
  if (iconEl) {
    iconEl.classList.remove('spinning');
    if (data.status === 'done') {
      iconEl.innerHTML = '&#10003;';
    } else if (data.status === 'running') {
      iconEl.innerHTML = '&#9881;';
      iconEl.classList.add('spinning');
    } else if (data.status === 'error') {
      iconEl.innerHTML = '&#10007;';
    }
  }

  const msgEl = stepEl.querySelector('.step-message');
  if (msgEl) {
    msgEl.textContent = data.message || '';
  }
}

// ---- Pipeline actions ----

async function runPipeline(projectId) {
  const resp = await fetch(`/api/projects/${projectId}/run`, { method: 'POST' });
  if (resp.ok) {
    connectSSE(projectId);
    const btn = document.getElementById('btn-run-pipeline');
    if (btn) { btn.disabled = true; btn.textContent = 'Running...'; }
  } else {
    const err = await resp.json().catch(() => ({}));
    alert(err.detail || 'Failed to start pipeline');
  }
}

async function runFrom(projectId, step) {
  const resp = await fetch(`/api/projects/${projectId}/run-from/${step}`, { method: 'POST' });
  if (resp.ok) {
    connectSSE(projectId);
  } else {
    const err = await resp.json().catch(() => ({}));
    alert(err.detail || 'Failed to start pipeline');
  }
}

// ---- Delete project ----

async function deleteProject(projectId) {
  if (!confirm('Delete this project and all its files?')) return;
  const resp = await fetch(`/api/projects/${projectId}`, { method: 'DELETE' });
  if (resp.ok) {
    window.location.href = '/';
  } else {
    alert('Delete failed');
  }
}
