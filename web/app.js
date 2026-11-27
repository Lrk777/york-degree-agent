// Degree Navigator front end. The rules engine is the Python package in /py, run with Pyodide.
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NOCACHE = { cache: "no-cache" };          // revalidate, so a new deploy shows up right away
const PILL = { met: "Complete", in_progress: "In progress", missing: "Still needed", manual: "Ask an advisor", info: "Note" };
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

let INDEX = null, py = null, api = null, loadedYear = null, lastAudit = null, chatMsgs = [], PROGS = [];
let pendingRun = false;
const yearData = {};                              // slug -> Promise of [courses, programs, chunks] text

// ---------------------------------------------------------------- loading
function progress(pct, text) {
  $("loadfill").style.width = pct + "%";
  $("loadstatus").textContent = text;
  if (pct >= 100) setTimeout(() => $("loading").classList.add("hidden"), 1200);
  else $("loading").classList.remove("hidden");
}

function fetchYear(slug) {
  if (!yearData[slug]) {
    const base = `data/${slug}/`;
    yearData[slug] = Promise.all(["courses.json", "programs.json", "chunks.json"].map((f) => fetch(base + f, NOCACHE).then((r) => r.text())));
  }
  return yearData[slug];
}

async function boot() {
  progress(5, "Loading the list of programs…");
  try {
    INDEX = await (await fetch("data/index.json", NOCACHE)).json();
  } catch (e) { progress(5, "Could not load the calendar data. Please refresh the page."); return; }
  const years = INDEX.years.map((y) => y.year);
  $("year").innerHTML = years.map((y) => `<option>${esc(y)}</option>`).join("");
  $("year").value = years.includes("FW23/24") ? "FW23/24" : years[years.length - 1];
  setYearPrograms();
  showCoverage();
  fetchYear(yearInfo().slug);                     // start downloading this year's data in parallel
  progress(15, "Starting the checker (first visit only takes about 15 seconds; you can pick your program meanwhile)…");
  try {
    py = await loadPyodide();
    progress(55, "Loading the rules engine…");
    const files = await (await fetch("py/manifest.json", NOCACHE)).json();
    py.FS.mkdirTree("/home/pyodide/ydn");
    const texts = await Promise.all(files.map((f) => fetch(`py/ydn/${f}`, NOCACHE).then((r) => r.text())));
    files.forEach((f, i) => py.FS.writeFile(`/home/pyodide/ydn/${f}`, texts[i]));
    py.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
    api = py.pyimport("ydn.webapi");
  } catch (e) { progress(15, "Could not start the checker: " + e + ". Try refreshing, or a different browser."); return; }
  await prepareYear();
}

async function prepareYear() {
  if (!api) return;
  const y = yearInfo();
  if (loadedYear === y.year) return;
  progress(75, `Loading the ${y.year} calendar (${y.courses.toLocaleString()} courses)…`);
  const [courses, programs, chunks] = await fetchYear(y.slug);
  const rules = await fetch("data/rules.json", NOCACHE).then((r) => r.text());
  api.load_year(y.year, courses, programs, rules, chunks);
  loadedYear = y.year;
  progress(100, `Ready: ${y.year} calendar loaded.`);
  updateRunButton();
  if ($("transcript").value.trim()) showDetected();
  if (pendingRun) { pendingRun = false; runAudit(); }
}

function yearInfo() { return INDEX.years.find((y) => y.year === $("year").value); }

function showCoverage() {
  const cov = INDEX.coverage[$("year").value];
  if (cov) $("coverage").textContent = `For the ${cov.year} calendar, ${cov.checkable_pct}% of ${cov.rules.toLocaleString()} program rules are checked automatically; the rest are marked "ask an advisor". Data updated ${INDEX.generated}.`;
}

// ---------------------------------------------------------------- program picker
function parseTitle(p) {
  const parts = p.title.split(" - ").map((s) => s.trim());
  let degree = (parts[1] || "").replace(/^Bachelors of/, "Bachelor of").trim();
  let type = parts.slice(2).join(" - ").replace(/^90 credits$/i, "90 Credits").trim();
  if (parts.length === 1) { degree = "Other programs"; type = "—"; }
  if (degree === "Honours Minor") { type = type || "Minor (added to another degree)"; }
  if (!type) type = "Standard";
  const fac = (p.code || "").split("-")[0];
  return { ...p, name: parts[0], degree, type, fac };
}

