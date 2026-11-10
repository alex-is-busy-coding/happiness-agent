// the happiness desk: the front page, the live research board, and story rendering.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === false || value == null) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else node.setAttribute(key, value === true ? "" : value);
    }
    node.append(...children.filter((child) => child != null));
    return node;
}

const STORE = "happiness-desk:stories";
const QUERY_BUDGET = 6;
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const EXAMPLES = [
    ["relationships", "Do people who trust others report being happier?"],
    ["money", "Does feeling richer than average matter more than actual income?"],
    ["time", "In which decade were Americans happiest?"],
    ["world", "Which countries gained the most happiness since 2015?"],
    ["daily life", "Is a life people call exciting also a happy one?"],
    ["relationships", "Do people with kids say they are happier?"],
];

const state = {
    meta: {},
    featured: [],
    guesses: [],
    guessAt: 0,
    score: { played: 0, close: 0 },
    cache: new Map(), // story id -> story, for this page load
    run: null, // the run in progress, if any
    liveRunId: null, // the last finished run, whose raw context the server still holds
    liveStoryId: null,
    charts: [],
};

// --- Small helpers ---

async function getJSON(url, fallback) {
    try {
        const response = await fetch(url);
        return response.ok ? await response.json() : fallback;
    } catch {
        return fallback;
    }
}

function myStories() {
    try {
        return JSON.parse(localStorage.getItem(STORE)) || [];
    } catch {
        return [];
    }
}

function remember(story) {
    try {
        const kept = [story, ...myStories().filter((s) => s.id !== story.id)].slice(0, 12);
        localStorage.setItem(STORE, JSON.stringify(kept));
    } catch {
        /* private window or full storage: the story still shows, it just isn't kept */
    }
}

const issue = (number) => "#" + String(number ?? 0).padStart(3, "0");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function monthYear(iso) {
    const date = new Date(iso + "T12:00:00");
    return isNaN(date) ? "" : date.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function duration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m ? `${m} min ${s} s` : `${s} s`;
}

function fmt(value, unit = "") {
    if (!Number.isFinite(value)) return "n/a";
    const abs = Math.abs(value);
    let text;
    if (abs >= 1000) text = Math.round(value).toLocaleString("en-US");
    else if (Number.isInteger(value)) text = String(value);
    else if (abs >= 100) text = value.toFixed(0);
    else if (abs >= 10) text = value.toFixed(1);
    else text = String(parseFloat(value.toFixed(2)));
    return text + unit;
}

// --- Views and routing ---

function show(view) {
    document.body.dataset.view = view;
    for (const main of $$("main.view")) main.hidden = main.id !== view;
    if (view !== "story") {
        delete document.body.dataset.theme;
        document.title = "the happiness desk";
    }
    $("#working").hidden = !(state.run && view !== "desk");
    window.scrollTo(0, 0);
}

async function findStory(id) {
    if (state.cache.has(id)) return state.cache.get(id);
    const story = myStories().find((s) => s.id === id) || (await getJSON("/stories/" + encodeURIComponent(id), null));
    if (story) state.cache.set(id, story);
    return story;
}

async function route() {
    const id = new URLSearchParams(location.search).get("story");
    if (id) {
        const story = await findStory(id);
        if (story) return renderStory(story);
        history.replaceState({}, "", "/"); // a story that only lived in a restarted server's memory
    }
    show("home");
}

function goHome(anchor) {
    if (location.search) history.pushState({}, "", "/");
    show("home");
    if (anchor) document.getElementById(anchor)?.scrollIntoView();
}

// --- Home ---

function storyCard(story, mine = false) {
    const lead = (story.stats || [])[0];
    return el(
        "a",
        { class: "story-card", href: "?story=" + story.id, "data-theme": story.theme, "data-story": story.id },
        mine ? el("span", { class: "yours", text: "yours" }) : null,
        el("div", { class: "meta" }, el("span", { text: issue(story.number) }), el("span", { text: (story.sources || []).join(" + ") })),
        el("h3", { text: story.title }),
        el("p", { text: story.hook || story.question }),
        lead ? el("div", { class: "card-stat" }, el("b", { text: lead.value }), el("span", { text: lead.label })) : null,
        el("span", { class: "read", text: "read →" })
    );
}

