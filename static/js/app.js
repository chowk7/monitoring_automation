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
    loadScheduleStatus();
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

    // Source query templates toggle
    const sourceQueryToggle = document.getElementById("sourceQueryToggle");
    const sourceQueryBody = document.getElementById("sourceQueryBody");
    if (sourceQueryToggle) {
        sourceQueryToggle.addEventListener("click", () => {
            const isOpen = sourceQueryBody.style.display !== "none";
            sourceQueryBody.style.display = isOpen ? "none" : "block";
            sourceQueryToggle.textContent = isOpen ? "펼치기 ▼" : "접기 ▲";
        });
    }

    // Source query save buttons
    document.querySelectorAll(".btn-save-source-query").forEach(btn => {
        btn.addEventListener("click", () => {
            const source = btn.dataset.source;
            const inputMap = {
                "yahoo": "queryYahoo",
                "newsapi": "queryNewsapi",
                "google": "queryGoogle",
                "naver": "queryNaver",
                "market_us": "queryMarketUs",
                "market_korea": "queryMarketKorea",
                "market_china": "queryMarketChina",
                "market_hongkong": "queryMarketHongkong",
                "market_japan": "queryMarketJapan",
                "market_europe": "queryMarketEurope",
            };
            const keyMap = {
                "yahoo": "query_yahoo",
                "newsapi": "query_newsapi",
                "google": "query_google",
                "naver": "query_naver",
                "market_us": "query_market_us",
                "market_korea": "query_market_korea",
                "market_china": "query_market_china",
                "market_hongkong": "query_market_hongkong",
                "market_japan": "query_market_japan",
                "market_europe": "query_market_europe",
            };
            const inputId = inputMap[source];
            const key = keyMap[source];
            const input = document.getElementById(inputId);
            if (!input || !key) return;
            
            const value = input.value.trim();
            saveSourceQuery(key, value, btn);
        });
    });

    // Save threshold
    if (saveThresholdBtn) {
        saveThresholdBtn.addEventListener("click", saveThreshold);
    }

    // Save Gmail read settings
    const saveGmailReadBtn = document.getElementById("saveGmailReadBtn");
    if (saveGmailReadBtn) {
        saveGmailReadBtn.addEventListener("click", saveGmailReadSettings);
    }

    const testGmailReadBtn = document.getElementById("testGmailReadBtn");
    if (testGmailReadBtn) {
        testGmailReadBtn.addEventListener("click", testGmailRead);
    }

    const saveAutoScheduleBtn = document.getElementById("saveAutoScheduleBtn");
    if (saveAutoScheduleBtn) {
        saveAutoScheduleBtn.addEventListener("click", saveAutoSchedule);
    }

    // News source toggles — auto-save on change
    [
        ["yahooFinanceEnabled", "yahoo_finance_enabled"],
        ["newsApiEnabled", "newsapi_enabled"],
        ["googleCseEnabled", "google_cse_enabled"],
        ["naverEnabled", "naver_enabled"],
    ].forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("change", () => saveNewsSourceEnabled(key, el.checked));
        }
    });

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

            // Update news source checkboxes
            if (data.news_sources) {
                // Yahoo Finance — always available, just respects user toggle
                const yahooCb = document.getElementById("yahooFinanceEnabled");
                if (yahooCb) {
                    yahooCb.checked = data.yahoo_finance_enabled !== false;
                }

                // NewsAPI
                const newsApiCb = document.getElementById("newsApiEnabled");
                const newsApiStatusEl = document.getElementById("newsApiStatus");
                if (newsApiCb) {
                    const available = !!data.news_sources.newsapi;
                    newsApiCb.disabled = !available;
                    newsApiCb.checked = available && data.newsapi_enabled !== false;
                    if (newsApiStatusEl) newsApiStatusEl.textContent = available ? "" : "(API 키 없음)";
                }

                // Google CSE
                const googleCseCb = document.getElementById("googleCseEnabled");
                const googleCseStatusEl = document.getElementById("googleCseStatus");
                if (googleCseCb) {
                    const available = !!data.news_sources.google_cse;
                    googleCseCb.disabled = !available;
                    googleCseCb.checked = available && data.google_cse_enabled !== false;
                    if (googleCseStatusEl) googleCseStatusEl.textContent = available ? "" : "(API 키 없음)";
                }

                // Naver
                const naverCb = document.getElementById("naverEnabled");
                const naverStatusEl = document.getElementById("naverStatus");
                if (naverCb) {
                    const available = !!data.news_sources.naver;
                    naverCb.disabled = !available;
                    naverCb.checked = available && data.naver_enabled !== false;
                    if (naverStatusEl) naverStatusEl.textContent = available ? "" : "(API 키 없음)";
                }

                // Gmail memo status badge
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

            // Load source-specific query templates
            const queryInputs = {
                "queryYahoo": data.query_yahoo,
                "queryNewsapi": data.query_newsapi,
                "queryGoogle": data.query_google,
                "queryNaver": data.query_naver,
                "queryMarketUs": data.query_market_us,
                "queryMarketKorea": data.query_market_korea,
                "queryMarketChina": data.query_market_china,
                "queryMarketHongkong": data.query_market_hongkong,
                "queryMarketJapan": data.query_market_japan,
                "queryMarketEurope": data.query_market_europe,
            };
            Object.entries(queryInputs).forEach(([id, value]) => {
                const el = document.getElementById(id);
                if (el) el.value = value || "";
            });

            // Load change threshold
            if (data.change_threshold !== undefined) {
                currentThreshold = parseFloat(data.change_threshold) || 5.0;
                if (thresholdInput) thresholdInput.value = currentThreshold;
            }

            // Load auto-schedule settings
            const autoScheduleEnabledEl = document.getElementById("autoScheduleEnabled");
            const autoScheduleTimeEl = document.getElementById("autoScheduleTime");
            if (autoScheduleEnabledEl) autoScheduleEnabledEl.checked = !!data.auto_schedule_enabled;
            if (autoScheduleTimeEl && data.auto_schedule_time) autoScheduleTimeEl.value = data.auto_schedule_time;
            loadScheduleStatus();

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
        // deprecated - custom query is now per-source
    }

    async function saveSourceQuery(key, value, btn) {
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ [key]: value }),
            });
            if (resp.ok) {
                const label = key.replace("query_", "").replace("market_", "market ");
                showSuccess(value ? `${label} 검색어 저장됨` : "기본값으로 초기화됨");
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

    async function saveAutoSchedule() {
        const enabled = document.getElementById("autoScheduleEnabled")?.checked || false;
        const time = document.getElementById("autoScheduleTime")?.value || "09:00";
        try {
            const resp = await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    auto_schedule_enabled: enabled,
                    auto_schedule_time: time,
                }),
            });
            if (resp.ok) {
                showSuccess(enabled ? `자동 분석 예약됨: 매일 ${time} KST` : "자동 분석 비활성화됨");
                loadScheduleStatus();
            } else {
                showError("저장 실패");
            }
        } catch (err) {
            showError("저장 오류");
        }
    }

    async function loadScheduleStatus() {
        const statusEl = document.getElementById("autoScheduleStatus");
        if (!statusEl) return;
        try {
            const resp = await fetch("/api/schedule/status");
            const data = await resp.json();
            if (!data.scheduler_available) {
                statusEl.textContent = "스케줄러 미설치";
                statusEl.className = "status-badge status-inactive";
                statusEl.style.display = "";
                return;
            }
            if (data.enabled && data.next_run) {
                statusEl.textContent = `다음 실행: ${data.next_run}`;
                statusEl.className = "status-badge status-active";
                statusEl.style.display = "";
            } else if (data.enabled) {
                statusEl.textContent = "활성화됨";
                statusEl.className = "status-badge status-active";
                statusEl.style.display = "";
            } else {
                statusEl.style.display = "none";
            }
        } catch {
            // silently ignore
        }
    }

    async function saveNewsSourceEnabled(key, value) {
        try {
            await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ [key]: value }),
            });
        } catch (err) {
            console.error("Failed to save news source setting:", err);
        }
    }

    async function testGmailRead() {
        const subject = document.getElementById("gmailSubjectFilter")?.value.trim() || "";
        const max = document.getElementById("gmailMaxEmails")?.value || 5;
        const resultEl = document.getElementById("gmailTestResult");
        if (!resultEl) return;

        resultEl.style.display = "block";
        resultEl.innerHTML = "<span style='color:#aaa'>IMAP 연결 중...</span>";

        try {
            const params = new URLSearchParams({ max });
            if (subject) params.set("subject", subject);
            const resp = await fetch(`/api/gmail/test?${params}`);
            const data = await resp.json();

            if (!data.ok) {
                resultEl.innerHTML = `<span style='color:#e74c3c'>❌ 오류: ${data.error}</span>`;
                return;
            }

            if (data.count === 0) {
                resultEl.innerHTML = `<span style='color:#f39c12'>⚠ <b>${data.account}</b> 받은편지함에서 "<b>${data.subject_filter}</b>" 제목의 이메일을 찾지 못했습니다.</span>`;
                return;
            }

            let html = `<div style='color:#2ecc71;margin-bottom:8px'>✓ <b>${data.account}</b>에서 "<b>${data.subject_filter}</b>" 이메일 <b>${data.count}개</b> 읽기 성공</div>`;
            data.articles.forEach((a, i) => {
                html += `
                <div style='border:1px solid #444;border-radius:4px;padding:8px;margin-bottom:8px;background:#252535'>
                    <div style='display:flex;justify-content:space-between;margin-bottom:4px'>
                        <span style='color:#7ec8e3;font-weight:bold'>[${i + 1}] ${escHtml(a.title)}</span>
                        <span style='color:#888;font-size:0.9em'>${a.date || "날짜 없음"}</span>
                    </div>
                    <div style='color:#ccc;white-space:pre-wrap;word-break:break-all'>${escHtml(a.body || a.snippet || "(본문 없음)")}</div>
                </div>`;
            });
            resultEl.innerHTML = html;
        } catch (err) {
            resultEl.innerHTML = `<span style='color:#e74c3c'>❌ 요청 오류: ${err.message}</span>`;
        }
    }

    function escHtml(str) {
        return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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

    // HTML 이스케이프 (XSS 방지)
    function escapeHtml(str) {
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    /**
     * Remove English summary sentences and REFS:[...] tag from Gemini analysis text.
     * English sentences are identified as lines/segments with only ASCII Latin letters.
     */
    function stripEnglishAndRefs(text) {
        if (!text) return "";
        // Remove REFS:[...] tag
        let cleaned = text.replace(/REFS:\[[^\]]*\]/g, "").trim();
        // Split by newlines, keep only Korean/Chinese/Japanese lines (non-ASCII dominant)
        const lines = cleaned.split(/\n/);
        const koreanLines = [];
        for (const line of lines) {
            // Skip empty lines
            if (!line.trim()) continue;
            // Count ASCII Latin letters
            const asciiCount = (line.match(/[A-Za-z]/g) || []).length;
            const totalChars = line.replace(/\s/g, "").length;
            // If more than 30% ASCII, treat as English and skip
            if (totalChars > 0 && asciiCount / totalChars > 0.3) continue;
            koreanLines.push(line);
        }
        return koreanLines.join(" ").replace(/\s+/g, " ").trim();
    }

    // HTML 색상 적용된 이메일본문 생성 (innerHTML용)
    function buildEmailTextPreviewHTML() {
        if (currentResults.length === 0) return "";

        const MARKET_INDICES_MAP = {
            "^DJI":       { name: "Dow",       region: "미국" },
            "^IXIC":      { name: "Nasdaq",    region: "미국" },
            "^GSPC":      { name: "S&P 500",   region: "미국" },
            "^KS11":      { name: "코스피", region: "한국" },
            "^KQ11":      { name: "코스닥",    region: "한국" },
            "000001.SS":  { name: "中상해",    region: "중국" },
            "^HSI":       { name: "홍콩항셍",  region: "홍콩" },
            "^N225":      { name: "日니케이",   region: "일본" },
            "^FTSE":      { name: "英FTSE",     region: "영국" },
            "^FCHI":      { name: "CAC",        region: "프랑스" },
            "^GDAXI":     { name: "獨DAX",      region: "독일" },
        };
        const REGION_GROUPS = [
            ["미\u00a0\u00a0국", ["미국"]],
            ["아시아",           ["한국", "중국", "홍콩", "일본"]],
            ["유\u00a0\u00a0럽", ["영국", "프랑스", "독일"]],
        ];

        const dateInput = document.getElementById("dateInput");
        const dateStr = dateInput ? dateInput.value : new Date().toISOString().split("T")[0];
        let dateLabel = dateStr;
        try {
            const [y, m, d] = dateStr.split("-");
            dateLabel = `${m}/${d}`;
        } catch (e) {}

        // 양수 1.23% (파랑 0,0,255), 음수 △1.23% (빨강 255,0,0)
        const fmtChgHtml = (v) => {
            const absVal = Math.abs(v).toFixed(2);
            const label = v < 0 ? `△${absVal}%` : `${absVal}%`;
            if (v < 0) {
                return `<span style="color:rgb(255,0,0)">${escapeHtml(label)}</span>`;
            } else {
                return `<span style="color:rgb(0,0,255)">${escapeHtml(label)}</span>`;
            }
        };

        const lines = [];
        lines.push(escapeHtml("안녕하십니까,"));
        lines.push(escapeHtml(`${dateLabel}일 종가기준 모니터링 업체 현황 송부드립니다.`));
        lines.push("");

        // 시장 section
        if (currentMarketData && currentMarketData.indices) {
            const indexByTicker = {};
            for (const m of currentMarketData.indices) {
                if (!m.error) indexByTicker[m.ticker] = m;
            }
            const marketParts = [];
            for (const [regionLabel, regions] of REGION_GROUPS) {
                const tickers = Object.keys(MARKET_INDICES_MAP).filter(t =>
                    MARKET_INDICES_MAP[t] && regions.includes(MARKET_INDICES_MAP[t].region)
                );
                const items = [];
                for (const ticker of tickers) {
                    // market data not received yet - skip (don't show wrong 휴장)
                    if (!indexByTicker[ticker]) continue;
                    const m = indexByTicker[ticker];
                    const display = escapeHtml(MARKET_INDICES_MAP[ticker]?.name || m.name || ticker);
                    // If is_closed but we have valid change_pct, market was actually trading
                    const isReallyClosed = m.is_closed && (m.change_pct === 0 || m.change_pct == null);
                    if (isReallyClosed) {
                        items.push(`${display} (휴장)`);
                    } else {
                        items.push(`${display} (${fmtChgHtml(m.change_pct)})`);
                    }
                }
                if (items.length > 0) {
                    marketParts.push(`  - ${escapeHtml(regionLabel)} :  ${items.join(", ")}`);
                }
            }
            if (marketParts.length > 0) {
                lines.push("<b><u>시&nbsp;&nbsp;장</u></b>");
                lines.push(...marketParts);
                lines.push("");
            }
        }

        // 개별회사 section
        if (currentResults.length > 0) {
            lines.push("<b><u>개별회사</u></b>");
            for (const r of currentResults) {
                const analysis = escapeHtml(stripEnglishAndRefs(r.analysis || ""));
                // 개별회사 등락률은 소수점 1자리 + 색상 적용
                const chgVal = r.change_pct < 0 ? `△${Math.abs(r.change_pct).toFixed(1)}%` : `${r.change_pct.toFixed(1)}%`;
                const chgHtml = r.change_pct < 0
                    ? `<span style="color:rgb(255,0,0)">${chgVal}</span>`
                    : `<span style="color:rgb(0,0,255)">${chgVal}</span>`;
                lines.push(`- ${escapeHtml(r.name)} (${chgHtml}): ${analysis}`);
            }
        }

        return lines.join("<br>");
    }

    // 클립보드 복사용 plain text (HTML 태그 없음)
    function buildEmailTextPreviewPlain() {
        if (currentResults.length === 0) return "";

        const MARKET_INDICES_MAP = {
            "^DJI":       { name: "Dow",       region: "미국" },
            "^IXIC":      { name: "Nasdaq",    region: "미국" },
            "^GSPC":      { name: "S&P 500",   region: "미국" },
            "^KS11":      { name: "코스피", region: "한국" },
            "^KQ11":      { name: "코스닥",    region: "한국" },
            "000001.SS":  { name: "中상해",    region: "중국" },
            "^HSI":       { name: "홍콩항셍",  region: "홍콩" },
            "^N225":      { name: "日니케이",   region: "일본" },
            "^FTSE":      { name: "英FTSE",     region: "영국" },
            "^FCHI":      { name: "CAC",        region: "프랑스" },
            "^GDAXI":     { name: "獨DAX",      region: "독일" },
        };
        const REGION_GROUPS = [
            ["미\u00a0\u00a0국", ["미국"]],
            ["아시아",           ["한국", "중국", "홍콩", "일본"]],
            ["유\u00a0\u00a0럽", ["영국", "프랑스", "독일"]],
        ];

        const dateInput = document.getElementById("dateInput");
        const dateStr = dateInput ? dateInput.value : new Date().toISOString().split("T")[0];
        let dateLabel = dateStr;
        try {
            const [y, m, d] = dateStr.split("-");
            dateLabel = `${m}/${d}`;
        } catch (e) {}

        const fmtChg = (v) => v < 0 ? `△${Math.abs(v).toFixed(2)}%` : `${v.toFixed(2)}%`;

        const lines = [];
        lines.push("안녕하십니까,");
        lines.push(`${dateLabel}일 종가기준 모니터링 업체 현황 송부드립니다.`);
        lines.push("");

        // 시장 section
        if (currentMarketData && currentMarketData.indices) {
            const indexByTicker = {};
            for (const m of currentMarketData.indices) {
                if (!m.error) indexByTicker[m.ticker] = m;
            }
            const marketParts = [];
            for (const [regionLabel, regions] of REGION_GROUPS) {
                const tickers = Object.keys(MARKET_INDICES_MAP).filter(t =>
                    MARKET_INDICES_MAP[t] && regions.includes(MARKET_INDICES_MAP[t].region)
                );
                const items = [];
                for (const ticker of tickers) {
                    // market data not received yet - skip (don't show wrong 휴장)
                    if (!indexByTicker[ticker]) continue;
                    const m = indexByTicker[ticker];
                    const display = MARKET_INDICES_MAP[ticker]?.name || m.name || ticker;
                    // If is_closed but we have valid change_pct, market was actually trading
                    const isReallyClosed = m.is_closed && (m.change_pct === 0 || m.change_pct == null);
                    if (isReallyClosed) {
                        items.push(`${display} (휴장)`);
                    } else {
                        items.push(`${display} (${fmtChg(m.change_pct)})`);
                    }
                }
                if (items.length > 0) {
                    marketParts.push(`  - ${regionLabel} :  ${items.join(", ")}`);
                }
            }
            if (marketParts.length > 0) {
                lines.push("[시  장]");
                lines.push(...marketParts);
                lines.push("");
            }
        }

        // 개별회사 section
        if (currentResults.length > 0) {
            lines.push("[개별회사]");
            for (const r of currentResults) {
                const analysis = stripEnglishAndRefs(r.analysis || "");
                // 개별회사 등락률은 소수점 1자리
                const chg1 = r.change_pct < 0 ? `△${Math.abs(r.change_pct).toFixed(1)}%` : `${r.change_pct.toFixed(1)}%`;
                lines.push(`- ${r.name} (${chg1}): ${analysis}`);
            }
        }

        return lines.join("\n");
    }

    function updateEmailCopyPreview() {
        const html = buildEmailTextPreviewHTML();
        if (!html) return;
        const section = document.getElementById("emailCopySection");
        const preview = document.getElementById("emailCopyPreview");
        if (section && preview) {
            preview.innerHTML = html;
            // 클립보드 복사용 plain text 저장
            preview.dataset.plainText = buildEmailTextPreviewPlain();
            section.style.display = "block";
        }
    }

    // Copy email text to clipboard (HTML rich text + plain fallback)
    document.addEventListener("click", async (e) => {
        if (e.target && e.target.id === "copyEmailBtn") {
            const preview = document.getElementById("emailCopyPreview");
            if (!preview) return;
            const btn = e.target;
            const originalText = btn.textContent;

            // Build rich HTML clipboard content
            const innerHtml = preview.innerHTML;
            const clipboardHtml = `<div style="font-family:'맑은고딕',Malgun Gothic,Arial,sans-serif;font-size:10pt;background:transparent;color:#000000;">${innerHtml}</div>`;
            const plainText = preview.dataset.plainText || preview.textContent || "";

            try {
                const item = new ClipboardItem({
                    "text/html": new Blob([clipboardHtml], { type: "text/html" }),
                    "text/plain": new Blob([plainText], { type: "text/plain" }),
                });
                await navigator.clipboard.write([item]);
                btn.textContent = "복사 완료!";
                btn.style.background = "#10b981";
                setTimeout(() => {
                    btn.textContent = originalText;
                    btn.style.background = "";
                }, 2000);
            } catch (err) {
                // Fallback to plain text
                try {
                    await navigator.clipboard.writeText(plainText);
                    btn.textContent = "복사 완료!";
                    btn.style.background = "#10b981";
                    setTimeout(() => {
                        btn.textContent = originalText;
                        btn.style.background = "";
                    }, 2000);
                } catch (fallbackErr) {
                    showError("복사에 실패했습니다.");
                }
            }
        }
    });

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
        const customQuery = "";

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
                            // Show email send button (don't auto-send, McAfee blocks POST)
                            if (sendEmailBtn && currentResults.length > 0) {
                                sendEmailBtn.style.display = "inline-flex";
                            }

                            // Update email copy preview
                            updateEmailCopyPreview();

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
            const cls = idx.error ? "error" : idx.is_closed ? "neutral" : chg > 0 ? "positive" : chg < 0 ? "negative" : "neutral";
            const sign = chg > 0 ? "+" : "";
            let changeText;
            if (idx.error) {
                changeText = "오류";
            } else if (idx.is_closed) {
                changeText = "휴장";
            } else {
                changeText = `${sign}${chg.toFixed(2)}%`;
            }
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
                    let titleHtml;
                    if (a.source === "Gmail 메모" && a.snippet) {
                        // Gmail: show email body (snippet) as the main content
                        titleHtml = `<span style="color:#c8d0da;font-weight:500;">${escapeHtml(a.title)}</span>`
                            + `<div style="margin-top:4px;padding:6px 8px;background:#1a1a2e;border-left:2px solid #555;font-size:0.83em;color:#a0aab8;white-space:pre-wrap;max-height:120px;overflow-y:auto;">${escapeHtml(a.snippet)}</div>`;
                    } else {
                        titleHtml = a.link
                            ? `<a href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.title)}</a>`
                            : escapeHtml(a.title);
                    }
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
        analysisResults.insertAdjacentHTML("beforeend", html);

        // Scroll to show new results (only after first batch to avoid disruption)
        if (currentResults.length <= (results?.length || 0) + 3) {
            const lastCard = analysisResults.querySelector(".result-card:last-child");
            if (lastCard) lastCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
