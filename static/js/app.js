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
                        } else if (data.message.includes("분석 중")) {
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

            // Build article list HTML
            let articlesHtml = "";
            if (result.articles && result.articles.length > 0) {
                articlesHtml = '<div class="result-articles"><h4>참고 기사</h4><ul>';
                for (const article of result.articles) {
                    const dateStr = article.date ? `<span class="article-date">${escapeHtml(article.date)}</span>` : "";
                    articlesHtml += `<li>${dateStr}<a href="${escapeHtml(article.link)}" target="_blank" rel="noopener">${escapeHtml(article.title)}</a></li>`;
                }
                articlesHtml += "</ul></div>";
            }

            html += `
                <div class="result-card ${cls}">
                    <div class="result-header">
                        <span class="result-name">${escapeHtml(result.name)}</span>
                        <span class="result-change ${cls}">${sign}${result.change_pct.toFixed(1)}%</span>
                        <span class="result-meta">${result.article_count}건의 기사 분석</span>
                    </div>
                    <div class="result-analysis">${analysisHtml}</div>
                    ${articlesHtml}
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
