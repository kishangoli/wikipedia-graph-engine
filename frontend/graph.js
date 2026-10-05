class SearchGraph {
  constructor(element) {
    this.svg = d3.select(element);
    this.nodes = new Map();
    this.edges = [];
    this.selectedId = null;
    this.showLabels = false;
    this.userMoved = false;
    this.searchId = null;
    this.lastFit = 0;
    this.transform = d3.zoomIdentity;
    this.reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.viewport = this.svg.append("g");
    this.edgeLayer = this.viewport.append("g");
    this.nodeLayer = this.viewport.append("g");
    const defs = this.svg.append("defs");
    for (const [id, color] of [["edge-arrow", "#7186a2"], ["route-arrow", "#f7df83"]]) {
      defs.append("marker").attr("id", id).attr("viewBox", "0 -4 8 8").attr("refX", 8).attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto").append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", color);
    }
    this.zoom = d3.zoom().scaleExtent([0.12, 5]).on("zoom", event => {
      this.transform = event.transform;
      this.viewport.attr("transform", event.transform);
      if (event.sourceEvent) this.userMoved = true;
      this.updateLabels();
    });
    this.svg.call(this.zoom).on("dblclick.zoom", null);
    this.simulation = d3.forceSimulation().force("links", d3.forceLink().id(node => node.id).distance(85).strength(.3)).force("charge", d3.forceManyBody().strength(-150)).force("collide", d3.forceCollide(node => node.radius + 9)).force("x", d3.forceX(0).strength(.04)).force("y", d3.forceY(0).strength(.04)).on("tick", () => this.tick()).stop();
    this.drag = d3.drag().on("start", (event, node) => {
      this.userMoved = true;
      if (!event.active) this.simulation.alphaTarget(.15).restart();
      node.fx = node.x;
      node.fy = node.y;
    }).on("drag", (event, node) => { node.fx = event.x; node.fy = event.y; }).on("end", event => { if (!event.active) this.simulation.alphaTarget(0); });
    document.querySelector("#zoom-in").addEventListener("click", () => this.zoomBy(1.3));
    document.querySelector("#zoom-out").addEventListener("click", () => this.zoomBy(1 / 1.3));
    document.querySelector("#fit-graph").addEventListener("click", () => { this.userMoved = false; this.fit(); });
    document.querySelector("#toggle-labels").addEventListener("click", event => {
      this.showLabels = !this.showLabels;
      event.currentTarget.textContent = this.showLabels ? "Labels: all" : "Labels: auto";
      event.currentTarget.setAttribute("aria-pressed", String(this.showLabels));
      this.updateLabels();
    });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(element.parentElement);
    this.resize();
  }

  resize() {
    const bounds = this.svg.node().getBoundingClientRect();
    this.width = bounds.width || 800;
    this.height = bounds.height || 640;
    this.svg.attr("viewBox", `0 0 ${this.width} ${this.height}`);
    if (!this.userMoved) this.fit();
  }

  zoomBy(factor) {
    this.userMoved = true;
    this.svg.call(this.zoom.scaleBy, factor);
  }

  fit() {
    const nodes = [...this.nodes.values()].filter(node => Number.isFinite(node.x));
    if (!nodes.length) return;
    const minX = d3.min(nodes, node => node.x - node.radius - 35);
    const maxX = d3.max(nodes, node => node.x + node.radius + 35);
    const minY = d3.min(nodes, node => node.y - node.radius - 25);
    const maxY = d3.max(nodes, node => node.y + node.radius + 25);
    const scale = Math.max(.12, Math.min(1.5, (this.width - 100) / Math.max(1, maxX - minX), (this.height - 150) / Math.max(1, maxY - minY)));
    this.svg.call(this.zoom.transform, d3.zoomIdentity.translate(this.width / 2, this.height / 2).scale(scale).translate(-(minX + maxX) / 2, -(minY + maxY) / 2));
    this.lastFit = performance.now();
  }

  update(state) {
    const reset = state.id !== this.searchId;
    if (reset) {
      this.searchId = state.id;
      this.nodes.clear();
      this.selectedId = null;
      this.userMoved = false;
      this.simulation.stop();
      this.simulation.force("links").links([]);
    }
    const descriptions = new Map(state.graph.nodes.map(node => [node.article.page_id, { ...node }]));
    if (!descriptions.has(state.destination.page_id)) descriptions.set(state.destination.page_id, { article: state.destination, depth: null, phase: "target" });
    const routeEdges = new Map();
    const routeIds = new Set(state.status === "path_found" ? [state.start.page_id] : []);
    for (const [index, edge] of state.route.entries()) {
      routeEdges.set(`${edge.source.page_id}:${edge.destination.page_id}`, edge);
      routeIds.add(edge.source.page_id);
      routeIds.add(edge.destination.page_id);
      if (!descriptions.has(edge.source.page_id)) descriptions.set(edge.source.page_id, { article: edge.source, depth: index });
      descriptions.set(edge.destination.page_id, { ...(descriptions.get(edge.destination.page_id) || {}), article: edge.destination, depth: index + 1 });
    }
    const allEdges = new Map(state.graph.edges.map(edge => [`${edge.source.page_id}:${edge.destination.page_id}`, edge]));
    for (const [key, edge] of routeEdges) allEdges.set(key, edge);
    const oldNodeCount = this.nodes.size;
    for (const [id, description] of descriptions) {
      let node = this.nodes.get(id);
      if (!node) {
        const parent = [...allEdges.values()].find(edge => edge.destination.page_id === id);
        const origin = parent && this.nodes.get(parent.source.page_id);
        const angle = (id * 2.39996) % (Math.PI * 2);
        node = { id, x: (origin?.x || 0) + Math.cos(angle) * 60, y: (origin?.y || 0) + Math.sin(angle) * 60 };
        this.nodes.set(id, node);
      }
      Object.assign(node, description, { isStart: id === state.start.page_id, isTarget: id === state.destination.page_id, onRoute: routeIds.has(id), degree: 0 });
    }
    for (const id of this.nodes.keys()) { if (!descriptions.has(id)) this.nodes.delete(id); }
    const oldKeys = this.edges.map(edge => edge.key).join(",");
    this.edges = [...allEdges.entries()].filter(([, edge]) => this.nodes.has(edge.source.page_id) && this.nodes.has(edge.destination.page_id)).map(([key, edge]) => ({ key, source: edge.source.page_id, target: edge.destination.page_id, anchor: edge.anchor_text, onRoute: routeEdges.has(key) }));
    for (const edge of this.edges) { this.nodes.get(edge.source).degree++; this.nodes.get(edge.target).degree++; }
    for (const node of this.nodes.values()) node.radius = node.isStart || node.isTarget ? 17 : Math.min(14, 5 + Math.sqrt(node.degree) * 1.7);
    const structuralChange = reset || oldNodeCount !== this.nodes.size || oldKeys !== this.edges.map(edge => edge.key).join(",");
    // D3 mutates layout data, which stays separate from the backend state.
    this.simulation.force("links").links([]);
    this.simulation.nodes([...this.nodes.values()]);
    this.simulation.force("links").links(this.edges);
    this.lines = this.edgeLayer.selectAll("line").data(this.edges, edge => edge.key).join("line").attr("stroke", edge => edge.onRoute ? "#f7df83" : "#607797").attr("stroke-opacity", edge => edge.onRoute ? .95 : .3).attr("stroke-width", edge => edge.onRoute ? 2.5 : 1).attr("marker-end", edge => `url(#${edge.onRoute ? "route-arrow" : "edge-arrow"})`);
    this.lines.selectAll("title").data(edge => [edge]).join("title").text(edge => `Click “${edge.anchor}” on ${edge.source.article.title}`);
    this.groups = this.nodeLayer.selectAll("g.graph-node").data([...this.nodes.values()], node => node.id).join(enter => {
      const group = enter.append("g").attr("class", "graph-node").attr("tabindex", 0).attr("role", "button");
      group.append("circle").attr("class", "node-ring");
      group.append("circle").attr("class", "node-dot");
      group.append("text").attr("class", "node-label").attr("text-anchor", "middle");
      group.append("title");
      return group;
    }).call(this.drag).attr("aria-label", node => `${node.article.title}, ${node.phase || "discovered"}. Select to highlight connections.`).classed("working", node => this.isWorking(node)).classed("selected", node => node.id === this.selectedId).on("click", (event, node) => { if (!event.defaultPrevented) this.select(node); }).on("keydown", (event, node) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.select(node); } }).on("mouseenter", (event, node) => { this.hoverId = node.id; this.updateLabels(); }).on("mouseleave", () => { this.hoverId = null; this.updateLabels(); });
    this.groups.select(".node-dot").attr("r", node => node.radius).attr("fill", node => this.color(node)).attr("stroke", "#0b1220").attr("stroke-width", 2);
    this.groups.select(".node-ring").attr("r", node => node.radius + 5).attr("stroke", node => this.color(node));
    this.groups.select(".node-label").attr("y", node => node.radius + 16).text(node => node.article.title);
    this.groups.select("title").text(node => `${node.article.title}\n${node.depth === null ? "Destination — not reached yet" : `${node.depth} clicks from start`}\n${node.phase || "discovered"}`);
    this.updateLabels();
    document.querySelector("#graph-empty").hidden = true;
    document.querySelector("#graph-count").textContent = `${this.nodes.size} nodes · ${this.edges.length} edges`;
    document.querySelector("#graph-note").textContent = state.graph.truncated ? "Showing a capped sample plus the full route. The search continues beyond the displayed nodes." : "Validated connections arrive live. The destination stays separate until a real link reaches it.";
    if (structuralChange) {
      if (this.reducedMotion) { this.simulation.alpha(1).stop().tick(100); this.tick(); }
      else this.simulation.alpha(.65).restart();
    }
    this.tick();
    if (reset && !this.userMoved) this.fit();
  }

  isWorking(node) {
    return ["cache", "waiting", "fetching", "parsing", "validating", "retrying"].includes(node.phase);
  }

  color(node) {
    if (node.onRoute) return "#f7df83";
    if (node.isStart) return "#75e0b4";
    if (node.isTarget) return "#ff8e97";
    if (this.isWorking(node)) return "#ffc776";
    return node.expanded ? "#69cde1" : "#a99cfa";
  }

  updateLabels() {
    this.groups?.select(".node-label").attr("display", node => this.showLabels || this.nodes.size < 15 || this.transform.k > 1.4 || node.isStart || node.isTarget || node.onRoute || this.isWorking(node) || node.id === this.selectedId || node.id === this.hoverId ? null : "none");
  }

  tick() {
    this.groups?.attr("transform", node => `translate(${node.x},${node.y})`);
    this.lines?.each(function(edge) {
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const length = Math.hypot(dx, dy) || 1;
      d3.select(this).attr("x1", edge.source.x + dx / length * (edge.source.radius + 2)).attr("y1", edge.source.y + dy / length * (edge.source.radius + 2)).attr("x2", edge.target.x - dx / length * (edge.target.radius + 4)).attr("y2", edge.target.y - dy / length * (edge.target.radius + 4));
    });
    if (!this.userMoved && performance.now() - this.lastFit > 250) this.fit();
  }

  select(node) {
    this.selectedId = node.id;
    this.groups.classed("selected", item => item.id === node.id);
    this.lines.attr("stroke-opacity", edge => edge.onRoute || edge.source.id === node.id || edge.target.id === node.id ? .95 : .12);
    this.updateLabels();
  }

}
