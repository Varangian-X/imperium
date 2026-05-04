/* ── State ─────────────────────────────────────────────────────────────────── */
let currentMode = 'image_prompt';

const MODE_LABELS = {
    image_prompt: 'Image Prompt Fragment',
    lore_hooks:   'Lore & Narrative Hooks',
    audit:        'Lore Audit',
};

const SUBMIT_LABELS = {
    image_prompt: 'Generate Image Prompt',
    lore_hooks:   'Generate Lore Hooks',
    audit:        'Run Lore Audit',
};

const QUERY_PLACEHOLDERS = {
    image_prompt: 'Describe the subject — faction, character, location, scene…',
    lore_hooks:   'Enter a topic, event, or concept to generate narrative hooks for…',
    audit:        'Paste or type the new lore / data you want to audit against the knowledge base…',
};

/* ── DOM refs ──────────────────────────────────────────────────────────────── */
const apiStatus     = document.getElementById('api-status');
const modeBtns      = document.querySelectorAll('.mode-btn');
const contextInput  = document.getElementById('context-input');
const queryInput    = document.getElementById('query-input');
const queryLabel    = document.getElementById('query-label');
const submitBtn     = document.getElementById('submit-btn');
const submitLabel   = document.getElementById('submit-label');
const resultsEl     = document.getElementById('results');
const loadingEl     = document.getElementById('loading');
const errorBanner   = document.getElementById('error-banner');
const errorText     = document.getElementById('error-text');
const auditBanner   = document.getElementById('audit-banner');
const auditIcon     = document.getElementById('audit-icon');
const auditVerdict  = document.getElementById('audit-verdict-text');
const auditConf     = document.getElementById('audit-confidence');
const resultModeLabel = document.getElementById('result-mode-label');
const resultBody    = document.getElementById('result-body');
const copyBtn       = document.getElementById('copy-btn');
const sourcesSection = document.getElementById('sources-section');
const sourcesList   = document.getElementById('sources-list');

/* ── Health check ──────────────────────────────────────────────────────────── */
fetch('/api/health')
    .then(r => r.json())
    .then(d => {
        if (d && d.ok) {
            apiStatus.className = 'api-status ok';
            apiStatus.querySelector('.status-text').textContent = 'API connected';
        } else throw new Error();
    })
    .catch(() => {
        apiStatus.className = 'api-status err';
        apiStatus.querySelector('.status-text').textContent = 'API unreachable';
    });

/* ── Mode switching ────────────────────────────────────────────────────────── */
modeBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        currentMode = btn.dataset.mode;
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        submitLabel.textContent = SUBMIT_LABELS[currentMode];
        queryInput.placeholder  = QUERY_PLACEHOLDERS[currentMode];
        queryLabel.textContent  = currentMode === 'audit' ? 'New Data to Audit' : 'Query / Topic';
        hideResults();
    });
});

/* ── Submit ────────────────────────────────────────────────────────────────── */
submitBtn.addEventListener('click', runQuery);
queryInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runQuery();
});

async function runQuery() {
    const query   = queryInput.value.trim();
    const context = contextInput.value.trim();

    if (!query) {
        showError('Please enter a query.');
        return;
    }

    setLoading(true);
    hideResults();
    hideError();

    try {
        const resp = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: currentMode, query, context: context || null }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || err.error || `Server error ${resp.status}`);
        }

        const data = await resp.json();
        renderResult(data);
    } catch (err) {
        showError(err.message || 'An unexpected error occurred.');
    } finally {
        setLoading(false);
    }
}

/* ── Render ────────────────────────────────────────────────────────────────── */
function renderResult(data) {
    resultModeLabel.textContent = MODE_LABELS[data.mode] || data.mode;

    // Audit mode: parse JSON result and show banner
    if (data.mode === 'audit') {
        renderAudit(data);
    } else {
        auditBanner.classList.add('hidden');
        resultBody.textContent = data.result;
    }

    // Sources
    if (data.sources && data.sources.length > 0) {
        sourcesList.innerHTML = data.sources
            .map(s => `<li>${escHtml(s)}</li>`)
            .join('');
        sourcesSection.classList.remove('hidden');
    } else {
        sourcesSection.classList.add('hidden');
    }

    resultsEl.classList.remove('hidden');
    resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderAudit(data) {
    let parsed = null;
    try {
        const clean = data.result.replace(/```(?:json)?|```/g, '').trim();
        parsed = JSON.parse(clean);
    } catch (_) { /* raw fallback */ }

    if (parsed) {
        const isPass = parsed.verdict === 'PASS';
        auditBanner.className = `audit-banner ${isPass ? 'pass' : 'fail'}`;
        auditIcon.textContent = isPass ? '✓' : '⚠';
        auditVerdict.textContent = isPass ? 'PASS — No contradictions detected' : '⚠ CONTRADICTION DETECTED';
        auditConf.textContent = parsed.confidence ? `(confidence: ${parsed.confidence})` : '';
        auditBanner.classList.remove('hidden');

        // Build structured body
        const div = document.createElement('div');
        div.className = 'audit-detail';

        if (parsed.issues && parsed.issues.length > 0) {
            const ul = document.createElement('ul');
            ul.className = 'audit-issues';
            parsed.issues.forEach(issue => {
                const li = document.createElement('li');
                li.textContent = issue;
                ul.appendChild(li);
            });
            div.appendChild(ul);
        } else if (isPass) {
            const p = document.createElement('p');
            p.style.color = 'var(--pass)';
            p.textContent = 'The submitted data is consistent with the established lore.';
            div.appendChild(p);
        }

        if (parsed.notes) {
            const notes = document.createElement('p');
            notes.className = 'audit-notes';
            notes.textContent = parsed.notes;
            div.appendChild(notes);
        }

        resultBody.innerHTML = '';
        resultBody.appendChild(div);
    } else {
        // Fallback: raw text, show generic banner
        auditBanner.className = 'audit-banner fail';
        auditIcon.textContent = '⚠';
        auditVerdict.textContent = data.verdict === 'PASS' ? 'PASS' : 'Review Required';
        auditConf.textContent = '';
        auditBanner.classList.remove('hidden');
        resultBody.textContent = data.result;
    }
}

/* ── Copy ──────────────────────────────────────────────────────────────────── */
copyBtn.addEventListener('click', () => {
    const text = resultBody.innerText || resultBody.textContent;
    navigator.clipboard.writeText(text).then(() => {
        copyBtn.textContent = '✓ Copied';
        copyBtn.classList.add('copied');
        setTimeout(() => {
            copyBtn.textContent = '⎘ Copy';
            copyBtn.classList.remove('copied');
        }, 2000);
    });
});

/* ── Helpers ───────────────────────────────────────────────────────────────── */
function setLoading(on) {
    loadingEl.classList.toggle('hidden', !on);
    submitBtn.disabled = on;
}

function hideResults() {
    resultsEl.classList.add('hidden');
    auditBanner.classList.add('hidden');
    sourcesSection.classList.add('hidden');
}

function showError(msg) {
    errorText.textContent = msg;
    errorBanner.classList.remove('hidden');
}

function hideError() {
    errorBanner.classList.add('hidden');
}

function escHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
