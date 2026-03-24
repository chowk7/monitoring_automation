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

    // Stop button
    const stopBtn = document.getElementById("stopBtn");

    // Ticker list toggle
    const tickerListToggle = document.getElementById("tickerListToggle");
    const tickerListBody = document.getElementById("tickerListBody");

    // Custom query elements
    const customQueryInput = document.getElementById("customQueryInput");
    const saveCustomQueryBtn = document.getElementById("saveCustomQueryBtn");

    // Threshold elements
    const thresholdInput = document.getElementById("thresholdInput");
    const saveThresholdBtn = document.getElementById("saveThresholdBtn");
    let currentThreshold = 5.0;

    // Category elements
    const categoryInput = document.getElementById("categoryInput");
    const categoryList = document.getElementById("categoryList");
    const categoryStats = document.getElementById("categoryStats");

    // Market indices elements
    const marketIndicesSection = document.getElementById("marketIndicesSection");
    const marketIndicesGrid = document.getElementById("marketIndicesGrid");
    const marketIndicesAnalysis = document.getElementById("marketIndicesAnalysis");

    // Store analysis results for email sending
    let currentResults = [];
    // Store market indices data for email
    let currentMarketData = null;
    // Store default prompts for reset
    let defaultPrompts = { with_articles: "", without_articles: "" };
    // Active EventSource (for stop functionality)
    let currentEventSource = null;

    // ─── Init ─────────────────────────────────────────────────────────────

    // Set default analysis date to yesterday in KST
    if (analysisDate) {
        analysisDate.value = getKstYesterdayString();
    }

    loadTickers();
    loadSettings();
    loadEmailRecipients();
    loadWebhookInfo();

    // ─── Event Listeners ─────────────────────────────────────────────────

    addBtn.addEventListener("click", addTicker);
    tickerInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") addTicker();
    });

    if (clearAllBtn) {
        clearAllBtn.addEventListener("click", clearAllTickers);
    }

    analyzeBtn.addEventListener("click", runAnalysis);

    // Ticker list toggle
    if (tickerListToggle && tickerListBody) {
        tickerListToggle.addEventListener("click", () => {
            const collapsed = tickerListBody.style.display === "none";
            tickerListBody.style.display = collapsed ? "block" : "none";
            tickerListToggle.textContent = collapsed ? "접기 ▲" : "펼치기 ▼";
        });
    }

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

    // Stop analysis
    if (stopBtn) {
        stopBtn.addEventListener("click", stopAnalysis);
    }

    // Save custom query
    if (saveCustomQueryBtn) {
        saveCustomQueryBtn.addEventListener("click", saveCustomQuery);
    }

    // Save threshold
    if (saveThresholdBtn) {
        saveThresholdBtn.addEventListener("click", saveThreshold);
    }

    // Save Gmail read settings
    const saveGmailReadBtn = document.getElementById("saveGmailReadBtn");
    if (saveGmailReadBtn) {
        saveGmailReadBtn.addEventListener("click", saveGmailReadSettings);
    }

    // ─── Date Utility ─────────────────────────────────────────────────────

    function getKstDateString() {
        // KST = UTC + 9h
        const now = new Date();
        const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
        return kst.toISOString().slice(0, 10);
    }

    function getKstYesterdayString() {
        // Last trading weekday in KST (skip Sunday=0, Saturday=6)
        const now = new Date();
        const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
        kst.setUTCDate(kst.getUTCDate() - 1); // go to yesterday
        const dow = kst.getUTCDay();
        if (dow === 0) {       // Sunday → go back to Friday
            kst.setUTCDate(kst.getUTCDate() - 2);
        } else if (dow === 6) { // Saturday → go back to Friday
            kst.setUTCDate(kst.getUTCDate() - 1);
        }
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
                const naverEl = document.getElementById("naverStatus");
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
                if (naverEl) {
                    if (data.news_sources.naver) {
                        naverEl.textContent = "Naver ✓";
                        naverEl.className = "status-badge status-active";
                    } else {
                        naverEl.textContent = "Naver ✗ (NAVER_CLIENT_ID/SECRET 필요)";
                        naverEl.className = "status-badge status-inactive";
                    }
                }
                const gmailReadEl = document.getElementById("gmailReadStatus");
                if (gmailReadEl) {
                    if (data.news_sources.gmail_read) {
                        gmailReadEl.textContent = "Gmail 메모 ✓";
                        gmailReadEl.className = "status-badge status-active";
                    } else {
                        gmailReadEl.textContent = "Gmail 메모 ✗ (비활성)";
                        gmailReadEl.className = "status-badge status-inactive";
                    }
                }
            }

            // Load Gmail read settings
            const gmailEnabledEl = document.getElementById("gmailReadEnabled");
            const gmailSubjectEl = document.getElementById("gmailSubjectFilter");
            const gmailMaxEl = document.getElementById("gmailMaxEmails");
            if (gmailEnabledEl) gmailEnabledEl.checked = !!data.gmail_read_enabled;
            if (gmailSubjectEl) gmailSubjectEl.value = data.gmail_subject_filter || "";
            if (gmailMaxEl) gmailMaxEl.value = data.gmail_max_emails || 3;

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

            // Load custom query
            if (customQueryInput) {
                customQueryInput.value = data.custom_query || "";
            }

            // Load change threshold
            if (data.change_threshold !== undefined) {
                currentThreshold = parseFloat(data.change_threshold) || 5.0;
                if (thresholdInput) thresholdInput.value = currentThreshold;
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

    async function saveThreshold() {
        const val = thresholdInput ? parseFloat(thresholdInput.value) : NaN;
        if (isNaN(val) || val <= 0 || val > 100) {
            showError("변동률은 0 초과 100 이하 숫자로 입력해주세요.");
            return;
        }
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ change_threshold: val }),
            });
            if (resp.ok) {
                currentThreshold = val;
                showSuccess(`기준 변동률 저장됨: ${val}%`);
            } else {
                showError("저장 실패");
            }
        } catch (err) {
            showError("저장 오류");
        }
    }

    async function saveCustomQuery() {
        const query = customQueryInput ? customQueryInput.value.trim() : "";
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ custom_query: query }),
            });
            if (resp.ok) {
                showSuccess(query ? `검색어 템플릿 저장됨: ${query}` : "검색어 기본값으로 초기화됨");
            } else {
                showError("저장 실패");
            }
        } catch (err) {
            showError("저장 오류");
        }
    }

    async function saveGmailReadSettings() {
        const enabled = document.getElementById("gmailReadEnabled")?.checked || false;
        const subject = document.getElementById("gmailSubjectFilter")?.value.trim() || "";
        const maxEmails = parseInt(document.getElementById("gmailMaxEmails")?.value || "3", 10);
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    gmail_read_enabled: enabled,
                    gmail_subject_filter: subject,
                    gmail_max_emails: maxEmails,
                }),
            });
            if (resp.ok) {
                const data = await resp.json();
                showSuccess(enabled && subject ? `Gmail 메모 읽기 활성화: "${subject}"` : "Gmail 메모 읽기 비활성화됨");
                // Update status badge
                const gmailReadEl = document.getElementById("gmailReadStatus");
                if (gmailReadEl) {
                    if (data.news_sources?.gmail_read) {
                        gmailReadEl.textContent = "Gmail 메모 ✓";
                        gmailReadEl.className = "status-badge status-active";
                    } else {
                        gmailReadEl.textContent = "Gmail 메모 ✗ (비활성)";
                        gmailReadEl.className = "status-badge status-inactive";
                    }
                }
            } else {
                showError("저장 실패");
            }
        } catch (err) {
            showError("저장 오류");
        }
    }

    function stopAnalysis() {
        if (currentEventSource) {
            currentEventSource.close();
            currentEventSource = null;
        }
        loadingSection.style.display = "none";
        progressFill.style.width = "0%";
        analyzeBtn.disabled = false;
        if (stopBtn) stopBtn.style.display = "none";
        showSuccess("분석이 중지되었습니다. 지금까지의 결과는 유지됩니다.");
    }

    // ─── Webhook Functions ────────────────────────────────────────────────

    async function loadWebhookInfo() {
        try {
            const resp = await fetch("/api/webhook/info");
            const data = await resp.json();
            updateWebhookDisplay(data.token);
        } catch (err) {
            console.error("Failed to load webhook info:", err);
        }
    }

    function updateWebhookDisplay(token) {
        const urlEl = document.getElementById("webhookUrl");
        if (urlEl) {
            const base = window.location.origin;
            urlEl.textContent = `${base}/api/webhook/run-analysis?token=${token}`;
        }
    }

    const copyWebhookBtn = document.getElementById("copyWebhookBtn");
    if (copyWebhookBtn) {
        copyWebhookBtn.addEventListener("click", () => {
            const urlEl = document.getElementById("webhookUrl");
            if (urlEl) {
                navigator.clipboard.writeText(urlEl.textContent).then(() => {
                    showSuccess("웹훅 URL이 복사되었습니다.");
                });
            }
        });
    }

    const regenTokenBtn = document.getElementById("regenTokenBtn");
    if (regenTokenBtn) {
        regenTokenBtn.addEventListener("click", async () => {
            if (!confirm("토큰을 재생성하면 기존 스케줄러 설정을 업데이트해야 합니다. 계속하시겠습니까?")) return;
            try {
                const resp = await fetch("/api/webhook/token/regenerate", { method: "POST" });
                const data = await resp.json();
                updateWebhookDisplay(data.token);
                showSuccess("토큰이 재생성되었습니다. 스케줄러 URL을 업데이트해주세요.");
            } catch (err) {
                showError("토큰 재생성 실패");
            }
        });
    }

    // ─── Email Recipient Functions ────────────────────────────────────────

    async function loadEmailRecipients() {
        try {
            const resp = await fetch("/api/email/recipients");
            const data = await resp.json();

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
                body: JSON.stringify({ results: currentResults, market_data: currentMarketData }),
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

        const category = categoryInput ? categoryInput.value.trim() : "";
        // Support comma-separated, space-separated, or newline-separated input
        const symbols = raw.split(/[,\s\n]+/).map(t => t.trim()).filter(Boolean);

        if (symbols.length > 1) {
            // Bulk add (assign same category to all)
            const tickers = symbols.map(s => ({ ticker: s, category, name: "" }));
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
        } else if (symbols.length === 1) {
            // Single add
            try {
                const resp = await fetch("/api/tickers", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ticker: symbols[0], category }),
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
            updateCategoryDatalist(tickers);
            return;
        }

        // Group by category
        const groups = {};
        const noCat = [];
        for (const t of tickers) {
            const cat = t.category || "";
            if (cat) {
                if (!groups[cat]) groups[cat] = [];
                groups[cat].push(t);
            } else {
                noCat.push(t);
            }
        }

        const makeTag = (t) => `
            <span class="ticker-tag" title="${escapeHtml(t.name || t.ticker)}">
                ${escapeHtml(t.ticker)}${t.name ? `<span class="ticker-name">${escapeHtml(t.name)}</span>` : ""}
                <button class="remove-btn" data-ticker="${escapeHtml(t.ticker)}" title="Remove">&times;</button>
            </span>`;

        let html = "";
        for (const [cat, items] of Object.entries(groups)) {
            html += `<div class="ticker-category-group">
                <span class="ticker-category-label">${escapeHtml(cat)}</span>
                <div class="ticker-category-items">${items.map(makeTag).join("")}</div>
            </div>`;
        }
        if (noCat.length) {
            html += `<div class="ticker-category-group">
                <span class="ticker-category-label" style="color:#5a6a7a;">미분류</span>
                <div class="ticker-category-items">${noCat.map(makeTag).join("")}</div>
            </div>`;
        }

        tickerList.innerHTML = html;

        tickerList.querySelectorAll(".remove-btn").forEach(btn => {
            btn.addEventListener("click", () => removeTicker(btn.dataset.ticker));
        });

        updateCategoryDatalist(tickers);
    }

    function updateCategoryDatalist(tickers) {
        if (!categoryList) return;
        const cats = [...new Set(tickers.map(t => t.category).filter(Boolean))];
        categoryList.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">`).join("");
    }

    // ─── Analysis Functions ───────────────────────────────────────────────

    function runAnalysis() {
        hideError();
        resultsSection.style.display = "none";
        loadingSection.style.display = "block";
        analyzeBtn.disabled = true;
        if (stopBtn) stopBtn.style.display = "inline-block";

        // Reset results
        allStocksOverview.innerHTML = "";
        analysisResults.innerHTML = "";
        currentResults = [];
        currentMarketData = null;
        if (categoryStats) { categoryStats.innerHTML = ""; categoryStats.style.display = "none"; }
        if (marketIndicesSection) { marketIndicesSection.style.display = "none"; marketIndicesGrid.innerHTML = ""; marketIndicesAnalysis.style.display = "none"; }

        const selectedModel = modelSelect ? (modelSelect.value.trim() || "gemini-2.5-pro") : "gemini-2.5-pro";
        const dateStr = analysisDate ? analysisDate.value : getKstDateString();
        const customQuery = customQueryInput ? customQueryInput.value.trim() : "";

        // Use SSE for streaming
        let url = `/api/analyze/stream?model=${encodeURIComponent(selectedModel)}&date=${encodeURIComponent(dateStr)}`;
        if (customQuery) url += `&custom_query=${encodeURIComponent(customQuery)}`;
        const eventSource = new EventSource(url);
        currentEventSource = eventSource;

        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case "progress":
                        loadingText.textContent = data.message;
                        // Update progress bar
                        if (data.message.includes("글로벌 지수")) {
                            progressFill.style.width = "10%";
                        } else if (data.message.includes("주가 수집")) {
                            progressFill.style.width = "25%";
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

                    case "market_indices":
                        currentMarketData = { indices: data.indices, analysis: data.analysis, date: data.date };
                        resultsSection.style.display = "block";
                        renderMarketIndices(data);
                        break;

                    case "stocks":
                        resultsSection.style.display = "block";
                        if (data.category_stats) renderCategoryStats(data.category_stats);
                        renderAllStocksOverview(data.all_stocks);
                        break;

                    case "results":
                        currentResults = currentResults.concat(data.results);
                        appendAnalysisResults(data.results);
                        break;

                    case "done":
                        eventSource.close();
                        currentEventSource = null;
                        progressFill.style.width = "100%";
                        loadingText.textContent = "완료!";

                        setTimeout(() => {
                            loadingSection.style.display = "none";
                            progressFill.style.width = "0%";
                            analyzeBtn.disabled = false;
                            if (stopBtn) stopBtn.style.display = "none";

                            // Auto-send email and show button if results exist
                            if (sendEmailBtn && currentResults.length > 0) {
                                sendEmailBtn.style.display = "inline-flex";
                                sendEmailReport();
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
            currentEventSource = null;
            loadingSection.style.display = "none";
            analyzeBtn.disabled = false;
            if (stopBtn) stopBtn.style.display = "none";
            showError("분석 중 오류가 발생했습니다. 다시 시도해주세요.");
        };
    }

    function renderMarketIndices(data) {
        if (!marketIndicesSection || !data || !data.indices) return;

        // Render index tiles
        const indices = data.indices || [];
        marketIndicesGrid.innerHTML = indices.map(idx => {
            const chg = idx.change_pct || 0;
            const cls = idx.error ? "error" : chg > 0 ? "positive" : chg < 0 ? "negative" : "neutral";
            const sign = chg > 0 ? "+" : "";
            const changeText = idx.error ? "오류" : `${sign}${chg.toFixed(2)}%`;
            return `<div class="market-index-item ${cls}">
                <span class="idx-region">${escapeHtml(idx.region)}</span>
                <span class="idx-name">${escapeHtml(idx.name)}</span>
                <span class="idx-change">${changeText}</span>
            </div>`;
        }).join("");

        // Render Gemini analysis
        if (data.analysis) {
            marketIndicesAnalysis.textContent = data.analysis;
            marketIndicesAnalysis.style.display = "block";
        }

        marketIndicesSection.style.display = "block";
    }

    function renderCategoryStats(stats) {
        if (!categoryStats || !stats || Object.keys(stats).length === 0) return;

        const entries = Object.entries(stats).sort((a, b) => Math.abs(b[1].avg) - Math.abs(a[1].avg));
        let html = '<h3 style="margin-bottom:10px;">카테고리별 평균 등락</h3><div class="category-stats-grid">';
        for (const [cat, s] of entries) {
            const cls = s.avg > 0 ? "positive" : s.avg < 0 ? "negative" : "neutral";
            const sign = s.avg > 0 ? "+" : "";
            html += `<div class="category-stat-item ${cls}">
                <span class="cat-name">${escapeHtml(cat)}</span>
                <span class="cat-avg">${sign}${s.avg.toFixed(2)}%</span>
                <span class="cat-count">${s.count}종목</span>
            </div>`;
        }
        html += "</div>";
        categoryStats.innerHTML = html;
        categoryStats.style.display = "block";
    }

    function renderAllStocksOverview(allStocks) {
        if (!allStocks || Object.keys(allStocks).length === 0) {
            allStocksOverview.innerHTML = "";
            return;
        }

        // Group by category
        const catGroups = {};
        for (const [ticker, info] of Object.entries(allStocks)) {
            const cat = info.category || "미분류";
            if (!catGroups[cat]) catGroups[cat] = [];
            catGroups[cat].push([ticker, info]);
        }
        // Sort items within each group
        for (const cat of Object.keys(catGroups)) {
            catGroups[cat].sort((a, b) => Math.abs(b[1].change_pct) - Math.abs(a[1].change_pct));
        }

        const makeItem = ([ticker, info]) => {
            if (info.error && info.change_pct === 0) {
                return `<div class="overview-item neutral" title="${escapeHtml(info.error)}">${escapeHtml(info.name || ticker)} (오류)</div>`;
            }
            const cls = info.change_pct > 0 ? "positive" : info.change_pct < 0 ? "negative" : "neutral";
            const filtered = Math.abs(info.change_pct) >= currentThreshold ? " filtered" : "";
            const sign = info.change_pct > 0 ? "+" : "";
            return `<div class="overview-item ${cls}${filtered}">${escapeHtml(info.name || ticker)} ${sign}${info.change_pct.toFixed(1)}%</div>`;
        };

        let overviewHtml = '<h3>전체 종목 변동률</h3>';
        const hasCats = Object.keys(catGroups).some(c => c !== "미분류");
        if (hasCats) {
            for (const [cat, items] of Object.entries(catGroups)) {
                overviewHtml += `<div class="overview-category-group">
                    <span class="overview-category-label">${escapeHtml(cat)}</span>
                    <div class="overview-grid">${items.map(makeItem).join("")}</div>
                </div>`;
            }
        } else {
            overviewHtml += `<div class="overview-grid">${Object.entries(catGroups["미분류"] || {}).map(makeItem).join("")}</div>`;
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

    // ─── GCS 동기화 ───────────────────────────────────────────────────

    async function loadGcsStatus() {
        try {
            const resp = await fetch("/api/gcs/status");
            const data = await resp.json();
            const section = document.getElementById("gcsSyncSection");
            if (data.configured && section) {
                section.style.display = "";
                const badge = document.getElementById("gcsBucketBadge");
                if (badge) badge.textContent = data.bucket;
            }
        } catch (e) {
            // GCS not available – section stays hidden
        }
    }

    async function saveToGcs() {
        const btn = document.getElementById("gcsSaveBtn");
        const status = document.getElementById("gcsSyncStatus");
        if (!btn || !status) return;
        btn.disabled = true;
        status.textContent = "저장 중...";
        status.style.color = "";
        try {
            const resp = await fetch("/api/gcs/save", { method: "POST" });
            const data = await resp.json();
            if (data.success) {
                status.textContent = "✓ GCS 저장 완료";
                status.style.color = "var(--success, #10b981)";
            } else {
                const failed = Object.entries(data.uploaded || {}).filter(([, v]) => !v).map(([k]) => k).join(", ");
                status.textContent = `✗ 저장 실패 (${failed || "알 수 없음"})`;
                status.style.color = "#ef4444";
            }
        } catch (e) {
            status.textContent = "✗ 오류 발생";
            status.style.color = "#ef4444";
        } finally {
            btn.disabled = false;
            setTimeout(() => { status.textContent = ""; }, 5000);
        }
    }

    const gcsSaveBtn = document.getElementById("gcsSaveBtn");
    if (gcsSaveBtn) gcsSaveBtn.addEventListener("click", saveToGcs);

    loadGcsStatus();
});
