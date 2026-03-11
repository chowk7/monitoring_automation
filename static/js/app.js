// ─── Shared Utilities ─────────────────────────────────────────────────────────

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ─── Global Indices ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    const indicesBtn        = document.getElementById("indicesBtn");
    const indicesLoading    = document.getElementById("indicesLoading");
    const indicesLoadingText= document.getElementById("indicesLoadingText");
    const indicesData       = document.getElementById("indicesData");
    const indicesNews       = document.getElementById("indicesNews");
    const indicesDate       = document.getElementById("indicesDate");
    const newsSummaryText   = document.getElementById("newsSummaryText");
    const usIndices         = document.getElementById("usIndices");
    const asiaIndices       = document.getElementById("asiaIndices");
    const euIndices         = document.getElementById("euIndices");

    const emailSection   = document.getElementById("emailSection");
    const emailInput     = document.getElementById("emailInput");
    const emailAddBtn    = document.getElementById("emailAddBtn");
    const emailSendBtn   = document.getElementById("emailSendBtn");
    const emailStatus    = document.getElementById("emailStatus");
    const recipientTags  = document.getElementById("recipientTags");

    // Default recipients from server env (DEFAULT_RECIPIENTS)
    let recipients = Array.isArray(window.DEFAULT_RECIPIENTS) ? [...window.DEFAULT_RECIPIENTS] : [];
    renderRecipientTags();

    const datePicker        = document.getElementById("indicesDatePicker");
    const REGION_CONTAINERS = { "미국": usIndices, "아시아": asiaIndices, "유럽": euIndices };

    // Default date picker to today (local browser date)
    if (datePicker) {
        const today = new Date();
        datePicker.value = today.toISOString().slice(0, 10);
        // Prevent future dates
        datePicker.max = today.toISOString().slice(0, 10);
    }

    // Cached data for email payload
    let _cachedIndicesData = null;
    let _cachedNewsSummary = "";
    let _cachedReportDate  = "";

    if (indicesBtn) {
        indicesBtn.addEventListener("click", fetchIndices);
    }

    function fetchIndices() {
        indicesBtn.disabled = true;
        indicesData.style.display = "none";
        indicesNews.style.display = "none";
        indicesLoading.style.display = "block";
        indicesLoadingText.textContent = "데이터 수집 중...";
        newsSummaryText.innerHTML = "";
        emailStatus.textContent = "";
        [usIndices, asiaIndices, euIndices].forEach(el => { el.innerHTML = ""; });

        const selectedDate = datePicker ? datePicker.value : "";
        const streamUrl = selectedDate
            ? `/api/indices/stream?date=${encodeURIComponent(selectedDate)}`
            : "/api/indices/stream";
        const es = new EventSource(streamUrl);

        es.onmessage = function(event) {
            try {
                const msg = JSON.parse(event.data);

                switch (msg.type) {
                    case "progress":
                        indicesLoadingText.textContent = msg.message;
                        break;

                    case "indices":
                        _cachedIndicesData = msg.data;
                        renderIndices(msg.data);
                        indicesLoading.style.display = "none";
                        indicesData.style.display = "block";
                        break;

                    case "news":
                        _cachedNewsSummary = msg.summary;
                        renderNews(msg.summary);
                        indicesNews.style.display = "block";
                        break;

                    case "done":
                        es.close();
                        indicesLoading.style.display = "none";
                        indicesBtn.disabled = false;
                        // 수신자가 있으면 자동 발송
                        if (recipients.length > 0) sendEmail();
                        break;
                }
            } catch (err) {
                console.error("Indices SSE parse error:", err);
            }
        };

        es.onerror = function() {
            es.close();
            indicesLoading.style.display = "none";
            indicesBtn.disabled = false;
            indicesLoadingText.textContent = "오류 발생. 다시 시도해주세요.";
            indicesLoading.style.display = "block";
        };
    }

    function renderIndices(data) {
        // Collect the representative local date per region (first valid index)
        const regionDates = {};

        for (const [region, indices] of Object.entries(data)) {
            const container = REGION_CONTAINERS[region];
            if (!container) continue;

            container.innerHTML = "";
            for (const idx of indices) {
                if (idx.error) {
                    const el = document.createElement("div");
                    el.className = "index-error";
                    el.textContent = `${escapeHtml(idx.name)}: 오류`;
                    container.appendChild(el);
                    continue;
                }

                if (idx.date && !(region in regionDates)) regionDates[region] = idx.date;

                const sign = idx.change_pct >= 0 ? "+" : "";
                const cls  = idx.change_pct > 0 ? "positive" : idx.change_pct < 0 ? "negative" : "neutral";
                const val  = idx.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

                const row = document.createElement("div");
                row.className = "index-row";
                row.innerHTML = `
                    <span class="index-name" title="${escapeHtml(idx.name)}">${escapeHtml(idx.name)}</span>
                    <span class="index-value">${val}</span>
                    <span class="index-change ${cls}">${sign}${idx.change_pct.toFixed(2)}%</span>
                `;
                container.appendChild(row);
            }
        }

        if (Object.keys(regionDates).length && indicesDate) {
            const label = Object.entries(regionDates)
                .map(([r, d]) => `${r} ${d}`)
                .join(" | ");
            indicesDate.textContent = `(현지 기준: ${label})`;
            _cachedReportDate = Object.values(regionDates).sort().at(-1); // latest for email subject
        }
    }

    function renderRecipientTags() {
        if (!recipientTags) return;
        recipientTags.innerHTML = "";
        recipients.forEach((email) => {
            const tag = document.createElement("span");
            tag.className = "recipient-tag";
            tag.innerHTML = `${email}<button class="recipient-tag-remove" title="제외">&times;</button>`;
            tag.querySelector("button").addEventListener("click", () => {
                const i = recipients.indexOf(email);
                if (i !== -1) recipients.splice(i, 1);
                renderRecipientTags();
            });
            recipientTags.appendChild(tag);
        });
    }

    function addRecipient() {
        const val = emailInput.value.trim();
        if (!val) return;
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) {
            setEmailStatus("올바른 이메일 형식이 아닙니다.", "error");
            return;
        }
        if (recipients.includes(val)) {
            setEmailStatus("이미 추가된 주소입니다.", "error");
            return;
        }
        recipients.push(val);
        emailInput.value = "";
        emailStatus.textContent = "";
        renderRecipientTags();
    }

    if (emailAddBtn) emailAddBtn.addEventListener("click", addRecipient);
    if (emailInput) {
        emailInput.addEventListener("keydown", e => {
            if (e.key === "Enter") { e.preventDefault(); addRecipient(); }
        });
    }
    if (emailSendBtn) emailSendBtn.addEventListener("click", sendEmail);

    async function sendEmail() {
        if (recipients.length === 0) {
            setEmailStatus("수신자를 추가해주세요.", "error");
            return;
        }
        if (!_cachedIndicesData) {
            setEmailStatus("먼저 지수 데이터를 조회하세요.", "error");
            return;
        }

        emailSendBtn.disabled = true;
        setEmailStatus("발송 중...", "sending");

        try {
            const resp = await fetch("/api/send-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    to_emails:    recipients,
                    indices_data: _cachedIndicesData,
                    news_summary: _cachedNewsSummary,
                    report_date:  _cachedReportDate,
                }),
            });
            const data = await resp.json();
            if (resp.ok) {
                setEmailStatus(data.message || "발송 완료!", "success");
            } else {
                setEmailStatus(data.error || "발송 실패", "error");
            }
        } catch (err) {
            setEmailStatus("서버 오류. 다시 시도해주세요.", "error");
        } finally {
            emailSendBtn.disabled = false;
        }
    }

    function setEmailStatus(msg, cls) {
        emailStatus.textContent = msg;
        emailStatus.className = `email-status ${cls}`;
        if (cls === "success") {
            setTimeout(() => { emailStatus.textContent = ""; }, 5000);
        }
    }

    function renderNews(summary) {
        // Convert basic markdown (## / ### heading, - list, plain text) to HTML
        // Strip surrounding code fences if Gemini wraps output in ```markdown ... ```
        const stripped = summary.replace(/^```[a-z]*\n?/i, "").replace(/\n?```$/i, "");
        const lines = stripped.split("\n");
        let html = "";
        let inUl = false;
        let sectionCount = 0;

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) {
                if (inUl) { html += "</ul>"; inUl = false; }
                continue;
            }
            if (trimmed.startsWith("## ") || trimmed.startsWith("### ")) {
                if (inUl) { html += "</ul>"; inUl = false; }
                const text = trimmed.startsWith("### ") ? trimmed.slice(4) : trimmed.slice(3);
                // Add a visual divider between sections (not before the first one)
                if (sectionCount > 0) {
                    html += `<hr class="news-section-divider">`;
                }
                html += `<h2>${escapeHtml(text)}</h2>`;
                sectionCount++;
            } else if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
                if (!inUl) { html += "<ul>"; inUl = true; }
                html += `<li>${escapeHtml(trimmed.slice(2))}</li>`;
            } else if (/^\d+\.\s/.test(trimmed)) {
                if (inUl) { html += "</ul>"; inUl = false; }
                html += `<p>${escapeHtml(trimmed)}</p>`;
            } else {
                if (inUl) { html += "</ul>"; inUl = false; }
                html += `<p>${escapeHtml(trimmed)}</p>`;
            }
        }
        if (inUl) html += "</ul>";

        newsSummaryText.innerHTML = html;
    }
});

