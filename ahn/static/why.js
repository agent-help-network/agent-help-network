// D108: the /en/why and /ko/why page. It sends one public hostname and the chosen codes to GET /v1/why and shows the
// answer's own sentences. Every string from the answer is put in with textContent, never as HTML. The page writes no sentence
// of its own about a cause, a person or a site: the labels below are headings and state names only.
"use strict";

const WHY_COPY = {
  en: {
    host_bad: "That does not look like a public website hostname. Nothing was sent.",
    failed: "AHN did not answer. Try again in a moment.",
    limited: "Too many lookups right now. Try again later.",
    sent: "Sent to AHN",
    not_sent: "Not sent: the path, query, sign-in details and port of what you pasted.",
    none: "no choices",
    where: "Where to look first",
    person: "Does a person have to take this step?",
    person_yes: "Yes",
    person_no: "Not indicated",
    who: "Who",
    server: "Server summary (as given)",
    action: "Next action",
    happened: "What AHN's records and your answers show",
    unknowns: "What AHN cannot see",
    do_now: "What to check now",
    others: "What other agents reported (last 7 days)",
    timeline: "Recorded changes",
    handoff: "Ready-to-send notes",
    copy: "Copy",
    copied: "Copied",
    previous: "Previous answer",
    layer: { site: "The site", platform: "The agent product", account: "The account and sign-in", agent_env: "The agent's browser", task: "The task", unknown: "No single layer" },
    state: { evidence: "AHN record", your_answer: "your answer", background: "background only", not_seen: "not seen by AHN", not_reviewed: "not reviewed", no_doc: "no official page found", unread: "page could not be read" },
    to: { user: "the person using the agent", it_admin: "company IT", platform_admin: "the agent product's administrator", platform_vendor: "the agent product's vendor", site_support: "the site's support" },
    check_who: { person: "a person", agent: "the agent", it_admin: "company IT", platform_admin: "the agent product's administrator" },
  },
  ko: {
    host_bad: "공개 웹사이트 호스트명으로 보이지 않아요. 아무것도 보내지 않았어요.",
    failed: "AHN 이 답하지 않았어요. 잠시 뒤 다시 해 주세요.",
    limited: "지금은 조회가 너무 많아요. 나중에 다시 해 주세요.",
    sent: "AHN 에 보낸 것",
    not_sent: "보내지 않은 것: 붙여 넣은 주소의 경로·쿼리·로그인 정보·포트.",
    none: "고른 값 없음",
    where: "먼저 볼 곳",
    person: "사람이 직접 해야 하는 단계인가요?",
    person_yes: "예",
    person_no: "표시 없음",
    who: "대상",
    server: "서버 요약(서버 원문, 영어)",
    action: "다음 행동",
    happened: "AHN 기록과 고른 답이 보여 주는 것",
    unknowns: "AHN 이 볼 수 없는 것",
    do_now: "지금 확인할 것",
    others: "다른 에이전트가 알린 것(최근 7일)",
    timeline: "기록된 변경",
    handoff: "넘길 때 쓸 문장",
    copy: "복사",
    copied: "복사했어요",
    previous: "이전 답",
    layer: { site: "사이트", platform: "에이전트 제품", account: "계정·로그인", agent_env: "에이전트의 브라우저", task: "작업", unknown: "한 층으로 좁혀지지 않음" },
    state: { evidence: "AHN 기록", your_answer: "고른 답", background: "참고만", not_seen: "AHN 이 보지 못함", not_reviewed: "검토하지 않음", no_doc: "공식 문서 없음", unread: "문서를 읽지 못함" },
    to: { user: "에이전트를 쓰는 사람", it_admin: "회사 IT", platform_admin: "에이전트 제품 관리자", platform_vendor: "에이전트 제품 공급사", site_support: "사이트 지원팀" },
    check_who: { person: "사람", agent: "에이전트", it_admin: "회사 IT", platform_admin: "에이전트 제품 관리자" },
  },
};

const CHOICE_FIELDS = ["agent_platform", "agent_env", "external_browser", "stop_point", "screen_message", "since_kind", "task_context"];
const HOST_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const RESERVED_TLDS = new Set(["local", "localhost", "internal", "invalid", "example", "arpa", "onion", "lan", "corp", "home", "intranet", "private", "localdomain", "alt"]);

