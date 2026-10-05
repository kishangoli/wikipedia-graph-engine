// Python owns the search. This page renders snapshots and incremental events.
const form = document.querySelector("#search-form");
const searchButton = document.querySelector("#search-button");
const cancelButton = document.querySelector("#cancel-button");
const statusText = document.querySelector("#status");
const statsText = document.querySelector("#stats");
const routeList = document.querySelector("#route");
let network = null;
let searchId = null;
let stream = null;
let state = null;
let generation = 0;
let lastUpdate = performance.now();
let elapsedAtUpdate = 0;
let connectionMessage = "";

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the article inputs and try again.");
  return data;
}

function articleLink(article, url = null) {
  const link = document.createElement("a");
  link.textContent = article.title;
  link.href = url || "https://en.wikipedia.org/wiki/" + encodeURIComponent(article.title.replaceAll(" ", "_"));
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function updateStats(stats) {
  state.stats = stats;
  elapsedAtUpdate = stats.elapsed_seconds;
  lastUpdate = performance.now();
}

function renderStats() {
  if (!state) return;
  const stats = state.stats;
  const waiting = (performance.now() - lastUpdate) / 1000;
  const elapsed = state.status === "running" ? elapsedAtUpdate + waiting : stats.elapsed_seconds;
  statsText.textContent = `Depth ${stats.depth} · ${stats.discovered} discovered · ${stats.expanded} fully expanded · ${stats.network_requests} requests · ${stats.cache_hits} cached expansions · ${elapsed.toFixed(1)}s${state.status === "running" && waiting >= 3 ? ` · waiting for response (${Math.floor(waiting)}s)` : ""}`;
}

function render() {
  statusText.classList.toggle("error", state.status === "failed");
  statusText.textContent = connectionMessage || `${state.status.replaceAll("_", " ")}: ${state.message}`;
  renderStats();
  routeList.replaceChildren();
  document.querySelector("#route-summary").textContent = state.status === "path_found" ? `${state.click_count} ${state.click_count === 1 ? "click" : "clicks"}. Shortest for the article data observed in this search.` : state.status === "running" ? "Exploring connections. The route appears when the destination is reached." : "No route established in this search.";
  if (state.status === "path_found") {
    const start = document.createElement("li");
    start.append(articleLink(state.start));
    routeList.append(start);
    for (const edge of state.route) {
      const item = document.createElement("li");
      const phrase = document.createElement("span");
      phrase.className = "route-phrase";
      phrase.textContent = `Click “${edge.anchor_text}” → `;
      item.append(phrase, articleLink(edge.destination, edge.url));
      routeList.append(item);
    }
  }
  try {
    if (!network) network = new SearchGraph(document.querySelector("#graph"));
    network.update(state);
  } catch (error) {
    console.error("Graph rendering failed", error);
    statusText.classList.add("error");
    statusText.textContent = `Search status: ${state.status.replaceAll("_", " ")}. Graph rendering failed: ${error.message}`;
  }
  if (state.status !== "running") {
    stream?.close();
    searchButton.disabled = false;
    cancelButton.disabled = true;
  }
}

function acceptSnapshot(data) {
  state = data;
  updateStats(data.stats);
  connectionMessage = "";
  render();
}

function mergeConnections(data) {
  const ids = new Set(state.graph.nodes.map(node => node.article.page_id));
  const pairs = new Set(state.graph.edges.map(edge => `${edge.source.page_id}:${edge.destination.page_id}`));
  for (const node of data.nodes || []) {
    if (!ids.has(node.article.page_id)) { ids.add(node.article.page_id); state.graph.nodes.push(node); }
  }
  for (const edge of data.edges || []) {
    const key = `${edge.source.page_id}:${edge.destination.page_id}`;
    if (!pairs.has(key)) { pairs.add(key); state.graph.edges.push(edge); }
  }
  if (data.truncated !== undefined) state.graph.truncated = data.truncated;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const run = ++generation;
  stream?.close();
  stream = null;
  searchId = null;
  searchButton.disabled = true;
  cancelButton.disabled = true;
  statusText.classList.remove("error");
  statusText.textContent = "Validating the start and destination articles…";
  connectionMessage = "";
  try {
    const initial = await api("/api/searches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start: form.elements.start.value, destination: form.elements.destination.value }) });
    if (run !== generation) return;
    searchId = initial.id;
    cancelButton.disabled = false;
    const source = new EventSource(`/api/searches/${searchId}/events`);
    stream = source;
    const listen = (type, handler) => source.addEventListener(type, event => {
      if (run !== generation) return;
      connectionMessage = "";
      handler(JSON.parse(event.data));
    });
    for (const type of ["snapshot", "started", "finished"]) listen(type, acceptSnapshot);
    listen("activity", data => {
      state.activities = data.activities;
      updateStats(data.stats);
      for (const activity of data.activities) {
        const node = state.graph.nodes.find(node => node.article.page_id === activity.article.page_id);
        if (node) { node.phase = activity.stage; node.expanded = activity.stage === "expanded"; }
      }
      render();
    });
    listen("connections", data => { mergeConnections(data); updateStats(data.stats); render(); });
    listen("expanded", data => {
      state.activities = (state.activities || []).filter(activity => activity.article.page_id !== data.article.page_id);
      state.data_fetched_at[data.article.page_id] = data.fetched_at;
      const node = state.graph.nodes.find(node => node.article.page_id === data.article.page_id);
      if (node) { node.expanded = true; node.phase = "expanded"; }
      mergeConnections(data);
      updateStats(data.stats);
      render();
    });
    listen("layer_completed", data => { updateStats(data.stats); render(); });
    source.onerror = async () => {
      if (run !== generation) return;
      connectionMessage = "Progress connection interrupted; reconnecting. The backend search continues.";
      render();
      try {
        const snapshot = await api(`/api/searches/${initial.id}`);
        if (run === generation) acceptSnapshot(snapshot);
      } catch (error) {
        if (run !== generation) return;
        source.close();
        statusText.classList.add("error");
        statusText.textContent = error.message;
        searchButton.disabled = false;
        cancelButton.disabled = true;
      }
    };
    acceptSnapshot(initial);
  } catch (error) {
    if (run !== generation) return;
    statusText.classList.add("error");
    statusText.textContent = error.message;
    searchButton.disabled = false;
  }
});

cancelButton.addEventListener("click", async () => {
  const run = generation;
  cancelButton.disabled = true;
  try {
    const snapshot = await api(`/api/searches/${searchId}/cancel`, { method: "POST" });
    if (run === generation) acceptSnapshot(snapshot);
  } catch (error) {
    if (run !== generation) return;
    statusText.textContent = error.message;
    cancelButton.disabled = false;
  }
});

setInterval(renderStats, 1000);
