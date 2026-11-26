// Degree Navigator front end. The rules engine is the Python package in /py, run with Pyodide.
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS = { met: "Done", in_progress: "In progress", missing: "Still needed", manual: "Ask advisor", info: "Note", not_chosen: "Not chosen" };
const SAMPLE = `SC MATH 1013 3.00 A
SC MATH 1014 3.00 B+
SC MATH 1019 3.00 B
SC MATH 1025 3.00 A
SC MATH 1130 3.00 A
SC MATH 1131 3.00 B+
SC MATH 2015 3.00 B
SC MATH 2030 3.00 B
SC MATH 2130 3.00 A
SC MATH 2131 3.00 B+
SC MATH 3330 3.00 A
SC MATH 3333 3.00 A
SC MATH 4036 3.00 IP
SC MATH 4931 3.00 IP
SC MATH 4949 6.00 IP
LE EECS 1015 3.00 A
LE EECS 1516 3.00 B
LE EECS 2502 3.00 B
AP ITEC 2620 3.00 B
AP ITEC 3221 3.00 A
AP ITEC 3310 3.00 A
AP WRIT 2202 3.00 B+
SC MATH 2022 3.00 B
SC MATH 2041 3.00 A
SC MATH 3241 3.00 B
SC BIOL 1000 3.00 B
SC CHEM 1000 3.00 C+
HH PSYC 1010 6.00 B
AP HIST 1010 6.00 B`;

let INDEX = null, py = null, api = null, loadedYear = null, lastAudit = null, chatMsgs = [];

function setStatus(t) { $("loadstatus").textContent = t; }

async function boot() {
  try {
    INDEX = await (await fetch("data/index.json")).json();
  } catch (e) { setStatus("Could not load calendar data."); return; }
  const years = INDEX.years.map((y) => y.year);
  $("year").innerHTML = years.map((y) => `<option>${esc(y)}</option>`).join("");
  $("year").value = years.includes("FW23/24") ? "FW23/24" : years[years.length - 1];
  fillFaculties();
  const cov = INDEX.coverage[$("year").value];
  if (cov) $("coverage").textContent = `Coverage for ${cov.year}: ${cov.checkable_pct}% of ${cov.rules} program rules can be checked automatically; the rest are flagged for an advisor. Data updated ${INDEX.generated}.`;
  $("year").onchange = () => { fillFaculties(); prepareYear(); };
  $("faculty").onchange = fillPrograms;
  setStatus("Loading the rules engine (first visit takes ~10 seconds)…");
  try {
    py = await loadPyodide();
    const files = await (await fetch("py/manifest.json")).json();
    py.FS.mkdirTree("/home/pyodide/ydn");
    for (const f of files) py.FS.writeFile(`/home/pyodide/ydn/${f}`, await (await fetch(`py/ydn/${f}`)).text());
    py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
    api = py.pyimport("ydn.webapi");
  } catch (e) { setStatus("Could not start the rules engine: " + e); return; }
  await prepareYear();
}

function yearInfo() { return INDEX.years.find((y) => y.year === $("year").value); }

function fillFaculties() {
  const facs = [...new Set(yearInfo().programs.map((p) => p.faculty || "Other"))].sort();
  $("faculty").innerHTML = facs.map((f) => `<option>${esc(f)}</option>`).join("");
  fillPrograms();
}

function fillPrograms() {
  const progs = yearInfo().programs.filter((p) => (p.faculty || "Other") === $("faculty").value)
    .sort((a, b) => a.title.localeCompare(b.title));
  $("program").innerHTML = progs.map((p) => `<option value="${esc(p.pid)}">${esc(p.title)}</option>`).join("");
}

async function prepareYear() {
  if (!api) return;
  const y = yearInfo();
  if (loadedYear === y.year) return;
  $("run").disabled = true;
  setStatus(`Loading the ${y.year} calendar (${y.courses.toLocaleString()} courses)…`);
  const base = `data/${y.slug}/`;
  const [courses, programs, chunks, rules] = await Promise.all(
    ["courses.json", "programs.json", "chunks.json"].map((f) => fetch(base + f).then((r) => r.text()))
      .concat([fetch("data/rules.json").then((r) => r.text())]));
  const info = JSON.parse(api.load_year(y.year, courses, programs, rules, chunks));
  loadedYear = y.year;
  setStatus(`Ready: ${info.programs} programs and ${info.courses.toLocaleString()} courses from the ${y.year} calendar.`);
  $("run").disabled = false;
}