// Mirrors ahn/explain.py clean_host and valid_host: what is left of a pasted address once the scheme, path, query, fragment,
// sign-in details and port are cut. Returns null when no valid hostname is left. Nothing cut is kept.
function cleanHost(raw) {
  let text = String(raw == null ? "" : raw).trim();
  const low = text.toLowerCase();
  for (const scheme of ["https://", "http://"]) {
    if (low.startsWith(scheme)) { text = text.slice(scheme.length); break; }
  }
  for (const sep of ["/", "?", "#"]) {
    const index = text.indexOf(sep);
    if (index >= 0) text = text.slice(0, index);
  }
  if (text.includes("@")) text = text.slice(text.lastIndexOf("@") + 1);
  const colon = text.lastIndexOf(":");
  if (colon > 0 && /^[0-9]+$/.test(text.slice(colon + 1))) text = text.slice(0, colon);
  const host = text.trim().replace(/\.+$/, "").toLowerCase();
  return validHost(host) ? host : null;
}

function validHost(host) {
  if (!host || host.length > 253) return false;
  const labels = host.split(".");
  if (labels.length < 2 || labels.some((label) => !HOST_LABEL.test(label))) return false;
  const tld = labels[labels.length - 1];
  if (RESERVED_TLDS.has(tld) || /^[0-9]+$/.test(tld) || !(/^[a-z]+$/.test(tld) || tld.startsWith("xn--"))) return false;
  return true;
}

// Mirrors ahn/service.py aggregate_eligible_host / policy_host and ahn/response_guide.py apex_host. The two tables come from the
// server (data-rules on the form), so the page cannot drift from them.
function makeRules(rules) {
  const suffixes = new Set(rules.suffixes2);
  const tlds = new Set(rules.tlds);
  const eligible = (host) => {
    const labels = host.split(".");
    if (labels[0].startsWith("xn--")) return false;
    if (labels.length === 2) return tlds.has(labels[1]);
    return labels.length === 3 && suffixes.has(labels.slice(1).join("."));
  };
  const policyHost = (host) => {
    if (host.startsWith("www.") && !eligible(host)) {
      const apex = host.slice(4);
      if (eligible(apex)) return apex;
    }
    return host;
  };
  const apexHost = (host) => {
    const labels = host.split(".");
    for (const width of [2, 3]) {
      if (labels.length <= width) break;
      const candidate = labels.slice(-width).join(".");
      if (suffixes.has(candidate)) continue;
      if (eligible(candidate) && candidate !== host) return candidate;
    }
    return null;
  };
  // The hostname that is sent: the pasted address cut to its host, and an account or tenant subdomain to its registered domain.
  return (raw) => {
    const host = cleanHost(raw);
    if (host === null) return null;
    const apex = apexHost(host);
    return apex && apex !== policyHost(host) ? apex : host;
  };
}

