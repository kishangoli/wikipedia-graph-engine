// Python owns the search. This page only sends inputs and renders progress.
const form = document.querySelector("#search-form");
const searchButton = document.querySelector("#search-button");
const cancelButton = document.querySelector("#cancel-button");
const statusText = document.querySelector("#status");
const statsText = document.querySelector("#stats");
const graph = document.querySelector("#graph");
const routeList = document.querySelector("#route");
let searchId = null;
let stream = null;
let state = null;

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === "string" ? data.detail : "Check the article inputs and try again.";
    throw new Error(message);
  }
  return data;
}

function articleLink(article, text = article.title, url = null) {
  const link = document.createElement("a");
  link.textContent = text;
  link.href = url || "https://en.wikipedia.org/wiki/" + encodeURIComponent(article.title.replaceAll(" ", "_"));
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function svgElement(tag, attributes = {}, text = null) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== null) element.textContent = text;
  return element;
}

function drawGraph() {
  graph.replaceChildren();
  const nodes = new Map(state.graph.nodes.map(node => [node.article.page_id, node]));
  const routePairs = new Set();
  const edges = [...state.graph.edges];
  state.route.forEach((edge, index) => {
    if (!nodes.has(edge.source.page_id)) nodes.set(edge.source.page_id, { article: edge.source, depth: index });
    if (!nodes.has(edge.destination.page_id)) nodes.set(edge.destination.page_id, { article: edge.destination, depth: index + 1 });
    const key = `${edge.source.page_id}:${edge.destination.page_id}`;
    routePairs.add(key);
    if (!edges.some(item => `${item.source.page_id}:${item.destination.page_id}` === key)) edges.push(edge);
  });
  const layers = new Map();
  for (const node of nodes.values()) {
    if (!layers.has(node.depth)) layers.set(node.depth, []);
    layers.get(node.depth).push(node);
  }
  const positions = new Map();
  let height = 150;
  for (const [depth, layer] of layers) {
    height = Math.max(height, 75 + layer.length * 40);
    layer.forEach((node, index) => positions.set(node.article.page_id, { x: 110 + depth * 240, y: 65 + index * 40 }));
    graph.append(svgElement("text", { x: 110 + depth * 240, y: 25, "text-anchor": "middle", fill: "#526273", "font-size": 12 }, `${depth} clicks`));
  }
  const width = Math.max(450, 220 + Math.max(0, ...layers.keys()) * 240);
  graph.setAttribute("width", width);
  graph.setAttribute("height", height);
  const defs = svgElement("defs");
  const marker = svgElement("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 5, markerHeight: 5, orient: "auto-start-reverse" });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#8497ad" }));
  defs.append(marker);
  graph.append(defs);
  for (const edge of edges) {
    const source = positions.get(edge.source.page_id);
    const target = positions.get(edge.destination.page_id);
    if (!source || !target) continue;
    const highlighted = routePairs.has(`${edge.source.page_id}:${edge.destination.page_id}`);
    const line = svgElement("line", { x1: source.x, y1: source.y, x2: target.x, y2: target.y, stroke: highlighted ? "#167342" : "#bdc9d7", "stroke-width": highlighted ? 3 : 1, "marker-end": "url(#arrow)" });
    line.append(svgElement("title", {}, `Click “${edge.anchor_text}” on ${edge.source.title}`));
    graph.append(line);
  }
  const routeIds = new Set([...(state.status === "path_found" ? [state.start.page_id] : []), ...state.route.flatMap(edge => [edge.source.page_id, edge.destination.page_id])]);
  for (const node of nodes.values()) {
    const position = positions.get(node.article.page_id);
    const group = svgElement("g");
    group.append(svgElement("rect", { x: position.x - 95, y: position.y - 14, width: 190, height: 28, rx: 4, fill: routeIds.has(node.article.page_id) ? "#e4f3e9" : node.expanded ? "#e9eef5" : "white", stroke: "#8d9eaf" }));
    const link = svgElement("a", { href: "https://en.wikipedia.org/wiki/" + encodeURIComponent(node.article.title.replaceAll(" ", "_")), target: "_blank", rel: "noopener noreferrer" });
    link.append(svgElement("text", { x: position.x, y: position.y + 4, "text-anchor": "middle", "font-size": 12, fill: "#202832" }, node.article.title.length > 25 ? node.article.title.slice(0, 24) + "…" : node.article.title));
    link.append(svgElement("title", {}, node.article.title));
    group.append(link);
    graph.append(group);
  }
  document.querySelector("#graph-note").textContent = state.graph.truncated ? "Showing a limited graph sample. All discovered articles still count toward the search; the complete final route is shown." : "Nodes are grouped by click depth. Arrows follow article hyperlinks.";
}