function runAudit() {
  const text = $("transcript").value;
  if (!text.trim()) { $("readcount").textContent = "Paste or type your courses first."; return; }
  const out = JSON.parse(api.audit($("program").value, text, $("major").dataset.user ? $("major").value : "", $("stream").value || ""));
  lastAudit = out;
  $("readcount").textContent = `Read ${out.courses_read} course${out.courses_read === 1 ? "" : "s"}.`;
  if (!$("major").dataset.user) $("major").value = out.major_subjects.join(", ");
  if (out.streams.length) {
    const opts = out.streams[0];
    const cur = $("stream").value;
    $("stream").innerHTML = `<option value="">Pick for me (closest match)</option>` + opts.map((s) => `<option>${esc(s)}</option>`).join("");
    $("stream").value = opts.includes(cur) ? cur : "";
    $("streamwrap").classList.remove("hidden");
  } else $("streamwrap").classList.add("hidden");
  renderSummary(out);
  $("tree").innerHTML = renderNode(out.result, 0);
  for (const id of ["results", "countcheck", "chat", "advisor"]) $(id).classList.remove("hidden");
  $("summarytext").value = api.advisor_summary();
  $("finder").classList.add("hidden");
  $("results").scrollIntoView({ behavior: "smooth" });
}

function leaves(n, acc = []) {
  if (n.status === "not_chosen") return acc;
  if (!n.children.length && n.kind !== "note") acc.push(n);
  n.children.forEach((c) => leaves(c, acc));
  return acc;
}

function renderSummary(out) {
  const ls = leaves(out.result);
  const c = (s) => ls.filter((l) => l.status === s).length;
  const total = ls.find((l) => l.kind === "total");
  $("summary").innerHTML = [
    total ? `<div class="pill">${esc(total.message)}</div>` : "",
    `<div class="pill"><b>${c("met")}</b> done</div>`,
    `<div class="pill"><b>${c("in_progress")}</b> in progress</div>`,
    `<div class="pill"><b>${c("missing")}</b> still needed</div>`,
    `<div class="pill"><b>${c("manual")}</b> to confirm with an advisor</div>`,
  ].join("");
}

function renderNode(n, depth) {
  if (n.status === "not_chosen" && depth > 1) return "";
  if (n.kind === "note") return "";
  const label = n.label || ({ all_of: "Required courses", credits_from: "Choose from a list", bucket: "Credits", upper_level: "Upper-level credits", total: "Total credits" }[n.kind] || "Requirements");
  const findable = ["missing", "manual", "in_progress"].includes(n.status) && ["bucket", "credits_from", "all_of"].includes(n.kind) && n.status !== "manual";
  const head = `<div class="head"><span class="badge ${n.status}">${STATUS[n.status] || n.status}</span>
    <div><span class="label">${esc(label)}</span>${n.need && n.status !== "met" ? ` <span class="msg">· ${n.need} credits to go</span>` : ""}
    ${n.kind !== "group" && n.message ? `<div class="msg">${esc(n.message)}</div>` : ""}
    ${n.used && n.used.length ? `<div class="used">Using: ${esc(n.used.join(", "))}</div>` : ""}
    ${findable ? `<button class="small" data-rule="${esc(n.rule_id)}" data-label="${esc(label)}">Find courses that count</button>` : ""}</div></div>`;
  const kids = n.children.map((c) => renderNode(c, depth + 1)).join("");
  if (n.kind === "group" && kids) {
    const open = depth < 2 || n.status !== "met";
    return `<details class="node" ${open ? "open" : ""}><summary>${head}</summary><div class="kids">${kids}</div></details>`;
  }
  return `<div class="node">${head}</div>`;
}

function showCourses(rule, label) {
  $("finder").classList.remove("hidden");
  $("findertitle").textContent = label;
  $("finder").dataset.rule = rule;
  refreshCourses();
  $("finder").scrollIntoView({ behavior: "smooth" });
}

function refreshCourses() {
  const out = JSON.parse(api.courses_for($("finder").dataset.rule, $("topic").value, 60));
  if (out.error) { $("courselist").innerHTML = `<p class="hint">${esc(out.error)}</p>`; return; }
  let list = out.courses;
  if ($("onlymet").checked) list = list.filter((c) => c.prerequisites === "met");
  const pre = { met: ["good", "Prerequisites met"], pending: ["warn", "Prerequisite in progress"], missing: ["bad", "Missing prerequisite"], check: ["warn", "Extra condition to check"] };
  $("courselist").innerHTML = `<p class="hint">${out.total_matching} matching course(s). ${esc(out.note || "")}</p><div class="courses">` +
    list.map((c) => {
      const [cls, txt] = pre[c.prerequisites] || ["", c.prerequisites];
      const off = c.offered_2026_27 == null ? `<span class="chip">Offering not checked</span>` :
        c.offered_2026_27.length ? `<span class="chip good">2026–27: ${esc(c.offered_2026_27.join(", "))}</span>` : `<span class="chip bad">Not offered 2026–27</span>`;
      return `<div class="course"><div class="t">${esc(c.course)} · ${esc(c.title)} <span class="msg">(${c.credits} cr)</span></div>
        <div class="msg">Counts because ${esc(c.counts_because)}.</div>
        <div class="chips"><span class="chip ${cls}" title="${esc(c.prereq_note)}">${txt}</span>${off}
        ${c.also_helps.map((h) => `<span class="chip good">Also helps: ${esc(h)}</span>`).join("")}</div>
        ${c.prerequisites !== "met" ? `<div class="used">${esc(c.prereq_note)}</div>` : ""}</div>`;
    }).join("") + "</div>";
}