// ─── Stock Ticker Analyzer ────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    const tickerInput = document.getElementById("tickerInput");
    const addBtn = document.getElementById("addBtn");
    const clearAllBtn = document.getElementById("clearAllBtn");
    const tickerList = document.getElementById("tickerList");
    const tickerCount = document.getElementById("tickerCount");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const loadingSection = document.getElementById("loadingSection");
    const loadingText = document.getElementById("loadingText");
    const progressFill = document.getElementById("progressFill");
    const resultsSection = document.getElementById("resultsSection");
    const allStocksOverview = document.getElementById("allStocksOverview");
    const analysisResults = document.getElementById("analysisResults");
    const errorSection = document.getElementById("errorSection");
    const errorText = document.getElementById("errorText");

    // Load saved tickers on startup
    loadTickers();

    // ─── Event Listeners ─────────────────────────────────────────────────

    addBtn.addEventListener("click", addTicker);

    tickerInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") addTicker();
    });

    if (clearAllBtn) {
        clearAllBtn.addEventListener("click", clearAllTickers);
    }

    analyzeBtn.addEventListener("click", runAnalysis);

    // ─── Functions ───────────────────────────────────────────────────────

    async function loadTickers() {
        try {
            const resp = await fetch("/api/tickers");
            const data = await resp.json();
            renderTickers(data.tickers);
        } catch (err) {
            console.error("Failed to load tickers:", err);
        }
    }

    async function addTicker() {
        const raw = tickerInput.value.trim();
        if (!raw) return;

        // Support comma-separated, space-separated, or newline-separated input
        const tickers = raw.split(/[,\s\n]+/).map(t => t.trim()).filter(Boolean);

        if (tickers.length > 1) {
            // Bulk add
            try {
                const resp = await fetch("/api/tickers/bulk", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ tickers }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    renderTickers(data.tickers);
                    if (data.added_count > 0) {
                        showSuccess(`${data.added_count}개 종목 추가됨`);
                    }
                } else {
                    showError(data.error || "Failed to add tickers");
                }
            } catch (err) {
                showError("Server error while adding tickers");
            }
        } else if (tickers.length === 1) {
            // Single add
            try {
                const resp = await fetch("/api/tickers", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ticker: tickers[0] }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    renderTickers(data.tickers);
                } else {
                    showError(data.error || "Failed to add ticker");
                }
            } catch (err) {
                showError("Server error while adding ticker");
            }
        }

        tickerInput.value = "";
        tickerInput.focus();
    }

    async function removeTicker(ticker) {
        try {
            const resp = await fetch(`/api/tickers/${ticker}`, { method: "DELETE" });
            const data = await resp.json();
            renderTickers(data.tickers);
        } catch (err) {
            showError("Failed to remove ticker");
        }
    }

    async function clearAllTickers() {
        if (!confirm("모든 종목을 삭제하시겠습니까?")) return;

        try {
            const resp = await fetch("/api/tickers/clear", { method: "DELETE" });
            const data = await resp.json();
            renderTickers(data.tickers);
            showSuccess("모든 종목이 삭제되었습니다.");
        } catch (err) {
            showError("Failed to clear tickers");
        }
    }

    function renderTickers(tickers) {
        tickerCount.textContent = tickers.length;
        analyzeBtn.disabled = tickers.length === 0;

        if (tickers.length === 0) {
            tickerList.innerHTML = '<p class="empty-message">등록된 Ticker가 없습니다. 위에서 추가해주세요.</p>';
            return;
        }

        tickerList.innerHTML = tickers.map(t => `
            <span class="ticker-tag">
                ${escapeHtml(t)}
                <button class="remove-btn" data-ticker="${escapeHtml(t)}" title="Remove">&times;</button>
            </span>
        `).join("");

        // Bind remove buttons
        tickerList.querySelectorAll(".remove-btn").forEach(btn => {
            btn.addEventListener("click", () => removeTicker(btn.dataset.ticker));
        });
    }

    function runAnalysis() {
        hideError();
        resultsSection.style.display = "none";
        loadingSection.style.display = "block";
        analyzeBtn.disabled = true;

        // Reset results area
        allStocksOverview.innerHTML = "";
        analysisResults.innerHTML = "";

        // Accumulated data for streaming
        let allStocksData = {};
        let allResults = [];

        // Use SSE for streaming
        const eventSource = new EventSource("/api/analyze/stream");

        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case "progress":
                        loadingText.textContent = data.message;
                        // Update progress bar based on message content
                        if (data.message.includes("주가 수집")) {
                            progressFill.style.width = "30%";
                        } else if (data.message.includes("분석 시작")) {
                            progressFill.style.width = "50%";
                        } else if (data.message.includes("분석 중") || data.message.includes("Gemini")) {
                            const match = data.message.match(/\((\d+)\/(\d+)\)/);
                            if (match) {
                                const current = parseInt(match[1]);
                                const total = parseInt(match[2]);
                                const pct = 50 + (current / total) * 45;
                                progressFill.style.width = pct + "%";
                            }
                        }
                        break;

                    case "stocks":
                        // Received all_stocks data - render overview
                        allStocksData = data.all_stocks;
                        resultsSection.style.display = "block";
                        renderAllStocksOverview(allStocksData);
                        break;

                    case "results":
                        // Received batch of analysis results - append to display
                        allResults = allResults.concat(data.results);
                        appendAnalysisResults(data.results);
                        break;

                    case "done":
                        eventSource.close();
                        progressFill.style.width = "100%";
                        loadingText.textContent = "완료!";

                        setTimeout(() => {
                            loadingSection.style.display = "none";
                            progressFill.style.width = "0%";
                            analyzeBtn.disabled = false;

                            // Show message if no filtered results
                            if (allResults.length === 0 && data.message) {
                                analysisResults.innerHTML = `
                                    <div class="no-filter-message">
                                        <p>${escapeHtml(data.message)}</p>
                                        <p style="margin-top:8px"><strong>Tip:</strong> 변동성이 큰 종목을 추가해보세요.</p>
                                    </div>
                                `;
                            }
                        }, 500);
                        break;
                }
            } catch (err) {
                console.error("Error parsing SSE data:", err);
            }
        };

        eventSource.onerror = function(err) {
            console.error("SSE error:", err);
            eventSource.close();
            loadingSection.style.display = "none";
            analyzeBtn.disabled = false;
            showError("Server error during analysis. Please try again.");
        };
    }

    function renderAllStocksOverview(allStocks) {
        if (!allStocks || Object.keys(allStocks).length === 0) {
            allStocksOverview.innerHTML = "";
            return;
        }

        let overviewHtml = '<h3>전체 종목 변동률</h3><div class="overview-grid">';
        const entries = Object.entries(allStocks).sort(
            (a, b) => Math.abs(b[1].change_pct) - Math.abs(a[1].change_pct)
        );

        for (const [ticker, info] of entries) {
            if (info.error && info.change_pct === 0) {
                overviewHtml += `<div class="overview-item neutral" title="${escapeHtml(info.error)}">${escapeHtml(ticker)} (오류: ${escapeHtml(info.error)})</div>`;
                continue;
            }
            const cls = info.change_pct > 0 ? "positive" : info.change_pct < 0 ? "negative" : "neutral";
            const filtered = Math.abs(info.change_pct) >= 5 ? " filtered" : "";
            const sign = info.change_pct > 0 ? "+" : "";
            const displayName = info.name || ticker;
            overviewHtml += `<div class="overview-item ${cls}${filtered}">${escapeHtml(displayName)} ${sign}${info.change_pct.toFixed(1)}%</div>`;
        }
        overviewHtml += "</div>";
        allStocksOverview.innerHTML = overviewHtml;
    }

    function appendAnalysisResults(results) {
        if (!results || results.length === 0) return;

        let html = "";
        for (const result of results) {
            const cls = result.change_pct > 0 ? "positive" : "negative";
            const sign = result.change_pct > 0 ? "+" : "";
            const analysisHtml = formatAnalysis(result.analysis);

            let newsHtml = "";
            if (result.news && result.news.length > 0) {
                const items = result.news.map(h => `<li>${escapeHtml(h)}</li>`).join("");
                newsHtml = `<div class="result-news"><span class="result-news-label">📰 네이버 뉴스</span><ul>${items}</ul></div>`;
            }

            html += `
                <div class="result-card ${cls}">
                    <div class="result-header">
                        <span class="result-name">${escapeHtml(result.name)}</span>
                        <span class="result-change ${cls}">${sign}${result.change_pct.toFixed(1)}%</span>
                        <span class="result-meta">Gemini 2.5 Pro 분석</span>
                    </div>
                    <div class="result-analysis">${analysisHtml}</div>
                    ${newsHtml}
                </div>
            `;
        }
        analysisResults.innerHTML += html;

        // Scroll to show new results
        const lastCard = analysisResults.querySelector(".result-card:last-child");
        if (lastCard) {
            lastCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }

    function formatAnalysis(text) {
        if (!text) return "<p>분석 결과 없음</p>";
        // Split by double newlines or single newlines for paragraphs
        const paragraphs = text.split(/\n{2,}/).filter(Boolean);
        if (paragraphs.length <= 1) {
            // Try single newlines
            const lines = text.split(/\n/).filter(Boolean);
            return lines.map(l => `<p>${escapeHtml(l)}</p>`).join("");
        }
        return paragraphs.map(p => `<p>${escapeHtml(p.trim())}</p>`).join("");
    }

    function showError(msg) {
        errorSection.style.display = "block";
        errorText.textContent = msg;
        errorSection.className = "error-section";
        setTimeout(() => { errorSection.style.display = "none"; }, 5000);
    }

    function showSuccess(msg) {
        errorSection.style.display = "block";
        errorText.textContent = msg;
        errorSection.className = "error-section success";
        setTimeout(() => { errorSection.style.display = "none"; }, 3000);
    }

    function hideError() {
        errorSection.style.display = "none";
    }

});