function setYearPrograms() {
  PROGS = yearInfo().programs.map(parseTitle);
  const counts = {};
  PROGS.forEach((p) => { counts[p.degree] = (counts[p.degree] || 0) + 1; });
  const first = ["Bachelor of Science", "Bachelor of Arts"];
  const rank = (d) => (first.indexOf(d) + 1) || (d === "Other programs" ? 999 : 99);
  const degrees = Object.keys(counts).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const prev = $("degree").value;
  $("degree").innerHTML = degrees.map((d) => `<option>${esc(d)}</option>`).join("");
  $("degree").value = degrees.includes(prev) ? prev : degrees[0];
  fillNames();
}

function fillNames() {
  const names = [...new Set(PROGS.filter((p) => p.degree === $("degree").value).map((p) => p.name))].sort((a, b) => a.localeCompare(b));
  const prev = $("progname").value;
  $("progname").innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("");
  if (names.includes(prev)) $("progname").value = prev;
  fillTypes();
}

function fillTypes() {
  const opts = PROGS.filter((p) => p.degree === $("degree").value && p.name === $("progname").value);
  const order = ["Honours", "Specialized Honours", "Honours Major/Minor", "Honours Double Major", "90 Credits", "Standard"];
  opts.sort((a, b) => ((order.indexOf(a.type) + 99) % 99) - ((order.indexOf(b.type) + 99) % 99) || a.type.localeCompare(b.type));
  const seen = {};
  opts.forEach((p) => { seen[p.type] = (seen[p.type] || 0) + 1; });
  const prev = $("progtype").value;
  $("progtype").innerHTML = opts.map((p) => `<option value="${esc(p.pid)}">${esc(p.type)}${seen[p.type] > 1 ? ` (${esc(p.code || p.fac)})` : ""}</option>`).join("");
  if (opts.some((p) => p.pid === prev)) $("progtype").value = prev;
  showChosen();
}

function currentProgram() { return PROGS.find((p) => p.pid === $("progtype").value); }

function showChosen() {
  const p = currentProgram();
  $("chosen").innerHTML = p ? `Checking: ${esc(p.title)} <span class="status">· ${esc(p.faculty || "")} · ${esc($("year").value)} calendar</span>` : "";
  delete $("major").dataset.user;
}

function selectProgram(pid) {
  const p = PROGS.find((x) => x.pid === pid);
  if (!p) return;
  $("degree").value = p.degree; fillNames();
  $("progname").value = p.name; fillTypes();
  $("progtype").value = p.pid; showChosen();
  $("searchhits").innerHTML = "";
  $("progsearch").value = "";
}

function searchPrograms() {
  const q = $("progsearch").value.toLowerCase().split(/\s+/).filter(Boolean)
    .map((w) => ({ bsc: "science", ba: "arts", hons: "honours" }[w] || w));
  if (!q.length) { $("searchhits").innerHTML = ""; return; }
  const hits = PROGS.filter((p) => q.every((w) => p.title.toLowerCase().includes(w))).slice(0, 12);
  $("searchhits").innerHTML = hits.length ? hits.map((p) => `<button class="hit" data-pid="${esc(p.pid)}">${esc(p.title)}</button>`).join("")
    : `<span class="hint">No program matches. Try fewer words.</span>`;
}

// ---------------------------------------------------------------- courses in
async function pdfText(file) {
  if (!window.pdfjsLib) {
    await new Promise((ok, bad) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
      s.onload = ok; s.onerror = () => bad(new Error("could not load the PDF reader"));
      document.head.appendChild(s);
    });
    pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }
  const doc = await pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
  const lines = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const items = (await (await doc.getPage(i)).getTextContent()).items;
    const rows = {};
    items.forEach((it) => { const y = Math.round(it.transform[5] / 3); (rows[y] = rows[y] || []).push(it); });
    Object.keys(rows).map(Number).sort((a, b) => b - a)
      .forEach((y) => lines.push(rows[y].sort((a, b) => a.transform[4] - b.transform[4]).map((it) => it.str).join(" ")));
  }
  return lines.join("\n");
}