function safeUrl(value) {
  return typeof value === "string" && /^https:\/\/[^\s]+$/.test(value) ? value : null;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function linkTo(url, label) {
  const href = safeUrl(url);
  if (!href) return null;
  const a = el("a", "", label || url);
  a.href = href;
  a.rel = "noopener noreferrer";
  return a;
}

function section(copy, title, className) {
  const box = el("section", "why-block" + (className ? " " + className : ""));
  box.appendChild(el("h2", "", title));
  return box;
}

function paragraphs(box, texts) {
  for (const text of texts) if (text) box.appendChild(el("p", "", text));
}

function renderWhere(copy, body) {
  const box = section(copy, copy.where);
  const w = body.where_to_look;
  box.appendChild(el("p", "why-lead", w.text));
  const list = el("ul", "why-layers");
  for (const name of ["site", "platform", "account", "agent_env", "task"]) {
    const item = el("li", name === w.first ? "why-first" : "");
    item.appendChild(el("strong", "", copy.layer[name] + ": "));
    item.appendChild(document.createTextNode(copy.state[w.layers[name]] || w.layers[name]));
    list.appendChild(item);
  }
  box.appendChild(list);
  return box;
}

function renderPerson(copy, body) {
  const box = section(copy, copy.person);
  const p = body.person;
  box.appendChild(el("p", "why-lead", p.needed === "yes" ? copy.person_yes : copy.person_no));
  if (p.needed === "yes") {
    const list = el("ul", "");
    for (const reason of p.reasons || []) list.appendChild(el("li", "", reason.text));
    box.appendChild(list);
    if (p.who && p.who.length) box.appendChild(el("p", "why-small", copy.who + ": " + p.who.map((w) => copy.to[w] || w).join(", ")));
  } else if (p.text) {
    box.appendChild(el("p", "", p.text));
  }
  return box;
}

function renderServer(copy, body) {
  const box = section(copy, copy.server, "why-server");
  const s = body.server;
  if (s.summary_line) {
    const line = el("p", "why-lead", s.summary_line);
    line.lang = "en";  // the lookup's own sentence, as given
    if (!body.server_note && body.lang === "ko") line.lang = "ko";  // D112: a Korean answer whose sentence is the Korean version
    box.appendChild(line);
    // D110: in a Korean answer, the fixed sentence that says this line is the lookup's own, in English.
    if (body.server_note && body.server_note.text) box.appendChild(el("p", "why-small", body.server_note.text));
  }
  // D112: the fixed sentence saying this block is the plain lookup answer, given without the step's context.
  if (body.server_context_note && body.server_context_note.text) box.appendChild(el("p", "why-small", body.server_context_note.text));
  const parts = el("p", "why-small");
  parts.appendChild(document.createTextNode(copy.action + ": "));
  parts.appendChild(el("code", "", (s.next_action && s.next_action.do) || (s.recommendation && s.recommendation.action) || ""));
  if (s.next_action && s.next_action.url) {
    const a = linkTo(s.next_action.url);
    if (a) { parts.appendChild(document.createTextNode(" ")); parts.appendChild(a); }
  }
  if (s.next_action && s.next_action.customer_url) {
    const a = linkTo(s.next_action.customer_url);
    if (a) { parts.appendChild(document.createTextNode(" ")); parts.appendChild(a); }
  }
  box.appendChild(parts);
  return box;
}

function renderHappened(copy, body) {
  const box = section(copy, copy.happened);
  const list = el("ul", "");
  for (const row of body.what_happened.layers) {
    const item = el("li", "", row.text);
    const a = row.source && linkTo(row.source.url, row.source.url);
    if (a) { item.appendChild(document.createTextNode(" ")); item.appendChild(a); }
    list.appendChild(item);
  }
  box.appendChild(list);
  if (body.what_happened.unknowns.length) {
    box.appendChild(el("h3", "", copy.unknowns));
    const unknowns = el("ul", "");
    for (const u of body.what_happened.unknowns) unknowns.appendChild(el("li", "", u.text));
    box.appendChild(unknowns);
  }
  return box;
}

function renderDoNow(copy, body) {
  const box = section(copy, copy.do_now);
  const list = el("ol", "");
  for (const check of body.do_now.checks) {
    const item = el("li", "");
    item.appendChild(el("span", "why-who", (copy.check_who[check.who] || check.who) + ": "));
    item.appendChild(document.createTextNode(check.text));
    const a = check.url && linkTo(check.url, check.url);
    if (a && !check.text.includes(check.url)) { item.appendChild(document.createTextNode(" ")); item.appendChild(a); }
    list.appendChild(item);
  }
  box.appendChild(list);
  return box;
}

function renderOthers(copy, body) {
  const box = section(copy, copy.others);
  box.appendChild(el("p", "", body.others_saw.text));
  if (body.others_saw.reports.length) {
    const list = el("ul", "");
    for (const report of body.others_saw.reports) list.appendChild(el("li", "", report.text));
    box.appendChild(list);
  }
  return box;
}

function renderTimeline(copy, body) {
  const t = body.timeline;
  if (!t.items.length && !t.limits_text) return null;
  const box = section(copy, copy.timeline);
  if (t.items.length) {
    const list = el("ul", "");
    for (const item of t.items) {
      const li = el("li", "", String(item.at).slice(0, 10) + ": " + item.text);
      const a = item.source_url && linkTo(item.source_url, item.source_url);
      if (a) { li.appendChild(document.createTextNode(" ")); li.appendChild(a); }
      list.appendChild(li);
    }
    box.appendChild(list);
  }
  box.appendChild(el("p", "why-small", t.limits_text));
  return box;
}

function renderHandoff(copy, body) {
  const box = section(copy, copy.handoff);
  for (const item of body.handoff) {
    const row = el("div", "why-handoff");
    row.appendChild(el("p", "why-small", copy.to[item.to] || item.to));
    row.appendChild(el("blockquote", "", item.text));
    const button = el("button", "why-copy", copy.copy);
    button.type = "button";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(item.text);
        button.textContent = copy.copied;
      } catch { /* clipboard not available: the text stays selectable */ }
    });
    row.appendChild(button);
    box.appendChild(row);
  }
  return box;
}