function render() {
  statusText.classList.remove("error");
  statusText.textContent = `${state.status.replaceAll("_", " ")}: ${state.message}`;
  const stats = state.stats;
  statsText.textContent = `Depth ${stats.depth} · ${stats.discovered} discovered · ${stats.expanded} expanded · ${stats.network_requests} requests · ${stats.cache_hits} cached expansions · ${stats.elapsed_seconds}s`;
  routeList.replaceChildren();
  document.querySelector("#route-summary").textContent = state.status === "path_found" ? `${state.click_count} clicks. Shortest for the article data observed in this search.` : "The route will appear when found.";
  if (state.status === "path_found") {
    const startItem = document.createElement("li");
    startItem.append(articleLink(state.start));
    routeList.append(startItem);
    for (const edge of state.route) {
      const item = document.createElement("li");
      const phrase = document.createElement("span");
      phrase.className = "route-phrase";
      phrase.textContent = `Click “${edge.anchor_text}” → `;
      item.append(phrase, articleLink(edge.destination, edge.destination.title, edge.url));
      routeList.append(item);
    }
  }
  drawGraph();
  if (state.status !== "running") {
    stream?.close();
    searchButton.disabled = false;
    cancelButton.disabled = true;
  }
}

function acceptSnapshot(event) {
  state = JSON.parse(event.data);
  render();
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  stream?.close();
  searchId = null;
  searchButton.disabled = true;
  cancelButton.disabled = true;
  statusText.classList.remove("error");
  statusText.textContent = "Validating articles…";
  try {
    state = await api("/api/searches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start: form.elements.start.value, destination: form.elements.destination.value }) });
    searchId = state.id;
    cancelButton.disabled = false;
    stream = new EventSource(`/api/searches/${searchId}/events`);
    for (const type of ["snapshot", "started", "finished"]) stream.addEventListener(type, acceptSnapshot);
    stream.addEventListener("expanded", event => {
      const data = JSON.parse(event.data);
      state.stats = data.stats;
      state.data_fetched_at[data.article.page_id] = data.fetched_at;
      const expanded = state.graph.nodes.find(node => node.article.page_id === data.article.page_id);
      if (expanded) expanded.expanded = true;
      for (const node of data.nodes) {
        if (!state.graph.nodes.some(item => item.article.page_id === node.article.page_id)) state.graph.nodes.push(node);
      }
      for (const edge of data.edges) {
        if (!state.graph.edges.some(item => item.source.page_id === edge.source.page_id && item.destination.page_id === edge.destination.page_id)) state.graph.edges.push(edge);
      }
      state.graph.truncated = data.truncated;
      render();
    });
    stream.addEventListener("layer_completed", event => { state.stats = JSON.parse(event.data).stats; render(); });
    stream.onerror = async () => {
      try {
        state = await api(`/api/searches/${searchId}`);
        render();
        if (state.status === "running") statusText.textContent = "Progress connection interrupted; reconnecting. Search continues on the backend.";
      } catch (error) {
        stream.close();
        statusText.classList.add("error");
        statusText.textContent = error.message;
        searchButton.disabled = false;
        cancelButton.disabled = true;
      }
    };
    render();
  } catch (error) {
    statusText.classList.add("error");
    statusText.textContent = error.message;
    searchButton.disabled = false;
  }
});

cancelButton.addEventListener("click", async () => {
  cancelButton.disabled = true;
  try {
    state = await api(`/api/searches/${searchId}/cancel`, { method: "POST" });
    render();
  } catch (error) {
    statusText.textContent = error.message;
    cancelButton.disabled = false;
  }
});