async function readFile(file) {
  if (!file) return;
  $("readcount").textContent = `Reading ${file.name}…`;
  try {
    const text = /\.pdf$/i.test(file.name) || file.type === "application/pdf" ? await pdfText(file) : await file.text();
    $("transcript").value = text;
    $("readcount").textContent = `Read ${file.name}.`;
    showDetected();
  } catch (e) {
    $("readcount").textContent = `Couldn't read ${file.name} (${e.message}). Open it, copy the course list, and paste it below instead.`;
    $("pastebox").open = true;
  }
}

function showDetected() {
  const text = $("transcript").value;
  if (!text.trim()) { $("detected").classList.add("hidden"); return; }
  if (!api) { $("detected").classList.remove("hidden"); $("detected").innerHTML = `<span class="hint">Your courses will be listed here as soon as the checker is ready.</span>`; return; }
  const out = JSON.parse(api.preview(text));
  const cls = { passed: "done", in_progress: "in_progress", failed: "grey", withdrawn: "grey" };
  $("detected").classList.remove("hidden");
  $("detected").innerHTML = out.courses.length
    ? `<b>We found ${out.courses.length} course${out.courses.length === 1 ? "" : "s"}.</b> <span class="hint">Missing one? Open "paste / type your courses" and add it.</span>
       <div class="chips">${out.courses.map((c) => `<span class="chip ${cls[c.status] || ""}" title="${esc(c.title)}">${esc(c.course)} <small>${esc(c.grade || "IP")}</small></span>`).join("")}</div>`
    : `<b>No courses found in that text.</b> <span class="hint">Each line needs a course code like SC MATH 2030 and a grade.</span>`;
}

function updateRunButton() {
  $("run").disabled = false;
  $("run").textContent = api && loadedYear ? "Check my degree" : "Check my degree (getting ready…)";
}

// ---------------------------------------------------------------- the check
function runAudit() {
  const text = $("transcript").value;
  if (!text.trim()) { $("readcount").textContent = "Upload or paste your courses first."; return; }
  if (!api || loadedYear !== $("year").value) {
    pendingRun = true;
    $("readcount").textContent = "Getting the checker ready; your results will appear automatically.";
    return;
  }
  const p = currentProgram();
  if (!p) { $("readcount").textContent = "Pick your program first."; return; }
  const out = JSON.parse(api.audit(p.pid, text, $("major").dataset.user ? $("major").value : "", $("stream").value || ""));
  lastAudit = out;
  if (!$("major").dataset.user) $("major").value = out.major_subjects.join(", ");
  if (out.streams.length) {
    const opts = out.streams[0], cur = $("stream").value;
    $("stream").innerHTML = `<option value="">Pick for me (closest match)</option>` + opts.map((s) => `<option>${esc(s)}</option>`).join("");
    $("stream").value = opts.includes(cur) ? cur : "";
    $("streamwrap").classList.remove("hidden");
  } else $("streamwrap").classList.add("hidden");
  render(out);
  for (const id of ["results", "countcheck", "chat", "advisor"]) $(id).classList.remove("hidden");
  $("summarytext").value = api.advisor_summary();
  $("finder").classList.add("hidden");
  $("results").scrollIntoView({ behavior: "smooth" });
}

function leaves(v, acc = []) {
  if (v.status === "not_chosen") return acc;
  if (v.kind === "group") (v.children || []).forEach((c) => leaves(c, acc));
  else acc.push(v);
  return acc;
}