function checkCount() {
  const out = JSON.parse(api.does_it_count($("codeinput").value));
  if (out.error || out.found === false) { $("countout").innerHTML = `<p class="hint">${esc(out.error || out.answer)}</p>`; return; }
  const li = (h, ok) => `<li><b>${esc(h.requirement)}</b> — ${esc(h.why)}${ok ? "" : ""}</li>`;
  $("countout").innerHTML = `<p><b>${esc(out.course)}</b> ${esc(out.title)} (${out.credits} cr)
    ${out.already_taken_or_excluded ? ' · <span class="chip bad">Already taken, or excluded by a course you took</span>' : ""}</p>
    <p class="msg">Prerequisites: ${esc(out.prereq_note)}</p>
    ${out.counts_toward.length ? `<p>Would count toward:</p><ul>${out.counts_toward.map((h) => li(h, true)).join("")}</ul>` : "<p>It doesn't fill any open rule-checked requirement.</p>"}
    ${out.does_not_count_toward.length ? `<p>Would not count toward:</p><ul>${out.does_not_count_toward.map((h) => li(h, false)).join("")}</ul>` : ""}
    ${out.double_count_warning ? `<p class="notice">${esc(out.double_count_warning)}</p>` : ""}`;
}

function addMsg(cls, text) {
  const d = document.createElement("div");
  d.className = "m " + cls;
  d.textContent = text;
  $("messages").appendChild(d);
  $("messages").scrollTop = 1e9;
}

async function ask() {
  const q = $("question").value.trim();
  const key = $("apikey").value.trim();
  if (!q) return;
  if (!key) { addMsg("bot", "Add an Anthropic API key above to use the assistant. Everything else on this page works without it."); return; }
  $("question").value = "";
  addMsg("user", q);
  const spec = JSON.parse(api.agent_spec());
  chatMsgs.push({ role: "user", content: q });
  try {
    for (let step = 0; step < 6; step++) {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
          "anthropic-dangerous-direct-browser-access": "true" },
        body: JSON.stringify({ model: $("model").value.trim(), max_tokens: 1500, system: spec.system, tools: spec.tools, messages: chatMsgs }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error?.message || r.status);
      chatMsgs.push({ role: "assistant", content: j.content });
      const uses = j.content.filter((b) => b.type === "tool_use");
      if (!uses.length) {
        addMsg("bot", j.content.filter((b) => b.type === "text").map((b) => b.text).join("\n"));
        return;
      }
      const results = uses.map((u) => {
        addMsg("tool", `checked: ${u.name} ${Object.values(u.input || {}).join(" ")}`);
        return { type: "tool_result", tool_use_id: u.id, content: String(api.tool(u.name, JSON.stringify(u.input || {}))).slice(0, 12000) };
      });
      chatMsgs.push({ role: "user", content: results });
    }
    addMsg("bot", "I couldn't finish within the step limit. Try a narrower question.");
  } catch (e) {
    addMsg("bot", "The AI request failed: " + e.message);
    chatMsgs = [];
  }
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-rule]");
  if (b) { e.preventDefault(); showCourses(b.dataset.rule, b.dataset.label); }
});
$("run").onclick = runAudit;
$("sample").onclick = () => {
  const y = yearInfo();
  const ds = y.programs.find((p) => /Data Science - Bachelor of Science - Honours/.test(p.title));
  if (ds) { $("faculty").value = ds.faculty || "Other"; fillPrograms(); $("program").value = ds.pid; }
  $("transcript").value = SAMPLE;
  $("readcount").textContent = "Sample: a made-up 4th-year Data Science (Computation stream) student.";
};
$("major").oninput = () => { $("major").dataset.user = "1"; };
$("major").onchange = () => lastAudit && runAudit();
$("stream").onchange = () => lastAudit && runAudit();
$("program").onchange = () => { delete $("major").dataset.user; };
$("topic").oninput = refreshCourses;
$("onlymet").onchange = refreshCourses;
$("countbtn").onclick = checkCount;
$("codeinput").onkeydown = (e) => { if (e.key === "Enter") checkCount(); };
$("ask").onclick = ask;
$("question").onkeydown = (e) => { if (e.key === "Enter") ask(); };
$("copy").onclick = () => navigator.clipboard.writeText($("summarytext").value);
boot();