function renderHome() {
    const { gss, whr, atus_wb: atus } = state.meta;
    const pills = [];
    if (gss) pills.push(["money", `${gss.people.toLocaleString("en-US")} Americans · ${gss.first}–${gss.last}`]);
    if (whr) pills.push(["world", `${whr.countries} countries · ${whr.first}–${whr.last}`]);
    if (atus) pills.push(["daily life", `${atus.episodes.toLocaleString("en-US")} moments from people's days · 2021`]);
    $("#data-pills").replaceChildren(...pills.map(([theme, text]) => el("li", { "data-theme": theme, text })));

    const sources = [
        gss && `NORC General Social Survey ${gss.first}–${gss.last}`,
        whr && "World Happiness Report 2026 (Gallup World Poll)",
        atus && "BLS American Time Use Survey Well-Being Module 2021",
    ].filter(Boolean);
    if (sources.length) $("#colophon-sources").textContent = "Sources: " + sources.join(" · ") + ".";

    $("#examples").replaceChildren(
        ...EXAMPLES.map(([theme, question]) => el("button", { type: "button", "data-theme": theme, "data-ask": question, text: question }))
    );
    renderShelves();
}

function renderShelves() {
    $("#archive-cards").replaceChildren(...state.featured.map((story) => storyCard(story)));
    const featuredIds = new Set(state.featured.map((s) => s.id));
    const mine = myStories().filter((s) => !featuredIds.has(s.id));
    $("#mine").hidden = !mine.length;
    $("#mine-cards").replaceChildren(...mine.map((story) => storyCard(story, true)));
}

function rotatePlaceholder() {
    const input = $("#ask-home");
    const prompts = ["does money buy happiness?", ...EXAMPLES.map(([, question]) => question.toLowerCase())];
    let at = 0;
    setInterval(() => {
        if (document.activeElement === input || input.value) return;
        at = (at + 1) % prompts.length;
        input.placeholder = prompts[at];
    }, 3200);
}

// --- Stories ---

const SOURCES = {
    GSS: (meta) =>
        `General Social Survey (NORC), 1972–2024. ${meta.gss ? meta.gss.people.toLocaleString("en-US") : "Tens of thousands of"} US adults asked how happy they are, alongside their income, health, marriage and social life. Shares are weighted.`,
    WHR: (meta) =>
        `World Happiness Report 2026 (Gallup World Poll). People in ${meta.whr ? meta.whr.countries : "over 140"} countries rate their own life from 0 to 10; scores are three-year averages.`,
    ATUS: () =>
        "American Time Use Survey Well-Being Module 2021 (BLS). People rated how happy, meaningful and stressful specific activities from one day felt.",
};

function countUp(node, value) {
    const match = String(value).match(/^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/);
    if (!match || REDUCED_MOTION) return void (node.textContent = value);
    const [, before, digits, after] = match;
    const target = parseFloat(digits.replace(/,/g, ""));
    const decimals = (digits.split(".")[1] || "").length;
    const started = performance.now();
    const tick = (now) => {
        const t = Math.min(1, (now - started) / 900);
        const eased = 1 - Math.pow(1 - t, 3);
        node.textContent = t === 1 ? value : before + (target * eased).toFixed(decimals) + after;
        if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}

const revealer = new IntersectionObserver(
    (entries) => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            entry.target.classList.add("in");
            entry.target.draw?.();
            revealer.unobserve(entry.target);
        }
    },
    { rootMargin: "0px 0px -8% 0px" }
);

function renderStory(story, { live = false } = {}) {
    state.charts.forEach((chart) => chart.destroy());
    state.charts = [];
    state.cache.set(story.id, story);
    live = live || story.id === state.liveStoryId;

    $("#story-issue").textContent = [issue(story.number), (story.sources || []).join(" + "), monthYear(story.created)].filter(Boolean).join(" · ");
    $("#story-title").textContent = story.title;
    $("#story-hook").textContent = story.hook || "";
    $("#story-asked").textContent = `you asked: “${story.question}”`;

    const answer = $("#story-answer");
    answer.textContent = story.answer || "";
    answer.hidden = !story.answer;

    const stats = (story.stats || []).map((stat) => {
        const value = el("b");
        const tile = el("div", { class: "stat reveal" }, value, el("span", { text: stat.label }));
        tile.draw = () => countUp(value, stat.value);
        return tile;
    });
    $("#story-stats").replaceChildren(...stats);

    renderBody(story);
    renderMethods(story, live);
    $("#story-followups").replaceChildren(
        ...(story.followups || []).map((question) => el("button", { type: "button", "data-ask": question, text: question }))
    );

    show("story");
    document.body.dataset.theme = story.theme;
    document.title = `${story.title} · the happiness desk`;
    $$("#story .reveal").forEach((node) => revealer.observe(node));
}