function render(out) {
  const all = out.sections.flatMap((s) => leaves(s));
  const reqs = all.filter((l) => l.kind !== "total");
  const n = (s) => reqs.filter((l) => l.status === s).length;
  const total = all.find((l) => l.kind === "total");
  let done = 0, ip = 0, need = 120;
  if (total) {
    const m = /([\d.]+) of ([\d.]+) credits done(?:, ([\d.]+) in progress)?/.exec(total.message || "");
    if (m) { done = +m[1]; need = +m[2]; ip = +(m[3] || 0); }
  }
  const pct = (x) => Math.min(100, (100 * x) / (need || 1));
  $("headline").innerHTML = `
    <div><div class="big-num">${n("met")}<span style="font-size:18px;color:var(--muted)"> / ${reqs.length}</span></div><div class="sub2">requirements complete</div></div>
    <div class="bar-wrap"><div class="prog">${esc(out.program)}</div>
      <div class="sub2">${esc(out.year)} calendar · ${out.courses_read} courses read</div>
      ${total ? `<div class="bar"><span class="b-done" style="width:${pct(done)}%;float:left"></span><span class="b-ip" style="width:${pct(ip)}%;float:left"></span></div>
      <div class="legend">${done} credits done${ip ? ` + ${ip} in progress` : ""} of ${need} needed</div>` : ""}</div>`;

  // Still to do
  const missing = reqs.filter((l) => l.status === "missing");
  const manual = reqs.filter((l) => l.status === "manual");
  const inprog = reqs.filter((l) => l.status === "in_progress");
  const todo = $("todo");
  todo.classList.toggle("alldone", !missing.length && !manual.length);
  const what = (l) => {
    const miss = (l.items || []).filter((i) => i.state === "missing");
    if (l.kind === "credits_from" && (l.items || []).length <= 3 && miss.length === l.items.length)
      return `Take ${miss.length > 1 ? "one of: " : ""}${miss.map((i) => `<b>${esc(i.course)}</b>${i.title ? ` ${esc(i.title)}` : ""}`).join(" or ")}`;
    if (l.kind === "all_of" && miss.length) return `Still to take: ${miss.map((i) => `<b>${esc(i.course)}</b>${i.title ? ` ${esc(i.title)}` : ""}`).join(", ")}`;
    if (l.need) return `<b>${l.need} more credits</b> needed${l.rule_text ? `: ${esc(l.rule_text)}` : ""}`;
    return esc(l.message || "");
  };
  todo.innerHTML = (!missing.length && !manual.length)
    ? `<h2>✓ Nothing missing</h2><p>Every requirement the checker can read is done${inprog.length ? " or in progress" : ""}. Confirm with your advisor before you apply to graduate.</p>`
    : `<h2>Still to do (${missing.length})</h2><ul>${missing.map((l) => `<li><div class="where">${esc(l.section)}${titleOf(l) !== l.section ? ` › ${esc(titleOf(l))}` : ""}</div>${what(l)}</li>`).join("")}
       ${manual.map((l) => `<li class="manual"><div class="where">${esc(l.section)} › ${esc(titleOf(l))}</div>Ask an advisor: ${esc((l.message || "").replace(/^Confirm with an advisor /, ""))}</li>`).join("")}</ul>
       ${inprog.length ? `<p class="status">${inprog.length} more will be complete when your in-progress courses finish.</p>` : ""}`;

  $("sections").innerHTML = out.sections.map(sectionCard).join("");
  myCourses(out.courses);
}

function sectionCard(s) {
  const st = s.status === "info" ? "met" : s.status;
  const tot = leaves(s).find((l) => l.kind === "total");
  const left = tot ? tot.need : s.need;
  const body = (s.children || []).map((c) => node(c, 0)).join("");
  return `<div class="section ${st}"><div class="sec-head"><h3>${esc(s.title)}</h3><span class="pill ${st}">${PILL[st] || st}${left && st !== "met" ? ` · ${left} cr left` : ""}</span></div>
    <div class="sec-body">${body || `<p class="status">Nothing to check here.</p>`}</div></div>`;
}

function node(v, depth) {
  if (v.status === "not_chosen") return "";
  if (v.kind === "group") {
    const kids = v.children || [];
    const repeats = kids.some((c) => c.title === v.title);
    let head = v.title && depth < 3 && !repeats ? `<div class="subhead">${esc(v.title)}</div>` : "";
    if (v.mode && v.mode !== "all") {
      const chosen = kids.filter((c) => c.status !== "not_chosen");
      const names = (v.options || []);
      head += names.length
        ? `<div class="choice">Choose ${v.mode === "1" ? "one" : v.mode} of: ${names.map((o) => (v.chosen || []).includes(o) ? `<b>${esc(o)}</b>` : esc(o)).join(" · ")}${(v.chosen || []).length ? " (picked from your courses; change it under “Adjust the check”)" : ""}</div>`
        : `<div class="choice">Any one of these options:</div>`;
      return head + (chosen.length ? chosen : kids).map((c) => node(c, depth + 1)).join("");
    }
    return head + kids.map((c) => node(c, depth + 1)).join("");
  }
  return leaf(v);
}