const SECTIONS = {
  where_to_look: renderWhere, person: renderPerson, what_happened: renderHappened, do_now: renderDoNow,
  others_saw: renderOthers, timeline: renderTimeline, handoff: renderHandoff,
};

// The answer's own order (render_order); the lookup's own words (server) come first, or after the checks when the answer says so.
function renderAnswer(copy, body) {
  const wrap = el("div", "why-answer");
  const order = Array.isArray(body.render_order) ? body.render_order.filter((name) => SECTIONS[name]) : Object.keys(SECTIONS);
  const last = Array.isArray(body.render_order) && body.render_order.includes("server");  // D112: the answer puts the lookup block last
  const first = !last && body.server && body.server.position === "first";
  const parts = [];
  for (const name of order) {
    if (name === "person" && first) { parts.push(renderPerson(copy, body)); parts.push(renderServer(copy, body)); continue; }
    parts.push(SECTIONS[name](copy, body));
    if (name === "do_now" && !first && !last) parts.push(renderServer(copy, body));
  }
  if (!order.includes("do_now") && !first && !last) parts.push(renderServer(copy, body));
  if (last) parts.push(renderServer(copy, body));
  for (const part of parts) if (part) wrap.appendChild(part);
  if (body.basis_text) wrap.appendChild(el("p", "why-small why-basis", body.basis_text));
  return wrap;
}

function init() {
  const form = document.getElementById("why-form");
  if (!form) return;
  const lang = form.dataset.lang === "ko" ? "ko" : "en";
  const copy = WHY_COPY[lang];
  const sanitize = makeRules(JSON.parse(form.dataset.rules));
  const result = document.getElementById("why-result");
  const sent = document.getElementById("why-sent");
  const submit = form.querySelector("button[type=submit]");
  const sinceKind = document.getElementById("why-since_kind");
  const sinceRow = document.getElementById("why-since-date-row");
  let previous = null;  // the one earlier answer kept for comparison, as [label, node]
  sinceKind.addEventListener("change", () => { sinceRow.hidden = sinceKind.value !== "worked_before"; });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const host = sanitize(document.getElementById("why-host").value);
    if (host === null) { sent.textContent = copy.host_bad; return; }
    const params = new URLSearchParams();
    params.set("site_host", host);
    params.set("lang", lang);
    const labels = [];
    for (const name of CHOICE_FIELDS) {
      const select = document.getElementById("why-" + name);
      if (select.value) { params.set(name, select.value); labels.push(select.options[select.selectedIndex].text); }
    }
    const day = document.getElementById("why-since_date").value;
    if (sinceKind.value === "worked_before" && /^\d{4}-\d{2}-\d{2}$/.test(day)) params.set("since_date", day);
    sent.textContent = "";
    sent.appendChild(document.createTextNode(copy.sent + ": "));
    sent.appendChild(el("code", "", "GET /v1/why?" + params.toString()));
    sent.appendChild(el("br"));
    sent.appendChild(document.createTextNode(copy.not_sent));
    submit.disabled = true;
    try {
      const response = await fetch("/v1/why?" + params.toString(), { headers: { Accept: "application/json" } });
      if (!response.ok) {
        sent.appendChild(el("br"));
        sent.appendChild(document.createTextNode(response.status === 400 ? copy.host_bad : response.status === 429 ? copy.limited : copy.failed));
        return;
      }
      const body = await response.json();
      const answer = renderAnswer(copy, body);
      // Keep the answer before this one, closed, so two askings with different choices can be read together.
      const stack = el("div", "why-stack");
      const heading = el("h2", "why-host-title", body.site_host);
      stack.appendChild(heading);
      stack.appendChild(answer);
      if (previous) {
        const details = el("details", "why-previous");
        details.appendChild(el("summary", "", copy.previous + " (" + previous.host + "): " + (previous.labels.join(" · ") || copy.none)));
        details.appendChild(previous.node);
        stack.appendChild(details);
      }
      result.textContent = "";
      result.appendChild(stack);
      const kept = answer.cloneNode(true);
      kept.querySelectorAll("button").forEach((node) => node.remove());  // a cloned copy button has no handler; the earlier answer is for reading
      previous = { host: body.site_host, labels, node: kept };
    } catch {
      sent.appendChild(el("br"));
      sent.appendChild(document.createTextNode(copy.failed));
    } finally {
      submit.disabled = false;
    }
  });
}

if (typeof document !== "undefined") init();
if (typeof module !== "undefined" && module.exports) module.exports = { cleanHost, makeRules, validHost };