function renderBody(story) {
    const body = $("#story-body");
    const html = window.marked ? marked.parse(story.body || "") : "";
    if (window.marked && window.DOMPurify) body.innerHTML = DOMPurify.sanitize(html);
    else body.textContent = story.body || "";

    for (const code of $$("pre > code.language-chart", body)) {
        let figure = null;
        try {
            figure = chartFigure(JSON.parse(code.textContent));
        } catch {
            /* a chart the writer got wrong is dropped, not shown as raw JSON */
        }
        if (figure) code.parentElement.replaceWith(figure);
        else code.parentElement.remove();
    }
    chipCitations(body);
    for (const node of body.children) node.classList.add("reveal");
}

// "(GSS 1972-2024)" at the end of a sentence becomes a small chip. A paragraph whose sentences
// all cite the same source keeps one chip, at its end.
const CITATION = /\s*\(((?:GSS|WHR|ATUS)[^()]*)\)/g;

function chipCitations(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: (node) => (node.parentElement.closest("figure, pre, code") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    for (const node of texts) {
        const text = node.textContent;
        const matches = [...text.matchAll(CITATION)];
        if (!matches.length) continue;
        const pieces = document.createDocumentFragment();
        let last = 0;
        for (const match of matches) {
            const names = [...new Set(match[1].match(/GSS|WHR|ATUS/g))];
            pieces.append(text.slice(last, match.index));
            pieces.append(
                el("span", { class: "cite", "data-key": names.join("+") },
                    ...names.map((name) => el("button", { type: "button", class: "src", "data-source": name, title: "See how this was measured", text: name })))
            );
            last = match.index + match[0].length;
        }
        pieces.append(text.slice(last));
        node.replaceWith(pieces);
    }
    for (const block of $$("p, li", root)) {
        const cites = $$(".cite", block);
        if (cites.length > 1 && cites.every((cite) => cite.dataset.key === cites[0].dataset.key)) {
            cites.slice(0, -1).forEach((cite) => cite.remove());
        }
    }
}

function renderMethods(story, live) {
    const trail = story.trail || [];
    const queries = trail.reduce((n, entry) => n + entry.queries.length, 0);
    const box = $("#story-methods");
    const peek =
        live && state.liveRunId
            ? el("p", { class: "peek" }, el("button", { type: "button", class: "link", "data-context-toggle": true, text: "see how the agents think" }),
                " (the exact messages each agent sent and received)")
            : "";
    box.replaceChildren(
        el("h2", { text: "how we did this" }),
        el("p", {
            text:
                `${plural(trail.length, "researcher")} ran ${plural(queries, "query").replace("querys", "queries")} against the survey data` +
                (story.seconds ? `, and the story was filed ${duration(story.seconds)} after the question came in. ` : ". ") +
                "Every number above came out of one of those queries. Open a researcher to see the SQL and what came back.",
        }),
        el("ul", { class: "sources" },
            ...(story.sources || []).filter((name) => SOURCES[name]).map((name) =>
                el("li", {}, el("span", { class: "src", text: name }), " " + SOURCES[name](state.meta)))),
        el("h3", { text: "the paper trail" }),
        el("div", { class: "trail" },
            ...trail.map((entry) =>
                el("details", {},
                    el("summary", {}, el("b", { text: `${entry.agent.toLowerCase()} · ${plural(entry.queries.length, "query").replace("querys", "queries")}` }), entry.task),
                    ...entry.queries.flatMap((query) => [el("pre", { class: "sql", text: query.sql }), resultTable(query.result)])))),
        peek
    );
}

function resultTable(markdown) {
    const rows = (markdown || "").split("\n").filter((line) => line.startsWith("|")).map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
    const box = el("div", { class: "result" });
    if (rows.length < 3) return box.append(el("p", { text: (markdown || "No result.").slice(0, 400) })), box;
    const [head, , ...body] = rows;
    const shown = body.slice(0, 12);
    box.append(
        el("table", {},
            el("thead", {}, el("tr", {}, ...head.map((cell) => el("th", { text: cell })))),
            el("tbody", {}, ...shown.map((row) => el("tr", {}, ...row.map((cell) => el("td", { text: cell }))))))
    );
    if (body.length > shown.length) box.append(el("p", { text: `and ${plural(body.length - shown.length, "more row")}` }));
    return box;
}

// --- Charts ---
// Bars are drawn in HTML: horizontal, so long category names fit, with the value at the tip.
// Lines and scatters go to Chart.js. Every chart carries a table of its own data.

function chartFigure(spec) {
    const series = (spec.series || []).filter((s) => Array.isArray(s.data)).slice(0, 3);
    if (!series.length) return null;
    const scatter = spec.type === "scatter";
    const unit = typeof spec.unit === "string" ? spec.unit : "";
    const figure = el("figure", { class: "chart" });
    if (spec.title) figure.append(el("h3", { text: spec.title }));
    const sub = scatter && spec.x_label ? `${spec.y_label || "y"} against ${spec.x_label}` : spec.y_label;
    if (sub) figure.append(el("p", { class: "sub", text: sub }));

    if (series.length > 1) {
        figure.append(
            el("div", { class: "legend" },
                ...series.map((s, i) => el("span", {}, el("i", { style: `background: var(--series-${i + 1})` }), s.name || `series ${i + 1}`)))
        );
    }

    const numbers = series.flatMap((s) => s.data).filter((v) => typeof v === "number");
    if (spec.type === "bar" && numbers.length && numbers.every((v) => v >= 0)) {
        figure.append(barRows(spec, series, unit));
    } else {
        const canvas = el("canvas", { role: "img", "aria-label": spec.title || "chart" });
        figure.append(el("div", { class: "plot" }, canvas));
        figure.draw = () => drawPlot(canvas, spec, series, unit);
    }

    if (spec.note) figure.append(el("p", { class: "note", text: spec.note }));
    figure.append(el("details", {}, el("summary", { text: "view as table" }), dataTable(spec, series, unit)));
    return figure;
}

function barRows(spec, series, unit) {
    const labels = spec.labels || [];
    const max = Math.max(...series.flatMap((s) => s.data).filter(Number.isFinite), 0) || 1;
    const single = series.length === 1;
    const focus = single && spec.highlight ? labels.findIndex((label) => String(label).toLowerCase() === String(spec.highlight).toLowerCase()) : -1;
    const labelEvery = single || labels.length * series.length <= 12;
    const rows = el("div", { class: "bars" + (single ? "" : " multi") });
    labels.forEach((label, i) => {
        const lines = series.map((s, k) => {
            const value = Number(s.data[i]);
            const tone = single ? (focus >= 0 && i !== focus ? " lo" : "") : ` s${k + 1}`;
            const text = `${s.name ? s.name + ": " : ""}${fmt(value, unit)}`;
            return el("div", { class: "bar-line", title: `${label}, ${text}` },
                el("div", { class: "bar-fill" + tone, style: `--v: ${Number.isFinite(value) ? value / max : 0}; --i: ${i}` }),
                labelEvery ? el("span", { class: "bar-value", text: fmt(value, unit) }) : null);
        });
        rows.append(el("div", { class: "bar-label", text: label }), el("div", { class: "bar-group" }, ...lines));
    });
    return rows;
}

function dataTable(spec, series, unit) {
    const scatter = spec.type === "scatter";
    const head = scatter ? ["", spec.x_label || "x", spec.y_label || "y"] : ["", ...series.map((s) => s.name || "value")];
    const rows = scatter
        ? series.flatMap((s) => s.data.map((p) => [p.label || s.name || "", fmt(Number(p.x)), fmt(Number(p.y), unit)]))
        : (spec.labels || []).map((label, i) => [label, ...series.map((s) => fmt(Number(s.data[i]), unit))]);
    return el("table", {},
        el("thead", {}, el("tr", {}, ...head.map((cell) => el("th", { text: cell })))),
        el("tbody", {}, ...rows.map((row) => el("tr", {}, ...row.map((cell) => el("td", { text: cell }))))));
}

// On a scatter, name the points at the four extremes: the ones a reader looks for first.
const extremeLabels = {
    id: "extremeLabels",
    afterDatasetsDraw(chart) {
        const points = chart.data.datasets.flatMap((dataset, d) =>
            dataset.data.map((raw, i) => ({ raw, at: chart.getDatasetMeta(d).data[i] })).filter((p) => p.raw && p.raw.label && p.at));
        if (!points.length) return;
        const by = (key, pick) => points.reduce((best, p) => (pick(p.raw[key], best.raw[key]) ? p : best));
        const named = new Set([by("y", (a, b) => a > b), by("y", (a, b) => a < b), by("x", (a, b) => a > b), by("x", (a, b) => a < b)]);
        const { ctx, chartArea } = chart;
        ctx.save();
        ctx.font = `600 11px ${css("--sans")}`;
        ctx.fillStyle = css("--ink-2");
        for (const point of named) {
            const room = point.at.x < chartArea.right - 110;
            ctx.textAlign = room ? "left" : "right";
            ctx.fillText(point.raw.label, point.at.x + (room ? 10 : -10), point.at.y + 4);
        }
        ctx.restore();
    },
};

function drawPlot(canvas, spec, series, unit) {
    if (!window.Chart) return;
    const scatter = spec.type === "scatter";
    const kind = scatter ? "scatter" : spec.type === "line" ? "line" : "bar";
    const colors = [css("--series-1"), css("--series-2"), css("--series-3")];
    const axis = (title, ticks = {}) => ({
        grid: { color: css("--grid") },
        border: { color: css("--baseline") },
        ticks: { color: css("--ink-2"), font: { size: 12 }, ...ticks },
        title: { display: !!title, text: title || "", color: css("--ink-2"), font: { size: 12 } },
    });
    const values = axis(scatter ? spec.y_label : "", { callback: (v) => fmt(Number(v), unit === "%" ? "%" : "") });
    const categories = axis(scatter ? spec.x_label : "");
    Chart.defaults.font.family = css("--sans");
    state.charts.push(
        new Chart(canvas, {
            type: kind,
            data: {
                labels: scatter ? undefined : spec.labels,
                datasets: series.map((s, i) => ({
                    label: s.name || "",
                    data: s.data,
                    borderColor: colors[i],
                    backgroundColor: colors[i],
                    borderWidth: kind === "line" ? 2 : 0,
                    pointRadius: scatter ? 5 : 4,
                    pointHoverRadius: 7,
                    pointBackgroundColor: colors[i],
                    pointBorderColor: css("--card"),
                    pointBorderWidth: 2,
                    pointHoverBorderWidth: 2,
                    maxBarThickness: 20,
                    borderRadius: 4,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: kind === "bar" ? "y" : "x",
                animation: REDUCED_MOTION ? false : { duration: 900, easing: "easeOutCubic" },
                interaction: scatter ? { mode: "nearest", intersect: false } : { mode: "index", intersect: false },
                layout: { padding: { top: 8, right: scatter ? 16 : 8 } },
                scales: kind === "bar" ? { x: values, y: categories } : { x: { ...categories, type: scatter ? "linear" : "category" }, y: { ...values, grace: "8%" } },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: css("--ink"),
                        titleFont: { weight: "600" },
                        padding: 10,
                        cornerRadius: 8,
                        boxPadding: 4,
                        callbacks: {
                            label: (item) => {
                                if (scatter) return `${item.raw.label || item.dataset.label}: ${fmt(Number(item.raw.x))}, ${fmt(Number(item.raw.y), unit)}`;
                                return `${item.dataset.label ? item.dataset.label + ": " : ""}${fmt(Number(item.raw), unit)}`;
                            },
                        },
                    },
                },
            },
            plugins: scatter ? [extremeLabels] : [],
        })
    );
}

// --- The desk: a run in progress ---

function setStage(stage) {
    const order = ["plan", "research", "check", "write"];
    const at = order.indexOf(stage);
    $$("#stages li").forEach((item, i) => {
        item.classList.toggle("done", i < at);
        item.classList.toggle("active", i === at);
    });
}

function setLead(text, typing = false) {
    const status = $("#lead-status");
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("typing", typing);
}

function agentCard(name, wide = false) {
    return el("article", { class: "agent-card" + (wide ? " wide" : "") },
        el("header", {}, el("span", { class: "dot" }), el("span", { text: name }), el("span", { class: "flavor" })));
}

function ask(question) {
    question = (question || "").trim();
    if (!question) return;
    endRun();
    if (location.search) history.pushState({}, "", "/");

    const run = { question, started: Date.now(), agents: new Map(), working: 0, round: 1, id: null };
    state.run = run;

    $("#desk-eyebrow").textContent = "the desk is on it";
    $("#desk-question").textContent = `“${question}”`;
    $("#outcome").hidden = true;
    $("#desk-grid").hidden = false;
    $("#stages").hidden = false;
    $("#desk-meta").hidden = false;
    $("#cancel").hidden = false;
    setStage("plan");

    const lead = agentCard("the lead", true);
    lead.id = "lead-card";
    lead.append(el("p", { class: "task", id: "lead-status", "aria-live": "polite" }));
    $("#board").replaceChildren(lead);
    setLead("Reading your question and planning the investigation", true);

    document.documentElement.classList.remove("show-context");
    syncContextToggles();
    resetContext();

    $("#timer").textContent = "0:00";
    run.timer = setInterval(() => {
        const seconds = Math.floor((Date.now() - run.started) / 1000);
        const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
        $("#timer").textContent = clock;
        $("#working").textContent = `desk is working · ${clock}`;
    }, 1000);

    showGuess();
    show("desk");

    run.source = new EventSource("/run?" + new URLSearchParams({ question }));
    run.source.onmessage = (message) => state.run === run && handle(JSON.parse(message.data));
    run.source.onerror = () => state.run === run && fail("Lost the connection to the server.");
}

function endRun() {
    const run = state.run;
    if (!run) return;
    run.source?.close();
    clearInterval(run.timer);
    state.run = null;
    $("#working").hidden = true;
    $("#cancel").hidden = true;
}

function handle(event) {
    const run = state.run;
    if (event.type === "start") {
        run.id = event.run_id;
        state.liveRunId = event.run_id;
        syncContextToggles();
    } else if (event.type === "context") {
        if (document.documentElement.classList.contains("show-context")) showContext(run.id);
    } else if (event.type === "task") onTask(run, event);
    else if (event.type === "query") onQuery(run, event);
    else if (event.type === "filed") onFiled(run, event);
    else if (event.type === "activity") onActivity(run, event);
    else if (event.type === "report") {
        endRun();
        state.liveStoryId = event.story.id;
        remember(event.story);
        history.pushState({}, "", "?story=" + event.story.id);
        renderStory(event.story, { live: true });
        renderShelves();
    } else if (event.type === "clarify") clarify(run.question, event);
    else if (event.type === "error") fail(event.message);
}

function onTask(run, event) {
    if (run.working === 0 && run.agents.size > 0) run.round += 1;
    const card = agentCard(event.agent.toLowerCase());
    $(".flavor", card).textContent = "reading the brief";
    const sql = el("pre", { class: "sql", hidden: true });
    const meter = el("div", { class: "meter" }, ...Array.from({ length: QUERY_BUDGET }, () => el("i")), el("span", { text: "no queries yet" }));
    card.append(el("p", { class: "task", text: event.task }), sql, meter);
    const writer = $("#writer-card");
    writer ? writer.before(card) : $("#board").append(card);
    run.agents.set(event.agent, { card, sql, meter, queries: 0, round: run.round });
    run.working += 1;

    const sent = [...run.agents.values()].filter((agent) => agent.round === run.round).length;
    if (run.round === 1) {
        setStage("research");
        setLead(`Split your question into ${plural(sent, "task")} and sent ${plural(sent, "researcher")} to work on them at the same time.`);
    } else {
        setStage("check");
        setLead(`Found a gap in the findings. Sent ${plural(sent, "more researcher")} to close it.`);
    }
}

function flavor(sql) {
    const text = sql.toLowerCase();
    const { gss, whr } = state.meta;
    if (/\batus_wb\b/.test(text)) return "reading people's day diaries";
    if (/\bwhr\b/.test(text) && /\bgss\b/.test(text)) return "cross-checking two surveys";
    if (/\bwhr\b/.test(text)) return whr ? `comparing ${whr.countries} countries` : "comparing countries";
    if (/\bgss\b/.test(text)) return gss ? `asking ${gss.people.toLocaleString("en-US")} Americans` : "asking Americans";
    return "querying";
}

function onQuery(run, event) {
    const agent = run.agents.get(event.agent);
    if (!agent) return;
    agent.queries += 1;
    agent.sql.textContent = event.sql;
    agent.sql.hidden = false;
    $(".flavor", agent.card).textContent = flavor(event.sql);
    $$("i", agent.meter).forEach((dot, i) => dot.classList.toggle("on", i < agent.queries));
    $("span", agent.meter).textContent = plural(agent.queries, "query").replace("querys", "queries");
}

function onFiled(run, event) {
    const agent = run.agents.get(event.agent);
    if (!agent) return;
    agent.card.classList.add("filed");
    agent.card.append(el("span", { class: "stamp" + (event.ok ? "" : " late"), text: event.ok ? "filed" : "out of budget" }));
    $(".flavor", agent.card).textContent = event.ok ? "findings are in" : "ran out of queries";
    run.working -= 1;
    if (run.working === 0) {
        setStage("check");
        setLead("All findings are in. Checking whether anything is still unanswered", true);
    }
}

function onActivity(run, event) {
    if (event.agent !== "Writer" || event.text !== "started") return;
    setStage("write");
    setLead("Nothing important is missing. Over to the writer.");
    $("#lead-card").classList.add("filed");
    const card = agentCard("the writer", true);
    card.id = "writer-card";
    card.append(el("p", { class: "task typing", text: "Turning the findings into a story" }));
    $("#board").append(card);
}

function outcomeBox(...children) {
    $("#desk-grid").hidden = true;
    $("#stages").hidden = true;
    $("#desk-meta").hidden = true;
    const box = $("#outcome");
    box.replaceChildren(el("div", { class: "box" }, ...children));
    box.hidden = false;
    show("desk");
}

function askForm(value) {
    return el("form", { class: "ask", autocomplete: "off" },
        el("input", { name: "question", type: "text", value, "aria-label": "Your question" }),
        el("button", { type: "submit", text: "ask the desk →" }));
}

function clarify(question, event) {
    endRun();
    $("#desk-eyebrow").textContent = "the desk has a question for you";
    outcomeBox(
        el("p", { class: "eyebrow", text: "before anyone starts digging" }),
        el("h2", { text: "that needs a sharper question" }),
        el("p", { text: event.message }),
        el("div", { class: "tries" }, ...(event.options || []).map((option) => el("button", { type: "button", "data-ask": option, text: option }))),
        askForm(question)
    );
}

function fail(message) {
    const question = state.run?.question || "";
    endRun();
    $("#desk-eyebrow").textContent = "the desk dropped this one";
    outcomeBox(
        el("p", { class: "eyebrow", text: "no story filed" }),
        el("h2", { text: "that run fell over" }),
        el("p", { text: "Something broke before the writer could file. It is usually a hiccup talking to the model, and asking again tends to work." }),
        el("p", { class: "error", text: message }),
        el("div", { class: "tries" }, el("button", { type: "button", "data-ask": question, text: "try again" })),
        el("p", { class: "next-links" }, el("a", { href: "/", "data-home": true, text: "← back to the archive" }))
    );
}

// --- The desk: guess the stat while you wait ---

function showGuess() {
    const box = $("#guess");
    const guesses = state.guesses;
    box.hidden = !guesses.length;
    if (!guesses.length) return;

    if (state.guessAt >= guesses.length) {
        box.replaceChildren(
            el("p", { class: "eyebrow" }, el("span", { text: "guess the stat" })),
            el("h2", { text: "That was all of them." }),
            el("p", { class: "verdict", text: `You were close on ${state.score.close} of ${state.score.played}. The desk is still digging.` })
        );
        return;
    }

    const guess = guesses[state.guessAt];
    const middle = guess.min + Math.round((guess.max - guess.min) / 2 / guess.step) * guess.step;
    const output = el("output");
    const paint = (value) => output.replaceChildren(fmt(value), el("small", { text: guess.unit }));
    const slider = el("input", { type: "range", min: guess.min, max: guess.max, step: guess.step, value: middle, "aria-label": "Your guess" });
    slider.addEventListener("input", () => paint(Number(slider.value)));
    paint(middle);

    const range = el("div", { class: "range" }, slider,
        el("div", { class: "ends" }, el("span", { text: fmt(guess.min) }), el("span", { text: fmt(guess.max) })));
    const verdict = el("div", { class: "verdict", "aria-live": "polite" });
    const button = el("button", { type: "button", text: "lock it in" });
    const score = el("span", { class: "score", text: state.score.played ? `close on ${state.score.close} of ${state.score.played}` : "" });

    button.addEventListener("click", () => {
        if (slider.disabled) {
            state.guessAt += 1;
            return showGuess();
        }
        const mine = Number(slider.value);
        const off = Math.abs(mine - guess.answer);
        const close = off <= (guess.max - guess.min) * 0.05;
        state.score.played += 1;
        state.score.close += close ? 1 : 0;
        slider.disabled = true;
        range.append(el("i", { class: "actual", style: `--p: ${(guess.answer - guess.min) / (guess.max - guess.min)}` }));
        const points = guess.unit === "%" || guess.unit.includes("10") ? " points" : "";
        const how = off < guess.step / 2 ? "Spot on." : close ? `Close: off by ${fmt(off)}${points}.` : `You were ${fmt(off)}${points} too ${mine > guess.answer ? "high" : "low"}.`;
        verdict.replaceChildren(el("b", { text: `${fmt(guess.answer)}${guess.unit}. ` }), how + " " + guess.reveal + " ", el("span", { class: "src", text: guess.source }));
        score.textContent = `close on ${state.score.close} of ${state.score.played}`;
        button.textContent = "next one →";
    });

    box.replaceChildren(
        el("p", { class: "eyebrow" }, el("span", { text: "guess while you wait" }), el("span", { text: `${state.guessAt + 1} / ${guesses.length}` })),
        el("h2", { text: guess.prompt }),
        output, range, verdict,
        el("div", { class: "actions" }, button, score)
    );
}

// --- The raw-context panel ---

function syncContextToggles() {
    const on = document.documentElement.classList.contains("show-context");
    for (const button of $$("[data-context-toggle]")) {
        button.textContent = on ? "hide the agents' context" : "see how the agents think";
        button.hidden = !state.liveRunId;
    }
}

function toggleContext() {
    const on = document.documentElement.classList.toggle("show-context");
    if (on && state.liveRunId) showContext(state.liveRunId);
    syncContextToggles();
}

// --- Wiring ---

document.addEventListener("submit", (event) => {
    const form = event.target.closest("form.ask");
    if (!form) return;
    event.preventDefault();
    ask(form.elements.question.value);
});

document.addEventListener("click", (event) => {
    const asker = event.target.closest("[data-ask]");
    if (asker) return ask(asker.dataset.ask);

    if (event.target.closest("[data-context-toggle]")) return toggleContext();

    if (event.target.closest("button.src[data-source]")) {
        const methods = $("#story-methods");
        methods.classList.add("in");
        return methods.scrollIntoView({ block: "start" });
    }

    const link = event.target.closest("a[data-story], a[data-home], #working");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    if (link.id === "working") return show("desk");
    if (link.dataset.story) {
        history.pushState({}, "", "?story=" + link.dataset.story);
        return route();
    }
    goHome(link.dataset.home);
});

$("#cancel").addEventListener("click", () => {
    endRun();
    goHome();
});

$("#copy-link").addEventListener("click", async (event) => {
    try {
        await navigator.clipboard.writeText(location.href);
        event.target.textContent = "link copied";
    } catch {
        event.target.textContent = "copy it from the address bar";
    }
    setTimeout(() => (event.target.textContent = "copy link to this story"), 2000);
});

window.addEventListener("popstate", route);

(async function init() {
    const [meta, featured, guesses] = await Promise.all([getJSON("/meta", {}), getJSON("/stories", []), getJSON("/guesses", [])]);
    state.meta = meta;
    state.featured = featured;
    state.guesses = guesses.sort(() => Math.random() - 0.5);
    renderHome();
    rotatePlaceholder();
    syncContextToggles();
    const prefill = new URLSearchParams(location.search).get("q");
    if (prefill) $("#ask-home").value = prefill;
    route();
})();