function chip(i) {
  const label = i.state === "done" ? "✓" : i.state === "in_progress" ? "◔" : "✗";
  return `<span class="chip ${i.state}" title="${esc(i.title)}">${label} ${esc(i.course)}${i.grade ? ` <small>${esc(i.grade)}</small>` : ""}</span>`;
}

function titleOf(l) {
  if (l.kind === "credits_from" && (l.items || []).length === 1) return "Required course";
  if (l.kind === "bucket" && l.title === "Credits") return /elective/i.test(l.rule_text || "") ? "Elective credits" : "Credits from approved courses";
  return l.title;
}

function leaf(l) {
  const st = l.status === "info" ? "met" : l.status;
  const title = titleOf(l);
  const used = (l.used || []).map((u) => `<span class="chip ${u.status === "passed" ? "done" : "in_progress"}">${u.status === "passed" ? "✓" : "◔"} ${esc(u.course)} <small>${esc(u.grade)}</small></span>`).join("");
  const findBtn = ["missing", "in_progress"].includes(st) && ["bucket", "credits_from"].includes(l.kind)
    ? `<button class="small" data-rule="${esc(l.rule_id)}" data-label="${esc(title)}">Find courses that count</button>` : "";
  let inner = "";
  if (l.kind === "all_of") {
    inner = `<div class="chips">${(l.items || []).map(chip).join("")}</div>`;
  } else if (l.kind === "credits_from") {
    const took = (l.items || []).filter((i) => i.state !== "missing");
    const rest = (l.items || []).filter((i) => i.state === "missing");
    inner = `<div class="req-note">${l.credits} credits from ${l.items.length > 1 ? "these courses" : "this course"}</div>
      <div class="chips">${took.map(chip).join("")}${st !== "met" && rest.length <= 4 ? rest.map((i) => `<span class="chip ${st === "missing" ? "missing" : "option"}" title="${esc(i.title)}">${st === "missing" ? "✗" : ""} ${esc(i.course)}</span>`).join("") : ""}</div>
      ${rest.length > 4 || (st === "met" && rest.length) ? `<details class="opts"><summary>${st === "met" ? "Other options" : "Options"} (${rest.length})</summary><div class="chips">${rest.map((i) => `<span class="chip option" title="${esc(i.title)}">${esc(i.course)}${i.title ? ` <small>${esc(i.title)}</small>` : ""}</span>`).join("")}</div></details>` : ""}`;
  } else if (l.kind === "bucket") {
    inner = `${l.rule_text ? `<div class="req-note">${esc(l.rule_text)}</div>` : ""}<div class="chips">${used || (st === "manual" ? "" : `<span class="chip grey">none yet</span>`)}</div>`;
  } else {
    inner = `<div class="req-note">${esc(l.message || "")}</div>`;
  }
  const needLine = st === "missing" && l.need ? `<div class="need">✗ ${l.need} more credit${l.need === 1 ? "" : "s"} needed</div>` : "";
  const why = ((l.message || "").match(/\(([^)]*)\)/) || [])[1] || "unclear wording";
  const askLine = st === "manual" ? `<div class="ask">The checker can't read this rule (${esc(why)}). Ask your advisor.</div>` : "";
  return `<div class="req"><div class="req-head"><div class="req-title">${esc(title)}</div><span class="pill ${st}">${PILL[st] || st}</span></div>
    ${inner}${needLine}${askLine}${findBtn ? `<div style="margin-top:8px">${findBtn}</div>` : ""}</div>`;
}

