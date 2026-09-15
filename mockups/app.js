(() => {
  "use strict";

  const data = window.GEO_MOCK_DATA;
  const screen = document.getElementById("screen");
  const projectSelect = document.getElementById("project-select");
  const navigation = document.getElementById("navigation");
  const drawer = document.getElementById("drawer");
  const drawerBackdrop = document.getElementById("drawer-backdrop");
  const modal = document.getElementById("modal");
  const modalBackdrop = document.getElementById("modal-backdrop");
  const allowedViews = new Set(["projects", "dashboard", "chat", "control-plane", "cms-updates", "integrations"]);
  const CMS_STATUSES = ["Needs review", "Approved", "In progress", "Ready to publish", "Editorial review", "Published", "Rejected"];
  let overlayTrigger = null;

  const savedProject = sessionStorage.getItem("geo-mock-project");
  const state = {
    projectId: data.projects.some(project => project.id === savedProject) ? savedProject : data.projects[0].id,
    projectSelected: data.projects.some(project => project.id === savedProject),
    view: readView(),
    projectSessions: {},
    get chat() { return this.projectSessions[this.projectId].chat; },
    set chat(value) {
      const session = this.projectSessions[this.projectId];
      session.chat = value;
      if (!session.chats.some(chat => chat.id === value.id)) session.chats.unshift(value);
    },
    get activeRun() { return this.projectSessions[this.projectId].activeRun; },
    set activeRun(value) { this.projectSessions[this.projectId].activeRun = value; },
    get runTimer() { return this.projectSessions[this.projectId].runTimer; },
    set runTimer(value) { this.projectSessions[this.projectId].runTimer = value; },
    selectedRecommendations: new Set(),
    resultFilters: { priority: "All", source: "All", effort: "All", status: "All" },
    cmsFilter: "all",
    cmsSelectedItemId: null,
    cmsBundles: {},
    expandedUpdateId: null,
    mobileNavOpen: false
  };

  initialise();

  function initialise() {
    document.querySelectorAll(".nav-item").forEach(button => {
      button.querySelector(".nav-icon").innerHTML = icon(button.dataset.view);
    });
    const allProjects = document.createElement("option");
    allProjects.value = "projects";
    allProjects.textContent = "All projects";
    projectSelect.append(allProjects);
    for (const project of data.projects) {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.name;
      projectSelect.append(option);
      state.cmsBundles[project.id] = clone(project.cmsBundles[0]);
      state.cmsBundles[project.id].items = state.cmsBundles[project.id].items.map(createCmsItem);
      state.cmsBundles[project.id].brandVersions = brandVersions(project);
      const welcomeChat = createChat(project);
      const previousChats = project.runs.filter(run => run.status === "Completed").map(run => ({
        ...createChat(project), goal:run.goal, scope:run.scope, stage:"results", date:run.started,
        sourceRun:run.id, messages:[
          {speaker:"user",text:run.goal},
          {speaker:"agent",text:`The saved demo analysis for ${run.scope} is ready. We used WebIQ grounding and the customer brand documents. Ask about the evidence or continue reviewing the suggested updates.`}
        ]
      }));
      state.projectSessions[project.id] = {
        chat: welcomeChat, chats:[welcomeChat,...previousChats], activeRun: null, runTimer: null,
        history: [], bundles: [], draftTimer: null, scenario: "success",
        integrationGroups: { analytics: false, cms: false, iq: true }, selectedFiles: []
      };
    }
    projectSelect.value = state.projectId;
    bindGlobalEvents();
    render();
  }

  function bindGlobalEvents() {
    window.addEventListener("hashchange", () => {
      state.view = readView();
      closeMobileNav();
      render();
    });

    projectSelect.addEventListener("change", event => {
      if (event.target.value === "projects") {
        navigate("projects");
        return;
      }
      state.projectId = event.target.value;
      state.projectSelected = true;
      sessionStorage.setItem("geo-mock-project", state.projectId);
      state.selectedRecommendations.clear();
      state.expandedUpdateId = null;
      state.cmsSelectedItemId = null;
      state.cmsFilter = "all";
      closeDrawer();
      toast(`Switched to ${project().name}.`);
      if (state.view === "projects") navigate("dashboard");
      else render();
    });

    document.getElementById("mobile-menu").addEventListener("click", () => {
      state.mobileNavOpen = !state.mobileNavOpen;
      navigation.classList.toggle("open", state.mobileNavOpen);
      document.getElementById("mobile-menu").setAttribute("aria-expanded", String(state.mobileNavOpen));
    });

    navigation.addEventListener("click", event => {
      const button = event.target.closest("[data-view]");
      if (button) navigate(button.dataset.view);
    });

    document.getElementById("help-button").addEventListener("click", openModal);
    document.getElementById("modal-close").addEventListener("click", closeModal);
    modalBackdrop.addEventListener("click", closeModal);
    document.getElementById("drawer-close").addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);

    document.addEventListener("keydown", event => {
      if (event.target.id === "chat-input" && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        event.target.form.requestSubmit();
        return;
      }
      const overlay = !drawer.hidden ? drawer : !modal.hidden ? modal : null;
      if (overlay && event.key === "Tab") {
        const focusable = [...overlay.querySelectorAll('button:not(:disabled), a[href], input, textarea, select, [tabindex="0"]')]
          .filter(element => element.getClientRects().length);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
      if (event.key !== "Escape") return;
      if (!drawer.hidden) closeDrawer();
      if (!modal.hidden) closeModal();
      const chatScreen = document.querySelector(".chat-screen.history-open");
      if (chatScreen) {
        chatScreen.classList.remove("history-open");
        document.querySelector(".history-mobile-toggle").setAttribute("aria-expanded", "false");
        document.querySelector(".history-mobile-toggle").focus();
      }
      closeMobileNav();
    });

    screen.addEventListener("click", handleClick);
    screen.addEventListener("submit", handleSubmit);
    screen.addEventListener("change", handleChange);
    screen.addEventListener("input", handleInput);
    screen.addEventListener("toggle", event => {
      const category = event.target.dataset.integrationGroup;
      if (category) state.projectSessions[state.projectId].integrationGroups[category] = event.target.open;
    }, true);
    document.getElementById("drawer-content").addEventListener("click", handleClick);
  }

  function readView() {
    const requested = location.hash.replace(/^#\/?/, "");
    return allowedViews.has(requested) ? requested : "projects";
  }

  function navigate(view) {
    if (!allowedViews.has(view)) return;
    if (location.hash === `#/${view}`) {
      state.view = view;
      render();
      return;
    }
    location.hash = `#/${view}`;
  }

  function project() {
    return data.projects.find(item => item.id === state.projectId);
  }

  function resetChat() {
    state.chat = createChat(project());
  }

  function createChat(current) {
    return {
      id: crypto.randomUUID(),
      date: "This session",
      stage: "goal",
      goal: "",
      scope: "",
      scopeType: "",
      messages: [
        {
          speaker: "agent",
          text: `Welcome to ${current.name}. What GEO outcome would you like to improve? I will ground the work with WebIQ, use Clarity when it is configured, and reference the approved Microsoft IQ brand sources before proposing changes.`
        }
      ]
    };
  }

  function render() {
    const previousInput = document.getElementById("chat-input");
    const restoreInputFocus = previousInput && document.activeElement === previousInput;
    const selection = previousInput ? [previousInput.selectionStart, previousInput.selectionEnd] : null;
    const previousStream = document.getElementById("messages");
    const scrollPosition = previousStream?.scrollTop || 0;
    const nearBottom = !previousStream || previousStream.scrollHeight - previousStream.scrollTop - previousStream.clientHeight < 100;
    if (!state.projectSelected && state.view !== "projects") {
      state.view = "projects";
      history.replaceState(null, "", "#/projects");
    }
    const choosingProject = state.view === "projects";
    if (choosingProject) {
      state.projectSelected = false;
      sessionStorage.removeItem("geo-mock-project");
    }
    navigation.hidden = choosingProject;
    document.getElementById("mobile-menu").hidden = choosingProject;
    document.querySelector(".app-shell").classList.toggle("project-chooser", choosingProject);
    document.querySelector(".app-shell").classList.toggle("chat-workspace", state.view === "chat");
    document.querySelector(".main-content").classList.toggle("is-chat", state.view === "chat");
    updateNavigation();
    projectSelect.value = state.view === "projects" ? "projects" : state.projectId;
    const views = {
      projects: renderProjects,
      dashboard: renderDashboard,
      chat: renderChat,
      "control-plane": renderControlPlane,
      "cms-updates": renderCmsUpdates,
      integrations: renderIntegrations
    };
    screen.innerHTML = views[state.view]();
    const input = document.getElementById("chat-input");
    if (restoreInputFocus && input) {
      input.focus({ preventScroll: true });
      input.setSelectionRange(...selection);
    }
    const stream = document.getElementById("messages");
    if (stream) stream.scrollTop = state.chat.stage === "goal" ? 0 : nearBottom ? stream.scrollHeight : scrollPosition;
    document.title = `${viewTitle(state.view)} | Microsoft GEO`;
  }

  function updateNavigation() {
    for (const button of document.querySelectorAll(".nav-item")) {
      button.classList.toggle("active", button.dataset.view === state.view);
      button.setAttribute("aria-current", button.dataset.view === state.view ? "page" : "false");
    }
    const active = Object.values(state.projectSessions).filter(session =>
      session.activeRun?.status === "Running" || session.draftTimer).length;
    const activeCount = document.getElementById("active-run-count");
    activeCount.hidden = !active;
    activeCount.textContent = active ? String(active) : "";
    const bundle = state.cmsBundles[state.projectId];
    const reviewCount = bundle ? bundle.items.filter(item => item.decision === "pending").length : 0;
    document.getElementById("review-count").textContent = String(reviewCount);
  }

  function viewTitle(view) {
    return {
      projects: "Projects",
      dashboard: "Dashboard",
      chat: "Chat",
      "control-plane": "Control Plane",
      "cms-updates": "CMS Updates",
      integrations: "Integrations"
    }[view];
  }

  function screenHeader(eyebrow, title, description, actions = "") {
    return `
      <header class="screen-header">
        <div>
          <p class="eyebrow">${escapeHtml(eyebrow)}</p>
          <h1>${escapeHtml(title)}</h1>
          <p>${escapeHtml(description)}</p>
        </div>
        ${actions ? `<div class="screen-actions">${actions}</div>` : ""}
      </header>`;
  }

  function renderProjects() {
    return `
      <section class="screen">
        ${screenHeader("Your workspace", "Projects", "Choose a project to open its dashboard.")}
        <div class="grid two">
          ${data.projects.map(renderProjectCard).join("")}
        </div>
      </section>`;
  }

  function renderDashboard() {
    const current = project();
    return `
      <section class="screen">
        ${screenHeader(
          "Project dashboard",
          `${current.name} overview`,
          "Track your GEO goals, opportunities, and content updates."
        )}

        <div class="stack dashboard-overview">
          ${renderScoreTrend(current)}
          <section class="dashboard-opportunities" aria-labelledby="opportunities-title">
            <h2 id="opportunities-title">Opportunities and content</h2>
            <div class="grid three">
              ${metric("Open opportunities", current.metrics.opportunities, "Evidence-backed recommendations")}
              ${metric("Awaiting CMS review", state.cmsBundles[current.id].items.filter(item => item.decision === "pending").length, `${current.cms.type} change items`)}
              ${metric("Published this month", current.metrics.publishedThisMonth, "Human-approved updates")}
            </div>
          </section>
          <section class="card dashboard-context">
            <div class="card-heading"><h2>Project overview</h2>${pill(current.health, current.health === "On track" ? "green" : "amber")}</div>
            <div class="summary-line">
              <span class="status-dot success" aria-hidden="true"></span>
              <span><strong>Current goal</strong><small>${escapeHtml(current.activeGoal)}</small></span>
            </div>
            <div class="summary-line">
              <span class="status-dot ${current.clarity.status === "connected" ? "success" : "warning"}" aria-hidden="true"></span>
              <span><strong>Project context</strong><small>WebIQ grounding is available. Clarity is ${current.clarity.status === "connected" ? "connected" : "not configured"}. Microsoft IQ has ${current.iq.sources.length} approved brand sources.</small></span>
            </div>
            <div class="summary-line">
              <span class="status-dot success" aria-hidden="true"></span>
              <span><strong>Content operations</strong><small>${escapeHtml(current.cms.type)} / ${escapeHtml(current.cms.status)}. Current bundle: ${escapeHtml(state.cmsBundles[current.id].status)}.</small></span>
            </div>
          </section>

        </div>
      </section>`;
  }

  function renderScoreTrend(current) {
    const { scores, startDate } = current.scoreTrend;
    const first = scores[0];
    const last = scores[scores.length - 1];
    const increase = ((last - first) / first * 100).toFixed(1);
    const minimum = Math.max(0, Math.floor((Math.min(...scores) - 10) / 10) * 10);
    const maximum = Math.min(100, Math.ceil((Math.max(...scores) + 10) / 10) * 10);
    const width = 1000;
    const height = 260;
    const points = scores.map((score, index) => ({
      x: index / (scores.length - 1) * width,
      y: (maximum - score) / (maximum - minimum) * height
    }));
    let line = `M ${points[0].x} ${points[0].y}`;
    for (let index = 1; index < points.length; index++) {
      const start = points[index - 1];
      const end = points[index];
      const before = points[Math.max(0, index - 2)];
      const after = points[Math.min(points.length - 1, index + 1)];
      const step = (end.x - start.x) / 3;
      // Bound the cubic handles to prevent smoothing from inventing peaks or dips.
      const bound = value => Math.max(Math.min(start.y, end.y), Math.min(Math.max(start.y, end.y), value));
      const controlStart = bound(start.y + (end.y - before.y) / (end.x - before.x) * step);
      const controlEnd = bound(end.y - (after.y - start.y) / (after.x - start.x) * step);
      line += ` C ${start.x + step} ${controlStart}, ${end.x - step} ${controlEnd}, ${end.x} ${end.y}`;
    }
    const area = `${line} L ${width} ${height} L 0 ${height} Z`;
    const date = index => new Date(Date.parse(`${startDate}T00:00:00Z`) + index * 86400000);
    const label = index => date(index).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
    const ticks = Array.from({ length: (maximum - minimum) / 10 + 1 }, (_, index) => maximum - index * 10);
    const lastY = points[points.length - 1].y / height * 100;
    return `
      <section class="card score-trend" aria-labelledby="score-trend-title">
        <div class="score-trend-heading">
          <div><h2 id="score-trend-title">${icon("trend")} GEO performance</h2><p>Building your visibility in AI-powered discovery</p></div>
          <span class="chart-period">Past 30 days <span>${label(0)} - ${label(scores.length - 1)} ${date(scores.length - 1).getUTCFullYear()}</span></span>
        </div>
        <div class="score-headline">
          <div class="score-current"><span>GEO score</span><strong>${last}<small>/100</small></strong></div>
          <div class="score-growth"><span>${icon("trend")} +${increase}%</span><small>vs. 30 days ago <b>+${Number((last - first).toFixed(1))} points</b></small></div>
        </div>
        <figure class="score-chart">
          <div class="score-axis" aria-hidden="true">${ticks.map(tick => `<span style="top:${(maximum - tick) / (maximum - minimum) * 100}%">${tick}</span>`).join("")}</div>
          <div class="score-plot">
            <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-labelledby="chart-title chart-description">
              <title id="chart-title">${escapeHtml(current.name)} GEO score over 30 days</title>
              <desc id="chart-description">Synthetic daily scores from ${first} on ${label(0)} to ${last} on ${label(scores.length - 1)}. Increase of ${increase} percent. Displayed axis range ${minimum} to ${maximum}; the score scale is 0 to 100.</desc>
              <defs><linearGradient id="score-area-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0078d4" stop-opacity=".20"/><stop offset="100%" stop-color="#0078d4" stop-opacity=".01"/></linearGradient></defs>
              ${ticks.map(tick => `<line x1="0" x2="${width}" y1="${(maximum - tick) / (maximum - minimum) * height}" y2="${(maximum - tick) / (maximum - minimum) * height}" class="score-grid-line"/>`).join("")}
              <path d="${area}" fill="url(#score-area-fill)"/>
              <path class="score-curve" d="${line}" fill="none" stroke="#0078d4" stroke-width="3" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
            </svg>
            <span class="score-endpoint" style="top:${lastY}%" aria-hidden="true"></span>
            <span class="score-end-label" style="top:${lastY}%" aria-hidden="true">${last}</span>
          </div>
          <div class="score-dates" aria-hidden="true">${[0, 6, 12, 18, 24, 30].map(index => `<span>${label(index)}</span>`).join("")}</div>
          <figcaption><span><i></i> Daily GEO score</span><span>Synthetic data &middot; Score out of 100</span></figcaption>
        </figure>
        <details class="score-data"><summary>View daily scores</summary><div class="table-scroll"><table><caption>Illustrative daily GEO scores. No live analytics.</caption><thead><tr><th scope="col">Date</th><th scope="col">GEO score</th></tr></thead><tbody>${scores.map((score, index) => `<tr><td>${label(index)}</td><td>${score}</td></tr>`).join("")}</tbody></table></div></details>
      </section>`;
  }

  function renderProjectCard(item) {
    return `
      <article class="card project-card">
        <div class="project-title">
          <span class="project-avatar" style="background:${item.colour}">${escapeHtml(item.initials)}</span>
          <div>
            <h2>${escapeHtml(item.name)}</h2>
            <small>${escapeHtml(item.domain)}</small>
          </div>
        </div>
        <div class="project-actions">
          <button class="button primary" type="button" data-action="select-project" data-project="${item.id}">
            Open project
          </button>
        </div>
      </article>`;
  }

  function renderChat() {
    const current = project();
    const welcome = state.chat.stage === "goal";
    const chats = state.projectSessions[current.id].chats;
    return `
      <section class="chat-screen">
          <aside class="conversation-history" aria-label="Project chat history">
            <div class="history-heading"><h2>Conversations</h2><span>${chats.length}</span><button class="icon-button history-close" type="button" data-action="toggle-chat-history" aria-label="Close conversations">&times;</button></div>
            <button class="new-chat-button" type="button" data-action="reset-chat">${icon("compose")} New chat ${icon("plus")}</button>
            <label class="history-search" for="chat-search">${icon("search")}<input id="chat-search" type="search" placeholder="Search chats" autocomplete="off"></label>
            <nav aria-label="Saved conversations">
              ${["This session", "Earlier"].map(group => `
                <div class="history-group"><h3>${group === "This session" ? "Today" : group}</h3>
                ${chats.filter(chat => group === "This session" ? chat.date === group : chat.date !== "This session").map(chat => `
                <button class="chat-history-item ${chat.id === state.chat.id ? "selected" : ""}" type="button" data-action="open-chat" data-id="${chat.id}" aria-current="${chat.id === state.chat.id ? "true" : "false"}">
                  ${icon("chat")}
                  <span title="${escapeAttribute(chat.goal || "New conversation")}"><strong>${escapeHtml(chat.goal || "New conversation")}</strong><small>${chat.stage === "running" ? "Agents working" : escapeHtml(chat.date)}</small></span>
                  ${chat.stage === "running" ? '<span class="status-dot working" aria-label="Working"></span>' : ""}
                </button>`).join("")}
                </div>`).join("")}
            </nav>
            <p id="history-empty" class="small muted" hidden>No conversations found.</p>
            <p class="history-footnote">${icon("lock")} Private to this demo project<br><span>Refresh resets demo conversations.</span></p>
          </aside>
          <section class="conversation-canvas">
            <header class="conversation-toolbar">
              <button class="history-mobile-toggle icon-button" type="button" data-action="toggle-chat-history" aria-label="Show conversations" aria-expanded="false">${icon("panel")}</button>
              <div class="conversation-heading"><span class="agent-symbol">${icon("spark")}</span><span><strong>GEO assistant</strong><small>${escapeHtml(current.name)}</small></span></div>
              <div class="conversation-toolbar-actions">
                ${state.chat.stage === "running" ? pill("Agents working", "blue") : ""}
                <button class="button ghost" type="button" data-action="chat-context">${icon("sources")} <span>Sources</span></button>
              </div>
            </header>
            <div class="agent-surfaces" role="group" aria-label="Planned agent surfaces. These channels are not connected in this prototype.">
              <span class="agent-surfaces-label">Agent surfaces</span>
              <span class="surface-badge teams-surface" title="Microsoft Teams: planned channel, not connected"><span class="teams-symbol" aria-hidden="true">T</span>Teams</span>
              <span class="surface-badge" title="Custom Web Apps: conceptual host surface">${icon("web-app")}Custom Web Apps</span>
              <span class="surface-badge" title="MCP: planned connection, not connected">${icon("integrations")}MCP</span>
              <span class="surfaces-status">Planned</span>
            </div>
            <div class="conversation-stream ${welcome ? "welcome-stream" : ""}" id="messages">
              ${welcome ? renderChatPrompt() : `
                <div class="thread">${state.chat.messages.map(renderMessage).join("")}${renderChatPrompt()}</div>
                ${state.chat.stage === "results" ? `<div class="chat-results">${renderResults()}</div>` : ""}
              `}
            </div>
            <div class="composer-dock">
              ${renderComposer()}
              <p class="conversation-disclaimer">Synthetic demo. Review AI suggestions before using them.</p>
            </div>
          </section>
      </section>`;
  }

  function renderMessage(message) {
    const user = message.speaker === "user";
    return `
      <div class="thread-message ${user ? "from-user" : "from-agent"}">
        ${user ? "" : `<span class="thread-avatar">${icon("spark")}</span>`}
        <div class="thread-bubble">
          <div class="thread-speaker">${user ? "You" : "GEO assistant"}</div>
          <p>${escapeHtml(message.text)}</p>
        </div>
      </div>`;
  }

  function renderChatPrompt() {
    const current = project();
    if (state.chat.stage === "goal") {
      return `
        <div class="chat-welcome">
          <span class="welcome-symbol">${icon("spark")}</span>
          <span class="welcome-project">${escapeHtml(current.name)} workspace</span>
          <h1>What would you like<br>to improve today?</h1>
          <p>Turn your GEO goals into evidence-backed<br class="desktop-break"> recommendations and brand-ready content.</p>
          <div class="welcome-suggestions">
            ${data.goals.map((goal,index) => `<button class="suggestion-card" type="button" data-action="quick-goal" data-value="${escapeAttribute(goal)}">
              <span class="suggestion-icon">${icon(["trend","search","sources","spark"][index])}</span>
              <strong>${escapeHtml(goal)}</strong>${icon("arrow")}
            </button>`).join("")}
          </div>
          <div class="welcome-grounding"><span class="status-dot success"></span> Grounded in WebIQ <span class="context-separator">&middot;</span> Your customer knowledge ${icon("lock")}</div>
        </div>`;
    }
    if (state.chat.stage === "confirm") {
      return `
        <div class="message">
          <span class="message-avatar">GEO</span>
          <div class="message-bubble">
            <div class="message-speaker">Run confirmation</div>
            <p>I have interpreted the request as follows. Review the context before starting.</p>
            <div class="confirmation">
              <div class="confirmation-row"><span>Goal</span><strong>${escapeHtml(state.chat.goal)}</strong></div>
              <div class="confirmation-row"><span>Scope</span><strong>${escapeHtml(state.chat.scope)} (${escapeHtml(state.chat.scopeType)})</strong></div>
              <div class="confirmation-row"><span>Grounding</span><strong>WebIQ external evidence</strong></div>
              <div class="confirmation-row"><span>Analytics</span><strong>${current.clarity.status === "connected" ? `Clarity / ${escapeHtml(current.clarity.projectName)}` : "Clarity unavailable, workflow will continue without on-site analytics"}</strong></div>
              <div class="confirmation-row"><span>Brand context</span><strong>${current.iq.sources.map(source => `${escapeHtml(source.name)} ${escapeHtml(source.version)}`).join("<br>")}</strong></div>
              <div class="confirmation-row"><span>CMS target</span><strong>${escapeHtml(current.cms.type)} / ${escapeHtml(current.cms.environment)} (changes require separate review)</strong></div>
            </div>
            <div class="button-row" style="margin-top:12px">
              <button class="button primary" type="button" data-action="start-analysis">Start analysis</button>
              <button class="button" type="button" data-action="edit-scope">Edit scope</button>
            </div>
          </div>
        </div>`;
    }
    if (state.chat.stage === "running") {
      return `
        <div class="message">
          <span class="message-avatar">GEO</span>
          <div class="message-bubble">
            <div class="message-speaker">Run in progress</div>
            <p>The agents are working. You can leave this conversation and follow detailed checkpoints in Control Plane.</p>
            <button class="button primary" type="button" data-view="control-plane">Open Control Plane</button>
          </div>
        </div>`;
    }
    if (state.chat.stage === "failed" || state.chat.stage === "cancelled") {
      return `<div class="callout amber"><strong>${state.chat.stage === "failed" ? "Analysis stopped" : "Run cancelled"}</strong><p>No new recommendations or CMS changes were created. Review checkpoints or start a new conversation.</p><button class="button" data-view="control-plane">Review checkpoints</button></div>`;
    }
    return "";
  }

  function renderComposer() {
    const config = {
      goal: { placeholder: "Describe the GEO outcome you want to improve...", label: "Enter a GEO goal" },
      scope: { placeholder: "Enter a domain or page URL, for example contoso.example/products/trail-shoe", label: "Enter a domain or URL" },
      running: { placeholder: "Ask for progress or explain the brand context...", label: "Ask about this run" },
      results: { placeholder: "Ask about evidence, brand guidelines, or next actions...", label: "Discuss the results" }
    }[state.chat.stage];
    if (!config) return "";
    return `
      <form class="prompt-composer" id="chat-form">
        <label class="sr-only" for="chat-input">${escapeHtml(config.label)}</label>
        <textarea id="chat-input" name="message" maxlength="1000" placeholder="${escapeAttribute(config.placeholder)}" required>${escapeHtml(state.chat.draft || "")}</textarea>
        <p class="error-text" id="chat-error" hidden></p>
        <div class="prompt-actions">
          <button class="prompt-context" type="button" data-action="chat-context">${icon("sources")} Customer context</button>
          <span class="prompt-key-hint">Enter to send</span>
          <button class="send-prompt" type="submit" aria-label="Send message">${icon("up")}</button>
        </div>
      </form>`;
  }

  function renderControlPlane() {
    const current = project();
    const activeForProject = state.activeRun && state.activeRun.projectId === current.id;
    return `
      <section class="screen">
        ${screenHeader(
          "Project operations",
          "Control Plane",
          "Follow the current execution and review this project's history."
        )}

        ${activeForProject ? renderActiveRun(state.activeRun) : ""}
        <section class="card" style="margin-top:16px">
          <div class="card-heading"><h2>Project History</h2><span class="small muted">${current.runs.length + state.projectSessions[current.id].history.length + (activeForProject ? 1 : 0)} runs</span></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Run</th><th>Goal and scope</th><th>Status</th><th>Agents</th><th>Duration</th><th>Clarity</th><th>CMS outcome</th></tr></thead>
              <tbody>
                ${activeForProject ? renderActiveRunTableRow(state.activeRun) : ""}
                ${[...state.projectSessions[current.id].history, ...current.runs].map(renderRunRow).join("")}
              </tbody>
            </table>
          </div>
        </section>
      </section>`;
  }

  function renderActiveRun(run) {
    const currentStep = run.agents.find(agent => agent.state === "working");
    const completed = run.agents.filter(agent => ["completed", "skipped"].includes(agent.state)).length;
    return `
      <section class="card accent">
        <div class="card-heading">
          <div>
            <p class="eyebrow">Active execution &middot; ${escapeHtml(run.id)}</p>
            <h2>${escapeHtml(run.goal)}</h2>
            <p class="small muted">${escapeHtml(run.scope)}</p>
          </div>
          ${pill(run.status, run.status === "Completed" ? "green" : run.status === "Running" ? "blue" : "amber")}
        </div>
        <div class="grid four" style="margin-bottom:16px">
          ${metric("Current stage", currentStep ? currentStep.name : run.status === "Completed" ? "Recommendations ready" : run.status, `${completed}/${run.agents.length} agents resolved`)}
          <section class="card flat metric-card"><div class="metric-label">Elapsed</div><div class="metric-value" data-run-elapsed aria-live="off">${formatDuration(runElapsed(run))}</div><small>${run.fastForwarded ? "Demo fast-forwarded" : `Expected demo duration: ${formatDuration(run.expectedDurationMs)}`}</small></section>
          ${metric("Evidence", run.agents.find(agent => agent.id === "webiq").state === "completed" ? "23 sources" : "Preparing", "WebIQ grounded passages")}
          ${metric("Brand context", run.agents.find(agent => agent.id === "brand").state === "completed" ? "2 sources" : "Queued", "Word and SharePoint")}
        </div>
        <div class="agent-grid">
          ${run.agents.map(renderAgentRow).join("")}
        </div>
        <div class="run-actions" style="margin-top:15px">
          <button class="button" type="button" data-action="show-run-details">View checkpoints</button>
          <button class="button" type="button" data-view="chat">View in Chat</button>
          ${run.status === "Running" ? `<button class="button" type="button" data-action="skip-run">Skip to results (demo)</button><button class="button danger" type="button" data-action="cancel-run">Cancel run</button>` : run.status === "Completed" ? `<button class="button primary" type="button" data-action="view-results">View results</button>` : ""}
        </div>
      </section>`;
  }

  function renderAgentRow(agent) {
    const states = {
      queued: ["Queued", ""],
      working: ["Working", "working"],
      completed: ["Completed", "completed"],
      skipped: ["Skipped", "skipped"],
      failed: ["Failed", "failed"]
    };
    const [label, css] = states[agent.state];
    return `
      <div class="agent-row ${css}" data-agent-id="${agent.id}">
        <div class="agent-title">
          <span class="status-dot ${agent.state === "working" ? "working" : agent.state === "completed" ? "success" : agent.state === "failed" ? "danger" : agent.state === "skipped" ? "warning" : ""}" aria-hidden="true"></span>
          <span><strong>${escapeHtml(agent.name)}</strong><small>${escapeHtml(agent.description)}</small></span>
        </div>
        <div class="progress-track" role="progressbar" aria-label="${escapeAttribute(agent.name)} progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${agent.progress || 0}" aria-live="off"><span style="width:${agent.progress || 0}%"></span></div>
        <span class="agent-state">${escapeHtml(label)}</span>
        <small data-agent-time aria-live="off">${agent.state === "skipped" ? "Not configured" : `${formatDuration(agent.elapsedMs || 0)} / ~${agent.duration}`}</small>
      </div>`;
  }

  function renderActiveRunTableRow(run) {
    return `
      <tr>
        <td><button class="button ghost" type="button" data-action="show-run-details" aria-label="View details for ${escapeAttribute(run.id)}">${escapeHtml(run.id)}</button><br><small>15 Sep 2026, 12:36</small></td>
        <td>${escapeHtml(run.goal)}<br><small>${escapeHtml(run.scope)}</small></td>
        <td>${pill(run.status, run.status === "Completed" ? "green" : "blue")}</td>
        <td>${run.agents.filter(agent => ["completed", "skipped"].includes(agent.state)).length}/${run.agents.length}</td>
        <td data-run-elapsed aria-live="off">${formatDuration(runElapsed(run))}</td>
        <td>${project().clarity.status === "connected" ? "Used" : "Skipped"}</td>
        <td>${run.status === "Completed" ? "Ready to prepare" : "Pending results"}</td>
      </tr>`;
  }

  function renderRunRow(run) {
    const statusColour = run.status === "Completed" ? "green" : run.status.includes("Failed") ? "red" : "blue";
    return `
      <tr>
        <td><button class="button ghost" type="button" data-action="open-run" data-run="${escapeAttribute(run.id)}" aria-label="View details for ${escapeAttribute(run.id)}">${escapeHtml(run.id)}</button><br><small>${escapeHtml(run.started)}</small></td>
        <td>${escapeHtml(run.goal)}<br><small>${escapeHtml(run.scope)}</small></td>
        <td>${pill(run.status, statusColour)}</td>
        <td>${escapeHtml(run.agents)}</td>
        <td>${escapeHtml(run.duration)}</td>
        <td>${escapeHtml(run.clarity)}</td>
        <td>${escapeHtml(run.cmsOutcome)}</td>
      </tr>`;
  }

  function renderResults() {
    const current = project();
    const recommendations = filteredRecommendations();
    return `
      <section style="margin-top:22px" id="results">
        ${screenHeader(
          "Completed analysis",
          "Prioritized actions",
          `Recommendations for "${state.chat.goal || current.activeGoal}" are grounded in WebIQ, constrained by Microsoft IQ brand guidance, and enriched with Clarity where available.`,
          `<button class="button" type="button" data-action="export-results">Export action plan</button>`
        )}
        <p class="callout small">Illustrative project fixtures, not measurements of the submitted URL. All excerpts, scores, priorities, and analytics are synthetic. A domain request demonstrates bounded page analysis, not a full-site crawl.</p>
        <div class="result-summary">
          <section class="card score-card">
            <div class="score-ring" style="--score:${current.latestScore}"><strong>${current.latestScore}</strong></div>
            <div><span class="small muted">Synthetic GEO evidence score</span><h2>${current.latestDelta} points</h2><small>Since the prior recurring run</small></div>
          </section>
          ${metric("Recommendations", current.recommendations.length, `${current.recommendations.filter(item => item.priority === "High").length} high priority`)}
          ${metric("Grounded sources", 23, "WebIQ evidence passages")}
          ${metric("Brand checks", `${current.recommendations.filter(item => item.brandAlignment === "Aligned").length}/${current.recommendations.length}`, "Microsoft IQ aligned")}
        </div>

        <div class="callout purple" style="margin:16px 0">
          <strong>Three-layer evidence model</strong>
          <p class="small">WebIQ explains the external discovery landscape. ${current.clarity.status === "connected" ? "Clarity adds on-site behavior." : "Clarity is unavailable for this project."} Microsoft IQ constrains suggestions using approved Word and SharePoint brand guidance.</p>
        </div>

        <div class="filter-row">
          ${filterSelect("priority-filter", "Priority", ["All", "High", "Medium", "Low"], state.resultFilters.priority)}
          ${filterSelect("effort-filter", "Effort", ["All", "Low", "Medium", "High"], state.resultFilters.effort)}
          ${filterSelect("source-filter", "Source", ["All", "WebIQ", "Clarity", "Microsoft IQ"], state.resultFilters.source)}
          ${filterSelect("status-filter", "Status", ["All", "Ready for review", "Proposed", "Discovery", "In backlog"], state.resultFilters.status)}
          <span class="filter-spacer"></span>
          <span class="small muted">${recommendations.length} shown</span>
        </div>

        <div class="recommendation-list">
          ${recommendations.length ? recommendations.map(renderRecommendation).join("") : `<div class="card empty-state"><strong>No recommendations match</strong><p>Change or clear the filters to continue.</p></div>`}
        </div>

        ${state.selectedRecommendations.size ? `<div class="results-handoff">
          <p class="small muted">Selected recommendations will become proposals for review. Preparing a proposal does not change the website.</p>
          <div class="button-row">
            <button class="button" type="button" data-action="clear-selection">Clear selection</button>
            <button class="button primary" type="button" data-action="prepare-cms">Prepare CMS updates</button>
          </div>
        </div>` : ""}
      </section>`;
  }

  function filteredRecommendations() {
    return project().recommendations.filter(item => {
      const priority = state.resultFilters.priority === "All" || item.priority === state.resultFilters.priority;
      const effort = state.resultFilters.effort === "All" || item.effort === state.resultFilters.effort;
      const status = state.resultFilters.status === "All" || item.status === state.resultFilters.status;
      let source = true;
      if (state.resultFilters.source === "Clarity") source = project().clarity.status === "connected";
      if (state.resultFilters.source === "Microsoft IQ") source = Boolean(item.evidence.iq);
      if (state.resultFilters.source === "WebIQ") source = Boolean(item.evidence.webiq);
      return priority && effort && status && source;
    });
  }

  function renderRecommendation(item) {
    const selected = state.selectedRecommendations.has(item.id);
    return `
      <article class="recommendation-card ${selected ? "selected" : ""}">
        <input class="select-box" type="checkbox" aria-label="Select ${escapeAttribute(item.title)}" data-action="select-recommendation" data-id="${item.id}" ${selected ? "checked" : ""}>
        <div class="result-title">
          <span class="priority-badge ${item.priority.toLowerCase()}">${item.rank}</span>
          <div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary)}</p></div>
        </div>
        <div class="result-cell"><span>Impact / effort</span><strong>${escapeHtml(item.impact)} / ${escapeHtml(item.effort)}</strong></div>
        <div class="result-cell"><span>Confidence</span><strong>${escapeHtml(item.confidence)}</strong></div>
        <div class="result-cell"><label for="owner-${item.id}">Owner</label><select id="owner-${item.id}" data-owner="${item.id}">${[...new Set([item.owner,"SEO manager","E-commerce content","Brand team"])].map(owner => `<option>${escapeHtml(owner)}</option>`).join("")}</select><small>${escapeHtml(item.status)}</small></div>
        <div class="result-cell"><span>Brand alignment</span><span class="brand-badge ${item.brandAlignment === "Aligned" ? "" : "review"}">${escapeHtml(item.brandAlignment)}</span></div>
        <div class="result-actions"><button class="button" type="button" data-action="recommendation-details" data-id="${item.id}">Evidence</button><button class="button ghost" type="button" data-action="backlog" data-id="${item.id}">Add to backlog</button></div>
      </article>`;
  }

  function renderIntegrations() {
    const current = project();
    const session = state.projectSessions[current.id];
    const clarityConnected = current.clarity.status === "connected";
    return `
      <section class="screen">
        ${screenHeader(
          "Project configuration",
          "Integrations",
          "Manage the analytics, content systems, and customer knowledge available to your agents."
        )}

        <div class="integration-categories">
          <details class="integration-category" data-integration-group="analytics" ${session.integrationGroups.analytics ? "open" : ""}>
            <summary>
              <span class="category-symbol analytics">${icon("trend")}</span>
              <span class="category-heading"><strong>Analytics</strong><small>Understand visitor behaviour and AI referral traffic</small></span>
              ${pill(clarityConnected ? "1 connected" : "Not connected", clarityConnected ? "green" : "grey")}${icon("chevron")}
            </summary>
          <section class="integration-category-content integration-card">
            <div class="integration-title">
              <img class="connector-logo" src="assets/clarity.svg" alt="Microsoft Clarity" width="36" height="36">
              <div><h2>Microsoft Clarity</h2><small>Optional project analytics</small></div>
              <span style="margin-left:auto">${pill(current.clarity.label, clarityConnected ? "green" : "amber")}</span>
            </div>
            ${clarityConnected ? `
              <dl class="integration-detail">
                <dt>Project</dt><dd>${escapeHtml(current.clarity.projectName)}</dd>
                <dt>Last sync</dt><dd>${escapeHtml(current.clarity.lastSync)}</dd>
                <dt>Signals</dt><dd>${current.clarity.signals.map(escapeHtml).join(", ")}</dd>
                <dt>Run behavior</dt><dd>Automatically included when this project is analysed</dd>
              </dl>
              <div class="button-row"><button class="button" type="button" data-action="test-integration" data-name="Microsoft Clarity">Test connection</button><button class="button danger" type="button" data-action="toggle-clarity">Disconnect (demo)</button></div>
            ` : `
              <div class="callout amber"><strong>Not configured for this workspace</strong><p class="small">Runs continue with WebIQ and Microsoft IQ. Recommendations clearly state that on-site behavior was unavailable.</p></div>
              <button class="button primary" type="button" data-action="toggle-clarity">Connect Clarity (demo)</button>
            `}
          </section>
          </details>
          <details class="integration-category" data-integration-group="cms" ${session.integrationGroups.cms ? "open" : ""}>
            <summary>
              <span class="category-symbol cms">${icon("cms-updates")}</span>
              <span class="category-heading"><strong>Content destinations</strong><small>Prepare and review changes in your content systems</small></span>
              ${pill(`${current.cms.status === "connected" ? 2 : 1} connected`, "green")}${icon("chevron")}
            </summary>
          <section class="integration-category-content">
            ${data.cmsConnectors.map(connector => renderCmsConnector(current, connector)).join("")}
            <p class="file-selection-note">Connector states are simulated. Connecting or disconnecting here changes only this demo.</p>
          </section>
          </details>
          <details class="integration-category" data-integration-group="iq" ${session.integrationGroups.iq ? "open" : ""}>
            <summary>
              <span class="category-symbol iq">${icon("sources")}</span>
              <span class="category-heading"><strong>Microsoft IQ</strong><small>Customer documents and approved brand knowledge</small></span>
              ${pill(`${current.iq.sources.length} sources`, "purple")}${icon("chevron")}
            </summary>
        <section class="integration-category-content integration-card">
          <div class="card-heading">
            <div><h2>Customer knowledge</h2><p class="small muted">Give your agents the context to stay on brand.</p></div>
            <button class="button primary" type="button" data-action="add-iq-files">${icon("plus")} Add Files</button>
          </div>
          <input id="iq-file-input" type="file" accept=".doc,.docx,.pdf,.txt" multiple hidden aria-label="Select customer knowledge files">
          <p class="iq-connection-status">${pill(brandReady(current) ? "Connected" : "Needs attention", brandReady(current) ? "green" : "amber")} Approved demo sources</p>
          <ul class="iq-source-list" aria-label="Documents from Customer IQ">
            ${current.iq.sources.map(renderIqSource).join("")}
          </ul>
          ${session.selectedFiles.length ? `
            <div class="selected-knowledge-files">
              <h3>Selected files <span>${session.selectedFiles.length}</span></h3>
              <ul class="iq-source-list" aria-label="Selected files, not processed">
                ${session.selectedFiles.map(file => `<li class="selected-file">
                  ${sourceLogo(file.type)}
                  <span class="selected-file-copy"><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(file.type)} &middot; Not processed or used by agents</small></span>
                  <button class="icon-button" type="button" data-action="remove-iq-file" data-id="${file.id}" aria-label="Remove ${escapeAttribute(file.name)}">&times;</button>
                </li>`).join("")}
              </ul>
            </div>` : ""}
          <p class="file-selection-note">Word, PDF, or text files. Demo only: filenames stay in this tab; file contents are not read or uploaded. Refresh clears the selection.</p>
          <details class="small"><summary>Demo source controls</summary><div class="button-row" style="margin-top:10px">
            <button class="button" type="button" data-action="refresh-iq">Refresh sources</button>
            <button class="button" type="button" data-action="change-iq">Simulate updated guidance</button>
            <button class="button" type="button" data-action="unavailable-iq">Simulate access unavailable</button>
          </div></details>
        </section>
          </details>
        </div>
        <p class="integration-platform-note">${icon("lock")} WebIQ grounding is always on. It is a shared platform service, not a project integration.</p>
      </section>`;
  }

  function connectorLogo(connector) {
    return `<img class="connector-logo" src="assets/${connector.logo}.svg" alt="${escapeAttribute(connector.type)}" width="36" height="36">`;
  }

  function renderCmsConnector(current, connector) {
    const isPrimary = connector.type === current.cms.type;
    const isStatic = connector.type === current.staticSite.type;
    const config = isPrimary ? current.cms : isStatic ? current.staticSite : null;
    const connected = Boolean(config) && config.status === "connected";
    return `
      <article class="integration-card connector-card ${connected ? "" : "connector-available"}">
        <div class="integration-title">
          ${connectorLogo(connector)}
          <div><h2>${escapeHtml(connector.type)}</h2><small>${escapeHtml(isStatic ? "Static website hosting for new landing pages" : connector.summary)}</small></div>
          <span style="margin-left:auto">${pill(connected ? "Connected" : config ? "Disconnected" : "Available to connect", connected ? "green" : config ? "amber" : "grey")}</span>
        </div>
        ${config ? `
          <dl class="integration-detail">
            <dt>Connection</dt><dd>${escapeHtml(config.connection)}</dd>
            <dt>Environment</dt><dd>${escapeHtml(config.environment)}</dd>
            ${isStatic ? `<dt>Endpoint</dt><dd><code>${escapeHtml(config.endpoint)}</code></dd>` : ""}
            <dt>Last sync</dt><dd>${escapeHtml(config.lastSync)}</dd>
            <dt>Scopes</dt><dd>${config.scopes.map(escapeHtml).join(", ")}</dd>
            <dt>${isStatic ? "Used for" : "Publish rule"}</dt><dd>${isStatic ? "Landing page recommendations are deployed here, not to the primary CMS" : "Separate human approval is required"}</dd>
          </dl>
          <div class="button-row">
            <button class="button" type="button" data-action="test-integration" data-name="${escapeAttribute(connector.type)}">Test connection</button>
            ${isPrimary ? `<button class="button" type="button" data-action="toggle-cms">${connected ? "Disconnect" : "Connect"} (demo)</button>` : ""}
            <button class="button" type="button" data-view="cms-updates">View update queue</button>
          </div>` : `
          <dl class="integration-detail">
            <dt>Supports</dt><dd>${connector.capabilities.map(escapeHtml).join(", ")}</dd>
            <dt>Status</dt><dd>Not configured for ${escapeHtml(current.name)}</dd>
          </dl>
          <button class="button primary" type="button" data-action="connect-connector" data-name="${escapeAttribute(connector.type)}">Connect ${escapeHtml(connector.type)} (demo)</button>`}
      </article>`;
  }

  function renderIqSource(source) {    return `
      <li>
        <button class="iq-source-button" type="button" data-action="iq-source-details" data-id="${source.id}">
          ${sourceLogo(source.type)}
          <span><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(source.type)} &middot; Customer brand guidance</small></span>
          <span class="iq-source-arrow" aria-hidden="true">&#8250;</span>
        </button>
      </li>`;
  }

  function sourceLogo(type) {
    if (!["Word", "SharePoint"].includes(type)) return `<span class="local-file-icon" aria-label="${escapeAttribute(type)} file">${icon("sources")}</span>`;
    return `<img class="iq-source-logo" src="assets/${type === "Word" ? "word" : "sharepoint"}.svg" alt="Microsoft ${escapeAttribute(type)}" width="40" height="40">`;
  }

  function renderCmsUpdates() {
    const current = project();
    const bundle = state.cmsBundles[current.id];
    if (!bundle) {
      return `
        <section class="screen">
          ${screenHeader("Governed content operations", "CMS Updates", `Review evidence-backed changes before the Site Content agent prepares ${current.cms.type} drafts.`)}
          <div class="card empty-state"><strong>No CMS proposals</strong><p>Select recommendations from Chat results to prepare a governed update bundle.</p><button class="button primary" type="button" data-view="chat">Open Chat</button></div>
        </section>`;
    }
    const selected = bundle.items.find(item => item.id === state.cmsSelectedItemId) || null;
    return `
      <section class="screen cms-screen">
        ${screenHeader(
          "Governed content operations",
          "CMS Updates",
          `Review evidence-backed changes before the Site Content agent prepares ${current.cms.type} drafts. Publication always requires a separate human decision.`
        )}
        <div class="cms-workspace">
          ${renderCmsSidebar(current, bundle, selected)}
          <section class="cms-canvas">${renderCmsBundle(bundle, selected)}</section>
        </div>
      </section>`;
  }

  function renderCmsSidebar(current, bundle, selected) {
    const counts = cmsCounts(bundle);
    const visible = bundle.items.filter(item => state.cmsFilter === "all" || itemStatus(bundle, item) === state.cmsFilter);
    return `
      <aside class="update-history" aria-label="CMS update items">
        <div class="history-heading"><h2>Update items</h2><span>${bundle.items.length}</span></div>
        <label class="sidebar-field" for="bundle-select"><span>Review bundle</span>
          <select id="bundle-select">${[bundle, ...state.projectSessions[current.id].bundles].map(item => `<option value="${item.id}">${escapeHtml(item.id)}: ${escapeHtml(item.title)}</option>`).join("")}</select>
        </label>
        <label class="sidebar-field" for="cms-status-filter"><span>Status</span>
          <select id="cms-status-filter">
            <option value="all" ${state.cmsFilter === "all" ? "selected" : ""}>All statuses (${bundle.items.length})</option>
            ${CMS_STATUSES.map(status => `<option value="${status}" ${state.cmsFilter === status ? "selected" : ""} ${counts[status] ? "" : "disabled"}>${status} (${counts[status] || 0})</option>`).join("")}
          </select>
        </label>
        <nav aria-label="Proposed updates">
          <button class="update-nav-item ${selected ? "" : "selected"}" type="button" data-action="open-update" data-id="all" aria-current="${selected ? "false" : "true"}">
            ${icon("sources")}
            <span><strong>All items</strong><small>${visible.length} shown &middot; ${escapeHtml(bundle.status)}</small></span>
          </button>
          ${visible.map(item => {
            const status = itemStatus(bundle, item);
            return `
            <button class="update-nav-item ${selected && selected.id === item.id ? "selected" : ""}" type="button" data-action="open-update" data-id="${item.id}" aria-current="${selected && selected.id === item.id ? "true" : "false"}">
              <span class="status-dot ${statusDotClass(status)}" aria-hidden="true"></span>
              <span title="${escapeAttribute(item.title)}"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.type)} &middot; ${escapeHtml(status)}</small></span>
              ${item.workflow === "editorial" ? `<span class="nav-tag">Editorial</span>` : ""}
            </button>`;
          }).join("")}
          ${visible.length ? "" : `<p class="small muted" style="padding:8px 10px">No items with this status.</p>`}
        </nav>
        <p class="history-footnote">${icon("lock")} Synthetic proposals for this project<br><span>No live CMS content is read or changed.</span></p>
      </aside>`;
  }

  function renderCmsBundle(bundle, selected) {
    const pending = bundle.items.filter(item => item.decision === "pending").length;
    const approved = bundle.items.filter(item => item.decision === "approved").length;
    const rejected = bundle.items.filter(item => item.decision === "rejected").length;
    const shown = selected ? [selected] : bundle.items.filter(item => state.cmsFilter === "all" || itemStatus(bundle, item) === state.cmsFilter);
    return `
      <section class="card bundle-card">
        <div class="bundle-title">
          <div>
            <p class="eyebrow">${escapeHtml(bundle.id)} &middot; Source ${escapeHtml(bundle.sourceRun)}</p>
            <h2>${escapeHtml(bundle.title)}</h2>
            <small>${escapeHtml(bundle.connector)} / ${escapeHtml(bundle.environment)}${bundle.items.some(item => item.type === "Landing page") ? ` &middot; ${escapeHtml(project().staticSite.type)} for landing pages` : ""} &middot; Created ${escapeHtml(bundle.created)}</small>
          </div>
          ${pill(bundle.status, cmsStatusColour(bundle.status))}
        </div>

        ${selected ? `<nav class="cms-breadcrumb" aria-label="Breadcrumb"><button class="button ghost" type="button" data-action="open-update" data-id="all">&#8249; All items</button><span>${escapeHtml(selected.title)}</span></nav>` : `
        <div class="grid three">
          ${metric("Needs decision", pending, "Item-level review")}
          ${metric("Approved", approved, "Included in final bundle")}
          ${metric("Rejected", rejected, "Retained in audit history")}
        </div>

        <div class="callout purple">
          <strong>Brand context bound to this proposal</strong>
          <p class="small">Pinned source versions: ${escapeHtml(bundle.brandVersions || "Not yet reviewed")}. The Site Content agent rechecks these versions before drafting and publication.</p>
        </div>`}

        <div class="update-list">
          ${shown.length ? shown.map(item => renderUpdateItem(bundle, item)).join("") : `<div class="empty-state"><strong>No items with this status</strong><p>Choose another status in the sidebar to inspect the current synthetic workflow.</p></div>`}
        </div>

        ${renderBundleActions(bundle)}
        ${selected ? "" : `<details><summary>Proposal and approval audit trail</summary><ol>${bundle.audit.map(entry => `<li>${escapeHtml(entry)}</li>`).join("")}</ol></details>`}
      </section>`;
  }

  function itemStatus(bundle, item) {
    if (item.decision === "rejected") return "Rejected";
    if (item.delivery === "Published") return "Published";
    if (item.delivery === "Editorial review") return "Editorial review";
    if (item.delivery === "Validated draft") return "Ready to publish";
    if (item.decision !== "approved") return "Needs review";
    return bundle.status === "In progress" ? "In progress" : "Approved";
  }

  function statusDotClass(status) {
    return { "Needs review": "warning", Approved: "success", "In progress": "working", "Ready to publish": "success", "Editorial review": "warning", Published: "success", Rejected: "danger" }[status] || "warning";
  }

  function renderUpdateItem(bundle, item) {
    const expanded = state.expandedUpdateId === item.id;
    const editable = bundle.status === "Needs review" && item.delivery !== "Published";
    const recommendation = project().recommendations.find(rec => rec.id === item.recommendationId);
    const decisionClass = item.decision === "approved" ? "decision-approved" : item.decision === "rejected" ? "decision-rejected" : "";
    const destination = itemDestination(bundle, item);
    return `
      <article class="update-item ${decisionClass}">
        <div class="update-summary">
          <div><h3>${escapeHtml(item.title)}</h3><small>${escapeHtml(item.type)} &middot; ${escapeHtml(item.target)}</small></div>
          <span class="cms-destination">${destinationLogo(destination)} ${escapeHtml(destination.connector)} - ${escapeHtml(destination.environment)}</span>
          ${pill(item.workflow === "editorial" ? "Editorial" : "Routine", item.workflow === "editorial" ? "orange" : "blue")}
          <span>${pill(decisionLabel(item.decision), item.decision === "approved" ? "green" : item.decision === "rejected" ? "red" : "amber")}<br><small>${escapeHtml(item.delivery || "")}</small></span>
          <div class="button-row">
            <button class="button" type="button" data-action="toggle-update" data-id="${item.id}" aria-expanded="${expanded}" aria-controls="changes-${item.id}">${expanded ? "Hide" : "Review"}</button>
          </div>
        </div>
        ${expanded ? `
          <div class="update-details" id="changes-${item.id}">
            <div class="change-overview">
              <h4>What will change</h4>
              <p>${escapeHtml(item.summary)}</p>
              <dl class="change-context">
                <dt>Destination</dt><dd>${escapeHtml(destination.connector)} - ${escapeHtml(destination.environment)}${destination.endpoint ? `<br><code>${escapeHtml(destination.endpoint)}</code>` : ""}</dd>
                <dt>Content item</dt><dd>${escapeHtml(item.target)}</dd>
                <dt>Changes</dt><dd>${item.fields.length} field${item.fields.length === 1 ? "" : "s"} ${item.workflow === "editorial" ? "in a new draft concept" : "in the existing content item"}</dd>
              </dl>
              <p class="small muted">Synthetic before/after values and illustrative field names. No live CMS content has been read or changed.</p>
            </div>
            <div class="field-change-list">
              ${item.fields.map(field => `
                <section class="field-change">
                  <div class="field-change-heading"><h4>${escapeHtml(field.name)}</h4>${pill(field.before ? "Replace value" : "Add field", field.before ? "blue" : "green")}</div>
                  <div class="diff-grid">
                    <section class="diff current"><strong>Before</strong>${renderFieldValue(field.before, field.format)}</section>
                    <section class="diff proposed"><strong>After</strong>${renderFieldValue(field.after, field.format)}</section>
                  </div>
                </section>`).join("")}
            </div>
            ${recommendation ? `<section class="change-rationale"><h4>Why this change</h4><p>${escapeHtml(recommendation.summary)}</p><p class="small muted">${escapeHtml(recommendation.evidence.webiq)}</p><button class="button ghost" type="button" data-action="recommendation-details" data-id="${recommendation.id}">View supporting evidence</button></section>` : ""}
            <div class="brand-reference">
              <span class="source-icon iq">IQ</span>
              <span><strong>${recommendation ? escapeHtml(recommendation.brandSource) : "Microsoft IQ brand guidance"}</strong><br><small>${recommendation ? escapeHtml(recommendation.evidence.iq) : "Approved brand context is required before drafting."}</small></span>
            </div>
            <div class="callout" style="margin-top:12px"><strong>Checks before publishing</strong><p class="small">${escapeHtml(item.validation)}</p></div>
            ${item.delivery ? `<div class="callout green"><strong>Draft preview (synthetic)</strong><p class="draft-preview">${escapeHtml(item.proposed)}</p><small>${escapeHtml(item.delivery)}. ${escapeHtml(destination.connector)} draft; no external content was changed.</small></div>` : ""}
            ${item.workflow === "editorial" ? `<div class="callout amber" style="margin-top:12px"><strong>Additional human workflow</strong><p class="small">Net-new content is routed to copywriter, designer, brand, and final publish approval. It cannot be published directly by the Site Content agent.</p></div>` : ""}
            ${item.workflow === "editorial" && item.delivery === "Editorial review" ? `<div class="button-row">${["copywriter","designer","brand"].map(role => `<button class="button" type="button" data-action="editorial-approve" data-id="${item.id}" data-role="${role}" ${item.reviews?.[role] ? "disabled" : ""}>${item.reviews?.[role] ? "Approved" : "Approve as"} ${role} (demo)</button>`).join("")}</div>` : ""}
            <label class="form-field" style="margin-top:12px"><span>Reviewer note (optional)</span><textarea class="review-note" data-note="${item.id}" ${editable ? "" : "readonly"} placeholder="Add rationale for the decision...">${escapeHtml(item.note || "")}</textarea></label>
            ${editable ? `<div class="decision-actions" style="margin-top:12px">
              <button class="button danger" type="button" data-action="cms-decision" data-id="${item.id}" data-value="rejected">Reject</button>
              <button class="button success" type="button" data-action="cms-decision" data-id="${item.id}" data-value="approved">Approve item</button>
            </div>` : `<p class="small muted" style="margin-top:12px">Decisions and reviewer notes are locked after final bundle approval.</p>`}
          </div>` : ""}
      </article>`;
  }

  function itemDestination(bundle, item) {
    const current = project();
    if (item.type === "Landing page" && current.staticSite) {
      return { connector: current.staticSite.type, environment: current.staticSite.environment, endpoint: current.staticSite.endpoint, logo: "azure" };
    }
    const connector = data.cmsConnectors.find(entry => entry.type === bundle.connector);
    return { connector: bundle.connector, environment: bundle.environment, logo: connector ? connector.logo : null };
  }

  function destinationLogo(destination) {
    if (!destination.logo) return icon("cms-updates");
    return `<img class="destination-logo" src="assets/${destination.logo}.svg" alt="${escapeAttribute(destination.connector)}" width="18" height="18">`;
  }

  function renderFieldValue(value, format) {
    if (!value) return `<p class="empty-field">Not currently present</p>`;
    if (format === "json") return `<pre>${escapeHtml(JSON.stringify(JSON.parse(value), null, 2))}</pre>`;
    return `<p class="field-copy">${escapeHtml(value)}</p>`;
  }

  function createCmsItem(item) {
    const changes = clone(data.cmsChanges[item.recommendationId]);
    const content = side => changes.fields.map(field => `${field.name}\n${field[side] || "Not currently present"}`).join("\n\n");
    return { ...item, ...changes, current: content("before"), proposed: content("after") };
  }

  function renderBundleActions(bundle) {
    const pending = bundle.items.some(item => item.decision === "pending");
    const approved = bundle.items.some(item => item.decision === "approved");
    const landing = bundle.items.some(item => item.type === "Landing page");
    const staticNote = landing ? `<p class="callout small" style="margin-top:12px"><strong>Landing pages deploy to ${escapeHtml(project().staticSite.type)}</strong> (${escapeHtml(project().staticSite.environment)}), not to ${escapeHtml(bundle.connector)}. Publication still requires the separate human decision.</p>` : "";
    if (bundle.status === "Needs review") {
      return `${staticNote}
        <div class="approval-footer">
          <p><strong>Final bundle review</strong><br><small>Resolve every item, then approve the selected changes for CMS drafting.</small></p>
          <button class="button primary" type="button" data-action="approve-bundle" ${pending || !approved ? "disabled" : ""}>Approve selected bundle</button>
        </div>`;
    }

    if (bundle.status === "Approved") {
      return `
        <div class="approval-footer">
          <p><strong>Ready for Site Content agent</strong><br><small>The agent will create a ${escapeHtml(bundle.environment)} draft and validate fields, schema, and brand-source versions.</small></p>
          <button class="button primary" type="button" data-action="create-cms-draft">Create and validate CMS draft</button>
        </div>`;
    }
    if (bundle.status === "In progress") {
      return `<div class="callout"><strong>Site Content agent is working</strong><p class="small">Rechecking Microsoft IQ source versions, applying approved fields, and running validation in ${escapeHtml(bundle.connector)}.</p></div>`;
    }
    if (bundle.status === "Ready to publish") {
      return `
        <div class="approval-footer">
          <p><strong>Draft validated in ${escapeHtml(bundle.environment)}</strong><br><small>Only validated items are publishable. Any editorial work without all human reviews remains on hold. Target: simulated production.</small></p>
          <button class="button success" type="button" data-action="publish-bundle">Approve publication</button>
        </div>`;
    }
    if (bundle.status === "Published") {
      return `<div class="callout green"><strong>Approved routine changes published</strong><p class="small">The next scheduled run will monitor evidence and analytics. Editorial concepts remain in their human workflow.</p></div>`;
    }
    if (bundle.status === "Editorial review") {
      return `<div class="callout amber"><strong>Waiting for the content team</strong><p>Expand each approved editorial item and record copywriter, designer, and brand reviews. A separate publish decision is still required afterwards.</p></div>`;
    }
    return "";
  }

  function renderMaintenanceLoop() {
    return `
      <div class="maintenance-loop" aria-label="Continuous maintenance workflow">
        ${["Monitor", "Analyse", "Recommend", "Review", "Draft", "Validate", "Approve", "Publish"].map(step => `<div class="loop-step">${step}</div>`).join("")}
      </div>`;
  }

  function handleSubmit(event) {
    if (event.target.id !== "chat-form") return;
    event.preventDefault();
    const input = event.target.elements.message;
    const value = input.value.trim();
    const error = document.getElementById("chat-error");
    if (!value) {
      showInputError(error, "Enter a value to continue.");
      return;
    }
    if (["running", "results"].includes(state.chat.stage)) {
      state.chat.draft = "";
      state.chat.messages.push({ speaker: "user", text: value });
      const current = project();
      const answer = /brand|guideline|word|sharepoint|iq/i.test(value)
        ? `The Brand Context agent references ${current.iq.sources.map(source => `${source.name} (${source.version})`).join(" and ")}. Guidance must be current before new suggestions or CMS drafts can proceed.`
        : /progress|status/i.test(value)
          ? `The project run is ${state.activeRun?.status || "not started"}. Control Plane contains the saved checkpoints and any content work waiting for human review.`
          : `These are illustrative recommendations grounded in synthetic WebIQ excerpts${current.clarity.status === "connected" ? " with Clarity behaviour signals" : "; Clarity is unavailable"}. Each action lists confidence and brand constraints. Select actions to prepare a CMS review bundle. This demo does not perform unrestricted chat or make live model calls.`;
      state.chat.messages.push({ speaker: "agent", text: answer });
      render();
      const stream = document.getElementById("messages");
      const reply = stream.querySelector(".thread-message:last-child");
      const viewport = stream.getBoundingClientRect();
      const bounds = reply.getBoundingClientRect();
      // Move only the transcript; scrolling ancestors can displace the chat shell.
      if (bounds.top < viewport.top) stream.scrollTop += bounds.top - viewport.top;
      else if (bounds.bottom > viewport.bottom) stream.scrollTop += bounds.bottom - viewport.bottom;
      return;
    }
    if (state.chat.stage === "goal") {
      state.chat.draft = "";
      setGoal(value);
      return;
    }
    if (state.chat.stage === "scope") {
      const parsed = parseScope(value);
      if (!parsed) {
        showInputError(error, "Enter a valid domain or an http/https page URL.");
        return;
      }
      state.chat.scope = parsed.value;
      state.chat.draft = "";
      state.chat.scopeType = parsed.type;
      state.chat.messages.push({ speaker: "user", text: value });
      state.chat.messages.push({ speaker: "agent", text: `I will analyse ${parsed.value} as a ${parsed.type.toLowerCase()} scope. Before starting, I have assembled the available grounding, analytics, brand knowledge, and CMS context for review.` });
      state.chat.stage = "confirm";
      render();
    }
  }

  function handleClick(event) {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      navigate(viewButton.dataset.view);
      return;
    }
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    if (action === "add-iq-files") {
      document.getElementById("iq-file-input").click();
      return;
    }
    if (action === "remove-iq-file") {
      const session = state.projectSessions[state.projectId];
      session.selectedFiles = session.selectedFiles.filter(file => file.id !== target.dataset.id);
      render();
      document.querySelector('[data-action="add-iq-files"]').focus();
      toast("File removed from this project's demo selection.");
      return;
    }
    if (action === "toggle-chat-history") {
      const open = document.querySelector(".chat-screen").classList.toggle("history-open");
      document.querySelector(".history-mobile-toggle").setAttribute("aria-expanded", String(open));
      document.querySelector(open ? "#chat-search" : ".history-mobile-toggle").focus();
      return;
    }
    if (action === "chat-context") {
      openDrawer("Context for this conversation", "Customer sources", `
        <p class="muted">Suggestions use the approved customer documents below.</p>
        <ul class="iq-source-list">${project().iq.sources.map(renderIqSource).join("")}</ul>
        <div class="divider"></div>
        <p><strong>WebIQ</strong><br><small>Public discovery grounding</small></p>
        <p><strong>Microsoft Clarity</strong><br><small>${escapeHtml(project().clarity.label)}</small></p>
        <button class="button" data-action="open-integrations">Manage sources</button>`);
      return;
    }
    if (action === "open-integrations") { closeDrawer(); navigate("integrations"); return; }
    if (action === "open-chat") {
      const chat = state.projectSessions[state.projectId].chats.find(item => item.id === target.dataset.id);
      if (!chat) return;
      state.chat = chat;
      state.selectedRecommendations.clear();
      render();
      return;
    }
    if (action === "reset-demo") {
      for (const session of Object.values(state.projectSessions)) {
        clearInterval(session.runTimer);
        clearTimeout(session.draftTimer);
      }
      sessionStorage.removeItem("geo-mock-project");
      location.hash = "#/projects";
      location.reload();
      return;
    }
    if (action === "scheduled-run") {
      if (state.activeRun?.status === "Running") return toast("The current project already has an active run.");
      resetChat();
      state.chat.goal = "Recurring maintenance: detect new opportunities and recheck prior updates";
      state.chat.scope = `https://${project().domain}`;
      state.chat.scopeType = "Domain";
      startRun();
      state.activeRun.audit.push("Scheduled-run simulation: refresh brand sources and monitor previously published changes");
      return;
    }
    if (action === "replay-run") {
      const run = [...state.projectSessions[state.projectId].history,...project().runs].find(item => item.id === target.dataset.run);
      if (!run || run.status !== "Completed") return toast("This execution has no validated results.");
      if (state.activeRun?.status === "Running") return toast("Wait for the active analysis before opening a historical result.");
      state.chat = createChat(project());
      state.chat.goal = run.goal;
      state.chat.scope = run.scope;
      state.chat.stage = "results";
      state.chat.sourceRun = run.id;
      state.chat.messages.push({speaker:"agent",text:`Reopened ${run.id}: synthetic saved results. No new analysis was started.`});
      closeDrawer();
      navigate("chat");
      return;
    }
    if (action === "backlog") {
      const rec = project().recommendations.find(item => item.id === target.dataset.id);
      if (rec) rec.status = "In backlog";
      render();
      return;
    }
    if (action === "export-results") {
      const payload = JSON.stringify({ synthetic:true, project:project().name, goal:state.chat.goal, recommendations:project().recommendations }, null, 2);
      const url = URL.createObjectURL(new Blob([payload], {type:"application/json"}));
      const link = document.createElement("a");
      link.href = url; link.download = `${project().id}-synthetic-action-plan.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return;
    }
    if (action === "toggle-clarity") {
      const current = project();
      const connected = current.clarity.status !== "connected";
      current.clarity.status = connected ? "connected" : "unavailable";
      current.clarity.label = connected ? "Connected (demo)" : "Not configured";
      if (connected) {
        current.clarity.projectName = `${current.name} demo analytics`;
        current.clarity.lastSync = "Synthetic connection just configured";
        current.clarity.signals = ["Illustrative page engagement"];
      }
      toast("Clarity configuration updated for future simulated runs.");
      render(); return;
    }
    if (action === "toggle-cms") {
      project().cms.status = project().cms.status === "connected" ? "disconnected" : "connected";
      render(); return;
    }
    if (action === "change-iq" || action === "unavailable-iq") {
      const current = project();
      current.iq.status = action === "unavailable-iq" ? "unavailable" : "connected";
      current.iq.sources[0].status = action === "unavailable-iq" ? "unavailable" : "stale";
      if (action === "change-iq") current.iq.sources[0].version += ".revised";
      toast("Brand context changed. Refresh and review sources before generating new content.");
      render(); return;
    }
    if (action === "editorial-approve") {
      const bundle = state.cmsBundles[state.projectId];
      const item = bundle.items.find(entry => entry.id === target.dataset.id);
      if (!item || item.delivery !== "Editorial review" || !["copywriter","designer","brand"].includes(target.dataset.role)) return;
      item.reviews ||= {};
      item.reviews[target.dataset.role] = true;
      bundle.audit.push(`Human ${target.dataset.role} review approved: ${item.title} (simulated role)`);
      if (["copywriter","designer","brand"].every(role => item.reviews[role])) item.delivery = "Validated draft";
      bundle.status = bundle.items.some(entry => entry.delivery === "Validated draft") ? "Ready to publish" : "Editorial review";
      render(); return;
    }
    if (action === "source-excerpt") {
      const item = project().recommendations.find(rec => rec.id === target.dataset.id);
      if (!item) return;
      openDrawer("Synthetic WebIQ source", "Retained evidence excerpt", `
        <p><strong>Evidence ID:</strong> webiq-${escapeHtml(item.id)}</p>
        <p><strong>Source:</strong> <code>https://reference.example/discovery/${escapeHtml(item.id)}</code></p>
        <blockquote>${escapeHtml(item.evidence.webiq)}</blockquote>
        <p class="small muted">Illustrative saved comparison-packet summary. This excerpt and URL are fixtures, not a retrieved live page.</p>`);
      return;
    }

    if (action === "select-project") {
      state.projectId = target.dataset.project;
      state.projectSelected = true;
      projectSelect.value = state.projectId;
      sessionStorage.setItem("geo-mock-project", state.projectId);
      state.selectedRecommendations.clear();
      state.expandedUpdateId = null;
      state.cmsSelectedItemId = null;
      state.cmsFilter = "all";
      closeDrawer();
      navigate("dashboard");
      return;
    }
    if (action === "quick-goal") {
      setGoal(target.dataset.value);
      return;
    }
    if (action === "reset-chat") {
      state.selectedRecommendations.clear();
      resetChat();
      render();
      return;
    }
    if (action === "edit-scope") {
      state.chat.stage = "scope";
      render();
      return;
    }
    if (action === "start-analysis" || action === "start-sample-run") {
      if (action === "start-sample-run" && !state.chat.goal) {
        state.chat.goal = project().activeGoal;
        state.chat.scope = project().domain;
        state.chat.scopeType = "Domain";
      }
      startRun();
      return;
    }
    if (action === "skip-run") {
      if (state.activeRun?.status === "Running") state.activeRun.fastForwarded = true;
      completeRun();
      return;
    }
    if (action === "cancel-run") {
      cancelRun();
      return;
    }
    if (action === "view-results") {
      if (state.activeRun?.status !== "Completed") return toast("This run has no validated results.");
      state.chat = state.activeRun.chat;
      state.chat.stage = "results";
      navigate("chat");
      return;
    }
    if (action === "show-run-details" || action === "open-active-run") {
      showRunDrawer(state.activeRun);
      return;
    }
    if (action === "open-run") {
      showHistoricalRun(target.dataset.run);
      return;
    }
    if (action === "recommendation-details") {
      showRecommendationDrawer(target.dataset.id);
      return;
    }
    if (action === "select-recommendation") {
      setRecommendationSelected(target.dataset.id, target.checked);
      return;
    }
    if (action === "clear-selection") {
      state.selectedRecommendations.clear();
      render();
      return;
    }
    if (action === "prepare-cms") {
      prepareCmsBundle();
      return;
    }
    if (action === "open-update") {
      const id = target.dataset.id;
      state.cmsSelectedItemId = id === "all" ? null : id;
      state.expandedUpdateId = state.cmsSelectedItemId;
      render();
      return;
    }
    if (action === "toggle-update") {
      state.expandedUpdateId = state.expandedUpdateId === target.dataset.id ? null : target.dataset.id;
      render();
      return;
    }
    if (action === "cms-decision") {
      setCmsDecision(target.dataset.id, target.dataset.value);
      return;
    }
    if (action === "approve-bundle") {
      approveBundle();
      return;
    }
    if (action === "create-cms-draft") {
      createCmsDraft();
      return;
    }
    if (action === "publish-bundle") {
      publishBundle();
      return;
    }
    if (action === "iq-source-details") {
      showIqSource(target.dataset.id);
      return;
    }
    if (action === "refresh-iq") {
      project().iq.status = "connected";
      project().iq.sources.forEach(source => source.status = "current");
      toast("Microsoft IQ sources refreshed. Both approved sources remain current.");
      render();
      return;
    }
    if (action === "test-integration") {
      const name = target.dataset.name;
      const current = project();
      const connected = name === "Microsoft Clarity" ? current.clarity.status === "connected"
        : name === current.staticSite.type ? current.staticSite.status === "connected"
        : current.cms.status === "connected";
      toast(`${name}: ${connected ? "connected" : "not connected"} (simulation).`);
      return;
    }
    if (action === "connect-connector") {
      toast(`${target.dataset.name} is not configured for ${project().name}. Connector setup is out of scope for this demo.`);
      return;
    }
    if (action === "show-maintenance") {
      showMaintenanceDrawer();
      return;
    }
    if (action === "show-methodology") {
      showMethodologyDrawer();
      return;
    }
    if (action === "concept-only") {
      toast("This control is shown for product design only and makes no external change.");
    }
  }

  function handleChange(event) {
    if (event.target.id === "iq-file-input") {
      const files = Array.from(event.target.files);
      if (!files.length) return;
      const supportedTypes = { doc: "Word", docx: "Word", pdf: "PDF", txt: "Text" };
      const unsupported = files.filter(file => !Object.hasOwn(supportedTypes, file.name.split(".").pop().toLowerCase()));
      if (unsupported.length) {
        event.target.value = "";
        toast("No files added. Select Word (.doc or .docx), PDF, or text (.txt) files.");
        return;
      }
      const selected = state.projectSessions[state.projectId].selectedFiles;
      let added = 0;
      for (const file of files) {
        if (selected.some(existing => existing.name === file.name)) continue;
        selected.push({ id: crypto.randomUUID(), name: file.name, type: supportedTypes[file.name.split(".").pop().toLowerCase()] });
        added += 1;
      }
      render();
      document.querySelector('[data-action="add-iq-files"]').focus();
      toast(added ? `${added} file${added === 1 ? "" : "s"} selected for this project. Contents were not read or uploaded.` : "These filenames are already selected for this project.");
      return;
    }
    if (event.target.id === "run-scenario") {
      state.projectSessions[state.projectId].scenario = event.target.value; return;
    }
    if (event.target.id === "cms-status-filter") {
      state.cmsFilter = event.target.value;
      const bundle = state.cmsBundles[state.projectId];
      const selected = bundle?.items.find(item => item.id === state.cmsSelectedItemId);
      if (selected && state.cmsFilter !== "all" && itemStatus(bundle, selected) !== state.cmsFilter) {
        state.cmsSelectedItemId = null;
        state.expandedUpdateId = null;
      }
      render();
      return;
    }
    if (event.target.id === "bundle-select") {
      const archive = state.projectSessions[state.projectId].bundles;
      const index = archive.findIndex(bundle => bundle.id === event.target.value);
      if (index >= 0) {
        const previous = state.cmsBundles[state.projectId];
        state.cmsBundles[state.projectId] = archive.splice(index, 1, previous)[0];
        state.cmsSelectedItemId = null;
        state.cmsFilter = "all";
        state.expandedUpdateId = null;
        render();
      }
      return;
    }
    if (event.target.dataset.owner) {
      const rec = project().recommendations.find(item => item.id === event.target.dataset.owner);
      if (rec) rec.owner = event.target.value;
      return;
    }
    if (event.target.id === "priority-filter") state.resultFilters.priority = event.target.value;
    else if (event.target.id === "effort-filter") state.resultFilters.effort = event.target.value;
    else if (event.target.id === "source-filter") state.resultFilters.source = event.target.value;
    else if (event.target.id === "status-filter") state.resultFilters.status = event.target.value;
    else return;
    render();
  }

  function handleInput(event) {
    if (event.target.id === "chat-search") {
      const query = event.target.value.trim().toLowerCase();
      document.querySelectorAll(".chat-history-item").forEach(button => {
        button.hidden = !button.innerText.toLowerCase().includes(query);
      });
      document.querySelectorAll(".history-group").forEach(group => {
        group.hidden = !group.querySelector(".chat-history-item:not([hidden])");
      });
      document.getElementById("history-empty").hidden = Boolean(document.querySelector(".chat-history-item:not([hidden])"));
      return;
    }
    if (event.target.id === "chat-input") {
      state.chat.draft = event.target.value;
      return;
    }
    if (!event.target.dataset.note) return;
    const bundle = state.cmsBundles[state.projectId];
    if (!bundle || bundle.status !== "Needs review") return;
    const item = bundle.items.find(candidate => candidate.id === event.target.dataset.note);
    if (item && item.delivery !== "Published") item.note = event.target.value;
  }

  function setGoal(goal) {
    state.chat.draft = "";
    state.chat.goal = goal;
    state.chat.messages.push({ speaker: "user", text: goal });
    state.chat.messages.push({ speaker: "agent", text: "Understood. Which domain or specific page URL should I analyse for this goal?" });
    state.chat.stage = "scope";
    render();
  }

  function parseScope(value) {
    const trimmed = value.trim();
    if (/^https?:\/\/[a-z0-9.-]+(?::\d+)?(?:\/.*)?$/i.test(trimmed)) {
      try {
        const url = new URL(trimmed);
        return { value: url.href.replace(/\/$/, ""), type: url.pathname && url.pathname !== "/" ? "Page URL" : "Domain" };
      } catch {
        return null;
      }
    }
    if (/^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/[^\s]*)?$/i.test(trimmed)) {
      return { value: `https://${trimmed.replace(/\/$/, "")}`, type: trimmed.includes("/") ? "Page URL" : "Domain" };
    }
    return null;
  }

  function showInputError(element, message) {
    element.hidden = false;
    element.textContent = message;
  }

  function startRun() {
    if (state.activeRun && state.activeRun.status === "Running") {
      toast("This project already has an active run. View it in Control Plane.");
      navigate("control-plane");
      return;
    }
    if (state.runTimer) clearInterval(state.runTimer);
    const current = project();
    const session = state.projectSessions[current.id];
    if (session.activeRun) {
      const previous = session.activeRun;
      session.history.unshift({
        id: previous.id, goal: previous.goal, scope: previous.scope, status: previous.status,
        started: "Earlier in this demo", duration: `${formatDuration(runElapsed(previous))}${previous.fastForwarded ? " (fast-forwarded)" : ""}`, agents: `${previous.agents.filter(a => a.state === "completed").length}/${previous.agents.length}`,
        recommendations: previous.status === "Completed" ? current.recommendations.length : 0,
        cmsOutcome: "See linked CMS bundles", clarity: previous.clarityIncluded ? "Used" : "Skipped", audit: [...previous.audit]
      });
    }
    const agents = data.agentBlueprint.map(agent => ({
      ...agent,
      progress: 0,
      elapsedMs: 0,
      state: agent.id === "clarity" && current.clarity.status !== "connected" ? "skipped" : "queued"
    }));
    const startedClock = performance.now();
    const firstRunnable = agents.find(agent => agent.state === "queued");
    if (firstRunnable) {
      firstRunnable.state = "working";
      firstRunnable.startedClock = startedClock;
    }
    state.activeRun = {
      id: `RUN-${crypto.randomUUID().slice(0, 8)}`,
      projectId: current.id,
      goal: state.chat.goal || current.activeGoal,
      scope: state.chat.scope || `https://${current.domain}`,
      status: "Running",
      startedClock,
      expectedDurationMs: agents.filter(agent => agent.state !== "skipped").reduce((total, agent) => total + agent.durationMs, 0),
      chat: state.chat,
      clarityIncluded: current.clarity.status === "connected",
      scenario: session.scenario,
      brandVersions: brandVersions(current),
      step: 0,
      agents,
      audit: [
        "0m 00s Coordinator accepted the human-confirmed goal and scope",
        `0m 00s WebIQ grounding enabled; Clarity ${current.clarity.status === "connected" ? "included" : "marked unavailable"}`,
        `0m 00s Microsoft IQ sources pinned: ${current.iq.sources.map(source => `${source.type} ${source.version}`).join(", ")}`
      ]
    };
    state.chat.stage = "running";
    state.chat.sourceRun = state.activeRun.id;
    state.chat.messages.push({ speaker: "agent", text: `Run ${state.activeRun.id} has started. This demo takes about ${formatDuration(state.activeRun.expectedDurationMs)}. You can keep working while the agents run, or skip to results from Control Plane.` });
    state.runTimer = setInterval(() => advanceRun(current.id), 1000);
    navigate("control-plane");
  }

  function advanceRun(projectId) {
    const run = state.projectSessions[projectId].activeRun;
    if (!run || run.status !== "Running") return;
    const now = performance.now();
    let transitioned = false;
    let working = run.agents.find(agent => agent.state === "working");
    while (working) {
      working.elapsedMs = Math.min(working.durationMs, Math.max(0, now - working.startedClock));
      working.progress = Math.floor(working.elapsedMs / working.durationMs * 100);
      if (working.elapsedMs < working.durationMs) break;
      working.state = "completed";
      const completedClock = working.startedClock + working.durationMs;
      const checkpoint = `${formatDuration(completedClock - run.startedClock)} ${working.name} completed`;
      run.audit.push(checkpoint);
      run.chat.messages.push({ speaker: "agent", text: checkpoint });
      transitioned = true;
      const next = run.agents.find(agent => agent.state === "queued");
      if (!next) {
        completeRun(projectId);
        return;
      }
      if (next.id === "recommendation" && (!brandReady(data.projects.find(p => p.id === projectId)) ||
          run.brandVersions !== brandVersions(data.projects.find(p => p.id === projectId)))) {
        failRun(projectId, "Brand context is stale, changed, or unavailable. No recommendations generated.");
        return;
      }
      next.state = "working";
      next.startedClock = completedClock;
      run.step += 1;
      run.audit.push(`${formatDuration(completedClock - run.startedClock)} ${next.name} started`);
      working = next;
    }
    if (state.projectId !== projectId) return;
    if (transitioned && (state.view === "control-plane" || (state.view === "chat" && state.chat === run.chat))) render();
    if (state.view === "control-plane") updateRunProgress(run);
  }

  function formatDuration(milliseconds) {
    const seconds = Math.floor(milliseconds / 1000);
    return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
  }

  function runElapsed(run) {
    return Math.max(0, (run.endedClock ?? performance.now()) - run.startedClock);
  }

  function updateRunProgress(run) {
    screen.querySelectorAll("[data-run-elapsed]").forEach(element => {
      element.textContent = formatDuration(runElapsed(run));
    });
    for (const agent of run.agents) {
      const row = screen.querySelector(`[data-agent-id="${agent.id}"]`);
      if (!row) continue;
      row.querySelector(".progress-track").setAttribute("aria-valuenow", agent.progress);
      row.querySelector(".progress-track span").style.width = `${agent.progress}%`;
      row.querySelector("[data-agent-time]").textContent = agent.state === "skipped" ? "Not configured" : `${formatDuration(agent.elapsedMs)} / ~${agent.duration}`;
    }
  }

  function completeRun(projectId = state.projectId) {
    const session = state.projectSessions[projectId];
    const current = data.projects.find(item => item.id === projectId);
    const run = session.activeRun;
    if (!run || run.status !== "Running") return;
    if (run.scenario === "evidence-failure") {
      failRun(projectId, "Unsupported evidence reference: the analysis failed validation. No CMS proposal created.");
      return;
    }
    if (!brandReady(current) || brandVersions(current) !== run.brandVersions) {
      failRun(projectId, "Microsoft IQ context changed or is unavailable. Refresh and review before a new run.");
      return;
    }
    if (session.runTimer) {
      clearInterval(session.runTimer);
      session.runTimer = null;
    }
    for (const agent of run.agents) {
      if (agent.state === "queued" || agent.state === "working") {
        agent.state = agent.id === "clarity" && !run.clarityIncluded ? "skipped" : "completed";
      }
      if (agent.state === "completed") agent.progress = 100;
    }
    run.status = "Completed";
    run.endedClock = performance.now();
    run.step = run.agents.length;
    run.audit.push(`${formatDuration(runElapsed(run))} ${run.fastForwarded ? "Operator fast-forwarded the demo. " : ""}Recommendation set completed with evidence, brand alignment, confidence, and verification`);
    run.chat.stage = "results";
    if (!run.chat.messages.some(message => message.text.includes("prioritized actions are ready"))) {
      run.chat.messages.push({
        speaker: "agent",
        text: `The analysis is complete. ${current.recommendations.length} prioritized actions are ready. Nothing has been changed in ${current.cms.type}; select recommendations to prepare a review bundle.`
      });
    }
    toast(`${current.name} analysis completed. Prioritized actions are ready.`);
    if (state.projectId === projectId) render();
  }

  function cancelRun() {
    if (!state.activeRun) return;
    if (state.runTimer) clearInterval(state.runTimer);
    state.runTimer = null;
    state.activeRun.status = "Cancelled";
    state.activeRun.endedClock = performance.now();
    const working = state.activeRun.agents.find(agent => agent.state === "working");
    if (working) working.state = "failed";
    state.activeRun.audit.push(`${formatDuration(runElapsed(state.activeRun))} Human operator cancelled the run; no further agents started`);
    state.activeRun.chat.stage = "cancelled";
    state.activeRun.chat.messages.push({ speaker: "agent", text: `Run ${state.activeRun.id} was cancelled. Completed checkpoints remain visible in Control Plane.` });
    toast("Run cancelled. No CMS proposal was created.");
    render();
  }

  function setRecommendationSelected(id, selected) {
    if (selected) state.selectedRecommendations.add(id);
    else state.selectedRecommendations.delete(id);
    render();
  }

  function prepareCmsBundle() {
    const current = project();
    const selected = current.recommendations.filter(item => state.selectedRecommendations.has(item.id));
    if (!selected.length) return;
    if (!brandReady(current)) return toast("Refresh and review Microsoft IQ sources before preparing a new proposal.");
    const previous = state.cmsBundles[current.id];
    if (previous) state.projectSessions[current.id].bundles.unshift(previous);
    const items = selected.map((recommendation, index) => createCmsItem({
      id: `CMS-DEMO-${index + 1}`,
      recommendationId: recommendation.id,
      type: recommendation.category,
      title: recommendation.title,
      target: recommendation.target,
      decision: "pending",
      workflow: ["Landing page", "Blog post"].includes(recommendation.category) ? "editorial" : "routine",
      validation: recommendation.verification
    }));
    state.cmsBundles[current.id] = {
      id: `CMS-${crypto.randomUUID().slice(0, 8)}`,
      title: `${current.name} GEO recommendation bundle`,
      sourceRun: state.chat.sourceRun || current.runs[0].id,
      created: "15 Sep 2026, 12:46",
      status: "Needs review",
      brandVersions: brandVersions(current),
      connector: current.cms.type,
      environment: current.cms.environment,
      items,
      audit: [
        "12:46 Human selected recommendations for CMS preparation",
        `12:46 Brand Context Agent bound ${current.iq.sources.map(source => `${source.type} ${source.version}`).join(" and ")}`,
        "12:46 Awaiting item-level reviewer decisions"
      ]
    };
    state.cmsSelectedItemId = null;
    state.cmsFilter = "all";
    state.expandedUpdateId = items[0].id;
    toast(`${items.length} recommendation${items.length === 1 ? "" : "s"} prepared for CMS review.`);
    navigate("cms-updates");
  }

  function setCmsDecision(id, decision) {
    const bundle = state.cmsBundles[state.projectId];
    if (!bundle || bundle.status !== "Needs review") {
      toast("Item decisions are locked after final bundle approval.");
      return;
    }
    if (!["approved", "rejected"].includes(decision)) return;
    const item = bundle.items.find(candidate => candidate.id === id);
    if (!item || item.delivery === "Published") return;
    item.decision = decision;
    bundle.audit.push(`12:48 Reviewer ${decision === "approved" ? "approved" : "rejected"} ${item.type}: ${item.title}`);
    if (bundle.items.every(entry => entry.decision === "rejected")) {
      bundle.status = "Rejected";
    }
    toast(`${item.title} ${decision}.`);
    render();
  }

  function approveBundle() {
    const bundle = state.cmsBundles[state.projectId];
    if (!bundle || bundle.status !== "Needs review") return;
    if (!checkBrandForBundle(project(), bundle)) { render(); return; }
    if (bundle.items.some(item => item.decision === "pending") || !bundle.items.some(item => item.decision === "approved")) return;
    bundle.status = "Approved";
    bundle.audit.push("12:49 Final bundle approval recorded; Site Content agent is authorized to create a non-live draft");
    toast("Bundle approved for CMS draft creation.");
    render();
  }

  function createCmsDraft() {
    const bundle = state.cmsBundles[state.projectId];
    if (!bundle || bundle.status !== "Approved") return;
    if (project().cms.status !== "connected") return toast("Connect the project CMS before creating a draft.");
    if (!checkBrandForBundle(project(), bundle)) { render(); return; }
    const projectId = state.projectId;
    const session = state.projectSessions[projectId];
    if (session.draftTimer) return toast("A Site Content agent is already drafting for this project.");
    bundle.status = "In progress";
    bundle.audit.push(`12:50 Site Content agent started ${bundle.connector} draft preparation`);
    render();
    session.draftTimer = setTimeout(() => {
      session.draftTimer = null;
      const current = data.projects.find(p => p.id === projectId);
      if (!checkBrandForBundle(current, bundle)) {
        if (state.projectId === projectId) render();
        return;
      }
      if (current.cms.status !== "connected") {
        bundle.status = "Approved";
        bundle.audit.push("Draft stopped: CMS disconnected. Explicit retry required.");
        if (state.projectId === projectId) render();
        toast("CMS disconnected; draft not applied.");
        return;
      }
      for (const item of bundle.items) {
        if (item.decision === "approved") item.delivery = item.workflow === "editorial" ? "Editorial review" : "Validated draft";
      }
      bundle.status = bundle.items.some(item => item.delivery === "Validated draft") ? "Ready to publish" : "Editorial review";
      bundle.audit.push("12:52 Microsoft IQ source versions rechecked; routine CMS fields validated");
      bundle.audit.push("12:52 Editorial concepts routed to copywriter and designer workflow");
      toast("CMS draft validated. Separate publication approval is required.");
      if (state.projectId === projectId && state.cmsBundles[projectId] === bundle) {
        render();
      }
    }, 1600);
  }

  function publishBundle() {
    const bundle = state.cmsBundles[state.projectId];
    if (!bundle || bundle.status !== "Ready to publish") return;
    if (project().cms.status !== "connected") return toast("CMS is disconnected. No publication performed.");
    if (!checkBrandForBundle(project(), bundle)) { render(); return; }
    const ready = bundle.items.filter(item => item.decision === "approved" && item.delivery === "Validated draft");
    if (!ready.length) return toast("No validated drafts are ready to publish.");
    ready.forEach(item => { item.delivery = "Published"; });
    bundle.status = bundle.items.some(item => item.delivery === "Editorial review") ? "Editorial review" : "Published";
    bundle.audit.push(`Human publication approval: ${ready.length} validated item(s) published to simulated ${bundle.connector} production`);
    project().metrics.publishedThisMonth += ready.length;
    toast("Validated, human-approved updates published in the simulation.");
    render();
  }

  function cmsCounts(bundle) {
    const counts = {};
    CMS_STATUSES.forEach(status => { counts[status] = 0; });
    if (!bundle) return counts;
    bundle.items.forEach(item => { counts[itemStatus(bundle, item)] += 1; });
    return counts;
  }

  function brandVersions(current) {
    return current.iq.sources.map(source => `${source.id}:${source.version}`).join("; ");
  }

  function brandReady(current) {
    return current.iq.status === "connected" && current.iq.sources.every(source => source.status === "current");
  }

  function checkBrandForBundle(current, bundle) {
    if (brandReady(current) && bundle.brandVersions === brandVersions(current)) return true;
    bundle.audit.push(`Brand gate blocked: pinned ${bundle.brandVersions}; current ${brandVersions(current)}. Prior approvals invalidated.`);
    bundle.status = "Needs review";
    bundle.items.forEach(item => {
      if (item.delivery !== "Published") {
        item.decision = "pending"; delete item.delivery; delete item.reviews;
      }
    });
    if (brandReady(current)) bundle.brandVersions = brandVersions(current);
        toast(`${current.name}: brand context requires fresh item and bundle review. No CMS writes performed.`);
    return false;
  }

  function failRun(projectId, reason) {
    const session = state.projectSessions[projectId];
    clearInterval(session.runTimer); session.runTimer = null;
    session.activeRun.status = "Failed validation";
    session.activeRun.endedClock = performance.now();
    session.activeRun.agents.forEach(agent => {
      if (agent.state === "working") agent.state = "failed";
    });
    session.activeRun.audit.push(reason);
    session.activeRun.chat.stage = "failed";
    session.activeRun.chat.messages.push({speaker:"agent",text:reason});
    toast(reason);
    if (state.projectId === projectId) render();
  }

  function renderContentAgent() {
    const bundle = state.cmsBundles[state.projectId];
    return `<section class="card purple-accent" style="margin-top:16px">
      <div class="card-heading"><h3>Site Content agent</h3>${pill(bundle.status, cmsStatusColour(bundle.status))}</div>
      <p>${escapeHtml(bundle.connector)} / ${escapeHtml(bundle.environment)}. ${escapeHtml(bundle.title)}.</p>
      <p class="small">Latest checkpoint: ${escapeHtml(bundle.audit.at(-1))}</p>
      <button class="button" data-view="cms-updates">Review CMS changes</button>
    </section>`;
  }

  function decisionLabel(value) {
    return value === "approved" ? "Approved" : value === "rejected" ? "Rejected" : "Needs decision";
  }

  function cmsStatusColour(status) {
    return {
      "Needs review": "amber",
      Approved: "blue",
      "In progress": "blue",
      "Ready to publish": "purple",
      "Editorial review": "orange",
      Published: "green",
      Rejected: "red"
    }[status] || "";
  }

  function showRecommendationDrawer(id) {
    const item = project().recommendations.find(candidate => candidate.id === id);
    if (!item) return;
    openDrawer(
      "Recommendation evidence",
      item.title,
      `
        <div class="grid two">
          ${metric("Priority", `${item.rank} / ${item.priority}`, "Qualitative recommendation order")}
          ${metric("Impact / effort", `${item.impact} / ${item.effort}`, `Confidence: ${item.confidence}`)}
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon">WQ</span><strong>WebIQ grounding</strong>${pill("External evidence", "blue")}</div>
          <p class="small">${escapeHtml(item.evidence.webiq)}</p>
          <button class="button ghost" type="button" data-action="source-excerpt" data-id="${item.id}">Open retained source: webiq-${escapeHtml(item.id)}</button>
          <small>Bounded synthetic source packet; not a claim about global ranking.</small>
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon clarity">CL</span><strong>Microsoft Clarity</strong>${pill(project().clarity.status === "connected" ? "Included" : "Unavailable", project().clarity.status === "connected" ? "teal" : "amber")}</div>
          <p class="small">${escapeHtml(item.evidence.clarity)}</p>
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon iq">IQ</span><strong>Microsoft IQ brand context</strong>${pill(item.brandAlignment, item.brandAlignment === "Aligned" ? "green" : "amber")}</div>
          <p class="small">${escapeHtml(item.evidence.iq)}</p>
          <small>${escapeHtml(item.brandSource)}</small>
          <ul class="iq-source-list">${project().iq.sources.map(renderIqSource).join("")}</ul>
        </div>
        <div class="callout"><strong>Proposed change</strong><p class="small">${escapeHtml(item.proposedChange)}</p></div>
        <div class="callout green"><strong>Verification</strong><p class="small">${escapeHtml(item.verification)}</p></div>
        <p class="small muted">Observed evidence and project analytics inform the recommendation. Expected impact and confidence remain hypotheses requiring human review.</p>
      `
    );
  }

  function showRunDrawer(run) {
    if (!run) return;
    openDrawer(
      "Execution detail",
      `${run.id} checkpoints`,
      `
        <div class="callout ${run.status === "Completed" ? "green" : ""}">
          <strong>${escapeHtml(run.status)}</strong>
          <p class="small">${escapeHtml(run.goal)}<br>${escapeHtml(run.scope)}</p>
        </div>
        <ol class="timeline">
          ${run.audit.map((entry, index) => {
            const match = entry.match(/^(\d\d:\d\d)\s(.*)$/);
            return `<li><time>${match ? match[1] : `12:${36 + index}`}</time><span>${escapeHtml(match ? match[2] : entry)}</span></li>`;
          }).join("")}
        </ol>
        <div class="brand-reference">
          <span class="source-icon iq">IQ</span>
          <span><strong>Brand context gate</strong><br><small>Recommendation generation waits for the approved Word and SharePoint sources to be referenced and versioned.</small></span>
        </div>
      `
    );
  }

  function showHistoricalRun(id) {
    const run = [...state.projectSessions[state.projectId].history, ...project().runs].find(item => item.id === id);
    if (!run) return;
    openDrawer(
      "Historical execution",
      id,
      `
        <div class="grid two">
          ${metric("Status", run.status, run.duration)}
          ${metric("Agents", run.agents, `${run.recommendations} recommendations`)}
        </div>
        <div class="callout"><strong>${escapeHtml(run.goal)}</strong><p class="small">${escapeHtml(run.scope)}</p></div>
        <dl class="integration-detail">
          <dt>Started</dt><dd>${escapeHtml(run.started)}</dd>
          <dt>Clarity</dt><dd>${escapeHtml(run.clarity)}</dd>
          <dt>Grounding</dt><dd>WebIQ evidence packet retained</dd>
          <dt>Brand context</dt><dd>Microsoft IQ sources versioned before suggestions</dd>
          <dt>CMS outcome</dt><dd>${escapeHtml(run.cmsOutcome)}</dd>
        </dl>
        ${run.status.includes("Failed") ? `<div class="callout amber"><strong>Failure retained</strong><p class="small">The run stopped at evidence validation. No unsupported recommendation or CMS proposal was created.</p></div>` : ""}
        ${run.status === "Completed" ? `<button class="button primary" type="button" data-action="replay-run" data-run="${escapeAttribute(run.id)}">Reopen saved results</button>` : ""}
        ${run.audit ? `<ol>${run.audit.map(entry => `<li>${escapeHtml(entry)}</li>`).join("")}</ol>` : ""}
      `
    );
  }

  function showIqSource(id) {
    const source = project().iq.sources.find(item => item.id === id);
    if (!source) return;
    openDrawer(
      "Microsoft IQ source",
      source.name,
      `
        <div class="brand-reference">
          ${sourceLogo(source.type)}
          <span><strong>${escapeHtml(source.location)}</strong><br><small>Explicitly connected synthetic project knowledge</small></span>
        </div>
        <dl class="integration-detail">
          <dt>Owner</dt><dd>${escapeHtml(source.owner)}</dd>
          <dt>Version</dt><dd>${escapeHtml(source.version)}</dd>
          <dt>Updated</dt><dd>${escapeHtml(source.updated)}</dd>
          <dt>Status</dt><dd>${escapeHtml(source.status)}</dd>
        </dl>
        <section class="card flat">
          <h3>Sections available to agents</h3>
          ${source.sections.map(section => `<div class="summary-line"><span class="status-dot success"></span><span><strong>${escapeHtml(section)}</strong><small>Approved for project context</small></span></div>`).join("")}
        </section>
        <p class="small muted">The concept limits retrieval to these approved project sources. It does not represent an unrestricted tenant-wide search.</p>
      `
    );
  }

  function showMethodologyDrawer() {
    openDrawer(
      "Evidence lineage",
      "How recommendations are formed",
      `
        <div class="source-layer">
          <div class="source-title"><span class="source-icon">WQ</span><strong>1. WebIQ grounding</strong></div>
          <p class="small">Retrieves bounded public-page and discovery evidence for the requested scope.</p>
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon clarity">CL</span><strong>2. Clarity analytics</strong></div>
          <p class="small">Adds project-specific on-site behavior only when the client workspace has Clarity configured.</p>
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon iq">IQ</span><strong>3. Microsoft IQ brand context</strong></div>
          <p class="small">References explicitly connected Word and SharePoint guidance before suggestions are generated.</p>
        </div>
        <div class="source-layer">
          <div class="source-title"><span class="source-icon">RA</span><strong>4. Recommendation synthesis</strong></div>
          <p class="small">Prioritizes actions with evidence, effort, confidence, ownership, limitations, and verification.</p>
        </div>
        <div class="callout purple"><strong>Governance boundary</strong><p class="small">Recommendations are proposals. CMS drafting and publication each require separate human authorization.</p></div>
      `
    );
  }

  function showMaintenanceDrawer() {
    openDrawer(
      "Recurring workflow",
      "Self-maintaining content loop",
      `
        ${renderMaintenanceLoop()}
        <div class="timeline" style="margin-top:18px">
          <div class="summary-line"><span class="status-dot success"></span><span><strong>Monitor</strong><small>Scheduled WebIQ scan plus Clarity when configured.</small></span></div>
          <div class="summary-line"><span class="status-dot success"></span><span><strong>Refresh context</strong><small>Microsoft IQ checks Word and SharePoint source versions.</small></span></div>
          <div class="summary-line"><span class="status-dot success"></span><span><strong>Propose</strong><small>New evidence creates prioritized recommendations, never silent edits.</small></span></div>
          <div class="summary-line"><span class="status-dot warning"></span><span><strong>Human review</strong><small>Item and bundle decisions authorize only the selected CMS work.</small></span></div>
          <div class="summary-line"><span class="status-dot warning"></span><span><strong>Draft and validate</strong><small>Site Content agent prepares a non-live Sitecore or Shopify draft.</small></span></div>
          <div class="summary-line"><span class="status-dot warning"></span><span><strong>Publish approval</strong><small>A separate human decision is required before routine updates become live.</small></span></div>
          <div class="summary-line"><span class="status-dot success"></span><span><strong>Measure again</strong><small>Future runs monitor evidence and analytics, then propose the next review cycle.</small></span></div>
        </div>
      `
    );
  }

  function openDrawer(eyebrow, title, content) {
    if (drawer.hidden) overlayTrigger = document.activeElement;
    document.getElementById("drawer-eyebrow").textContent = eyebrow;
    document.getElementById("drawer-title").textContent = title;
    document.getElementById("drawer-content").innerHTML = content;
    drawer.hidden = false;
    drawerBackdrop.hidden = false;
    document.body.style.overflow = "hidden";
    document.querySelector(".app-shell").inert = true;
    document.querySelector(".appbar").inert = true;
    document.getElementById("drawer-close").focus();
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawerBackdrop.hidden = true;
    document.body.style.overflow = "";
    document.querySelector(".app-shell").inert = false;
    document.querySelector(".appbar").inert = false;
    if (overlayTrigger?.isConnected) overlayTrigger.focus();
  }

  function openModal() {
    overlayTrigger = document.activeElement;
    modal.hidden = false;
    modalBackdrop.hidden = false;
    document.body.style.overflow = "hidden";
    document.querySelector(".app-shell").inert = true;
    document.querySelector(".appbar").inert = true;
    document.getElementById("modal-close").focus();
  }

  function closeModal() {
    modal.hidden = true;
    modalBackdrop.hidden = true;
    document.body.style.overflow = "";
    document.querySelector(".app-shell").inert = false;
    document.querySelector(".appbar").inert = false;
    if (overlayTrigger?.isConnected) overlayTrigger.focus();
  }

  function closeMobileNav() {
    state.mobileNavOpen = false;
    navigation.classList.remove("open");
    document.getElementById("mobile-menu").setAttribute("aria-expanded", "false");
  }

  function toast(message) {
    const region = document.getElementById("toast-region");
    const element = document.createElement("div");
    element.className = "toast";
    element.textContent = message;
    region.replaceChildren(element);
    setTimeout(() => element.remove(), 3600);
  }

  function metric(label, value, note) {
    return `
      <section class="card flat metric-card">
        <div class="metric-label"><span>${escapeHtml(String(label))}</span></div>
        <div class="metric-value">${escapeHtml(String(value))}</div>
        <small>${escapeHtml(String(note))}</small>
      </section>`;
  }

  function pill(text, colour = "") {
    return `<span class="pill ${colour}">${escapeHtml(String(text))}</span>`;
  }

  function icon(name) {
    const paths = {
      dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
      chat:'<path d="M20 11.5a8 8 0 0 1-8 8H5l-3 2v-10a9 9 0 0 1 18 0Z"/><path d="M7 10h8M7 14h5"/>',
      "control-plane":'<path d="M12 8V4M6 17H3v-4h18v4h-3M12 13V8"/><rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/>',
      "cms-updates":'<path d="M13 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-8"/><path d="m11 13 7-7 3 3-7 7-4 1Z"/>',
      integrations:'<path d="m8 3 0 4m8-4v4M6 7h12v3a6 6 0 0 1-6 6v5m-6-11h12"/>',
      spark:'<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z"/><path d="m20 2 .6 1.4L22 4l-1.4.6L20 6l-.6-1.4L18 4l1.4-.6Z"/>',
      compose:'<path d="M12 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-7M11 13l8-10 3 3-9 9-4 1Z"/>',
      plus:'<path d="M12 5v14M5 12h14"/>',
      search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
      sources:'<path d="M5 3h11l4 4v14H5Z"/><path d="M15 3v5h5M8 12h9M8 16h6"/>',
      lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
      panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
      arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
      up:'<path d="M12 19V5m-6 6 6-6 6 6"/>',
      trend:'<path d="m3 17 6-6 4 4 8-10M15 5h6v6"/>',
      chevron:'<path d="m6 9 6 6 6-6"/>',
      "web-app":'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6 6.5h1M10 6.5h1M8 13l-2 2 2 2M16 13l2 2-2 2"/>'
    };
    return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.chat}</svg>`;
  }

  function filterSelect(id, label, options, selected) {
    return `
      <label class="small" for="${id}"><span class="sr-only">${escapeHtml(label)}</span>
        <select id="${id}" aria-label="${escapeAttribute(label)}">
          ${options.map(option => `<option ${option === selected ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>
      </label>`;
  }

  function clone(value) {
    return value ? JSON.parse(JSON.stringify(value)) : null;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, "&#096;");
  }
})();
