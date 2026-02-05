document.addEventListener("DOMContentLoaded", () => {
    const tickerInput = document.getElementById("tickerInput");
    const addBtn = document.getElementById("addBtn");
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

        // Support comma-separated input
        const tickers = raw.split(",").map(t => t.trim()).filter(Boolean);

        for (const ticker of tickers) {
            try {
                const resp = await fetch("/api/tickers", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ticker }),
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

    async function runAnalysis() {
        hideError();
        resultsSection.style.display = "none";
        loadingSection.style.display = "block";
        analyzeBtn.disabled = true;

        // Animate progress bar
        const progressSteps = [
            { pct: 15, text: "주가 데이터를 수집하고 있습니다..." },
            { pct: 35, text: "변동률 5% 이상 종목을 필터링하고 있습니다..." },
            { pct: 55, text: "관련 뉴스 기사를 검색하고 있습니다..." },
            { pct: 75, text: "Gemini AI로 변동 원인을 분석하고 있습니다..." },
            { pct: 90, text: "분석 결과를 정리하고 있습니다..." },
        ];

        let stepIdx = 0;
        const progressInterval = setInterval(() => {
            if (stepIdx < progressSteps.length) {
                progressFill.style.width = progressSteps[stepIdx].pct + "%";
                loadingText.textContent = progressSteps[stepIdx].text;
                stepIdx++;
            }
        }, 3000);

        try {
            const resp = await fetch("/api/analyze", { method: "POST" });
            const data = await resp.json();

            clearInterval(progressInterval);
            progressFill.style.width = "100%";
            loadingText.textContent = "완료!";

            if (!resp.ok) {
                showError(data.error || "Analysis failed");
                loadingSection.style.display = "none";
                analyzeBtn.disabled = false;
                return;
            }

            setTimeout(() => {
                loadingSection.style.display = "none";
                progressFill.style.width = "0%";
                renderResults(data);
                analyzeBtn.disabled = false;
            }, 500);
        } catch (err) {
            clearInterval(progressInterval);
            loadingSection.style.display = "none";
            analyzeBtn.disabled = false;
            showError("Server error during analysis. Please try again.");
        }
    }

    function renderResults(data) {
        resultsSection.style.display = "block";

        // Render all stocks overview
        if (data.all_stocks && Object.keys(data.all_stocks).length > 0) {
            let overviewHtml = '<h3>전체 종목 변동률</h3><div class="overview-grid">';
            const entries = Object.entries(data.all_stocks).sort(
                (a, b) => Math.abs(b[1].change_pct) - Math.abs(a[1].change_pct)
            );

            for (const [ticker, info] of entries) {
                if (info.error && info.change_pct === 0) {
                    overviewHtml += `<div class="overview-item neutral">${escapeHtml(ticker)} (데이터 없음)</div>`;
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
        } else {
            allStocksOverview.innerHTML = "";
        }

        // Render analysis results
        if (data.results && data.results.length > 0) {
            let html = "";
            for (const result of data.results) {
                const cls = result.change_pct > 0 ? "positive" : "negative";
                const sign = result.change_pct > 0 ? "+" : "";
                const analysisHtml = formatAnalysis(result.analysis);

                html += `
                    <div class="result-card ${cls}">
                        <div class="result-header">
                            <span class="result-name">${escapeHtml(result.name)}</span>
                            <span class="result-change ${cls}">${sign}${result.change_pct.toFixed(1)}%</span>
                            <span class="result-meta">${result.article_count}건의 기사 분석</span>
                        </div>
                        <div class="result-analysis">${analysisHtml}</div>
                    </div>
                `;
            }
            analysisResults.innerHTML = html;
        } else {
            const msg = data.message || "변동률 +/- 5% 이상인 종목이 없습니다.";
            analysisResults.innerHTML = `
                <div class="no-filter-message">
                    <p>${escapeHtml(msg)}</p>
                    <p style="margin-top:8px"><strong>Tip:</strong> 변동성이 큰 종목을 추가해보세요.</p>
                </div>
            `;
        }

        // Scroll to results
        resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
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
        setTimeout(() => { errorSection.style.display = "none"; }, 5000);
    }

    function hideError() {
        errorSection.style.display = "none";
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }
});