function myCourses(courses) {
  const rows = courses.map((c) => {
    const failed = c.status === "failed" || c.status === "withdrawn";
    const tags = failed ? `<span class="tagc fail">${c.status === "failed" ? "Failed: doesn't count" : "Withdrawn"}</span>`
      : c.counted_in.length ? c.counted_in.map((t) => `<span class="tagc">${esc(t)}</span>`).join("")
      : `<span class="tagc none">Not used yet (extra credit${c.level >= 3000 ? ", counts as upper-level" : ""})</span>`;
    return `<tr class="${!failed && !c.counted_in.length ? "unused" : ""}"><td><b>${esc(c.course)}</b></td><td>${esc(c.title)}</td><td>${esc(c.grade || "IP")}</td><td>${tags}</td></tr>`;
  }).join("");
  $("mycourses").innerHTML = `<h2>Your courses and where each one counted</h2>
    <table class="courses"><thead><tr><th>Course</th><th>Title</th><th>Grade</th><th>Counted toward</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------- finder, course check, chat (unchanged behaviour)
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
  $("courselist").innerHTML = `<p class="hint">${out.total_matching} matching course(s). ${esc(out.note || "")}</p><div class="courses-list">` +
    list.map((c) => {
      const [cls, txt] = pre[c.prerequisites] || ["", c.prerequisites];
      return `<div class="course"><div class="t">${esc(c.course)} · ${esc(c.title)} <span class="msg">(${c.credits} cr)</span></div>
        <div class="msg">Counts because ${esc(c.counts_because)}.</div>
        <div class="chips"><span class="chip ${cls}" title="${esc(c.prereq_note)}">${txt}</span>
        ${c.also_helps.map((h) => `<span class="chip good">Also helps: ${esc(h)}</span>`).join("")}</div>
        ${c.prerequisites !== "met" ? `<div class="used">${esc(c.prereq_note)}</div>` : ""}</div>`;
    }).join("") + "</div>";
}

function checkCount() {
  const out = JSON.parse(api.does_it_count($("codeinput").value));
  if (out.error || out.found === false) { $("countout").innerHTML = `<p class="hint">${esc(out.error || out.answer)}</p>`; return; }
  const li = (h) => `<li><b>${esc(h.requirement)}</b> — ${esc(h.why)}</li>`;
  $("countout").innerHTML = `<p><b>${esc(out.course)}</b> ${esc(out.title)} (${out.credits} cr)
    ${out.already_taken_or_excluded ? ' · <span class="chip bad">Already taken, or excluded by a course you took</span>' : ""}</p>
    <p class="msg">Prerequisites: ${esc(out.prereq_note)}</p>
    ${out.counts_toward.length ? `<p>Would count toward:</p><ul>${out.counts_toward.map(li).join("")}</ul>` : "<p>It doesn't fill any open rule-checked requirement.</p>"}
    ${out.does_not_count_toward.length ? `<p>Would not count toward:</p><ul>${out.does_not_count_toward.map(li).join("")}</ul>` : ""}
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

// ---------------------------------------------------------------- wiring
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-rule]");
  if (b) { e.preventDefault(); showCourses(b.dataset.rule, b.dataset.label); return; }
  const h = e.target.closest("button.hit");
  if (h) { e.preventDefault(); selectProgram(h.dataset.pid); }
});
$("year").onchange = () => { setYearPrograms(); showCoverage(); fetchYear(yearInfo().slug); prepareYear(); updateRunButton(); };
$("degree").onchange = fillNames;
$("progname").onchange = fillTypes;
$("progtype").onchange = showChosen;
$("progsearch").oninput = searchPrograms;
$("pick").onclick = () => $("file").click();
$("file").onchange = () => readFile($("file").files[0]);
const drop = $("drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); readFile(e.dataTransfer.files[0]); };
$("transcript").oninput = () => { clearTimeout(window._t); window._t = setTimeout(showDetected, 400); };
$("run").onclick = runAudit;
$("sample").onclick = () => {
  const ds = PROGS.find((p) => /^Data Science - Bachelor of Science - Honours$/.test(p.title));
  if (ds) selectProgram(ds.pid);
  $("transcript").value = SAMPLE;
  $("pastebox").open = true;
  $("readcount").textContent = "Sample: a made-up 4th-year Data Science (Computation stream) student.";
  showDetected();
};
$("major").oninput = () => { $("major").dataset.user = "1"; };
$("major").onchange = () => lastAudit && runAudit();
$("stream").onchange = () => lastAudit && runAudit();
$("topic").oninput = refreshCourses;
$("onlymet").onchange = refreshCourses;
$("countbtn").onclick = checkCount;
$("codeinput").onkeydown = (e) => { if (e.key === "Enter") checkCount(); };
$("ask").onclick = ask;
$("question").onkeydown = (e) => { if (e.key === "Enter") ask(); };
$("copy").onclick = () => navigator.clipboard.writeText($("summarytext").value);
updateRunButton();
boot();
