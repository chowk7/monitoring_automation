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

    // Settings elements
    const modelSelect = document.getElementById("modelSelect");
    const saveModelBtn = document.getElementById("saveModelBtn");
    const settingsToggle = document.getElementById("settingsToggle");
    const settingsBody = document.getElementById("settingsBody");

    // Prompt editor elements
    const promptToggle = document.getElementById("promptToggle");
    const promptBody = document.getElementById("promptBody");
    const promptWithArticles = document.getElementById("promptWithArticles");
    const promptWithoutArticles = document.getElementById("promptWithoutArticles");
    const savePromptsBtn = document.getElementById("savePromptsBtn");
    const resetPromptsBtn = document.getElementById("resetPromptsBtn");

    // Email elements
    const emailInput = document.getElementById("emailInput");
    const addEmailBtn = document.getElementById("addEmailBtn");
    const emailToggle = document.getElementById("emailToggle");
    const emailBody = document.getElementById("emailBody");
    const sendEmailBtn = document.getElementById("sendEmailBtn");

    // Date element
    const analysisDate = document.getElementById("analysisDate");

    // CSV upload elements
    const csvUploadInput = document.getElementById("csvUploadInput");
    const csvUploadBtn = document.getElementById("csvUploadBtn");
    const resetDefaultsBtn = document.getElementById("resetDefaultsBtn");

    // Store analysis results for email sending
    let currentResults = [];
    // Store default prompts for reset
    let defaultPrompts = { with_articles: "", without_articles: "" };

    // ─── Init ─────────────────────────────────────────────────────────────

    // Set default analysis date to today in KST
    if (analysisDate) {
        analysisDate.value = getKstDateString();
    }

    loadTickers();
    loadSettings();
    loadEmailRecipients();

    // ─── Event Listeners ─────────────────────────────────────────────────

    addBtn.addEventListener("click", addTicker);
    tickerInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") addTicker();
    });

    if (clearAllBtn) {
        clearAllBtn.addEventListener("click", clearAllTickers);
    }

    analyzeBtn.addEventListener("click", runAnalysis);

    // Settings toggle
    settingsToggle.addEventListener("click", () => {
        const collapsed = settingsBody.style.display === "none";
        settingsBody.style.display = collapsed ? "block" : "none";
        settingsToggle.textContent = collapsed ? "접기 ▲" : "펼치기 ▼";
    });

    // Save model
    saveModelBtn.addEventListener("click", saveModel);

    // Prompt toggle
    if (promptToggle) {
        promptToggle.addEventListener("click", () => {
            const collapsed = promptBody.style.display === "none";
            promptBody.style.display = collapsed ? "block" : "none";
            promptToggle.textContent = collapsed ? "접기 ▲" : "펼치기 ▼";
        });
    }

    // Save prompts
    if (savePromptsBtn) {
        savePromptsBtn.addEventListener("click", savePrompts);
    }

    // Reset prompts to defaults
    if (resetPromptsBtn) {
        resetPromptsBtn.addEventListener("click", () => {
            if (promptWithArticles) promptWithArticles.value = defaultPrompts.with_articles;
            if (promptWithoutArticles) promptWithoutArticles.value = defaultPrompts.without_articles;
            showSuccess("프롬프트를 기본값으로 복원했습니다. 저장 버튼을 눌러 적용하세요.");
        });
    }

    // Email toggle
    emailToggle.addEventListener("click", () => {
        const collapsed = emailBody.style.display === "none";
        emailBody.style.display = collapsed ? "block" : "none";
        emailToggle.textContent = collapsed ? "접기 ▲" : "펼치기 ▼";
    });

    // Add email recipient
    addEmailBtn.addEventListener("click", addEmailRecipient);
    emailInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") addEmailRecipient();
    });

    // Send email
    if (sendEmailBtn) {
        sendEmailBtn.addEventListener("click", sendEmailReport);
    }

    // CSV upload
    if (csvUploadBtn && csvUploadInput) {
        csvUploadBtn.addEventListener("click", () => csvUploadInput.click());
        csvUploadInput.addEventListener("change", uploadCsvFile);
    }

    // Reset to default tickers
    if (resetDefaultsBtn) {
        resetDefaultsBtn.addEventListener("click", resetToDefaultTickers);
    }

    // ─── Date Utility ─────────────────────────────────────────────────────

    function getKstDateString() {
        // KST = UTC + 9h
        const now = new Date();
        const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
        return kst.toISOString().slice(0, 10);
    }

    // ─── Settings Functions ───────────────────────────────────────────────

    async function loadSettings() {
        try {
            const resp = await fetch("/api/settings");
            const data = await resp.json();

            // Set selected model
            if (data.gemini_model && modelSelect) {
                modelSelect.value = data.gemini_model;
            }

            // Update news sources status
            if (data.news_sources) {
                const newsApiEl = document.getElementById("newsApiStatus");
                const googleCseEl = document.getElementById("googleCseStatus");
                if (newsApiEl) {
                    if (data.news_sources.newsapi) {
                        newsApiEl.textContent = "NewsAPI ✓";
                        newsApiEl.className = "status-badge status-active";
                    } else {
                        newsApiEl.textContent = "NewsAPI ✗ (NEWS_API_KEY 필요)";
                        newsApiEl.className = "status-badge status-inactive";
                    }
                }
                if (googleCseEl) {
                    if (data.news_sources.google_cse) {
                        googleCseEl.textContent = "Google CSE ✓";
                        googleCseEl.className = "status-badge status-active";
                    } else {
                        googleCseEl.textContent = "Google CSE ✗ (API 키 필요)";
                        googleCseEl.className = "status-badge status-inactive";
                    }
                }
            }

            // Load prompt templates
            if (data.default_prompt_with_articles) {
                defaultPrompts.with_articles = data.default_prompt_with_articles;
            }
            if (data.default_prompt_without_articles) {
                defaultPrompts.without_articles = data.default_prompt_without_articles;
            }
            if (promptWithArticles) {
                promptWithArticles.value = data.prompt_with_articles || data.default_prompt_with_articles || "";
            }
            if (promptWithoutArticles) {
                promptWithoutArticles.value = data.prompt_without_articles || data.default_prompt_without_articles || "";
            }

        } catch (err) {
            console.error("Failed to load settings:", err);
        }
    }

    async function saveModel() {
        const model = (modelSelect.value || "").trim();
        if (!model) { showError("모델명을 입력해주세요."); return; }
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ gemini_model: model }),
            });
            if (resp.ok) {
                showSuccess(`모델 저장됨: ${model}`);
            }
        } catch (err) {
            showError("모델 저장 실패");
        }
    }

    async function savePrompts() {
        const body = {};
        if (promptWithArticles) body.prompt_with_articles = promptWithArticles.value;
        if (promptWithoutArticles) body.prompt_without_articles = promptWithoutArticles.value;

        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                showSuccess("프롬프트 저장됨");
            } else {
                showError("프롬프트 저장 실패");
            }
        } catch (err) {
            showError("저장 오류");
        }
    }

    // ─── Email Recipient Functions ────────────────────────────────────────

    async function loadEmailRecipients() {
        try {
            const resp = await fetch("/api/email/recipients");
            const data = await resp.json();

            // Email config warning
            const configWarning = document.getElementById("emailConfigWarning");
            if (configWarning) {
                configWarning.style.display = data.has_email_config ? "none" : "block";
            }

            // Show/update send email button visibility
            if (sendEmailBtn) {
                sendEmailBtn.style.display = data.has_email_config ? "inline-flex" : "none";
            }

            // Default recipients
            const defaultSection = document.getElementById("defaultRecipientsSection");
            const defaultList = document.getElementById("defaultRecipientsList");
            if (defaultSection && defaultList) {
                if (data.default_recipients && data.default_recipients.length > 0) {
                    defaultSection.style.display = "block";
                    defaultList.innerHTML = data.default_recipients.map(email =>
                        `<span class="recipient-tag default-tag">${escapeHtml(email)} <small>(기본)</small></span>`
                    ).join("");
                } else {
                    defaultSection.style.display = "none";
                }
            }

            // Extra recipients
            renderExtraRecipients(data.extra_recipients || []);

        } catch (err) {
            console.error("Failed to load email recipients:", err);
        }
    }

    function renderExtraRecipients(recipients) {
        const list = document.getElementById("extraRecipientsList");
        if (!list) return;

        if (recipients.length === 0) {
            list.innerHTML = '<p class="empty-message">추가 수신자가 없습니다.</p>';
            return;
        }

        list.innerHTML = recipients.map(email => `
            <span class="recipient-tag">
                ${escapeHtml(email)}
                <button class="remove-btn" data-email="${escapeHtml(email)}" title="삭제">&times;</button>
            </span>
        `).join("");

        list.querySelectorAll(".remove-btn").forEach(btn => {
            btn.addEventListener("click", () => removeEmailRecipient(btn.dataset.email));
        });
    }

    async function addEmailRecipient() {
        const email = emailInput.value.trim();
        if (!email) return;

        try {
            const resp = await fetch("/api/email/recipients", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });
            const data = await resp.json();
            if (resp.ok) {
                renderExtraRecipients(data.extra_recipients);
                emailInput.value = "";
                emailInput.focus();
                showSuccess(`${email} 추가됨`);
            } else {
                showError(data.error || "추가 실패");
            }
        } catch (err) {
            showError("서버 오류");
        }
    }

    async function removeEmailRecipient(email) {
        try {
            const resp = await fetch(`/api/email/recipients/${encodeURIComponent(email)}`, {
                method: "DELETE",
            });
            const data = await resp.json();
            if (resp.ok) {
                renderExtraRecipients(data.extra_recipients);
            }
        } catch (err) {
            showError("삭제 실패");
        }
    }

    async function sendEmailReport() {
        if (currentResults.length === 0) {
            showError("전송할 분석 결과가 없습니다. 먼저 분석을 실행해주세요.");
            return;
        }

        sendEmailBtn.disabled = true;
        sendEmailBtn.textContent = "전송 중...";

        try {
            const resp = await fetch("/api/send-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ results: currentResults }),
            });
            const data = await resp.json();
            if (resp.ok) {
                showSuccess(`이메일 전송 완료 (${data.count}명)`);
            } else {
                showError(data.error || "이메일 전송 실패");
            }
        } catch (err) {
            showError("이메일 전송 오류");
        } finally {
            sendEmailBtn.disabled = false;
            sendEmailBtn.textContent = "✉ 이메일 전송";
        }
    }

    // ─── Ticker Functions ─────────────────────────────────────────────────

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

    async function resetToDefaultTickers() {
        if (!confirm("현재 종목 목록을 디폴트 종목으로 교체하시겠습니까?")) return;
        try {
            const resp = await fetch("/api/tickers/reset-defaults", { method: "POST" });
            const data = await resp.json();
            if (resp.ok) {
                renderTickers(data.tickers);
                showSuccess(`디폴트 종목 ${data.count}개로 초기화됨`);
            } else {
                showError(data.error || "초기화 실패");
            }
        } catch (err) {
            showError("서버 오류");
        }
    }

    async function uploadCsvFile(e) {
        const file = e.target.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append("file", file);

        try {
            const resp = await fetch("/api/tickers/upload", {
                method: "POST",
                body: formData,
            });
            const data = await resp.json();
            if (resp.ok) {
                renderTickers(data.tickers);
                showSuccess(`CSV에서 ${data.added_count}개 종목 추가됨 (총 ${data.tickers.length}개)`);
            } else {
                showError(data.error || "CSV 업로드 실패");
            }
        } catch (err) {
            showError("CSV 업로드 오류");
        } finally {
            csvUploadInput.value = "";
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

    // ─── Analysis Functions ───────────────────────────────────────────────

    function runAnalysis() {
        hideError();
        resultsSection.style.display = "none";
        loadingSection.style.display = "block";
        analyzeBtn.disabled = true;

        // Reset results
        allStocksOverview.innerHTML = "";
        analysisResults.innerHTML = "";
        currentResults = [];

        const selectedModel = modelSelect ? (modelSelect.value.trim() || "gemini-2.5-pro") : "gemini-2.5-pro";
        const dateStr = analysisDate ? analysisDate.value : getKstDateString();

        // Use SSE for streaming
        const url = `/api/analyze/stream?model=${encodeURIComponent(selectedModel)}&date=${encodeURIComponent(dateStr)}`;
        const eventSource = new EventSource(url);

        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case "progress":
                        loadingText.textContent = data.message;
                        // Update progress bar
                        if (data.message.includes("주가 수집")) {
                            progressFill.style.width = "20%";
                        } else if (data.message.includes("분석 시작")) {
                            progressFill.style.width = "40%";
                        } else if (data.message.includes("뉴스 기사 검색")) {
                            progressFill.style.width = "55%";
                        } else if (data.message.includes("Gemini 분석")) {
                            progressFill.style.width = "75%";
                        } else if (data.message.includes("분석 중")) {
                            const match = data.message.match(/\((\d+)\/(\d+)\)/);
                            if (match) {
                                const current = parseInt(match[1]);
                                const total = parseInt(match[2]);
                                const pct = 40 + (current / total) * 55;
                                progressFill.style.width = pct + "%";
                            }
                        }
                        break;

                    case "stocks":
                        resultsSection.style.display = "block";
                        renderAllStocksOverview(data.all_stocks);
                        break;

                    case "results":
                        currentResults = currentResults.concat(data.results);
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

                            // Show send email button if results exist
                            if (sendEmailBtn && currentResults.length > 0) {
                                sendEmailBtn.style.display = "inline-flex";
                            }

                            if (currentResults.length === 0 && data.message) {
                                analysisResults.innerHTML = `
                                    <div class="no-filter-message">
                                        <p>${escapeHtml(data.message)}</p>
                                        <p style="margin-top:8px"><strong>Tip:</strong> 변동성이 큰 종목을 추가하거나 날짜를 변경해보세요.</p>
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
            showError("분석 중 오류가 발생했습니다. 다시 시도해주세요.");
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
            const modelLabel = result.model_used || "Gemini";

            // News badge
            let newsBadge = "";
            if (result.articles_found) {
                const sources = (result.articles_sources || []).join(", ");
                const srcLabel = sources ? ` (${sources})` : "";
                newsBadge = `<span class="news-badge">📰 뉴스 ${result.articles_count}건${srcLabel}</span>`;
            } else {
                newsBadge = `<span class="news-badge news-badge-none">Gemini 자체 분석</span>`;
            }

            // Article links (숨김: 개별이슈 미발견인 경우)
            const isNoIssue = (result.analysis || "").trim().startsWith("개별이슈 미발견");
            let articlesHtml = "";
            if (!isNoIssue && result.articles && result.articles.length > 0) {
                const articleItems = result.articles.map(a => {
                    const datePart = a.date
                        ? `<span class="article-date">${escapeHtml(a.date)}</span>`
                        : "";
                    const sourcePart = a.source
                        ? ` <small style="color:#5a6a7a;">(${escapeHtml(a.source)})</small>`
                        : "";
                    const titleHtml = a.link
                        ? `<a href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.title)}</a>`
                        : escapeHtml(a.title);
                    return `<li>${datePart}${titleHtml}${sourcePart}</li>`;
                }).join("");

                articlesHtml = `
                    <div class="result-articles">
                        <h4>📰 근거 기사</h4>
                        <ul>${articleItems}</ul>
                    </div>
                `;
            }

            html += `
                <div class="result-card ${cls}">
                    <div class="result-header">
                        <span class="result-name">${escapeHtml(result.name)}</span>
                        <span class="result-change ${cls}">${sign}${result.change_pct.toFixed(1)}%</span>
                        ${newsBadge}
                        <span class="result-meta">${escapeHtml(modelLabel)}</span>
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
        const paragraphs = text.split(/\n{2,}/).filter(Boolean);
        if (paragraphs.length <= 1) {
            const lines = text.split(/\n/).filter(Boolean);
            return lines.map(l => `<p>${escapeHtml(l)}</p>`).join("");
        }
        return paragraphs.map(p => `<p>${escapeHtml(p.trim())}</p>`).join("");
    }

    // ─── Utility Functions ────────────────────────────────────────────────

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
