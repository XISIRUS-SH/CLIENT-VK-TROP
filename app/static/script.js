/* Browser behavior for authenticated chat, durable history, model routing, and key management. */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = {
    messages: [],
    chats: [],
    activeChatId: localStorage.getItem("ai-balancer-active-chat") || "",
    socket: null,
    assistantBubble: null,
    assistantRow: null,
    assistantRawText: "",
    waiting: false,
    model: localStorage.getItem("ai-balancer-model") || "",
  };

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      let detail = "Request failed";
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }

  function setLoginVisible(visible) {
    const overlay = $("#loginOverlay");
    if (overlay) overlay.classList.toggle("hidden", !visible);
  }

  function showToast(message) {
    const toast = $("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    window.setTimeout(() => toast.classList.remove("show"), 3200);
  }

  function escapeText(value) {
    return typeof value === "string" ? value : "";
  }

  function cleanAssistantText(text) {
    return escapeText(text)
      .replace(/Ответ\s+через\s+ключ\s*#?\s*\d+\s*:?\s*/gi, "")
      .replace(/(?:ключ|key)\s*#\s*\d+\s*[:—-]?\s*/gi, "")
      .replace(/^\s*(?:AI Balancer|Assistant)\s*:\s*/i, "")
      .trim();
  }

  function formatTime(value) {
    if (!value) return "";
    try { return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(value)); } catch (_) { return ""; }
  }

  async function ensureSession() {
    try {
      const session = await request("/api/session");
      setLoginVisible(!session.authenticated);
      if (!session.authenticated) return;
      await loadStatus();
      if ($("#keysTable")) {
        await loadKeys();
        await loadFiles();
      } else {
        await loadModels();
        await loadChats();
        openSocket();
      }
    } catch (_) {
      setLoginVisible(true);
    }
  }

  async function loadStatus() {
    try {
      const status = await request("/api/status");
      ["activeKeys"].forEach((id) => { const element = document.getElementById(id); if (element) element.textContent = status.active_keys; });
      const paused = $("#pausedKeys"); if (paused) paused.textContent = status.paused_keys;
      const total = $("#totalKeys"); if (total) total.textContent = status.total_keys;
      const sidebarModel = $("#sidebarModel"); if (sidebarModel) sidebarModel.textContent = `${status.models.length} моделей · ${status.providers.length} провайдера`;
      const badges = document.querySelectorAll("#connectionBadge");
      badges.forEach((badge) => { badge.textContent = status.proxy_enabled ? "Пул подключён · proxy on" : "Пул подключён"; });
    } catch (_) {
      const badge = $("#connectionBadge");
      if (badge) { badge.textContent = "Нет соединения"; badge.style.color = "var(--red)"; }
    }
  }

  async function loadModels() {
    const select = $("#modelSelect");
    if (!select) return;
    try {
      const payload = await request("/api/models");
      select.innerHTML = "";
      if (!payload.models.length) {
        select.innerHTML = '<option value="">Добавьте API-ключ</option>';
        return;
      }
      payload.models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = `${model.id} · ${model.label}`;
        select.append(option);
      });
      if (!state.model || !payload.models.some((item) => item.id === state.model)) state.model = payload.models[0].id;
      select.value = state.model;
      select.addEventListener("change", () => {
        state.model = select.value;
        localStorage.setItem("ai-balancer-model", state.model);
      });
    } catch (_) {}
  }

  async function loadChats() {
    const list = await request("/api/chats");
    state.chats = list;
    if (!state.chats.length) {
      const created = await request("/api/chats", { method: "POST", body: JSON.stringify({}) });
      state.chats = [created];
    }
    renderChatList();
    const active = state.chats.find((chat) => chat.id === state.activeChatId) || state.chats[0];
    await loadChat(active.id);
  }

  function renderChatList() {
    const list = $("#chatList");
    if (!list) return;
    list.innerHTML = "";
    if (!state.chats.length) {
      list.innerHTML = '<div class="empty-state">Чатов пока нет.</div>';
      return;
    }
    state.chats.forEach((chat) => {
      const row = document.createElement("div");
      row.className = `chat-list-row ${chat.id === state.activeChatId ? "active" : ""}`;
      const button = document.createElement("button");
      button.className = "chat-list-item";
      button.type = "button";
      button.innerHTML = '<span class="chat-icon">C</span><span class="chat-list-copy"><strong></strong><small></small></span>';
      button.querySelector("strong").textContent = chat.title || "Новый чат";
      button.querySelector("small").textContent = `${chat.message_count || 0} сообщений · ${formatTime(chat.updated_at)}`;
      button.addEventListener("click", () => loadChat(chat.id));
      const deleteButton = document.createElement("button");
      deleteButton.className = "chat-delete";
      deleteButton.type = "button";
      deleteButton.textContent = "×";
      deleteButton.title = "Удалить чат";
      deleteButton.addEventListener("click", (event) => { event.stopPropagation(); deleteChat(chat.id); });
      row.append(button, deleteButton);
      list.append(row);
    });
  }

  async function loadChat(chatId) {
    const chat = await request(`/api/chats/${encodeURIComponent(chatId)}`);
    state.activeChatId = chat.id;
    state.messages = Array.isArray(chat.messages) ? chat.messages : [];
    localStorage.setItem("ai-balancer-active-chat", state.activeChatId);
    const title = $("#chatTitle"); if (title) title.textContent = chat.title || "Новый чат";
    renderChatList();
    renderMessages();
    closeMobileSidebar();
  }

  async function createChat() {
    const chat = await request("/api/chats", { method: "POST", body: JSON.stringify({}) });
    state.chats.unshift(chat);
    await loadChat(chat.id);
  }

  async function deleteChat(chatId) {
    if (!window.confirm("Удалить эту историю чата?")) return;
    try {
      await request(`/api/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" });
      state.chats = state.chats.filter((chat) => chat.id !== chatId);
      if (!state.chats.length) {
        await createChat();
      } else if (chatId === state.activeChatId) {
        await loadChat(state.chats[0].id);
      } else {
        renderChatList();
      }
    } catch (error) { showToast(error.message); }
  }

  function renderMessages() {
    const container = $("#messages");
    if (!container) return;
    container.innerHTML = "";
    if (!state.messages.length) {
      container.innerHTML = '<div class="welcome-message"><div class="welcome-orb">AB</div><h2>Готов к задаче</h2><p>Опишите проект, попросите код или настройку. Для запросов на сборку архив появится прямо в ответе.</p></div>';
      return;
    }
    state.messages.forEach((message) => addMessage(message.role, message.content, message.attachments, message.timestamp));
    container.scrollTop = container.scrollHeight;
  }

  function addMessage(role, text, attachments = [], timestamp = "") {
    const container = $("#messages");
    if (!container) return null;
    const welcome = container.querySelector(".welcome-message");
    if (welcome) welcome.remove();
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    const body = document.createElement("div");
    body.className = "message-bubble";
    const meta = document.createElement("div");
    meta.className = "message-label";
    meta.innerHTML = `<span>${role === "user" ? "Вы" : "AI Balancer"}</span><span>${formatTime(timestamp)}</span>`;
    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = role === "assistant" ? cleanAssistantText(text) : escapeText(text);
    body.append(meta, content);
    (attachments || []).forEach((file) => attachArchive(body, file));
    row.append(body);
    container.append(row);
    container.scrollTop = container.scrollHeight;
    return content;
  }

  function attachArchive(body, archive) {
    if (!archive || !archive.download_url) return;
    const link = document.createElement("a");
    link.className = "archive-link";
    link.href = archive.download_url;
    link.download = archive.filename;
    link.textContent = `Скачать ${archive.filename} · ${archive.file_count || 0} файлов`;
    body.append(link);
  }

  function openSocket() {
    if (state.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.socket.readyState)) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    state.socket = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);
    state.socket.onopen = () => { const badge = $("#connectionBadge"); if (badge) badge.textContent = "Готов к запросу"; };
    state.socket.onclose = () => {
      const badge = $("#connectionBadge"); if (badge) badge.textContent = "Переподключение…";
      if (!state.waiting) window.setTimeout(openSocket, 1200);
    };
    state.socket.onerror = () => { const badge = $("#connectionBadge"); if (badge) badge.textContent = "Ошибка соединения"; };
    state.socket.onmessage = async (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "route") {
        const indicator = $("#routeIndicator");
        if (indicator) indicator.textContent = `${payload.model} · ${payload.provider}`;
      } else if (payload.type === "token") {
        if (!state.assistantBubble) state.assistantBubble = addMessage("assistant", "");
        state.assistantRawText += payload.content || "";
        state.assistantBubble.textContent = cleanAssistantText(state.assistantRawText);
        $("#messages").scrollTop = $("#messages").scrollHeight;
      } else if (payload.type === "file" && state.assistantRow) {
        attachArchive(state.assistantRow.querySelector(".message-bubble"), payload.file);
      } else if (payload.type === "retry") {
        showToast(payload.message);
      } else if (payload.type === "error") {
        showToast(payload.message);
        state.waiting = false;
        setComposerEnabled(true);
      } else if (payload.type === "turn_complete") {
        state.waiting = false;
        setComposerEnabled(true);
        state.assistantBubble = null;
        state.assistantRow = null;
        state.assistantRawText = "";
        await loadChats();
      }
    };
  }

  function setComposerEnabled(enabled) {
    const input = $("#promptInput");
    const button = $("#sendButton");
    if (input) input.disabled = !enabled;
    if (button) button.disabled = !enabled;
  }

  function sendPrompt(prompt) {
    if (!prompt || state.waiting || !state.activeChatId) return;
    openSocket();
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) { showToast("Соединение ещё устанавливается."); return; }
    const message = { role: "user", content: prompt, timestamp: new Date().toISOString() };
    state.messages.push(message);
    addMessage("user", prompt, [], message.timestamp);
    state.assistantRawText = "";
    state.assistantBubble = addMessage("assistant", "", [], new Date().toISOString());
    state.assistantRow = state.assistantBubble ? state.assistantBubble.closest(".message-row") : null;
    state.waiting = true;
    setComposerEnabled(false);
    state.socket.send(JSON.stringify({ chat_id: state.activeChatId, messages: state.messages, model: state.model }));
  }

  async function loadKeys() {
    const table = $("#keysTable");
    if (!table) return;
    try {
      const keys = await request("/api/keys");
      table.innerHTML = "";
      if (!keys.length) { table.innerHTML = '<tr><td colspan="7" class="empty-cell">Добавьте первый ключ провайдера.</td></tr>'; return; }
      keys.forEach((key) => {
        const row = document.createElement("tr");
        const requestPercent = percent(key.remaining_requests, key.limit_requests);
        const tokenPercent = percent(key.remaining_tokens, key.limit_tokens);
        row.innerHTML = `
          <td><strong>${escapeHtml(key.name)}</strong><small class="provider-label">${escapeHtml(key.provider || "unknown")}</small></td>
          <td><span class="status-pill ${escapeHtml(key.status_tone)}">${escapeHtml(key.status_label)}</span></td>
          <td>${progressCell(key.remaining_requests, key.limit_requests, requestPercent)}</td>
          <td>${progressCell(key.remaining_tokens, key.limit_tokens, tokenPercent)}</td>
          <td><div class="model-tags">${(key.models || []).slice(0, 5).map((model) => `<span>${escapeHtml(model)}</span>`).join("") || "—"}</div></td>
          <td>${key.priority}</td>
          <td class="row-actions"><button class="row-action" data-edit="${key.id}">Изменить</button>${key.status === "paused" ? `<button class="row-action" data-resume="${key.id}">Возобновить</button>` : ""}<button class="row-action danger" data-delete="${key.id}">Удалить</button></td>`;
        table.append(row);
      });
    } catch (error) { showToast(error.message); }
  }

  function escapeHtml(value) {
    return escapeText(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
  }

  function percent(value, limit) {
    if (typeof value !== "number" || typeof limit !== "number" || limit <= 0) return null;
    return Math.max(0, Math.min(100, Math.round(value / limit * 100)));
  }

  function progressCell(value, limit, percentage) {
    if (percentage === null) return '<span class="muted-value">Нет данных</span>';
    return `<div class="progress-wrap"><div class="progress-bar"><span style="width:${percentage}%"></span></div><small>${value} / ${limit}</small></div>`;
  }

  async function loadFiles() {
    const list = $("#filesList");
    if (!list) return;
    try {
      const files = await request("/api/files");
      list.innerHTML = files.length ? "" : '<div class="empty-state">Архивов пока нет.</div>';
      files.forEach((file) => {
        const row = document.createElement("div");
        row.className = "file-row";
        row.innerHTML = `<div><strong>${escapeHtml(file.filename)}</strong><small>${formatTime(file.created_at)} · ${file.size_bytes} байт</small></div><a class="file-download" href="${file.download_url}" download>Скачать ↗</a>`;
        list.append(row);
      });
    } catch (error) { showToast(error.message); }
  }

  function closeMobileSidebar() {
    const sidebar = $("#chatSidebar");
    if (sidebar) sidebar.classList.remove("mobile-open");
  }

  document.addEventListener("DOMContentLoaded", () => {
    ensureSession();
    $("#loginForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const error = $("#loginError");
      try { await request("/api/login", { method: "POST", body: JSON.stringify({ password: $("#passwordInput").value }) }); setLoginVisible(false); await ensureSession(); }
      catch (exception) { error.textContent = exception.message; }
    });
    $("#logoutButton")?.addEventListener("click", async () => {
      await request("/api/logout", { method: "POST" });
      setLoginVisible(true);
      if (state.socket) state.socket.close();
    });
    $("#newChatButton")?.addEventListener("click", createChat);
    $("#mobileMenuButton")?.addEventListener("click", () => $("#chatSidebar")?.classList.toggle("mobile-open"));
    $("#sidebarClose")?.addEventListener("click", closeMobileSidebar);
    $("#chatForm")?.addEventListener("submit", (event) => { event.preventDefault(); const input = $("#promptInput"); sendPrompt(input.value.trim()); input.value = ""; });
    $("#promptInput")?.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chatForm").requestSubmit(); } });
    document.querySelectorAll(".prompt-chip").forEach((chip) => chip.addEventListener("click", () => { $("#promptInput").value = chip.dataset.prompt || ""; $("#promptInput").focus(); }));
    $("#openKeyModal")?.addEventListener("click", () => $("#keyModal").classList.remove("hidden"));
    $("#closeKeyModal")?.addEventListener("click", () => $("#keyModal").classList.add("hidden"));
    $("#keyValue")?.addEventListener("blur", async () => {
      const key = $("#keyValue").value.trim();
      if (!key) return;
      const target = $("#detectedProvider");
      target.textContent = "Проверяю провайдера и доступные модели…";
      try {
        const result = await request("/api/detect_provider", { method: "POST", body: JSON.stringify({ name: "detect", key, endpoint: $("#keyEndpoint").value || null, priority: 100 }) });
        target.textContent = `${result.label}: ${result.models.slice(0, 4).join(", ")}${result.error ? " · API моделей временно недоступен, сохранены безопасные значения по умолчанию" : ""}`;
      } catch (error) { target.textContent = error.message; }
    });
    $("#keyForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const error = $("#keyError");
      try {
        await request("/api/keys", { method: "POST", body: JSON.stringify({ name: $("#keyName").value, key: $("#keyValue").value, endpoint: $("#keyEndpoint").value || null, priority: Number($("#keyPriority").value || 100) }) });
        $("#keyForm").reset(); $("#keyModal").classList.add("hidden"); showToast("Ключ добавлен и зашифрован."); await loadKeys(); await loadStatus();
      } catch (exception) { error.textContent = exception.message; }
    });
    $("#keysTable")?.addEventListener("click", async (event) => {
      const target = event.target;
      try {
        if (target.dataset.delete) {
          if (!window.confirm("Удалить этот API-ключ?")) return;
          await request(`/api/keys/${target.dataset.delete}`, { method: "DELETE" }); showToast("Ключ удалён.");
        } else if (target.dataset.resume) {
          await request(`/api/keys/${target.dataset.resume}/resume`, { method: "POST" }); showToast("Ключ снова активен.");
        } else if (target.dataset.edit) {
          const name = window.prompt("Новое название ключа:");
          if (!name) return;
          const priority = Number(window.prompt("Приоритет (меньше — раньше):", "100"));
          await request(`/api/keys/${target.dataset.edit}`, { method: "PATCH", body: JSON.stringify({ name, priority: Number.isFinite(priority) ? priority : 100 }) });
          showToast("Настройки ключа обновлены.");
        }
        await loadKeys(); await loadStatus();
      } catch (exception) { showToast(exception.message); }
    });
  });
})();