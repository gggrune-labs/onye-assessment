/**
 * Clinical Data Reconciliation Engine - Frontend Logic
 *
 * Handles API communication, result rendering, and the approve/reject workflow.
 * Uses vanilla JS to keep dependencies minimal per the assessment scope.
 */

const API_BASE = window.location.origin;
const reviewLog = [];

// ── Tab switching ───────────────────────────────────────────────────

function switchTab(tab) {
    document.getElementById('panel-reconciliation').classList.toggle('hidden', tab !== 'reconciliation');
    document.getElementById('panel-quality').classList.toggle('hidden', tab !== 'quality');

    document.getElementById('tab-reconciliation').classList.toggle('tab-active', tab === 'reconciliation');
    document.getElementById('tab-quality').classList.toggle('tab-active', tab === 'quality');

    document.getElementById('tab-reconciliation').classList.toggle('text-gray-500', tab !== 'reconciliation');
    document.getElementById('tab-quality').classList.toggle('text-gray-500', tab !== 'quality');
}

// ── API communication ───────────────────────────────────────────────

function getApiKey() {
    return document.getElementById('apiKeyInput').value.trim();
}

function setStatus(state, message) {
    const indicator = document.getElementById('statusIndicator');
    const colors = { ready: 'bg-gray-300', loading: 'bg-yellow-400', success: 'bg-green-500', error: 'bg-red-500' };
    indicator.innerHTML = `
        <span class="w-2 h-2 rounded-full ${colors[state] || colors.ready} ${state === 'loading' ? 'animate-pulse' : ''}"></span>
        <span class="text-gray-500">${message}</span>
    `;
}

