/**
 * Kyverno maintainer dashboard — Hermes dashboard plugin.
 *
 * Renders the state the agent writes through update_dashboard (items / views / actions /
 * session), served by plugin_api.py at /api/plugins/kyverno-dashboard/state. Plain IIFE,
 * no build step; React comes from window.__HERMES_PLUGIN_SDK__.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useMemo, useCallback, useRef } = SDK.hooks;

  const API = "/api/plugins/kyverno-dashboard";
  const POLL_MS = 15000;
  const PAGE = 30;

  // ---------------------------------------------------------------- vocab

  const VERDICTS = {
    rescue:  { label: "Rescue",  glyph: "↻", order: 0 },
    decide:  { label: "Decide",  glyph: "?", order: 1 },
    review:  { label: "Review",  glyph: "◎", order: 2 },
    close:   { label: "Close",   glyph: "✕", order: 3 },
    triage:  { label: "Triage",  glyph: "◇", order: 4 },
    approve: { label: "Approve", glyph: "✓", order: 5 },
    defer:   { label: "Defer",   glyph: "⏸", order: 6 },
    done:    { label: "Done",    glyph: "✓", order: 7 },
  };
  const STATUS = {
    open:   { glyph: "●", label: "open" },
    merged: { glyph: "◆", label: "merged" },
    closed: { glyph: "✕", label: "closed" },
    draft:  { glyph: "○", label: "draft" },
  };
  const REL = {
    closes:          { label: "closes", dir: "out" },
    "closed-by":     { label: "closed by", dir: "in" },
    supersedes:      { label: "supersedes", dir: "out" },
    "superseded-by": { label: "superseded by", dir: "in" },
    "depends-on":    { label: "depends on", dir: "out" },
    blocks:          { label: "blocks", dir: "out" },
    competes:        { label: "competes", dir: "none" },
    duplicates:      { label: "duplicates", dir: "out" },
    "discussed-in":  { label: "discussed in", dir: "out" },
  };
  const INVERSE = {
    closes: "closed-by", "closed-by": "closes",
    supersedes: "superseded-by", "superseded-by": "supersedes",
    "depends-on": "blocks", blocks: "depends-on",
    competes: "competes", duplicates: "duplicates", "discussed-in": "discussed-in",
  };
  const KIND = {
    pr:         { short: "PR",    plural: "Pull requests" },
    issue:      { short: "Issue", plural: "Issues" },
    discussion: { short: "Disc",  plural: "Discussions" },
  };
  const TABS = [
    ["overview", "Overview"], ["pr", "PRs"], ["issue", "Issues"],
    ["discussion", "Discussions"], ["activity", "Activity"],
  ];

  // ---------------------------------------------------------------- helpers

  const kindOf = (key) => String(key).split(":")[0];
  const numOf = (key) => String(key).split(":")[1];
  const verdictOf = (it) => (it && it.verdict && VERDICTS[it.verdict.action]) ? it.verdict.action : null;
  const statusOf = (it, relState) => STATUS[relState] ? relState : (it && STATUS[it.status] ? it.status : "open");

  function ago(iso) {
    if (!iso) return "";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  function daysLeft(iso) {
    if (!iso) return null;
    const d = Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
    return d;
  }

  function toneOf(v) {
    const s = String(v);
    if (/fail|dirty|conflict|block|red|error/i.test(s)) return "bad";
    if (/pass|green|clean|success|ok\b/i.test(s)) return "good";
    return "";
  }

  function store(key, val) {
    try {
      if (val === undefined) return window.localStorage.getItem("kv." + key);
      window.localStorage.setItem("kv." + key, val);
    } catch (e) { /* storage may be unavailable */ }
    return null;
  }

  // edges for a key: its own related[] plus inverses declared by other items
  function edgesFor(key, items) {
    const out = [];
    const seen = new Set();
    const push = (e) => {
      const id = e.key + "|" + e.rel;
      if (!seen.has(id)) { seen.add(id); out.push(e); }
    };
    const own = items[key];
    ((own && own.related) || []).forEach((r) => REL[r.rel] && push(r));
    Object.keys(items).forEach((k) => {
      if (k === key) return;
      ((items[k].related) || []).forEach((r) => {
        if (r.key === key && INVERSE[r.rel]) {
          push({ key: k, rel: INVERSE[r.rel], state: items[k].status });
        }
      });
    });
    return out;
  }

  function competing(key, items) {
    const prs = edgesFor(key, items).filter((e) =>
      kindOf(e.key) === "pr" && (e.rel === "closed-by" || e.rel === "competes"));
    return prs.length >= 2 && kindOf(key) === "issue" ? prs : [];
  }

  // ---------------------------------------------------------------- small pieces

  function VerdictBadge(p) {
    const v = VERDICTS[p.action];
    if (!v) return null;
    return h("span", { className: "kv-verdict kv-v-" + p.action, title: p.reason || v.label }, v.label);
  }

  function Chip(p) {
    return h("span", { className: "kv-chip " + (p.tone || "") }, p.children);
  }

  function StateChips(p) {
    const st = p.item.state || {};
    const chips = [];
    if (st.stale) chips.push(h(Chip, { key: "stale", tone: "warn" }, "stale"));
    Object.keys(st).forEach((k) => {
      if (k === "labels" || k === "stale" || st[k] === null || st[k] === "" || st[k] === undefined) return;
      chips.push(h(Chip, { key: k, tone: toneOf(st[k]) }, h("span", { className: "kv-chip-k" }, k), String(st[k])));
    });
    (st.labels || []).slice(0, p.max || 4).forEach((l) =>
      chips.push(h(Chip, { key: "l" + l, tone: "label" }, l)));
    return h("span", { className: "kv-chips" }, chips);
  }

  function Empty(p) {
    return h("div", { className: "kv-empty" },
      h("div", { className: "kv-empty-title" }, p.title),
      p.hint && h("div", { className: "kv-empty-hint" }, p.hint));
  }

  function ExtLink(p) {
    return h("a", {
      href: p.href, target: "_blank", rel: "noopener noreferrer",
      className: p.className || "kv-link", onClick: (e) => e.stopPropagation(), "aria-label": p.label,
    }, p.children);
  }

  // ---------------------------------------------------------------- relationship graph

  const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

  // Focus card on the left, related items stacked on the right, curved connectors between.
  function Graph(p) {
    const { focusKey, items, onOpen } = p;
    const edges = edgesFor(focusKey, items);
    const shown = edges.slice(0, 8);
    const extra = edges.length - shown.length;
    const focus = items[focusKey] || {};
    const FW = 150, NW = 190, NH = 42, GAP = 14, W = 450, PAD = 6;
    const H = Math.max(NH, shown.length * (NH + GAP) - GAP) + PAD * 2;
    const fx = PAD, fy = H / 2, nx = W - NW - PAD;

    const card = (key, x, y, w, status, isFocus, ref, title) => {
      const label = KIND[kindOf(key)].short + " #" + numOf(key);
      const chars = Math.floor((w - 22) / 6);
      return h("g", {
        key: ref, className: "kv-node kv-st-" + status + (isFocus ? " focus" : ""),
        transform: "translate(" + x + "," + (y - NH / 2) + ")",
        tabIndex: isFocus ? -1 : 0, role: isFocus ? "img" : "button",
        "aria-label": label + " " + STATUS[status].label + (title ? ": " + title : ""),
        onClick: isFocus ? undefined : () => onOpen(key),
        onKeyDown: isFocus ? undefined : (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(key); } },
      },
        h("title", null, label + (title ? " · " + title : "")),
        h("rect", { className: "kv-node-box", width: w, height: NH, rx: 6 }),
        h("rect", { className: "kv-node-bar", width: 4, height: NH - 12, x: 6, y: 6, rx: 2 }),
        h("text", { x: 18, y: 17, className: "kv-node-text" },
          STATUS[status].glyph + " " + label),
        h("text", { x: 18, y: 33, className: "kv-node-title" }, clip(title, chars)));
    };

    const parts = shown.map((e, i) => {
      const o = items[e.key] || {};
      const ny = PAD + NH / 2 + i * (NH + GAP);
      const rel = REL[e.rel];
      const x1 = fx + FW, x2 = nx, mx = (x1 + x2) / 2;
      const [sx, sy, ex, ey] = rel.dir === "in" ? [x2, ny, x1, fy] : [x1, fy, x2, ny];
      const d = "M" + sx + "," + sy + " C" + mx + "," + sy + " " + mx + "," + ey + " " + ex + "," + ey;
      return [
        h("path", { key: "e" + i, className: "kv-edge kv-rel-" + e.rel, d, fill: "none",
          markerEnd: rel.dir === "none" ? undefined : "url(#kv-arrow)" }),
        h("text", { key: "t" + i, className: "kv-edge-label", x: mx, y: (fy + ny) / 2 + 3, textAnchor: "middle" }, rel.label),
        card(e.key, nx, ny, NW, statusOf(o, e.state), false, "n" + i, o.title || e.title),
      ];
    });

    return h("figure", { className: "kv-graph" },
      h("svg", { viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": "Relationships for " + focusKey },
        h("defs", null,
          h("marker", { id: "kv-arrow", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" },
            h("path", { d: "M0,0 L8,4 L0,8 z", className: "kv-arrow" }))),
        parts,
        card(focusKey, fx, fy, FW, statusOf(focus), true, "focus", focus.title)),
      h("figcaption", { className: "kv-graph-legend" },
        Object.keys(STATUS).map((s) => h("span", { key: s, className: "kv-leg kv-st-" + s },
          h("i", { "aria-hidden": "true" }, STATUS[s].glyph), STATUS[s].label)),
        extra > 0 && h("span", { className: "kv-leg" }, "+" + extra + " more in list")));
  }

  // ---------------------------------------------------------------- rows

  function Row(p) {
    const { item, entry, onOpen, selected } = p;
    const v = verdictOf(item);
    const key = item.key;
    const line2 = (entry && entry.reason) || item.needs_you || (item.verdict && item.verdict.reason) || item.summary || "";
    const rel = p.relCount;
    return h("div", {
      className: "kv-row" + (selected ? " selected" : "") + (item.acted ? " acted" : ""),
      role: "button", tabIndex: 0, onClick: () => onOpen(key),
      onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(key); } },
    },
      h("div", { className: "kv-row-id" },
        h("span", { className: "kv-kind" }, KIND[kindOf(key)].short),
        h("span", { className: "kv-num" }, "#" + numOf(key))),
      h("div", { className: "kv-row-main" },
        h("div", { className: "kv-row-title" },
          item.url
            ? h(ExtLink, { href: item.url, label: "Open " + key + " on GitHub" }, item.title || key)
            : (item.title || key)),
        line2 && h("div", { className: "kv-row-sub" }, line2),
        h(StateChips, { item, max: 3 })),
      h("div", { className: "kv-row-side" },
        item.acted
          ? h("span", { className: "kv-acted", title: ago(item.acted.ts) }, item.acted.action)
          : h(VerdictBadge, { action: v, reason: item.verdict && item.verdict.reason }),
        entry && entry.tier !== undefined && h("span", { className: "kv-tier" }, "tier " + entry.tier),
        rel > 0 && h("span", { className: "kv-rel-count", title: rel + " related" }, rel + " linked"),
        item.author && h("span", { className: "kv-author" }, "@" + item.author)));
  }

  // ---------------------------------------------------------------- drawer

  function Drawer(p) {
    const { itemKey, items, onOpen, onClose } = p;
    const it = items[itemKey];
    const [copied, setCopied] = useState(false);
    const ref = useRef(null);

    useEffect(() => {
      const onKey = (e) => { if (e.key === "Escape") onClose(); };
      window.addEventListener("keydown", onKey);
      if (ref.current) ref.current.focus();
      return () => window.removeEventListener("keydown", onKey);
    }, [itemKey]);

    useEffect(() => setCopied(false), [itemKey]);
    if (!it) return null;

    const edges = edgesFor(itemKey, items);
    const copy = () => {
      try {
        navigator.clipboard.writeText(it.draft).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        });
      } catch (e) { /* clipboard unavailable */ }
    };

    return h("aside", { className: "kv-drawer", role: "dialog", "aria-label": "Details for " + itemKey, tabIndex: -1, ref },
      h("div", { className: "kv-drawer-head" },
        h("div", null,
          h("div", { className: "kv-eyebrow" }, KIND[kindOf(itemKey)].short + " #" + numOf(itemKey) +
            (it.author ? " · @" + it.author : "")),
          h("h2", { className: "kv-drawer-title" }, it.title || itemKey)),
        h("button", { className: "kv-icon-btn", onClick: onClose, "aria-label": "Close details" }, "✕")),
      h("div", { className: "kv-drawer-actions" },
        it.url && h(ExtLink, { href: it.url, className: "kv-btn primary", label: "Open on GitHub" }, "Open on GitHub ↗"),
        h(VerdictBadge, { action: verdictOf(it) })),
      it.acted && h("div", { className: "kv-callout done" },
        "✓ " + it.acted.action + " · " + ago(it.acted.ts) + " ",
        it.acted.link && h(ExtLink, { href: it.acted.link, label: "Open action" }, "view ↗")),
      it.needs_you && h("div", { className: "kv-callout" }, h("b", null, "Needs you: "), it.needs_you),
      it.verdict && it.verdict.reason && h("p", { className: "kv-reason" }, it.verdict.reason),
      it.summary && h("p", { className: "kv-summary" }, it.summary),
      h(StateChips, { item: it, max: 12 }),
      edges.length > 0 && h("section", { className: "kv-sec" },
        h("h3", null, "Relationships"),
        h(Graph, { focusKey: itemKey, items, onOpen }),
        h("ul", { className: "kv-rel-list" }, edges.map((e) => {
          const o = items[e.key] || {};
          const st = statusOf(o, e.state);
          return h("li", { key: e.key + e.rel },
            h("span", { className: "kv-rel-tag" }, REL[e.rel].label),
            h("button", { className: "kv-linkbtn", onClick: () => onOpen(e.key) },
              h("span", { className: "kv-st-" + st + " kv-glyph", "aria-hidden": "true" }, STATUS[st].glyph),
              " " + KIND[kindOf(e.key)].short + " #" + numOf(e.key) + (o.title || e.title ? " " + (o.title || e.title) : "")),
            (o.url || e.url) && h(ExtLink, { href: o.url || e.url, label: "Open " + e.key + " on GitHub" }, "↗"));
        }))),
      (it.threads || []).length > 0 && h("section", { className: "kv-sec" },
        h("h3", null, "Unresolved threads"),
        h("ul", { className: "kv-thread-list" }, it.threads.map((t, i) =>
          h("li", { key: i }, t.url ? h(ExtLink, { href: t.url, label: "Open thread" }, t.text || "thread ↗") : t.text)))),
      it.draft && h("section", { className: "kv-sec" },
        h("h3", null, "Draft awaiting your confirm"),
        h("pre", { className: "kv-draft" }, it.draft),
        h("button", { className: "kv-btn", onClick: copy }, copied ? "Copied ✓" : "Copy draft")),
      h("div", { className: "kv-foot" }, "Updated " + ago(it.updated_at)));
  }

  // ---------------------------------------------------------------- list tab

  function useViews(kind, state) {
    return useMemo(() => {
      const views = Object.values(state.views || {})
        .filter((v) => (v.entries || []).some((e) => kindOf(e.key) === kind))
        .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
      return views;
    }, [state, kind]);
  }

  function ListTab(p) {
    const { kind, state, onOpen, selected } = p;
    const items = state.items || {};
    const views = useViews(kind, state);
    const [viewName, setViewName] = useState(null);
    const [q, setQ] = useState("");
    const [vf, setVf] = useState(null);
    const [sort, setSort] = useState("view");
    const [limits, setLimits] = useState({});
    const [shut, setShut] = useState({});

    const active = viewName === "__all" || (!viewName && !views.length)
      ? null : (views.find((v) => v.name === viewName) || views[0] || null);

    // entries: [{item, entry}]
    const rows = useMemo(() => {
      let list;
      if (active) {
        list = active.entries.filter((e) => kindOf(e.key) === kind && items[e.key])
          .map((e, i) => ({ item: items[e.key], entry: e, i }));
      } else {
        list = Object.values(items).filter((it) => kindOf(it.key) === kind)
          .map((it, i) => ({ item: it, entry: null, i }));
      }
      const needle = q.trim().toLowerCase();
      if (needle) {
        list = list.filter(({ item }) =>
          (item.title || "").toLowerCase().includes(needle) || item.key.includes(needle) ||
          (item.author || "").toLowerCase().includes(needle));
      }
      if (vf) list = list.filter(({ item }) => verdictOf(item) === vf);
      if (sort === "number") list.sort((a, b) => Number(numOf(b.item.key)) - Number(numOf(a.item.key)));
      else if (sort === "updated") list.sort((a, b) => String(b.item.updated_at).localeCompare(String(a.item.updated_at)));
      else if (sort === "verdict") list.sort((a, b) =>
        (VERDICTS[verdictOf(a.item)] ? VERDICTS[verdictOf(a.item)].order : 99) -
        (VERDICTS[verdictOf(b.item)] ? VERDICTS[verdictOf(b.item)].order : 99));
      return list;
    }, [state, kind, active, q, vf, sort]);

    const counts = useMemo(() => {
      const c = {};
      Object.values(items).filter((it) => kindOf(it.key) === kind).forEach((it) => {
        const v = verdictOf(it);
        if (v) c[v] = (c[v] || 0) + 1;
      });
      return c;
    }, [state, kind]);

    // grouping
    const groups = useMemo(() => {
      const defs = active && active.groups && active.groups.length ? active.groups : null;
      const map = new Map();
      const order = [];
      const gid = (r) => {
        if (active) return String(r.entry.group || (r.entry.tier !== undefined ? "tier-" + r.entry.tier : "other"));
        return verdictOf(r.item) || "other";
      };
      rows.forEach((r) => {
        const id = gid(r);
        if (!map.has(id)) { map.set(id, []); order.push(id); }
        map.get(id).push(r);
      });
      let ids = order;
      if (defs && sort === "view") {
        const known = defs.map((d) => d.id).filter((id) => map.has(id));
        ids = known.concat(order.filter((id) => !known.includes(id)));
      } else if (!active) {
        ids = order.sort((a, b) => (VERDICTS[a] ? VERDICTS[a].order : 99) - (VERDICTS[b] ? VERDICTS[b].order : 99));
      }
      return ids.map((id) => {
        const d = defs && defs.find((x) => x.id === id);
        const label = d ? d.label : (VERDICTS[id] ? VERDICTS[id].label : (id.startsWith("tier-") ? "Tier " + id.slice(5) : id));
        return { id, label, hint: d && d.hint, rows: map.get(id) };
      });
    }, [rows, active, sort]);

    const clusters = useMemo(() => {
      if (kind !== "issue") return [];
      return rows.filter(({ item }) => competing(item.key, items).length >= 2).map((r) => r.item.key);
    }, [rows, state, kind]);

    const total = Object.values(items).filter((it) => kindOf(it.key) === kind).length;
    if (!total) {
      return h(Empty, { title: "No " + KIND[kind].plural.toLowerCase() + " on the dashboard yet",
        hint: kind === "pr" ? "Ask Hermes for a PR queue or stale PRs and they land here."
          : kind === "issue" ? "Ask Hermes to triage issues and they land here."
          : "Ask Hermes for relevant Discussions and they land here." });
    }

    return h("div", { className: "kv-list" },
      h("div", { className: "kv-toolbar" },
        h("div", { className: "kv-seg", role: "tablist", "aria-label": "View" },
          views.map((v) => h("button", {
            key: v.name, role: "tab", "aria-selected": active && active.name === v.name,
            className: active && active.name === v.name ? "on" : "", onClick: () => setViewName(v.name),
          }, v.title)),
          h("button", { role: "tab", "aria-selected": !active, className: !active ? "on" : "", onClick: () => setViewName("__all") }, "All")),
        h("input", { className: "kv-input", type: "search", placeholder: "Filter title, #, author", value: q,
          "aria-label": "Filter", onChange: (e) => setQ(e.target.value) }),
        h("select", { className: "kv-input", value: sort, "aria-label": "Sort", onChange: (e) => setSort(e.target.value) },
          h("option", { value: "view" }, "Agent order"), h("option", { value: "verdict" }, "Verdict"),
          h("option", { value: "number" }, "Newest #"), h("option", { value: "updated" }, "Recently updated"))),
      h("div", { className: "kv-filter" },
        Object.keys(counts).sort((a, b) => VERDICTS[a].order - VERDICTS[b].order).map((k) =>
          h("button", { key: k, className: "kv-fchip kv-v-" + k + (vf === k ? " on" : ""), "aria-pressed": vf === k,
            onClick: () => setVf(vf === k ? null : k) }, VERDICTS[k].label, h("b", null, counts[k])))),
      active && active.description && h("p", { className: "kv-view-desc" }, active.description + " · updated " + ago(active.updated_at)),
      clusters.length > 0 && h("section", { className: "kv-clusters" },
        h("h3", null, "Competing PRs on one issue"),
        h("div", { className: "kv-cluster-grid" }, clusters.slice(0, 6).map((k) =>
          h("div", { key: k, className: "kv-card" },
            h("button", { className: "kv-linkbtn", onClick: () => onOpen(k) }, "Issue #" + numOf(k) + " · " + (items[k].title || "")),
            h(Graph, { focusKey: k, items, onOpen }))))),
      groups.length === 0 && h(Empty, { title: "Nothing matches these filters" }),
      groups.map((g) => {
        const lim = limits[g.id] || PAGE;
        const closed = shut[g.id];
        return h("section", { key: g.id, className: "kv-group" },
          h("button", { className: "kv-group-head", "aria-expanded": !closed, onClick: () => setShut({ ...shut, [g.id]: !closed }) },
            h("span", { className: "kv-caret" }, closed ? "▸" : "▾"),
            h("span", { className: "kv-group-label" }, g.label), h("span", { className: "kv-count" }, g.rows.length),
            g.hint && h("span", { className: "kv-group-hint" }, g.hint)),
          !closed && h("div", { className: "kv-group-body" },
            g.rows.slice(0, lim).map(({ item, entry }) =>
              h(Row, { key: item.key, item, entry, onOpen, selected: selected === item.key,
                relCount: edgesFor(item.key, items).length })),
            g.rows.length > lim && h("button", { className: "kv-more",
              onClick: () => setLimits({ ...limits, [g.id]: lim + PAGE }) }, "Show " + Math.min(PAGE, g.rows.length - lim) + " more (" + (g.rows.length - lim) + " hidden)")));
      }));
  }

  // ---------------------------------------------------------------- overview

  function Overview(p) {
    const { state, onOpen, selected, setTab } = p;
    const items = state.items || {};
    const [vf, setVf] = useState(null);
    const open = Object.values(items).filter((it) => verdictOf(it) && !it.acted && verdictOf(it) !== "done");
    const counts = {};
    open.forEach((it) => { const v = verdictOf(it); counts[v] = (counts[v] || 0) + 1; });
    const list = open.filter((it) => !vf || verdictOf(it) === vf)
      .sort((a, b) => VERDICTS[verdictOf(a)].order - VERDICTS[verdictOf(b)].order).slice(0, 14);
    const recent = (state.actions || []).slice(-5).reverse();
    const clusters = Object.keys(items).filter((k) => competing(k, items).length >= 2).slice(0, 4);

    if (!Object.keys(items).length && !Object.keys(state.session || {}).length) {
      return h(Empty, { title: "Nothing here yet",
        hint: "Ask Hermes for a PR queue, stale PRs or an issue triage. Results and confirmed actions appear here automatically." });
    }

    return h("div", { className: "kv-overview" },
      h("div", { className: "kv-filter" },
        Object.keys(VERDICTS).filter((k) => counts[k]).map((k) =>
          h("button", { key: k, className: "kv-fchip kv-v-" + k + (vf === k ? " on" : ""), "aria-pressed": vf === k,
            onClick: () => setVf(vf === k ? null : k) }, VERDICTS[k].label, h("b", null, counts[k])))),
      h("div", { className: "kv-cols" },
        h("section", { className: "kv-col main" },
          h("h3", null, "Needs you", vf && h("span", { className: "kv-sub" }, " · " + VERDICTS[vf].label)),
          list.length === 0 && h(Empty, { title: "Nothing waiting on you" }),
          list.map((it) => h(Row, { key: it.key, item: it, onOpen, selected: selected === it.key,
            relCount: edgesFor(it.key, items).length })),
          open.length > list.length && h("div", { className: "kv-foot" },
            (open.length - list.length) + " more in ",
            h("button", { className: "kv-linkbtn", onClick: () => setTab("pr") }, "PRs"), " and ",
            h("button", { className: "kv-linkbtn", onClick: () => setTab("issue") }, "Issues"))),
        h("div", { className: "kv-col side" },
          clusters.length > 0 && h("section", null,
            h("h3", null, "Competing effort"),
            clusters.map((k) => h("div", { key: k, className: "kv-card" },
              h("button", { className: "kv-linkbtn", onClick: () => onOpen(k) }, "Issue #" + numOf(k) + " · " + (items[k].title || "")),
              h(Graph, { focusKey: k, items, onOpen })))),
          h("section", null,
            h("h3", null, "Recent actions"),
            recent.length === 0 ? h("div", { className: "kv-foot" }, "No confirmed actions yet.")
              : recent.map((a, i) => h(ActionLine, { key: i, a, items, onOpen }))))));
  }

  function ActionLine(p) {
    const { a, items, onOpen } = p;
    const it = items[a.key] || {};
    return h("div", { className: "kv-action" },
      h("span", { className: "kv-action-dot", "aria-hidden": "true" }),
      h("div", null,
        h("div", { className: "kv-action-head" },
          h("b", null, a.action), " ",
          h("button", { className: "kv-linkbtn", onClick: () => onOpen(a.key) }, KIND[kindOf(a.key)].short + " #" + numOf(a.key)),
          a.link && h(ExtLink, { href: a.link, label: "Open on GitHub" }, " ↗")),
        a.summary && h("div", { className: "kv-row-sub" }, a.summary),
        h("div", { className: "kv-foot" }, ago(a.ts) + (it.title ? " · " + it.title : ""))));
  }

  function Activity(p) {
    const list = (p.state.actions || []).slice().reverse();
    if (!list.length) return h(Empty, { title: "No actions yet", hint: "Approvals, comments, nudges and closes you confirm with Hermes are logged here." });
    return h("div", { className: "kv-timeline" }, list.map((a, i) =>
      h(ActionLine, { key: i, a, items: p.state.items || {}, onOpen: p.onOpen })));
  }

  // ---------------------------------------------------------------- header

  function Header(p) {
    const s = p.state.session || {};
    const left = daysLeft(s.milestone_due);
    const c = s.counts || {};
    const meta = [];
    const add = (label, val, cls) => meta.push(h("div", { key: label }, h("span", null, label), h("b", { className: cls || "" }, val)));
    if (s.focus) add("Focus", s.focus);
    if (s.milestone) add("Milestone", s.milestone);
    if (left !== null) add("Due", left < 0 ? Math.abs(left) + "d overdue" : left + "d left", left <= 7 ? "warn" : "");
    if (s.gate) add("Gate", s.gate, toneOf(s.gate));
    Object.keys(c).slice(0, 4).forEach((k) => add(k.replace(/_/g, " "), String(c[k])));
    return h("header", { className: "kv-bar" },
      h("div", { className: "kv-meta" }, meta.length ? meta : h("span", null, "No focus set this session")),
      h("div", { className: "kv-fresh" + (p.err ? " err" : ""), title: p.state.updated_at || "" },
        p.err ? "Can't reach dashboard API" : p.state.updated_at ? "Updated " + ago(p.state.updated_at) : "Waiting for first update"),
      s.last_session && h("div", { className: "kv-lastsession" }, "Last session: " + s.last_session));
  }

  // ---------------------------------------------------------------- page

  function Page() {
    const [state, setState] = useState({ items: {}, views: {}, actions: [], session: {}, updated_at: null });
    const [loaded, setLoaded] = useState(false);
    const [err, setErr] = useState(false);
    const [tab, setTabState] = useState(store("tab") || "overview");
    const [sel, setSel] = useState(null);
    const setTab = (t) => { setTabState(t); store("tab", t); };

    const load = useCallback(() => {
      SDK.fetchJSON(API + "/state").then((d) => { setState(d); setErr(false); setLoaded(true); })
        .catch(() => { setErr(true); setLoaded(true); });
    }, []);
    useEffect(() => {
      load();
      const id = setInterval(load, POLL_MS);
      const vis = () => { if (!document.hidden) load(); };
      document.addEventListener("visibilitychange", vis);
      return () => { clearInterval(id); document.removeEventListener("visibilitychange", vis); };
    }, [load]);

    const open = (key) => setSel(key);
    const items = state.items || {};
    const tabCount = (k) => Object.keys(items).filter((x) => kindOf(x) === k).length;

    return h("div", { className: "kv" + (sel ? " has-drawer" : "") },
      h(Header, { state, err }),
      h("nav", { className: "kv-tabs", role: "tablist" }, TABS.map(([id, label]) =>
        h("button", { key: id, role: "tab", "aria-selected": tab === id, className: tab === id ? "on" : "", onClick: () => setTab(id) },
          label, KIND[id] && tabCount(id) > 0 && h("span", { className: "kv-count" }, tabCount(id))))),
      h("main", { className: "kv-main" },
        !loaded ? h("div", { className: "kv-skel" }, h("i"), h("i"), h("i"))
          : tab === "overview" ? h(Overview, { state, onOpen: open, selected: sel, setTab })
          : tab === "activity" ? h(Activity, { state, onOpen: open })
          : h(ListTab, { key: tab, kind: tab, state, onOpen: open, selected: sel })),
      sel && h("div", { className: "kv-scrim", onClick: () => setSel(null) }),
      sel && h(Drawer, { itemKey: sel, items, onOpen: open, onClose: () => setSel(null) }));
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("kyverno-dashboard", Page);
  }
})();
