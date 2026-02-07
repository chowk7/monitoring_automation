document.addEventListener("DOMContentLoaded", () => {
    const tickerInput = document.getElementById("tickerInput");
    const categorySelect = document.getElementById("categorySelect");
    const addBtn = document.getElementById("addBtn");
    const clearAllBtn = document.getElementById("clearAllBtn");
    const loadDefaultsBtn = document.getElementById("loadDefaultsBtn");
    const tickerList = document.getElementById("tickerList");
    const tickerCount = document.getElementById("tickerCount");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const loadingSection = document.getElementById("loadingSection");
    const loadingText = document.getElementById("loadingText");
    const progressFill = document.getElementById("progressFill");
    const resultsSection = document.getElementById("resultsSection");
    const categoryStatsSection = document.getElementById("categoryStatsSection");
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

    if (loadDefaultsBtn) {
        loadDefaultsBtn.addEventListener("click", loadDefaultTickers);
    }

    analyzeBtn.addEventListener("click", runAnalysis);

    // ─── Functions ───────────────────────────────────────────────────────

    async function loadTickers() {
        try {
            const resp = await fetch("/api/tickers");
            const data = await resp.json();
            renderTickers(data.tickers);
            // Populate category select if available
            if (data.categories && categorySelect) {
                populateCategorySelect(data.categories);
            }
        } catch (err) {
            console.error("Failed to load tickers:", err);
        }
    }

    function populateCategorySelect(categories) {
        categorySelect.innerHTML = categories.map(cat =>
            `<option value="${escapeHtml(cat)}">${escapeHtml(cat)}</option>`
        ).join("");
    }

    async function loadDefaultTickers() {
        try {
            const resp = await fetch("/api/tickers/defaults", { method: "POST" });
            const data = await resp.json();
            if (resp.ok) {
                renderTickers(data.tickers);
                showSuccess(`${data.count}개 기본 종목 로드됨`);
            } else {
                showError(data.error || "Failed to load default tickers");
            }
        } catch (err) {
            showError("Server error while loading default tickers");
        }
    }

    async function addTicker() {
        const raw = tickerInput.value.trim();
        if (!raw) return;

        const category = categorySelect ? categorySelect.value : "기타";

        // Support comma-separated, space-separated, or newline-separated input
        const tickers = raw.split(/[,\s\n]+/).map(t => t.trim()).filter(Boolean);

        if (tickers.length > 1) {
            // Bulk add
            try {
                const resp = await fetch("/api/tickers/bulk", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ tickers, category }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    renderTickers(data.tickers);
                    if (data.added_count > 0) {
                        showSuccess(`${data.added_count}개 종목 추가됨 (${category})`);
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
                    body: JSON.stringify({ ticker: tickers[0], category }),
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
        // tickers is now array of {ticker, category}
        const tickerArray = Array.isArray(tickers) ? tickers : [];
        tickerCount.textContent = tickerArray.length;
        analyzeBtn.disabled = tickerArray.length === 0;

        if (tickerArray.length === 0) {
            tickerList.innerHTML = '<p class="empty-message">등록된 Ticker가 없습니다. 위에서 추가해주세요.</p>';
            return;
        }

        // Group by category
        const byCategory = {};
        for (const item of tickerArray) {
            const cat = item.category || "기타";
            if (!byCategory[cat]) byCategory[cat] = [];
            byCategory[cat].push(item.ticker);
        }

        let html = "";
        for (const [cat, tickerList] of Object.entries(byCategory)) {
            html += `<div class="ticker-category-group">`;
            html += `<div class="ticker-category-label">${escapeHtml(cat)}</div>`;
            html += `<div class="ticker-category-items">`;
            html += tickerList.map(t => `
                <span class="ticker-tag">
                    ${escapeHtml(t)}
                    <button class="remove-btn" data-ticker="${escapeHtml(t)}" title="Remove">&times;</button>
                </span>
            `).join("");
            html += `</div></div>`;
        }

        document.getElementById("tickerList").innerHTML = html;

        // Bind remove buttons
        document.getElementById("tickerList").querySelectorAll(".remove-btn").forEach(btn => {
            btn.addEventListener("click", () => removeTicker(btn.dataset.ticker));
        });
    }

    function runAnalysis() {
        hideError();
        resultsSection.style.display = "none";
        loadingSection.style.display = "block";
        analyzeBtn.disabled = true;

        // Reset results area
        if (categoryStatsSection) categoryStatsSection.innerHTML = "";
        allStocksOverview.innerHTML = "";
        analysisResults.innerHTML = "";

        // Accumulated data for streaming
        let allStocksData = {};
        let categoryStats = {};
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
                        // Received all_stocks data with category stats
                        allStocksData = data.all_stocks;
                        categoryStats = data.category_stats || {};
                        resultsSection.style.display = "block";
                        renderCategoryStats(categoryStats);
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

    function renderCategoryStats(stats) {
        if (!categoryStatsSection || !stats || Object.keys(stats).length === 0) return;

        // Sort by avg_pct descending
        const sortedCategories = Object.entries(stats).sort((a, b) => b[1].avg_pct - a[1].avg_pct);

        let html = '<h3>카테고리별 통계</h3><div class="category-stats-grid">';
        for (const [category, data] of sortedCategories) {
            const avgCls = data.avg_pct > 0 ? "positive" : data.avg_pct < 0 ? "negative" : "neutral";
            const sign = data.avg_pct > 0 ? "+" : "";
            html += `
                <div class="category-stat-card">
                    <div class="category-name">${escapeHtml(category)}</div>
                    <div class="category-avg ${avgCls}">${sign}${data.avg_pct.toFixed(2)}%</div>
                    <div class="category-counts">
                        <span class="up-count">▲ ${data.up}</span>
                        <span class="down-count">▼ ${data.down}</span>
                        <span class="total-count">총 ${data.count}</span>
                    </div>
                </div>
            `;
        }
        html += "</div>";
        categoryStatsSection.innerHTML = html;
    }

    function renderAllStocksOverview(allStocks) {
        if (!allStocks || Object.keys(allStocks).length === 0) {
            allStocksOverview.innerHTML = "";
            return;
        }

        // Group by category
        const byCategory = {};
        for (const [ticker, info] of Object.entries(allStocks)) {
            const cat = info.category || "기타";
            if (!byCategory[cat]) byCategory[cat] = [];
            byCategory[cat].push({ ticker, ...info });
        }

        let overviewHtml = '<h3>전체 종목 변동률</h3>';

        // Category order
        const categoryOrder = ["반도체", "네트워크", "바이오", "의료기기", "공조", "가전", "전장", "게임", "삼성", "기타"];

        for (const cat of categoryOrder) {
            if (!byCategory[cat]) continue;

            const stocks = byCategory[cat].sort((a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct));

            overviewHtml += `<div class="category-group"><h4>${escapeHtml(cat)}</h4><div class="overview-grid">`;

            for (const stock of stocks) {
                if (stock.error && stock.change_pct === 0) {
                    overviewHtml += `<div class="overview-item neutral" title="${escapeHtml(stock.error)}">${escapeHtml(stock.ticker)} (오류)</div>`;
                    continue;
                }
                const cls = stock.change_pct > 0 ? "positive" : stock.change_pct < 0 ? "negative" : "neutral";
                const filtered = Math.abs(stock.change_pct) >= 5 ? " filtered" : "";
                const sign = stock.change_pct > 0 ? "+" : "";
                const displayName = stock.name || stock.ticker;
                overviewHtml += `<div class="overview-item ${cls}${filtered}">${escapeHtml(displayName)} ${sign}${stock.change_pct.toFixed(1)}%</div>`;
            }

            overviewHtml += "</div></div>";
        }

        // Add any other categories not in the order
        for (const [cat, stocks] of Object.entries(byCategory)) {
            if (categoryOrder.includes(cat)) continue;

            const sortedStocks = stocks.sort((a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct));

            overviewHtml += `<div class="category-group"><h4>${escapeHtml(cat)}</h4><div class="overview-grid">`;

            for (const stock of sortedStocks) {
                if (stock.error && stock.change_pct === 0) {
                    overviewHtml += `<div class="overview-item neutral" title="${escapeHtml(stock.error)}">${escapeHtml(stock.ticker)} (오류)</div>`;
                    continue;
                }
                const cls = stock.change_pct > 0 ? "positive" : stock.change_pct < 0 ? "negative" : "neutral";
                const filtered = Math.abs(stock.change_pct) >= 5 ? " filtered" : "";
                const sign = stock.change_pct > 0 ? "+" : "";
                const displayName = stock.name || stock.ticker;
                overviewHtml += `<div class="overview-item ${cls}${filtered}">${escapeHtml(displayName)} ${sign}${stock.change_pct.toFixed(1)}%</div>`;
            }

            overviewHtml += "</div></div>";
        }

        allStocksOverview.innerHTML = overviewHtml;
    }

    function appendAnalysisResults(results) {
        if (!results || results.length === 0) return;

        let html = "";
        for (const result of results) {
            const cls = result.change_pct > 0 ? "positive" : "negative";
            const sign = result.change_pct > 0 ? "+" : "";
            const analysisHtml = formatAnalysis(result.analysis);
            const categoryLabel = result.category ? `<span class="result-category">${escapeHtml(result.category)}</span>` : "";

            // Build sources HTML
            let sourcesHtml = "";
            if (result.sources && result.sources.length > 0) {
                sourcesHtml = '<div class="result-sources"><h4>참고 소스</h4><ul>';
                for (const source of result.sources) {
                    const title = source.title || source.url || "링크";
                    sourcesHtml += `<li><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a></li>`;
                }
                sourcesHtml += "</ul></div>";
            }

            html += `
                <div class="result-card ${cls}">
                    <div class="result-header">
                        <span class="result-name">${escapeHtml(result.name)}</span>
                        <span class="result-change ${cls}">${sign}${result.change_pct.toFixed(1)}%</span>
                        ${categoryLabel}
                        <span class="result-meta">Gemini 2.5 Pro</span>
                    </div>
                    <div class="result-analysis">${analysisHtml}</div>
                    ${sourcesHtml}
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
        errorSection.className = "card error-section";
        setTimeout(() => { errorSection.style.display = "none"; }, 5000);
    }

    function showSuccess(msg) {
        errorSection.style.display = "block";
        errorText.textContent = msg;
        errorSection.className = "card error-section success";
        setTimeout(() => { errorSection.style.display = "none"; }, 3000);
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