async function apiCall(endpoint, body) {
    const apiKey = getApiKey();
    if (!apiKey) {
        throw new Error('Please enter an API key');
    }

    setStatus('loading', 'Processing...');

    const response = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-API-Key': apiKey,
        },
        body: JSON.stringify(body),
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server error (${response.status})`);
    }

    setStatus('success', 'Complete');
    return response.json();
}

// ── Reconciliation ──────────────────────────────────────────────────

async function runReconciliation() {
    const btn = document.getElementById('reconcileBtn');
    const resultDiv = document.getElementById('reconciliationResult');

    try {
        btn.disabled = true;
        btn.textContent = 'Analyzing...';

        const patientContext = JSON.parse(document.getElementById('patientContextInput').value);
        const sources = JSON.parse(document.getElementById('sourcesInput').value);

        const result = await apiCall('/api/reconcile/medication', {
            patient_context: patientContext,
            sources: sources,
        });

        renderReconciliationResult(result, resultDiv);

    } catch (err) {
        setStatus('error', 'Failed');
        resultDiv.innerHTML = renderError(err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = `
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
            </svg>
            Reconcile Medications
        `;
    }
}

function renderReconciliationResult(result, container) {
    const confidenceColor = result.confidence_score >= 0.8 ? 'text-green-600' : result.confidence_score >= 0.6 ? 'text-yellow-600' : 'text-red-600';
    const confidenceBg = result.confidence_score >= 0.8 ? 'bg-green-50 border-green-200' : result.confidence_score >= 0.6 ? 'bg-yellow-50 border-yellow-200' : 'bg-red-50 border-red-200';
    const safetyColor = result.clinical_safety_check === 'PASSED' ? 'bg-green-100 text-green-800' : result.clinical_safety_check === 'WARNING' ? 'bg-yellow-100 text-yellow-800' : 'bg-red-100 text-red-800';

    const weightsHtml = result.source_weights ? Object.entries(result.source_weights).map(([src, w]) => {
        const pct = Math.round(w * 100);
        return `
            <div class="flex items-center justify-between text-xs">
                <span class="text-gray-600 truncate mr-2">${escapeHtml(src)}</span>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <div class="w-24 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                        <div class="h-full bg-clinical-500 rounded-full" style="width: ${pct}%"></div>
                    </div>
                    <span class="text-gray-500 w-8 text-right">${pct}%</span>
                </div>
            </div>`;
    }).join('') : '';

    const actionsHtml = (result.recommended_actions || []).map(a =>
        `<li class="text-sm text-gray-700 flex items-start gap-2">
            <span class="text-clinical-500 mt-0.5 flex-shrink-0">&#8227;</span>
            <span>${escapeHtml(a)}</span>
        </li>`
    ).join('');

    container.innerHTML = `
        <div class="fade-in space-y-5">
            <!-- Reconciled medication -->
            <div class="${confidenceBg} border rounded-lg p-4">
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Reconciled Medication</p>
                <p class="text-lg font-bold text-gray-900">${escapeHtml(result.reconciled_medication)}</p>
                <div class="flex items-center gap-4 mt-2">
                    <span class="text-sm ${confidenceColor} font-semibold">
                        ${Math.round(result.confidence_score * 100)}% confidence
                    </span>
                    <span class="text-xs px-2 py-0.5 rounded-full font-medium ${safetyColor}">
                        Safety: ${result.clinical_safety_check}
                    </span>
                </div>
            </div>

            <!-- Reasoning -->
            <div>
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Clinical Reasoning</p>
                <p class="text-sm text-gray-700 leading-relaxed">${escapeHtml(result.reasoning)}</p>
            </div>

            <!-- Source weights -->
            ${weightsHtml ? `
            <div>
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Evidence Weights</p>
                <div class="space-y-1.5">${weightsHtml}</div>
            </div>` : ''}

            <!-- Recommended actions -->
            ${actionsHtml ? `
            <div>
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Recommended Actions</p>
                <ul class="space-y-1.5">${actionsHtml}</ul>
            </div>` : ''}

            <!-- Approve / Reject -->
            <div class="flex gap-3 pt-2 border-t border-gray-100">
                <button onclick="handleReview('${result.reconciliation_id}', 'approved', '${escapeAttr(result.reconciled_medication)}')"
                        class="flex-1 bg-green-50 hover:bg-green-100 text-green-700 font-medium py-2 px-4 rounded-lg border border-green-200 text-sm transition-colors">
                    &#10003; Approve
                </button>
                <button onclick="handleReview('${result.reconciliation_id}', 'rejected', '${escapeAttr(result.reconciled_medication)}')"
                        class="flex-1 bg-red-50 hover:bg-red-100 text-red-700 font-medium py-2 px-4 rounded-lg border border-red-200 text-sm transition-colors">
                    &#10005; Reject
                </button>
            </div>

            <!-- Metadata -->
            <p class="text-xs text-gray-400">ID: ${result.reconciliation_id}</p>
        </div>
    `;
}

function handleReview(id, decision, medication) {
    const entry = {
        reconciliation_id: id,
        decision: decision,
        medication: medication,
        timestamp: new Date().toISOString(),
    };
    reviewLog.push(entry);

    const logSection = document.getElementById('actionsLog');
    const logContent = document.getElementById('actionsLogContent');
    logSection.classList.remove('hidden');

    const colorClass = decision === 'approved'
        ? 'bg-green-50 border-green-200 text-green-800'
        : 'bg-red-50 border-red-200 text-red-800';

    const entryHtml = `
        <div class="fade-in flex items-center justify-between p-3 rounded-lg border ${colorClass} text-sm">
            <div>
                <span class="font-medium">${decision === 'approved' ? '&#10003; Approved' : '&#10005; Rejected'}:</span>
                <span class="ml-1">${escapeHtml(medication)}</span>
            </div>
            <span class="text-xs opacity-70">${new Date().toLocaleTimeString()}</span>
        </div>
    `;
    logContent.insertAdjacentHTML('afterbegin', entryHtml);
}

// ── Data Quality ────────────────────────────────────────────────────

async function runQualityCheck() {
    const btn = document.getElementById('qualityBtn');
    const resultDiv = document.getElementById('qualityResult');

    try {
        btn.disabled = true;
        btn.textContent = 'Validating...';

        const record = JSON.parse(document.getElementById('qualityInput').value);
        const result = await apiCall('/api/validate/data-quality', record);

        renderQualityResult(result, resultDiv);

    } catch (err) {
        setStatus('error', 'Failed');
        resultDiv.innerHTML = renderError(err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = `
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
            Validate Data Quality
        `;
    }
}

function renderQualityResult(result, container) {
    const overallColor = result.overall_score >= 75 ? 'text-green-600' : result.overall_score >= 50 ? 'text-yellow-600' : 'text-red-600';

    const dimensions = [
        { label: 'Completeness', score: result.breakdown.completeness },
        { label: 'Accuracy', score: result.breakdown.accuracy },
        { label: 'Timeliness', score: result.breakdown.timeliness },
        { label: 'Clinical Plausibility', score: result.breakdown.clinical_plausibility },
    ];

    const barsHtml = dimensions.map(d => {
        const barColor = d.score >= 75 ? 'bg-green-500' : d.score >= 50 ? 'bg-yellow-500' : 'bg-red-500';
        return `
            <div>
                <div class="flex justify-between text-xs mb-1">
                    <span class="text-gray-600 font-medium">${d.label}</span>
                    <span class="font-semibold ${d.score >= 75 ? 'text-green-600' : d.score >= 50 ? 'text-yellow-600' : 'text-red-600'}">${d.score}</span>
                </div>
                <div class="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div class="h-full ${barColor} rounded-full transition-all duration-700" style="width: ${d.score}%"></div>
                </div>
            </div>`;
    }).join('');

    const issuesHtml = (result.issues_detected || []).map(issue => {
        const severityStyle = {
            high: 'bg-red-100 text-red-800 border-red-200',
            medium: 'bg-yellow-100 text-yellow-800 border-yellow-200',
            low: 'bg-blue-100 text-blue-800 border-blue-200',
        }[issue.severity] || 'bg-gray-100 text-gray-800 border-gray-200';

        return `
            <div class="p-3 rounded-lg border ${severityStyle} text-sm">
                <div class="flex items-center justify-between mb-1">
                    <span class="font-mono text-xs font-medium">${escapeHtml(issue.field)}</span>
                    <span class="text-xs font-semibold uppercase">${issue.severity}</span>
                </div>
                <p class="text-sm opacity-90">${escapeHtml(issue.issue)}</p>
            </div>`;
    }).join('');

    container.innerHTML = `
        <div class="fade-in space-y-5">
            <!-- Overall Score -->
            <div class="text-center py-4">
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Overall Quality Score</p>
                <div class="relative inline-flex items-center justify-center">
                    <svg class="w-28 h-28 transform -rotate-90" viewBox="0 0 100 100">
                        <circle cx="50" cy="50" r="42" fill="none" stroke="#e5e7eb" stroke-width="8"/>
                        <circle cx="50" cy="50" r="42" fill="none"
                                stroke="${result.overall_score >= 75 ? '#22c55e' : result.overall_score >= 50 ? '#eab308' : '#ef4444'}"
                                stroke-width="8" stroke-linecap="round"
                                stroke-dasharray="${2 * Math.PI * 42}"
                                stroke-dashoffset="${2 * Math.PI * 42 * (1 - result.overall_score / 100)}"
                                class="score-ring"/>
                    </svg>
                    <span class="absolute text-2xl font-bold ${overallColor}">${result.overall_score}</span>
                </div>
            </div>

            <!-- Dimension Bars -->
            <div class="space-y-3">${barsHtml}</div>

            <!-- Issues -->
            ${issuesHtml ? `
            <div>
                <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Issues Detected (${result.issues_detected.length})</p>
                <div class="space-y-2">${issuesHtml}</div>
            </div>` : '<p class="text-sm text-green-600 font-medium">No issues detected.</p>'}

            <!-- Metadata -->
            <p class="text-xs text-gray-400">ID: ${result.validation_id}</p>
        </div>
    `;
}

// ── Sample data loaders ─────────────────────────────────────────────

function loadSampleReconciliation() {
    document.getElementById('patientContextInput').value = JSON.stringify({
        "age": 67,
        "conditions": ["Type 2 Diabetes", "Hypertension", "Chronic Kidney Disease Stage 3"],
        "recent_labs": {"eGFR": 45, "hba1c": 7.2, "creatinine": 1.8}
    }, null, 2);

    document.getElementById('sourcesInput').value = JSON.stringify([
        {
            "system": "Hospital EHR",
            "medication": "Metformin 1000mg twice daily",
            "last_updated": "2024-10-15",
            "source_reliability": "high"
        },
        {
            "system": "Primary Care",
            "medication": "Metformin 500mg twice daily",
            "last_updated": "2025-01-20",
            "source_reliability": "high"
        },
        {
            "system": "Pharmacy",
            "medication": "Metformin 1000mg daily",
            "last_filled": "2025-01-25",
            "source_reliability": "medium"
        }
    ], null, 2);
}

function loadSampleQuality() {
    document.getElementById('qualityInput').value = JSON.stringify({
        "demographics": {"name": "John Doe", "dob": "1955-03-15", "gender": "M"},
        "medications": ["Metformin 500mg", "Lisinopril 10mg"],
        "allergies": [],
        "conditions": ["Type 2 Diabetes"],
        "vital_signs": {"blood_pressure": "340/180", "heart_rate": 72},
        "last_updated": "2024-06-15"
    }, null, 2);
}

// ── Utilities ───────────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    return (str || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function renderError(message) {
    return `
        <div class="fade-in bg-red-50 border border-red-200 rounded-lg p-4">
            <p class="text-sm font-medium text-red-800">Error</p>
            <p class="text-sm text-red-700 mt-1">${escapeHtml(message)}</p>
        </div>
    `;
}
