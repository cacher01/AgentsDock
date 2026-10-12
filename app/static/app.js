(function () {
  const $ = (id) => document.getElementById(id);
  const platformMode =
    window.location.port === "8080" ||
    new URLSearchParams(window.location.search).has("platform");
  const nativeFetch = window.fetch.bind(window);
  const apiPath = (path) => {
    if (!platformMode || !String(path).startsWith("/")) return path;
    if (String(path).startsWith("/api/v1/")) return path;
    return "/api/v1" + path;
  };
  function fetch(resource, options) {
    const target = typeof resource === "string" ? apiPath(resource) : resource;
    return nativeFetch(target, { credentials: "same-origin", ...options });
  }
  const form = $("taskForm");
  const input = $("questionInput");
  const submitButton = $("submitButton");
  const cancelButton = $("cancelButton");
  const shareButton = $("shareButton");
  const artifactsEl = $("artifacts");
  const statusEl = $("taskStatus");
  const taskIdEl = $("taskId");
  const workflowEl = $("workflowLabel");
  const eventCountEl = $("eventCount");
  const eventsEl = $("eventStream");
  const graphEl = $("executionTree");
  const graphEmptyEl = $("treeEmpty");
  const selectedEventEl = $("selectedEvent");
  const answerEl = $("finalAnswer");
  const citationsEl = $("citations");
  const detailsEl = $("executionDetails");
  const confidenceEl = $("confidenceLabel");

  let source = null;
  let activeTaskId = null;
  let resultFetched = false;
  let eventRecords = [];
  let graphNodes = new Map();
  let graphEdges = new Map();
  let activeSessionId = "";
  let selectedFiles = [];
  const terminalStatuses = new Set(["completed", "degraded", "failed", "cancelled"]);
  // Mirror of _requests_artifact() triggers in orchestrator.py — used to show a hint badge
  const ARTIFACT_TRIGGERS = /生成报告|导出|保存为文件|生成文件|压缩包|打包|csv|json\s*file|markdown\s*file|report\s*file|export|\bzip\b/i;

  function setStatus(value) {
    const status = value || "未知";
    statusEl.textContent = status;
    statusEl.dataset.state = status.toLowerCase();
  }

  function resetView(question) {
    if (source) source.close();
    source = null;
    activeTaskId = null;
    resultFetched = false;
    eventRecords = [];
    graphNodes = new Map();
    graphEdges = new Map();
    setStatus("提交中");
    taskIdEl.textContent = "创建任务中";
    workflowEl.textContent = "等待";
    eventCountEl.textContent = "0";
    eventsEl.innerHTML = "";
    graphEl.innerHTML = "";
    graphEmptyEl.hidden = false;
    selectedEventEl.textContent = "点击节点查看详情。";
    cancelButton.disabled = true;
    shareButton.disabled = true;
    answerEl.textContent = "任务运行中…";
    answerEl.classList.add("empty");
    citationsEl.innerHTML = "<li>暂无来源。</li>";
    citationsEl.classList.add("empty");
    if (artifactsEl) {
      artifactsEl.innerHTML = '<li class="artifact-empty">本任务未请求导出文件。提示：在任务中加入 <code>生成报告</code> / <code>导出 csv</code> / <code>打包 zip</code> 等关键词即可生成产物。</li>';
      artifactsEl.classList.add("empty");
    }
    const hintEl = $("artifactHint");
    if (hintEl) hintEl.hidden = !ARTIFACT_TRIGGERS.test(question || "");
    detailsEl.textContent = "{}";
    confidenceEl.textContent = "置信度 --";
    if ($("commentsSection")) $("commentsSection").hidden = !platformMode;
    if ($("commentList")) $("commentList").innerHTML = '<div class="empty-state">暂无评论。</div>';
  }

  function parseJson(value) {
    try {
      return JSON.parse(value);
    } catch (_) {
      return value;
    }
  }

  function pretty(value) {
    return typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  function eventType(payload, fallback) {
    return String(payload && (payload.type || payload.event_type) || fallback || "event");
  }

  function addEvent(name, payload) {
    const record = { name: name || "message", payload };
    eventRecords.push(record);
    eventCountEl.textContent = String(eventRecords.length);
    updateRunMetadata(record);
    updateGraphFromEvent(record);
    renderEvent(record, eventRecords.length);
  }

  function updateRunMetadata(record) {
    const type = eventType(record.payload, record.name);
    const payload = record.payload && record.payload.payload;
    if (type === "workflow_selected" && payload && payload.workflow) {
      workflowEl.textContent = payload.workflow;
    }
  }

  function renderEvent(record, index) {
    const type = eventType(record.payload, record.name);
    const summary = record.payload && record.payload.summary || type.replaceAll("_", " ");
    const item = document.createElement("li");
    item.className = `activity-item ${classForType(type)}`;
    item.innerHTML = `
      <button type="button">
        <span class="activity-index">${String(index).padStart(2, "0")}</span>
        <span class="activity-copy">
          <strong>${escapeHtml(summary)}</strong>
          <small>${escapeHtml(type)}</small>
        </span>
      </button>`;
    item.querySelector("button").addEventListener("click", () => {
      selectInspector(record.payload, item);
    });
    eventsEl.append(item);
    eventsEl.scrollTop = eventsEl.scrollHeight;
  }

  function selectInspector(payload, selectedElement) {
    document.querySelectorAll(".is-selected").forEach((item) => item.classList.remove("is-selected"));
    if (selectedElement) selectedElement.classList.add("is-selected");
    selectedEventEl.textContent = pretty(payload);
  }

  function classForType(type) {
    if (type.includes("failed") || type.includes("error")) return "is-error";
    if (type.includes("cancelled") || type.includes("blocked")) return "is-muted";
    if (type.includes("completed") || type.includes("answer")) return "is-complete";
    if (type.includes("tool")) return "is-tool";
    if (type.includes("agent")) return "is-agent";
    if (type.includes("workflow") || type.includes("plan")) return "is-workflow";
    return "is-neutral";
  }

  function updateGraphFromEvent(record) {
    const payload = record.payload && record.payload.payload;
    const type = eventType(record.payload, record.name);
    if (!payload) return;
    if (type === "graph_node_created" || type === "graph_node_updated") {
      graphNodes.set(payload.node_id, payload);
      renderWorkflowGraph();
    } else if (type === "graph_edge_created") {
      graphEdges.set(payload.edge_id, payload);
      renderWorkflowGraph();
    }
  }

  function renderWorkflowGraph() {
    const nodes = Array.from(graphNodes.values());
    const edges = Array.from(graphEdges.values());
    graphEmptyEl.hidden = nodes.length > 0;
    if (!nodes.length) {
      graphEl.innerHTML = "";
      return;
    }

    const levels = calculateLevels(nodes, edges);
    // Type priority for tie-breaking — Final answer / result type always last
    const typeOrder = { workflow: 0, subtask: 1, agent: 2, tool: 3, evidence: 4, result: 5 };
    const order = [...nodes].sort((left, right) => {
      const levelDiff = (levels.get(left.node_id) || 0) - (levels.get(right.node_id) || 0);
      if (levelDiff !== 0) return levelDiff;
      const leftP = typeOrder[left.node_type] ?? 99;
      const rightP = typeOrder[right.node_type] ?? 99;
      if (leftP !== rightP) return leftP - rightP;
      return String(left.node_id).localeCompare(String(right.node_id));
    });
    graphEl.innerHTML = order.map((node) => {
      const status = escapeHtml(node.status || "pending");
      const type = escapeHtml(node.node_type || "node");
      const label = escapeHtml(node.label || node.node_id);
      const depth = Math.min(levels.get(node.node_id) || 0, 4);
      const parentEdge = edges.find((edge) => edge.target === node.node_id);
      const relation = parentEdge ? parentEdge.edge_type : "root";
      const icon = nodeIcon(type);
      const statusLabel = nodeStatusLabel(status);
      return `
        <div class="tree-row" style="--depth:${depth}">
          <div class="tree-connector ${depth > 0 ? "has-parent" : ""}">
            <span class="tree-dot type-${type} status-${status}">${icon}</span>
          </div>
          <button class="tree-node" type="button" data-node-id="${escapeHtml(node.node_id)}" data-type="${type}" data-status="${status}">
            <span class="node-copy">
              <strong>${label}</strong>
              <small>${type} · ${escapeHtml(relation)}</small>
            </span>
            <span class="node-badge status-${status}">${statusLabel}</span>
          </button>
        </div>`;
    }).join("");
    graphEl.querySelectorAll(".tree-node").forEach((element) => {
      element.addEventListener("click", () => {
        selectInspector(graphNodes.get(element.dataset.nodeId) || {}, element);
      });
    });
  }

  function nodeIcon(type) {
    if (type === "workflow") return "⚙";
    if (type === "agent") return "▸";
    if (type === "tool") return "◆";
    if (type === "result") return "✓";
    return "●";
  }

  function nodeStatusLabel(status) {
    if (status === "completed") return "完成";
    if (status === "degraded") return "降级";
    if (status === "failed" || status === "blocked") return "失败";
    if (status === "running") return "运行中";
    return "等待";
  }

  function calculateLevels(nodes, edges) {
    const levels = new Map(nodes.map((node) => [node.node_id, 0]));
    for (let pass = 0; pass < nodes.length; pass += 1) {
      let changed = false;
      edges.forEach((edge) => {
        if (!levels.has(edge.source) || !levels.has(edge.target)) return;
        const next = levels.get(edge.source) + 1;
        if (next > levels.get(edge.target)) {
          levels.set(edge.target, next);
          changed = true;
        }
      });
      if (!changed) break;
    }
    return levels;
  }

  function renderCitations(citations) {
    citationsEl.innerHTML = "";
    citationsEl.classList.toggle("empty", !citations.length);
    if (!citations.length) {
      citationsEl.innerHTML = "<li>未提供引用来源。</li>";
      return;
    }
    citations.forEach((citation, index) => {
      const item = document.createElement("li");
      const title = citation.title || citation.source || `Source ${index + 1}`;
      const url = citation.url || citation.source_url;
      item.innerHTML = url
        ? `<span>${String(index + 1).padStart(2, "0")}</span><a href="${escapeAttribute(url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
        : `<span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(title)}</strong>`;
      citationsEl.append(item);
    });
  }

  function renderResult(result) {
    const answer = result.final_answer || result.answer || result.output || result.result || "";
    answerEl.innerHTML = renderMarkdown(String(answer || ""));
    answerEl.classList.toggle("empty", !answer);
    renderCitations(Array.isArray(result.citations) ? result.citations : []);
    detailsEl.textContent = pretty(result);
    confidenceEl.textContent = typeof result.confidence === "number"
      ? `Confidence ${Math.round(result.confidence * 100)}%`
      : "置信度 --";
  }

  function renderMarkdown(text) {
    if (!text) return "未返回答案。";
    const lines = text.split(/\r?\n/);
    let html = "";
    let unorderedOpen = false;
    let orderedOpen = false;
    let codeOpen = false;
    for (let index = 0; index < lines.length; index += 1) {
      const raw = lines[index];
      const line = raw.trim();
      if (line.startsWith("```")) {
        if (unorderedOpen) { html += "</ul>"; unorderedOpen = false; }
        if (orderedOpen) { html += "</ol>"; orderedOpen = false; }
        html += codeOpen ? "</code></pre>" : "<pre><code>";
        codeOpen = !codeOpen;
        continue;
      }
      if (codeOpen) {
        html += `${escapeHtml(raw)}\n`;
        continue;
      }
      if (
        line.includes("|")
        && index + 1 < lines.length
        && isMarkdownTableSeparator(lines[index + 1])
      ) {
        if (unorderedOpen) { html += "</ul>"; unorderedOpen = false; }
        if (orderedOpen) { html += "</ol>"; orderedOpen = false; }
        const table = renderMarkdownTable(lines, index);
        html += table.html;
        index = table.lastIndex;
        continue;
      }
      if (!line) {
        if (unorderedOpen) { html += "</ul>"; unorderedOpen = false; }
        if (orderedOpen) { html += "</ol>"; orderedOpen = false; }
        continue;
      }
      if (line.startsWith("### ")) html += `<h4>${inlineMarkdown(line.slice(4))}</h4>`;
      else if (line.startsWith("## ")) html += `<h3>${inlineMarkdown(line.slice(3))}</h3>`;
      else if (line.startsWith("# ")) html += `<h2>${inlineMarkdown(line.slice(2))}</h2>`;
      else if (line.startsWith("> ")) html += `<blockquote>${inlineMarkdown(line.slice(2))}</blockquote>`;
      else if (/^[-*]\s+/.test(line)) {
        if (orderedOpen) { html += "</ol>"; orderedOpen = false; }
        if (!unorderedOpen) {
          html += "<ul>";
          unorderedOpen = true;
        }
        html += `<li>${inlineMarkdown(line.replace(/^[-*]\s+/, ""))}</li>`;
      } else if (/^\d+\.\s+/.test(line)) {
        if (unorderedOpen) { html += "</ul>"; unorderedOpen = false; }
        if (!orderedOpen) {
          html += "<ol>";
          orderedOpen = true;
        }
        html += `<li>${inlineMarkdown(line.replace(/^\d+\.\s+/, ""))}</li>`;
      } else {
        if (unorderedOpen) { html += "</ul>"; unorderedOpen = false; }
        if (orderedOpen) { html += "</ol>"; orderedOpen = false; }
        html += `<p>${inlineMarkdown(line)}</p>`;
      }
    }
    if (unorderedOpen) html += "</ul>";
    if (orderedOpen) html += "</ol>";
    if (codeOpen) html += "</code></pre>";
    return html;
  }

  function renderMarkdownTable(lines, startIndex) {
    const headers = splitTableRow(lines[startIndex]);
    const rows = [];
    let index = startIndex + 2;
    while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
      rows.push(splitTableRow(lines[index]));
      index += 1;
    }
    const head = headers.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("");
    const body = rows.map((row) => {
      const cells = headers.map((_, cellIndex) => (
        `<td>${inlineMarkdown(row[cellIndex] || "")}</td>`
      )).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    return {
      html: `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`,
      lastIndex: index - 1,
    };
  }

  function isMarkdownTableSeparator(line) {
    return splitTableRow(line).every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "")
      .split("|").map((cell) => cell.trim());
  }

  function inlineMarkdown(text) {
    return escapeHtml(text)
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  }

  async function fetchResult(taskId) {
    if (resultFetched) return;
    const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/result`);
    if (!response.ok) throw new Error(`Result request failed: ${response.status}`);
    const result = await response.json();
    resultFetched = true;
    renderResult(result);
    await fetchArtifacts(taskId);
  }

  async function refreshTaskOutcome(taskId) {
    const response = await fetch(`/tasks/${encodeURIComponent(taskId)}`);
    if (!response.ok) throw new Error(`Task status request failed: ${response.status}`);
    const task = await response.json();
    const status = String(task.status || "").toLowerCase();
    if (task.status) setStatus(task.status);
    if (status === "completed" || status === "degraded") {
      await fetchResult(taskId);
    } else if (status === "failed") {
      await showTaskFailure(taskId, task);
    }
  }

  async function showTaskFailure(taskId, detail) {
    let replay = null;
    try {
      const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/replay`);
      if (response.ok) replay = await response.json();
    } catch (_) {
      replay = null;
    }
    const events = replay && Array.isArray(replay.events) ? replay.events : [];
    const lastFailure = [...events].reverse().find((event) => event.type === "task_failed");
    const message =
      detail.error_summary ||
      (detail.payload && detail.payload.error) ||
      (lastFailure && lastFailure.payload && lastFailure.payload.error) ||
      "任务在生成结果前失败。";
    answerEl.textContent = message;
    answerEl.classList.remove("empty");
    citationsEl.innerHTML = "<li>No sources provided.</li>";
    citationsEl.classList.add("empty");
    confidenceEl.textContent = "Confidence --";
    detailsEl.textContent = pretty(replay || detail);
  }

  async function fetchArtifacts(taskId) {
    if (!artifactsEl) return;
    const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/artifacts`);
    if (!response.ok) return;
    const artifactPayload = await response.json();
    const artifacts = Array.isArray(artifactPayload)
      ? artifactPayload
      : (artifactPayload.items || []);
    const hintEl = $("artifactHint");
    if (hintEl) hintEl.hidden = artifacts.length === 0;
    artifactsEl.innerHTML = "";
    artifactsEl.classList.toggle("empty", artifacts.length === 0);
    if (!artifacts.length) {
      artifactsEl.innerHTML = '<li class="artifact-empty">本任务未请求导出文件。提示：在任务中加入 <code>生成报告</code> / <code>导出 csv</code> / <code>打包 zip</code> 等关键词即可生成产物。</li>';
      return;
    }
    artifacts.forEach((artifact, index) => {
      const item = document.createElement("li");
      const artifactId = artifact.artifact_id || artifact.id;
      const suffix = platformMode ? "/download" : "";
      const url = apiPath(
        "/tasks/" + encodeURIComponent(taskId) + "/artifacts/" +
        encodeURIComponent(artifactId) + suffix
      );
      const sizeKb = (artifact.size_bytes / 1024).toFixed(1);
      item.innerHTML = `<span>${String(index + 1).padStart(2, "0")}</span><a href="${url}" download>${escapeHtml(artifact.filename)}</a><small class="artifact-size">${sizeKb} KB</small>`;
      artifactsEl.append(item);
    });
  }

  function handleStreamEvent(event, eventName) {
    const payload = parseJson(event.data);
    addEvent(eventName || event.type, payload);
    const namedStatus = terminalStatuses.has(eventName) ? eventName : "";
    const status = payload && (payload.status || payload.task_status || payload.state) || namedStatus;
    if (status) setStatus(status);
    if (activeTaskId && status && terminalStatuses.has(String(status).toLowerCase())) {
      if (source) source.close();
      cancelButton.disabled = true;
      loadHistory();
      refreshChip();
      if (!popoverEl.hidden) refreshQuotaDashboard();
      if (String(status).toLowerCase() === "failed") {
        showTaskFailure(activeTaskId, payload).catch((error) => addEvent("result_error", error.message));
      } else if (String(status).toLowerCase() === "cancelled") {
        answerEl.textContent = "任务已取消，未生成最终结果。";
        answerEl.classList.remove("empty");
      } else {
        fetchResult(activeTaskId).catch((error) => addEvent("result_error", error.message));
      }
    }
  }

  function connectStream(taskId) {
    source = new EventSource(
      apiPath("/tasks/" + encodeURIComponent(taskId) + "/stream")
    );
    source.onopen = () => setStatus("运行中");
    source.onmessage = (event) => handleStreamEvent(event, "message");
    source.onerror = () => {
      if (activeTaskId && !resultFetched) {
        refreshTaskOutcome(activeTaskId).catch(() => setStatus("连接错误"));
      } else if (!resultFetched) {
        setStatus("连接错误");
      }
    };
    ["event", "completed", "degraded", "failed", "cancelled"].forEach((name) => {
      source.addEventListener(name, (event) => handleStreamEvent(event, name));
    });
  }

  function renderSelectedFiles() {
    const list = $("fileList");
    list.innerHTML = selectedFiles.map((entry) =>
      '<span class="file-item" data-state="' + escapeAttribute(entry.state) +
      '" title="' + escapeAttribute(entry.file.name) + '">' +
      escapeHtml(entry.file.name) +
      (entry.state === "uploading" ? " | uploading" : "") +
      (entry.state === "uploaded" ? " | ready" : "") +
      (entry.state === "failed" ? " | failed" : "") +
      '</span>'
    ).join("");
  }

  $("fileInput").addEventListener("change", (event) => {
    selectedFiles = Array.from(event.target.files || []).map((file) => ({
      file,
      state: "pending",
      inputFileId: "",
    }));
    renderSelectedFiles();
  });

  async function uploadSelectedFiles() {
    const ids = [];
    for (const entry of selectedFiles) {
      if (entry.inputFileId) {
        ids.push(entry.inputFileId);
        continue;
      }
      entry.state = "uploading";
      renderSelectedFiles();
      const response = await fetch("/files", {
        method: "POST",
        headers: {
          "Content-Type": entry.file.type || "application/octet-stream",
          "X-Filename": entry.file.name,
        },
        body: entry.file,
      });
      if (!response.ok) {
        entry.state = "failed";
        renderSelectedFiles();
        throw new Error("File upload or parsing failed: " + entry.file.name);
      }
      const payload = await response.json();
      entry.inputFileId = payload.input_file_id;
      entry.state = "uploaded";
      ids.push(entry.inputFileId);
      renderSelectedFiles();
    }
    return ids;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    resetView(question);
    submitButton.disabled = true;
    try {
      const inputFileIds = platformMode
        ? await uploadSelectedFiles()
        : [];
      const endpoint = activeSessionId
        ? `/sessions/${encodeURIComponent(activeSessionId)}/tasks`
        : "/tasks";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input: question,
          input_file_ids: inputFileIds,
          model_variant_id: platformMode ? ($("modelSelect").value || null) : null,
        }),
      });
      if (!response.ok) throw new Error(`Task request failed: ${response.status}`);
      const payload = await response.json();
      activeTaskId = payload.task_id || payload.id || payload.taskId;
      if (!activeTaskId) throw new Error("Task response did not include a task_id.");
      taskIdEl.textContent = activeTaskId;
      setStatus(payload.status || "排队中");
      cancelButton.disabled = false;
      shareButton.disabled = !platformMode;
      if (platformMode) loadComments(activeTaskId);
      addEvent("task_created", payload);
      connectStream(activeTaskId);
      if (activeSessionId) loadSessionTasks(activeSessionId);
      if (platformMode) {
        selectedFiles = [];
        $("fileInput").value = "";
        renderSelectedFiles();
      }
    } catch (error) {
      setStatus("错误");
      addEvent("error", error.message);
    } finally {
      submitButton.disabled = false;
    }
  });

  cancelButton.addEventListener("click", async () => {
    if (!activeTaskId) return;
    cancelButton.disabled = true;
    try {
      const response = await fetch(`/tasks/${encodeURIComponent(activeTaskId)}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(`Cancel request failed: ${response.status}`);
      const payload = await response.json();
      setStatus(payload.status || "cancelled");
      addEvent("task_cancelled", payload);
    } catch (error) {
      addEvent("cancel_error", error.message);
    }
  });

  shareButton.addEventListener("click", async () => {
    if (!platformMode || !activeTaskId) return;
    const userId = window.prompt("Team member user ID");
    if (!userId || !userId.trim()) return;
    const response = await fetch(
      "/tasks/" + encodeURIComponent(activeTaskId) + "/shares",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          grantee_type: "user",
          grantee_id: userId.trim(),
          permission,
        }),
      }
    );
    if (!response.ok) {
      addEvent("share_error", "Task sharing failed");
      return;
    }
    addEvent("task_shared", { grantee_id: userId.trim() });
  });
  $("commentForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!platformMode || !activeTaskId) return;
    const body = $("commentBody").value.trim();
    if (!body) return;
    const mentioned = $("commentMention").value;
    const response = await fetch(
      "/tasks/" + encodeURIComponent(activeTaskId) + "/comments",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body,
          mentioned_user_ids: mentioned ? [mentioned] : [],
        }),
      }
    );
    if (!response.ok) {
      addEvent("comment_error", "Comment could not be sent");
      return;
    }
    $("commentBody").value = "";
    $("commentMention").value = "";
    await loadComments(activeTaskId);
  });
  $("notificationButton").addEventListener("click", async () => {
    $("notificationOverlay").hidden = false;
    await loadNotifications(true);
  });
  $("closeNotifications").addEventListener("click", () => {
    $("notificationOverlay").hidden = true;
  });
  document.querySelectorAll(".prompt-list button[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.prompt || "";
      input.focus();
    });
  });

  async function loadAvailableModels() {
    if (!platformMode) return;
    const response = await fetch("/api/v1/models");
    if (!response.ok) return;
    const payload = await response.json();
    const variants = (payload.items || []).flatMap((connection) =>
      (connection.variants || [])
        .filter((variant) => variant.enabled)
        .map((variant) => ({
          id: variant.id,
          label: connection.name + " / " + variant.model_name,
        }))
    );
    $("modelSelect").innerHTML = '<option value="">自动选择</option>' +
      variants.map((variant) => '<option value="' +
        escapeAttribute(variant.id) + '">' + escapeHtml(variant.label) +
        '</option>').join("");
    $("modelBar").hidden = false;
  }
  async function loadMembersForComments() {
    if (!platformMode) return;
    const response = await fetch("/api/v1/workspace/members");
    if (!response.ok) return;
    const payload = await response.json();
    const members = payload.items || payload.members || payload || [];
    $("commentMention").innerHTML = '<option value="">不提及成员</option>' +
      members.map((member) => {
        const id = member.user_id || member.UserID || member.id;
        const name = member.display_name || member.DisplayName || member.email || id;
        return '<option value="' + escapeAttribute(id) + '">' +
          escapeHtml(name) + '</option>';
      }).join("");
  }

  function renderComments(items) {
    const list = $("commentList");
    list.innerHTML = items.length
      ? items.map((comment) =>
          '<article class="comment-item"><header><strong>' +
          escapeHtml(comment.author_name || comment.author_user_id) +
          '</strong><time>' + formatDate(comment.created_at) +
          '</time></header><p>' + escapeHtml(comment.body) + '</p></article>'
        ).join("")
      : '<div class="empty-state">暂无评论。</div>';
  }

  async function loadComments(taskId) {
    if (!platformMode || !taskId) return;
    $("commentsSection").hidden = false;
    const response = await fetch(
      "/tasks/" + encodeURIComponent(taskId) + "/comments"
    );
    if (!response.ok) {
      $("commentList").innerHTML =
        '<div class="empty-state">评论不可用。</div>';
      return;
    }
    const payload = await response.json();
    renderComments(payload.items || []);
  }

  async function loadNotifications(openPanel) {
    if (!platformMode) return;
    const response = await fetch("/api/v1/notifications?limit=50");
    if (!response.ok) return;
    const payload = await response.json();
    const items = payload.items || [];
    const unread = items.filter((item) => !item.read_at).length;
    $("notificationCount").textContent = unread;
    $("notificationCount").hidden = unread === 0;
    if (!openPanel) return;
    $("notificationList").innerHTML = items.length
      ? items.map((item) => {
          const actor = item.payload &&
            (item.payload.author_name || item.payload.shared_by);
          return '<button type="button" class="notification-item" data-id="' +
            escapeAttribute(item.notification_id) + '" data-resource-id="' +
            escapeAttribute(item.resource_id) + '" data-read="' +
            Boolean(item.read_at) + '"><strong>' +
            escapeHtml(item.notification_type.replaceAll("_", " ")) +
            '</strong><span>' + escapeHtml(actor || item.resource_id) +
            '</span><time>' + formatDate(item.created_at) + '</time></button>';
        }).join("")
      : '<div class="empty-state">暂无通知。</div>';
    $("notificationList").querySelectorAll(".notification-item")
      .forEach((button) => button.addEventListener("click", async () => {
        if (button.dataset.read !== "true") {
          await fetch(
            "/api/v1/notifications/" + encodeURIComponent(button.dataset.id) +
              "/read",
            { method: "POST" }
          );
        }
        $("notificationOverlay").hidden = true;
        await replayTask(button.dataset.resourceId);
        await loadNotifications(false);
      }));
  }
  async function loadHistory() {
    const historyEl = $("taskHistory");
    historyEl.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const response = await fetch("/tasks?limit=50");
      if (!response.ok) throw new Error(`History request failed: ${response.status}`);
      const historyPayload = await response.json();
      const tasks = Array.isArray(historyPayload)
        ? historyPayload
        : (historyPayload.items || []);
      if (!tasks.length) {
        historyEl.innerHTML = '<div class="empty-state">暂无保存的任务。</div>';
        return;
      }
      historyEl.innerHTML = tasks.map((task) => `
        <button class="history-item" type="button" data-task-id="${escapeHtml(task.task_id)}">
          <span class="history-copy">
            <strong>${escapeHtml(task.input || task.input_text)}</strong>
            <small>${escapeHtml(task.workflow || "pending")} · ${formatDate(task.created_at)}</small>
          </span>
          <span class="history-status">${escapeHtml(task.status)}</span>
        </button>`).join("");
      historyEl.querySelectorAll(".history-item").forEach((button) => {
        button.addEventListener("click", () => replayTask(button.dataset.taskId));
      });
    } catch (error) {
      historyEl.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadSessions() {
    const select = $("sessionSelect");
    if (platformMode) {
      document.querySelector(".session-bar").hidden = true;
      document.querySelector(".session-panel").hidden = true;
      activeSessionId = "";
      return;
    }
    const previous = activeSessionId;
    const response = await fetch("/sessions?limit=50");
    if (!response.ok) return;
    const sessions = await response.json();
    select.innerHTML = '<option value="">— 无对话（独立任务）—</option>' + sessions.map(
      (session) => `<option value="${escapeHtml(session.session_id)}">${escapeHtml(session.title)} (${session.task_count})</option>`
    ).join("");
    if (previous && sessions.some((item) => item.session_id === previous)) {
      select.value = previous;
    }
  }

  async function createSession() {
    const title = window.prompt("给这个对话起个名字（同一对话内的任务会自动共享上下文）", "新对话");
    if (title === null) return;
    const response = await fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim() || "新对话" }),
    });
    if (!response.ok) return;
    const session = await response.json();
    activeSessionId = session.session_id;
    await loadSessions();
    $("sessionSelect").value = activeSessionId;
    await loadSessionTasks(activeSessionId);
  }

  async function loadSessionTasks(sessionId) {
    const container = $("sessionTasks");
    if (!sessionId) {
      container.innerHTML = '<div class="empty-state">请选择一个对话。<br>同一对话内的任务会自动注入前 5 个任务作为上下文。</div>';
      return;
    }
    const response = await fetch(`/sessions/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      container.innerHTML = '<div class="empty-state">对话不可用。</div>';
      return;
    }
    const payload = await response.json();
    if (!payload.tasks.length) {
      container.innerHTML = '<div class="empty-state">此对话暂无任务。<br>提交一个新任务即可开始。</div>';
      return;
    }
    container.innerHTML = payload.tasks.map((task, idx) => `
      <button class="history-item thread-item" type="button" data-task-id="${escapeHtml(task.task_id)}">
        <span class="thread-index">${idx + 1}</span>
        <span class="history-copy">
          <strong>${escapeHtml(task.input)}</strong>
          <small>${escapeHtml(task.workflow || "pending")} · ${formatDate(task.created_at)}</small>
        </span>
        <span class="history-status status-${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
      </button>`).join("");
    container.querySelectorAll(".history-item").forEach((button) => {
      button.addEventListener("click", () => replayTask(button.dataset.taskId));
    });
  }

  async function replayTask(taskId) {
    if (source) source.close();
    source = null;
    activeTaskId = taskId;
    const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/replay`);
    if (!response.ok) {
      selectInspector(`Replay request failed: ${response.status}`);
      return;
    }
    const replay = await response.json();
    resetView(replay.task.input || replay.task.input_text);
    activeTaskId = taskId;
    shareButton.disabled = !platformMode;
    taskIdEl.textContent = taskId;
    activeSessionId = replay.task.session_id || "";
    $("sessionSelect").value = activeSessionId;
    loadSessionTasks(activeSessionId);
    setStatus(replay.task.status);
    workflowEl.textContent = replay.task.workflow || "等待";
    cancelButton.disabled = true;
    replay.events.forEach((event) => addEvent("replay", event));
    if (replay.result) renderResult(replay.result);
    fetchArtifacts(taskId).catch(() => {});
    detailsEl.textContent = pretty(replay);
  }

  function formatDate(value) {
    if (!value) return "";
    try {
      return new Date(value).toLocaleString();
    } catch (_) {
      return value;
    }
  }

  $("refreshHistory").addEventListener("click", loadHistory);
  $("newSessionButton").addEventListener("click", createSession);

  // ── Quota dashboard ────────────────────────────────────────────
  const chipEl = $("quotaChip");
  const popoverEl = $("quotaPopover");
  let currentScope = "today";

  chipEl.addEventListener("click", async (event) => {
    event.stopPropagation();
    const isOpen = !popoverEl.hidden;
    if (isOpen) {
      closePopover();
    } else {
      openPopover();
      await refreshQuotaDashboard();
    }
  });
  document.addEventListener("click", (event) => {
    if (popoverEl.hidden) return;
    if (popoverEl.contains(event.target) || chipEl.contains(event.target)) return;
    closePopover();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popoverEl.hidden) closePopover();
  });
  popoverEl.querySelectorAll(".pop-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      popoverEl.querySelectorAll(".pop-tab").forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      currentScope = tab.dataset.scope;
      await refreshQuotaDashboard();
    });
  });

  function openPopover() {
    popoverEl.hidden = false;
    chipEl.setAttribute("aria-expanded", "true");
  }
  function closePopover() {
    popoverEl.hidden = true;
    chipEl.setAttribute("aria-expanded", "false");
  }

  async function refreshChip() {
    if (platformMode) {
      $("quotaChip").hidden = true;
      return;
    }
    try {
      const summary = await fetch("/quota/summary?scope=today").then((r) => r.json());
      const tokens = summary.tokens || 0;
      const cost = summary.cost_usd || 0;
      $("chipTokens").textContent = formatTokens(tokens) + " tokens";
      $("chipCost").textContent = "$" + cost.toFixed(cost < 0.01 ? 4 : 3);
    } catch (_) {
      $("chipTokens").textContent = "— tokens";
      $("chipCost").textContent = "$—";
    }
  }

  async function refreshQuotaDashboard() {
    try {
      const [summary, breakdown, timeline, limits, recent, sessions] = await Promise.all([
        fetch(`/quota/summary?scope=${currentScope}`).then((r) => r.json()),
        fetch(`/quota/breakdown?by=workflow&scope=${currentScope}`).then((r) => r.json()),
        fetch(`/quota/timeline?days=7`).then((r) => r.json()),
        fetch(`/quota/limits`).then((r) => r.json()),
        fetch(`/quota/recent?limit=12`).then((r) => r.json()),
        fetch(`/quota/sessions?limit=8`).then((r) => r.json()),
      ]);
      renderKpi(summary);
      renderLimits(limits);
      renderWorkflowBars(breakdown.items || []);
      renderTimeline(timeline.items || []);
      renderRecentTasks(recent.items || []);
      renderSessions(sessions.items || []);
    } catch (error) {
      console.warn("Quota dashboard refresh failed:", error);
    }
  }

  function renderKpi(summary) {
    const tasks = summary.tasks || 0;
    const completed = summary.completed || 0;
    const failed = summary.failed || 0;
    const degraded = summary.degraded || 0;
    const tokens = summary.tokens || 0;
    const cost = summary.cost_usd || 0;
    const successRate = (summary.success_rate || 0) * 100;
    const avgLatency = summary.avg_latency_seconds || 0;
    $("kpiTasks").textContent = String(tasks);
    $("kpiTasksSub").textContent =
      `✓${completed} · ✗${failed} · ⚠${degraded}`;
    $("kpiTokens").textContent = formatTokens(tokens);
    $("kpiTokensSub").textContent = tokens >= 1000 ? "tokens used" : "tokens";
    $("kpiCost").textContent = "$" + cost.toFixed(cost < 0.01 ? 4 : 3);
    $("kpiCostSub").textContent = "USD estimated";
    $("kpiSuccess").textContent = tasks ? successRate.toFixed(1) + "%" : "—";
    $("kpiSuccessSub").textContent = avgLatency > 0
      ? `avg ${avgLatency.toFixed(1)}s`
      : "no timing";
  }

  function renderLimits(limits) {
    const tokensPct = Math.round((limits.tokens_pct || 0) * 100);
    const costPct = Math.round((limits.cost_pct || 0) * 100);
    const tf = $("meterTokensFill");
    const cf = $("meterCostFill");
    tf.style.width = Math.min(100, tokensPct) + "%";
    tf.classList.toggle("warn", tokensPct >= 70 && tokensPct < 90);
    tf.classList.toggle("bad", tokensPct >= 90);
    cf.style.width = Math.min(100, costPct) + "%";
    cf.classList.toggle("warn", costPct >= 70 && costPct < 90);
    cf.classList.toggle("bad", costPct >= 90);
    $("meterTokensLabel").textContent =
      `${formatTokens(limits.today_tokens)} / ${formatTokens(limits.daily_tokens_cap)} (${tokensPct}%)`;
    $("meterCostLabel").textContent =
      `$${(limits.today_cost || 0).toFixed(3)} / $${(limits.daily_cost_cap || 0).toFixed(2)} (${costPct}%)`;
  }

  function renderWorkflowBars(items) {
    const host = $("chartWorkflow");
    const legend = $("chartWorkflowLegend");
    host.innerHTML = "";
    legend.innerHTML = "";
    if (!items.length) {
      host.innerHTML = '<div class="empty-state" style="padding: 30px 0;">暂无数据</div>';
      return;
    }
    const total = items.reduce((acc, x) => acc + (x.tokens || 0), 0) || 1;
    const max = Math.max(...items.map((x) => x.tokens || 0));
    const colors = ["#8b5cf6", "#ec4899", "#06b6d4", "#a78bfa", "#f0abfc", "#fbbf24"];
    const w = host.clientWidth || 280;
    const rowH = 18;
    const gap = 6;
    const labelW = 86;
    const valueW = 52;
    const barAreaW = w - labelW - valueW - 8;
    const h = items.length * (rowH + gap) + 4;
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "xMinYMid meet");
    items.forEach((item, i) => {
      const y = i * (rowH + gap) + 2;
      const len = ((item.tokens || 0) / max) * barAreaW;
      const color = colors[i % colors.length];
      // label
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", labelW - 6);
      label.setAttribute("y", y + rowH / 2 + 4);
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "11");
      label.setAttribute("font-family", "ui-monospace, JetBrains Mono, Consolas, monospace");
      label.setAttribute("fill", "#4b4368");
      label.textContent = String(item.key).slice(0, 12);
      svg.appendChild(label);
      // track
      const track = document.createElementNS(svgNS, "rect");
      track.setAttribute("x", labelW);
      track.setAttribute("y", y + 3);
      track.setAttribute("width", barAreaW);
      track.setAttribute("height", rowH - 6);
      track.setAttribute("rx", 4);
      track.setAttribute("fill", "rgba(139,92,246,0.10)");
      svg.appendChild(track);
      // bar
      const bar = document.createElementNS(svgNS, "rect");
      bar.setAttribute("x", labelW);
      bar.setAttribute("y", y + 3);
      bar.setAttribute("width", Math.max(2, len));
      bar.setAttribute("height", rowH - 6);
      bar.setAttribute("rx", 4);
      bar.setAttribute("fill", color);
      svg.appendChild(bar);
      // value
      const val = document.createElementNS(svgNS, "text");
      val.setAttribute("x", w - 4);
      val.setAttribute("y", y + rowH / 2 + 4);
      val.setAttribute("text-anchor", "end");
      val.setAttribute("font-size", "10.5");
      val.setAttribute("font-family", "ui-monospace, JetBrains Mono, Consolas, monospace");
      val.setAttribute("font-weight", "600");
      val.setAttribute("fill", "#16122b");
      val.textContent = formatTokens(item.tokens);
      svg.appendChild(val);
      // legend entry
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = color;
      const pct = (((item.tokens || 0) / total) * 100).toFixed(0);
      legend.appendChild(swatch);
      legend.appendChild(document.createTextNode(`${item.key} · ${pct}%`));
    });
    host.appendChild(svg);
  }

  function renderTimeline(items) {
    const host = $("chartTimeline");
    host.innerHTML = "";
    if (!items.length) {
      host.innerHTML = '<div class="empty-state" style="padding: 30px 0;">暂无数据</div>';
      return;
    }
    const w = host.clientWidth || 320;
    const h = 160;
    const padL = 32, padR = 8, padT = 12, padB = 24;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const max = Math.max(1, ...items.map((x) => x.tokens || 0));
    const stepX = plotW / Math.max(1, items.length - 1);
    const points = items.map((x, i) => ({
      x: padL + i * stepX,
      y: padT + plotH - ((x.tokens || 0) / max) * plotH,
      v: x.tokens || 0,
      day: x.day,
    }));
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "none");
    // gridlines
    [0, 0.5, 1].forEach((t) => {
      const y = padT + plotH * (1 - t);
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", padL);
      line.setAttribute("x2", w - padR);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("stroke", "rgba(124,86,205,0.10)");
      line.setAttribute("stroke-dasharray", "3 3");
      svg.appendChild(line);
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("x", padL - 6);
      label.setAttribute("y", y + 3);
      label.setAttribute("text-anchor", "end");
      label.setAttribute("font-size", "9");
      label.setAttribute("fill", "#7c7596");
      label.setAttribute("font-family", "ui-monospace, JetBrains Mono, Consolas, monospace");
      label.textContent = formatTokens(max * t);
      svg.appendChild(label);
    });
    // area path
    let area = `M ${points[0].x},${padT + plotH} `;
    points.forEach((p) => { area += `L ${p.x},${p.y} `; });
    area += `L ${points[points.length - 1].x},${padT + plotH} Z`;
    const areaEl = document.createElementNS(svgNS, "path");
    areaEl.setAttribute("d", area);
    const grad = document.createElementNS(svgNS, "linearGradient");
    const gid = "g" + Math.random().toString(36).slice(2, 7);
    grad.setAttribute("id", gid);
    grad.setAttribute("x1", "0"); grad.setAttribute("y1", "0");
    grad.setAttribute("x2", "0"); grad.setAttribute("y2", "1");
    grad.innerHTML =
      '<stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.32"/>' +
      '<stop offset="100%" stop-color="#8b5cf6" stop-opacity="0"/>';
    const defs = document.createElementNS(svgNS, "defs");
    defs.appendChild(grad);
    svg.appendChild(defs);
    areaEl.setAttribute("fill", `url(#${gid})`);
    svg.appendChild(areaEl);
    // line
    let linePath = `M ${points[0].x},${points[0].y}`;
    points.slice(1).forEach((p) => { linePath += ` L ${p.x},${p.y}`; });
    const lineEl = document.createElementNS(svgNS, "path");
    lineEl.setAttribute("d", linePath);
    lineEl.setAttribute("fill", "none");
    lineEl.setAttribute("stroke", "#8b5cf6");
    lineEl.setAttribute("stroke-width", "2");
    lineEl.setAttribute("stroke-linecap", "round");
    lineEl.setAttribute("stroke-linejoin", "round");
    svg.appendChild(lineEl);
    // dots
    points.forEach((p, i) => {
      const c = document.createElementNS(svgNS, "circle");
      c.setAttribute("cx", p.x);
      c.setAttribute("cy", p.y);
      c.setAttribute("r", i === points.length - 1 ? 4 : 2.5);
      c.setAttribute("fill", i === points.length - 1 ? "#ec4899" : "#8b5cf6");
      c.setAttribute("stroke", "#fff");
      c.setAttribute("stroke-width", "1.5");
      const t = document.createElementNS(svgNS, "title");
      t.textContent = `${p.day}: ${formatTokens(p.v)} tokens`;
      c.appendChild(t);
      svg.appendChild(c);
      // x-axis label (show every other to avoid clutter)
      if (i % 2 === 0 || i === points.length - 1) {
        const lbl = document.createElementNS(svgNS, "text");
        lbl.setAttribute("x", p.x);
        lbl.setAttribute("y", h - 6);
        lbl.setAttribute("text-anchor", "middle");
        lbl.setAttribute("font-size", "9.5");
        lbl.setAttribute("fill", "#7c7596");
        lbl.setAttribute("font-family", "ui-monospace, JetBrains Mono, Consolas, monospace");
        lbl.textContent = p.day.slice(5);
        svg.appendChild(lbl);
      }
    });
    host.appendChild(svg);
  }

  function renderRecentTasks(items) {
    const body = $("recentTasksBody");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-state" style="padding: 16px;">暂无任务</td></tr>';
      return;
    }
    body.innerHTML = items.map((t) => `
      <tr data-task-id="${escapeHtml(t.task_id)}">
        <td class="row-input" title="${escapeHtml(t.input)}">${escapeHtml(t.input)}</td>
        <td>${escapeHtml(t.workflow)}</td>
        <td><span class="status-chip ${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>
        <td class="num row-tokens">${formatTokens(t.tokens)}</td>
        <td class="num row-cost">$${(t.cost_usd || 0).toFixed(4)}</td>
      </tr>
    `).join("");
    body.querySelectorAll("tr[data-task-id]").forEach((row) => {
      row.addEventListener("click", () => {
        closePopover();
        replayTask(row.dataset.taskId);
      });
    });
  }

  function renderSessions(items) {
    const body = $("sessionsBody");
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty-state" style="padding: 16px;">暂无对话</td></tr>';
      return;
    }
    body.innerHTML = items.map((s) => `
      <tr>
        <td class="row-input" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</td>
        <td class="num">${s.tasks}</td>
        <td class="num row-tokens">${formatTokens(s.tokens)}</td>
        <td class="num row-cost">$${(s.cost_usd || 0).toFixed(4)}</td>
      </tr>
    `).join("");
  }

  function formatTokens(n) {
    if (n == null) return "—";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
    return String(n);
  }

  async function loadGovernance() {
    const response = await fetch("/api/v1/admin/governance/tasks?limit=100");
    if (!response.ok) throw new Error("Governance data unavailable");
    const payload = await response.json();
    const items = payload.items || [];
    $("governanceBody").innerHTML = items.length
      ? items.map((item) => {
          const cost = Number(item.cost || 0);
          const duration = item.duration_ms == null
            ? "-"
            : Math.round(item.duration_ms / 1000) + "s";
          return "<tr><td>" + escapeHtml(item.task_id) + "</td><td>" +
            escapeHtml(item.status) + "</td><td>" +
            escapeHtml(item.workflow || "pending") + "</td><td>" +
            formatTokens(item.token_estimate || 0) + "</td><td>$" +
            cost.toFixed(4) + "</td><td>" + duration + "</td></tr>";
        }).join("")
      : '<tr><td colspan="6" class="empty-state">No team tasks</td></tr>';
  }

  async function loadAdminResources() {
    const [modelsResponse, toolsResponse, budgetResponse, auditResponse] = await Promise.all([
      fetch("/api/v1/admin/models"),
      fetch("/api/v1/admin/tools"),
      fetch("/api/v1/admin/budget"),
      fetch("/api/v1/admin/audit?limit=50"),
    ]);
    if (modelsResponse.ok) {
      const payload = await modelsResponse.json();
      const models = payload.items || [];
      $("modelList").innerHTML = models.length
        ? models.map((connection) =>
            '<div class="admin-resource-item"><strong>' +
            escapeHtml(connection.name) + '</strong><small>' +
            escapeHtml(
              (connection.variants || [])
                .map((variant) => variant.model_name)
                .join(", ")
            ) + '</small></div>'
          ).join("")
        : '<div class="empty-state">No model configured</div>';
    }
    if (toolsResponse.ok) {
      const payload = await toolsResponse.json();
      const tools = payload.items || [];
      $("toolList").innerHTML = tools.map((tool) =>
        '<label class="tool-toggle"><input type="checkbox" ' +
        (tool.enabled ? "checked " : "") +
        'data-definition-id="' + escapeAttribute(tool.definition_id) +
        '"><span><strong>' + escapeHtml(tool.key) +
        '</strong><small>' + escapeHtml(tool.risk_level) +
        ' risk</small></span></label>'
      ).join("");
      $("toolList").querySelectorAll("input[data-definition-id]")
        .forEach((control) => {
          control.addEventListener("change", async () => {
            control.disabled = true;
            const response = await fetch(
              "/api/v1/admin/tools/" +
                encodeURIComponent(control.dataset.definitionId),
              {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  enabled: control.checked,
                  policy: {},
                  credential_ref: null,
                }),
              }
            );
            if (!response.ok) control.checked = !control.checked;
            control.disabled = false;
          });
        });
    }
    if (budgetResponse.ok) {
      const policy = await budgetResponse.json();
      $("budgetTokenLimit").value = policy.task_token_soft_limit;
      $("budgetTaskCostLimit").value = policy.task_cost_soft_limit;
      $("budgetDailyCostLimit").value = policy.daily_cost_soft_limit;
    }
    if (auditResponse.ok) {
      const payload = await auditResponse.json();
      const entries = payload.items || [];
      $("auditList").innerHTML = entries.length
        ? entries.map((entry) =>
            '<div class="admin-resource-item"><span><strong>' +
            escapeHtml(entry.action) + '</strong><small>' +
            escapeHtml(entry.resource_type + ": " + (entry.resource_id || "-")) +
            '</small></span><time>' +
            escapeHtml(new Date(entry.created_at).toLocaleString()) +
            '</time></div>'
          ).join("")
        : '<div class="empty-state">暂无审计记录。</div>';
    }
  }
  $("budgetForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const response = await fetch("/api/v1/admin/budget", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_token_soft_limit: Number($("budgetTokenLimit").value),
        task_cost_soft_limit: Number($("budgetTaskCostLimit").value),
        daily_cost_soft_limit: Number($("budgetDailyCostLimit").value),
      }),
    });
    $("budgetOutput").textContent = response.ok
      ? "软预算策略已保存。"
      : "软预算策略保存失败。";
  });
  $("modelForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const response = await fetch("/api/v1/admin/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("modelConnectionName").value.trim(),
        provider_type: "openai_compatible",
        base_url: $("modelBaseUrl").value.trim(),
        api_key: $("modelApiKey").value,
        model_name: $("modelName").value.trim(),
        capabilities: ["chat"],
        pricing: {
          input_per_million: Number($("modelInputPrice").value || 0),
          output_per_million: Number($("modelOutputPrice").value || 0),
        },
      }),
    });
    if (!response.ok) {
      window.alert("Model connection could not be saved.");
      return;
    }
    $("modelApiKey").value = "";
    window.alert("Model connection saved.");
    await loadAdminResources();
  });

  $("inviteForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const response = await fetch("/api/v1/workspace/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("inviteEmail").value.trim(),
        role: $("inviteRole").value,
      }),
    });
    if (!response.ok) {
      $("inviteOutput").textContent = "Invitation could not be created.";
      return;
    }
    const invitation = await response.json();
    $("inviteOutput").textContent =
      "Invitation token: " + invitation.token;
  });
  $("adminButton").addEventListener("click", async () => {
    $("governanceOverlay").hidden = false;
    try {
      await Promise.all([loadGovernance(), loadAdminResources()]);
    } catch (error) {
      $("governanceBody").innerHTML =
        '<tr><td colspan="6" class="empty-state">' +
        escapeHtml(error.message) + "</td></tr>";
    }
  });
  $("closeGovernance").addEventListener("click", () => {
    $("governanceOverlay").hidden = true;
  });

  async function startWorkspace() {
    if (platformMode) {
      const response = await fetch("/api/v1/me");
      if (!response.ok) {
        $("loginOverlay").hidden = false;
        return;
      }
      const principal = await response.json();
      const displayName =
        principal.display_name || principal.DisplayName ||
        principal.email || principal.Email || "User";
      const role = principal.role || principal.Role || "member";
      $("currentUser").textContent = displayName + " | " + role;
      $("currentUser").hidden = false;
      $("fileBar").hidden = false;
      shareButton.hidden = false;
      $("adminButton").hidden = role !== "admin";
      $("notificationButton").hidden = false;
      await Promise.all([
        loadAvailableModels(),
        loadMembersForComments(),
        loadNotifications(false),
      ]);
      window.setInterval(() => loadNotifications(false), 30_000);
      $("loginOverlay").hidden = true;
    } else {
      refreshChip();
      setInterval(refreshChip, 30_000);
    }
    await loadHistory();
    await loadSessions();
  }

  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("loginError").textContent = "";
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("loginEmail").value.trim(),
        password: $("loginPassword").value,
        workspace_id: $("loginWorkspace").value.trim(),
      }),
    });
    if (!response.ok) {
      $("loginError").textContent =
        "Login failed. Check account, password, and workspace.";
      return;
    }
    await startWorkspace();
  });

  $("sessionSelect").addEventListener("change", async (event) => {
    activeSessionId = event.target.value;
    await loadSessionTasks(activeSessionId);
  });
  startWorkspace().catch((error) => {
    setStatus("startup failed");
    addEvent("startup_error", error.message);
  });

  function shortLabel(value, limit) {
    const text = String(value || "");
    return escapeHtml(text.length > limit ? `${text.slice(0, limit)}...` : text);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }
})();
