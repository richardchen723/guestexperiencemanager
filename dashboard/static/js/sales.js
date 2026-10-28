/* Owner Sales CRM. Render all source content as escaped text; decisions stay on the server. */
(() => {
  "use strict";
  const root = document.querySelector("#sales-app");
  if (!root) return;
  const $ = (s, el = root) => el.querySelector(s),
    $$ = (s, el = root) => [...el.querySelectorAll(s)];
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const badge = (v, cls = "") =>
    `<span class="sales-badge ${cls}">${esc(v)}</span>`;
  const money = (v) =>
    v === null || v === undefined
      ? "Not assessed"
      : new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 0,
        }).format(v);
  const state = {
    boot: null,
    view: "leads",
    tab: "overview",
    deal: null,
    filters: { queue: "all" },
    page: 1,
    boardPages: {},
    request: 0,
  };
  let formSave = null,
    formCommand = null,
    returnFocus = null,
    importData = null;
  const date = (v) =>
    v
      ? new Intl.DateTimeFormat("en-US", {
          timeZone: state.boot?.settings.timezone || "America/Los_Angeles",
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
          timeZoneName: "short",
        }).format(new Date(v))
      : "—";
  const inputDate = (v) =>
    v
      ? new Intl.DateTimeFormat("sv-SE", {
          timeZone: state.boot.settings.timezone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })
          .format(new Date(v))
          .replace(" ", "T")
      : "";
  const note = (msg, error = false) => {
    const el = $(error ? "#sales-error" : "#sales-status");
    el.textContent = msg;
    el.hidden = !msg;
  };
  async function api(path, options = {}) {
    const res = await fetch("/sales/api/" + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": $("meta[name=sales-csrf]", document).content,
        ...options.headers,
      },
    });
    let data;
    try {
      data = await res.json();
    } catch {
      throw Error("Unexpected response. Reload the page or retry.");
    }
    if (!res.ok) {
      const e = Error(data.error || "The request failed.");
      e.status = res.status;
      throw e;
    }
    return data;
  }
  async function mutation(
    path,
    data,
    method = "POST",
    command = formCommand || crypto.randomUUID(),
  ) {
    return api(path, {
      method,
      body: JSON.stringify({ command_id: command, ...data }),
    });
  }
  const query = (extra) =>
    new URLSearchParams({ ...state.filters, ...extra }).toString();
  const btn = (action, label, extra = "", cls = "") =>
    `<button type="button" data-action="${action}" ${extra} class="${cls}">${label}</button>`;
  const pair = (label, value) =>
    `<dl class="sales-pair"><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></dl>`;
  const link = (url, label) =>
    url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
      : "—";
  const personOptions = () => [
    ["", "Unassigned"],
    ...state.boot.users.map((u) => [u.id, u.name]),
  ];
  const sources = () =>
    state.boot.sources.filter((s) => s.active).map((s) => [s.id, s.name]);
  const options = (items, selected) =>
    items
      .map((v) => {
        const [id, label] = Array.isArray(v) ? v : [v, v];
        return `<option value="${esc(id)}" ${String(selected ?? "") === String(id) ? "selected" : ""}>${esc(label)}</option>`;
      })
      .join("");
  function field(name, label, type = "text", value = "", opts = {}) {
    const {
      required = false,
      wide = false,
      items = [],
      hint = "",
      min,
      step,
      max,
    } = opts;
    const attrs = `name="${name}" aria-label="${esc(label)}" ${required ? "required" : ""} ${min !== undefined ? `min="${min}"` : ""} ${max !== undefined ? `max="${max}"` : ""} ${step ? `step="${step}"` : ""}`;
    const control =
      type === "select"
        ? `<select ${attrs}>${options(items, value)}</select>`
        : type === "textarea"
          ? `<textarea ${attrs}>${esc(value)}</textarea>`
          : `<input type="${type}" value="${esc(value)}" ${attrs} ${type === "url" ? 'placeholder="https://…"' : ""}>`;
    return `<label class="sales-field ${wide ? "wide" : ""}">${esc(label)}${control}${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
  }
  const section = (title) => `<h3 class="sales-divider">${esc(title)}</h3>`;
  const checks = (name, items, selected = []) =>
    `<fieldset><legend>${esc(name === "property_ids" ? "Property scope" : "Signatories")}</legend>${items.map((i) => `<label><input type="checkbox" name="${name}" value="${i.id}" ${selected.includes(i.id) ? "checked" : ""}> ${esc(i.name)}</label>`).join("")}</fieldset>`;
  function actionFields(task) {
    return (
      field("next_title", "Next action", "text", task?.title || "", {
        required: true,
        wide: true,
      }) +
      field(
        "next_user",
        "Responsible person",
        "select",
        task?.assigned_user_id ||
          state.deal?.assigned_user_id ||
          state.boot.current_user_id,
        { required: true, items: personOptions() },
      ) +
      field("next_due", "Due time", "datetime-local", inputDate(task?.due_at), {
        required: true,
        hint: state.boot.settings.timezone,
      }) +
      field("fold", "Repeated DST hour", "select", "", {
        items: [
          ["", "Choose only if time repeats"],
          ["0", "First occurrence"],
          ["1", "Second occurrence"],
        ],
        wide: true,
      })
    );
  }
  function actionData(fd) {
    return {
      title: fd.get("next_title"),
      assigned_user_id: Number(fd.get("next_user")),
      due_local: fd.get("next_due"),
      ...(fd.get("fold") !== "" ? { fold: Number(fd.get("fold")) } : {}),
    };
  }
  function modal(title, html, save, label = "Save") {
    returnFocus = document.activeElement;
    $("#sales-dialog-title").textContent = title;
    $("#sales-form-body").innerHTML = `<div class="sales-fields">${html}</div>`;
    $("#sales-form-error").hidden = true;
    $("#sales-save").textContent = label;
    $("#sales-save").disabled = false;
    formSave = save;
    formCommand = crypto.randomUUID();
    $("#sales-dialog").showModal();
    $(
      "#sales-form-body input, #sales-form-body select, #sales-form-body textarea",
    )?.focus();
  }
  function closeModal() {
    $("#sales-dialog").close();
    formCommand = null;
    returnFocus?.focus();
  }
  $("#sales-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!formSave) return;
    $("#sales-save").disabled = true;
    $("#sales-form-error").hidden = true;
    try {
      const result = await formSave(new FormData(event.target));
      if (result === false) return;
      closeModal();
      note("Saved.");
      if (result?.id) {
        await openDeal(result.id);
      } else {
        await refreshBootstrap();
        await render();
      }
    } catch (e) {
      $("#sales-form-error").textContent =
        e.message +
        (e.status === 409
          ? " Close this dialog and reload the opportunity before trying again."
          : "");
      $("#sales-form-error").hidden = false;
    } finally {
      $("#sales-save").disabled = false;
    }
  });
  function filterUI() {
    const f = state.filters,
      advanced = $(".sales-more-filters")?.open,
      focused = document.activeElement?.id === "sales-search",
      cursor = focused ? document.activeElement.selectionStart : null;
    $("#sales-filters").hidden = ["detail", "settings"].includes(state.view);
    if ($("#sales-filters").hidden) return;
    $("#sales-filters").innerHTML = `<div class="sales-queues">${[
      ["all", "All opportunities"],
      ["attention", "Needs attention"],
      ["today", "Due today"],
      ["overdue", "Overdue"],
      ["replies", "Needs reply"],
      ["unassigned", "Unassigned"],
      ["missing_action", "Missing next action"],
      ["nurture_due", "Nurture due"],
      ["handoffs", "Pending handoffs"],
    ]
      .map(([id, label]) =>
        btn(
          "queue",
          label,
          `data-value="${id}"`,
          f.queue === id ? "active" : "",
        ),
      )
      .join(
        "",
      )}</div><div class="sales-filterbar"><input aria-label="Search owners, contacts and properties" id="sales-search" placeholder="Search owners, contacts, properties…" value="${esc(f.search || "")}"><select aria-label="Assigned salesperson" data-filter="assigned_user_id">${options([["", "All salespeople"], ["unassigned", "Unassigned"], ...state.boot.users.map((u) => [u.id, u.name])], f.assigned_user_id)}</select><select aria-label="Market" data-filter="market">${options([["", "All markets"], ...state.boot.markets], f.market)}</select><select aria-label="Source" data-filter="source_id">${options([["", "All sources"], ...state.boot.sources.map((s) => [s.id, s.name])], f.source_id)}</select><select aria-label="Outcome" data-filter="outcome">${options([["", "All outcomes"], ...["open", "won", "nurture", "lost", "disqualified"]], f.outcome)}</select>${btn("clear-filters", "Reset")}</div><details class="sales-more-filters" ${advanced ? "open" : ""}><summary>Date range & saved views</summary><div class="sales-toolbar"><div class="sales-actions"><label>Created from <input type="date" aria-label="Created from" data-filter="created_from" value="${esc(f.created_from || "")}"></label><label>to <input type="date" aria-label="Created to" data-filter="created_to" value="${esc(f.created_to || "")}"></label></div><div class="sales-actions">${btn("save-view", "Save view")}${btn("load-view", "Load saved view")}${state.boot.can_export ? btn("export", "Export CSV") : ""}</div></div></details>`;
    if (focused) {
      $("#sales-search").focus();
      $("#sales-search").setSelectionRange(cursor, cursor);
    }
  }
  async function refreshBootstrap() {
    state.boot = await api("bootstrap");
    $("#sales-settings-tab").hidden = !state.boot.can_manage;
  }
  async function render() {
    const ticket = ++state.request;
    note("", true);
    filterUI();
    $$("[data-view]").forEach((b) =>
      b.setAttribute(
        "aria-current",
        b.dataset.view === state.view ? "page" : "false",
      ),
    );
    $("#sales-content").setAttribute("aria-busy", "true");
    try {
      let html;
      if (state.view === "leads") {
        const result = await api("leads?" + query({ page: state.page }));
        html = leadList(result);
      } else if (state.view === "pipeline") {
        const result = await api("pipeline?" + query(state.boardPages));
        html = pipeline(result);
      } else if (state.view === "reports") {
        html = reports(await api("reports?" + query({})));
      } else if (state.view === "tasks") {
        html = taskList(await api("tasks?" + query({ page: state.page })));
      } else if (state.view === "settings") {
        html = settingsView();
      } else {
        html = detailView();
      }
      if (ticket === state.request) $("#sales-content").innerHTML = html;
    } catch (e) {
      if (ticket === state.request) {
        note(e.message, true);
        $("#sales-content").innerHTML =
          `<div class="sales-empty">The workspace could not load. ${btn("reload", "Retry")}</div>`;
      }
    } finally {
      if (ticket === state.request)
        $("#sales-content").setAttribute("aria-busy", "false");
    }
  }
  function pager(page, total, size, extra = "") {
    return `<div class="sales-pages">${btn("page", "←", `data-page="${page - 1}" ${extra} ${page <= 1 ? "disabled" : ""} aria-label="Previous page"`)}<span>Page ${page} of ${Math.max(1, Math.ceil(total / size))}</span>${btn("page", "→", `data-page="${page + 1}" ${extra} ${page * size >= total ? "disabled" : ""} aria-label="Next page"`)}</div>`;
  }
  function leadCard(d) {
    return `<article class="sales-card"><div class="sales-between"><small>${esc(d.code)}</small>${badge(d.outcome === "open" ? d.stage_label : d.outcome, d.outcome === "won" ? "green" : "purple")}</div><h4><a href="/sales/leads/${d.id}" data-deal="${d.id}">${esc(d.title)}</a></h4><p>${esc(d.account_name || "Owner to confirm")} · ${d.property_count} properties</p><p>${esc(
      d.properties
        .map((p) => p.market)
        .filter(Boolean)
        .join(" · ") || "Market to confirm",
    )}</p><div class="sales-between"><strong>${money(d.annual_management_revenue)}</strong><small>${esc(d.assigned_name)}</small></div><div class="next">${d.primary_task ? `${esc(d.primary_task.title)}<p>${d.overdue ? badge("Overdue", "danger") : ""} ${date(d.primary_task.due_at)}</p>` : d.outcome === "won" ? `Operations: ${esc(d.handoff_status)}` : "No next action"}${d.needs_reply ? badge("Needs reply", "warn") : ""}</div></article>`;
  }
  function leadList(r) {
    return `<div class="sales-toolbar"><span>${r.total} opportunities · owners and properties remain linked</span><select aria-label="Sort opportunities" data-filter="sort">${options(
      [
        ["updated", "Recently updated"],
        ["created", "Recently captured"],
        ["title", "Opportunity title"],
        ["stage", "Pipeline stage"],
      ],
      state.filters.sort || "updated",
    )}</select></div>${r.total ? `<div class="sales-table-wrap leads-table"><table><thead><tr><th>Opportunity / owner</th><th>Properties / market</th><th>Stage</th><th>Salesperson</th><th>Next action</th><th>Annual management</th><th>Source</th><th>Last contact</th></tr></thead><tbody>${r.deals.map((d) => `<tr><td><strong><a href="/sales/leads/${d.id}" data-deal="${d.id}">${esc(d.title)}</a></strong><small>${esc(d.code)} · ${esc(d.account_name || "Owner to confirm")}</small>${d.is_test ? badge("Internal test", "warn") : ""}</td><td>${d.property_count} properties<small>${esc([...new Set(d.properties.map((p) => p.market).filter(Boolean))].join(" · ") || "To confirm")}</small></td><td>${badge(d.outcome === "open" ? d.stage_label : d.outcome, d.outcome === "won" ? "green" : "purple")}<small>${d.days_in_stage} days in stage</small></td><td>${esc(d.assigned_name)}</td><td>${d.primary_task ? `${esc(d.primary_task.title)}<small>${date(d.primary_task.due_at)}</small>` : "—"}${d.overdue ? badge("Overdue", "danger") : ""}${d.needs_reply ? badge("Needs reply", "warn") : ""}</td><td>${money(d.annual_management_revenue)}${d.unassessed_property_count ? `<small>${d.unassessed_property_count} unassessed</small>` : ""}</td><td>${esc(d.source_name)}</td><td><small>In: ${date(d.last_inbound_at)}</small><small>Out: ${date(d.last_outbound_at)}</small></td></tr>`).join("")}</tbody></table></div><div class="sales-mobile-cards">${r.deals.map(leadCard).join("")}</div>` : `<div class="sales-empty"><h2>No opportunities in this view</h2><p>Capture a new owner relationship or adjust your filters.</p>${btn("create", "＋ New opportunity", "", "primary")}</div>`}<div class="sales-toolbar"><span>All amounts USD · unknown estimates remain unassessed</span>${pager(r.page, r.total, r.page_size)}</div>`;
  }
  function pipeline(r) {
    return `<div class="sales-toolbar"><span>8 stages · move an opportunity from its detail page after reviewing requirements</span><span>${Object.entries(
      r.dispositions,
    )
      .map(([k, n]) => `${esc(k)}: ${n}`)
      .join(
        " · ",
      )}</span></div><div class="sales-board" aria-label="Sales pipeline">${r.columns.map((c) => `<section class="sales-column"><h3>${esc(c.label)} ${badge(c.total)}</h3>${c.deals.length ? c.deals.map(leadCard).join("") : '<p class="sales-subtle">No opportunities</p>'}${c.total > 15 ? pager(c.page, c.total, c.page_size, `data-stage="${c.stage}"`) : ""}</section>`).join("")}</div>`;
  }
  async function openDeal(id, push = true) {
    const ticket = ++state.request;
    try {
      const d = await api("leads/" + id);
      if (ticket !== state.request) return;
      state.deal = d;
      state.view = "detail";
      if (push) history.pushState({ deal: id }, "", `/sales/leads/${id}`);
      await render();
    } catch (e) {
      note(e.message, true);
    }
  }
  const contactLine = (c) =>
    `<div class="sales-property"><div class="sales-between"><strong>${esc(c.name)}</strong>${btn("edit-contact", "Edit", `data-id="${c.id}"`)}</div><p>${esc(c.role)} · ${esc(c.email || "No email")} · ${esc(c.phone || "No phone")}</p><div class="sales-actions">${c.permissions.map((p) => badge(`${p.channel}: ${p.status.replaceAll("_", " ")}`, p.status === "allowed" ? "green" : p.status === "opted_out" || p.status === "blocked" ? "danger" : "warn")).join("")}</div>${btn("permission", "Review contact permissions", `data-id="${c.id}"`)}</div>`;
  function detailView() {
    const d = state.deal;
    if (!d) return "";
    return `<div class="sales-detail-header">${btn("back", "← All opportunities", "", "link")}<div class="sales-between"><div><h1>${esc(d.title)}</h1><p>${esc(d.code)} · ${esc(d.account_name || "Owner to confirm")} · ${d.property_count} properties ${badge(d.outcome === "open" ? d.stage_label : d.outcome, "purple")}</p></div><div class="sales-actions">${btn("edit", "Edit opportunity")}${d.outcome !== "won" ? btn("transition", d.outcome === "open" ? "Change stage / outcome" : "Reopen opportunity") : ""}</div></div></div><div class="sales-detail-grid"><div><nav class="sales-tabs sales-detail-tabs" aria-label="Opportunity sections">${[
      ["overview", "Overview"],
      ["activity", "Activity"],
      ["qualification", "Qualification"],
      ["commercial", "Proposals"],
      ["onboarding", "Onboarding"],
    ]
      .map(
        ([id, label]) =>
          `<button data-action="detail-tab" data-tab="${id}" aria-current="${state.tab === id ? "page" : "false"}">${label}</button>`,
      )
      .join(
        "",
      )}</nav>${detailBody(d)}</div><aside><section class="sales-panel accent"><h3>${d.outcome === "won" ? "Operations handoff" : "Next best action"}</h3>${d.outcome === "won" ? `<strong>${esc(d.handoff?.status.replace("_", " "))}</strong><p>${esc(state.boot.users.find((u) => u.id === d.handoff?.assigned_user_id)?.name || "Former team member")}</p>${btn("detail-tab", "View onboarding", 'data-tab="onboarding"')}` : `${d.primary_task ? `<strong>${esc(d.primary_task.title)}</strong><p>${date(d.primary_task.due_at)}<br>${esc(d.primary_task.assigned_name)}</p>${d.overdue ? badge("Overdue", "danger") : ""}` : "<p>Assign a concrete next step and due time.</p>"}<div class="sales-actions">${["open", "nurture"].includes(d.outcome) ? btn("next-action", d.primary_task ? "Change next action" : "Set next action", "", "primary") : ""}${d.primary_task ? btn("complete-task", "Complete", `data-id="${d.primary_task.id}"`) : ""}</div>`}${d.needs_reply ? `<p>${badge("Owner reply needs attention", "warn")}</p>${btn("acknowledge", "Mark reply reviewed")}` : ""}</section><section class="sales-panel sales-context"><h3>Relationship context</h3>${pair("Salesperson", d.assigned_name)}${pair("Original source", d.source_name)}${pair("Captured", date(d.created_at))}${pair("Annual management", money(d.annual_management_revenue))}${pair("Last inbound", date(d.last_inbound_at))}${pair("Last outbound", date(d.last_outbound_at))}${link(d.source_reference, "View source evidence")}</section><section class="sales-panel"><h3>Record quality</h3><p class="sales-compact">Shared contact details suggest a match; confirm the identity before merging.</p>${btn("duplicates", "Review possible duplicates")}<p class="sales-compact">${d.notice_status.map((n) => `${n.count} ${esc(n.status)} alerts`).join(" · ") || "No queued alerts"}</p></section></aside></div>`;
  }
  function detailBody(d) {
    if (state.tab === "overview")
      return `<section class="sales-panel"><h2>Owner relationship</h2><p class="sales-plain">${esc(d.summary || "Add relationship context, motivation, and the best next step.")}</p>${pair("Priority", d.priority)}${pair("Expected close", d.expected_close_date)}${pair("Discovery notes", d.discovery_notes)}<div class="sales-actions">${btn("add-contact", "＋ Contact")}${btn("add-property", "＋ Property")}${btn("link-record", "Link existing record")}${btn("task", "＋ Task / appointment")}${btn("activity", "Log activity")}</div></section><section class="sales-panel"><h3>Properties · ${d.properties.length}</h3>${d.properties.map((p) => `<article class="sales-property"><div class="sales-between"><strong>${esc(p.name)}</strong>${btn("edit-property", "Edit", `data-id="${p.id}"`)}</div><p>${esc(p.address || "Address to confirm")} · ${esc(p.market || "Market to confirm")}</p>${pair("Property", `${p.bedrooms ?? "?"} beds / ${p.bathrooms ?? "?"} baths · ${p.property_type || "Type to confirm"}`)}${pair("Rental status", p.rental_status?.replaceAll("_", " "))}${pair("Annual rental", money(p.rental_revenue))}${pair("Annual management", money(p.management_revenue))}${badge(p.disposition)}${p.listing_id ? ` · ${link("/insights/" + p.listing_id, "Operational listing #" + p.listing_id)}` : ""}</article>`).join("") || "<p>No properties linked yet.</p>"}</section><section class="sales-panel"><h3>Decision makers & contacts</h3>${d.contacts.map(contactLine).join("") || "<p>No contacts linked yet.</p>"}</section>${tasksPanel(d)}`;
    if (state.tab === "activity")
      return `<section class="sales-panel"><div class="sales-between"><h2>Relationship timeline</h2>${btn("activity", "＋ Log activity", "", "primary")}</div><p>Recorded communication, research, decisions, and changes.</p><div class="sales-timeline" id="sales-timeline">${d.activities.map(activityHTML).join("") || "<p>No activity yet.</p>"}</div>${d.activity_has_more ? btn("more-activity", "Load earlier activity", `data-before="${Math.min(...d.activities.map((a) => a.id))}"`) : ""}</section>`;
    if (state.tab === "qualification") {
      const q = d.qualification,
        a = d.assessment;
      return `<section class="sales-panel"><div class="sales-between"><h2>Qualification</h2>${btn("qualify", "Review qualification")}</div><p>Use evidence and human judgment; no automatic numerical score.</p>${state.boot.settings.qualification_guidance ? `<p class="sales-plain">${esc(state.boot.settings.qualification_guidance)}</p>` : ""}${pair("Fit", q.fit)}${pair("Decision", q.decision)}${pair("Readiness", q.readiness)}${pair("Owner needs", q.owner_needs)}${pair("Operational fit", q.operational_fit)}${pair("Expectations", q.owner_expectations)}${pair("Reviewed", date(q.reviewed_at))}${(q.sources || []).map((u) => link(u, "Evidence")).join(" · ")}</section><section class="sales-panel"><div class="sales-between"><h2>Property assessment</h2>${btn("assess", "Review assessment")}</div>${pair("Assumptions", a.assumptions)}${pair("Service strategy", a.service_strategy)}${pair("Reviewed", date(a.reviewed_at))}<p>Annual rental and management estimates remain separate, in USD.</p></section>`;
    }
    if (state.tab === "commercial")
      return `<section class="sales-panel"><div class="sales-between"><h2>Proposal versions</h2>${d.outcome === "open" ? btn("proposal", "＋ New version", "", "primary") : ""}</div><p>Every version requires management approval. Delivery and signatures are recorded from the linked documents.</p>${state.boot.settings.commercial_guidance ? `<p>${esc(state.boot.settings.commercial_guidance)}</p>` : ""}${d.proposals.map((p, i) => `<article class="sales-property"><h3>Version ${p.number} ${badge(p.status, "purple")} ${i ? " · Superseded" : ""}</h3>${link(p.document_url, "Open proposal document")}${pair("Management fee", p.terms.fee_percent + "% · " + p.terms.fee_basis)}${pair("One-time fee", money(p.terms.one_time_fee))}${pair("Scope", p.terms.properties.map((x) => x.name).join(", "))}${pair("Services", p.terms.services)}${pair("Responsibilities", p.terms.responsibilities)}${pair("Exceptions", p.terms.exceptions)}${pair("Open questions", p.terms.open_questions)}${p.actions.map((a) => `<p class="sales-compact">${esc(a.action)} · ${date(a.occurred_at)} · ${esc(a.note)}</p>`).join("")}${i === 0 && d.outcome === "open" ? `<div class="sales-actions">${state.boot.can_manage && ["draft", "withdrawn"].includes(p.status) ? btn("proposal-action", "Approve", `data-id="${p.id}" data-kind="approved"`) : ""}${p.status === "approved" ? btn("proposal-action", "Record sent", `data-id="${p.id}" data-kind="sent"`) : ""}${["approved", "sent"].includes(p.status) ? btn("proposal-action", "Withdraw", `data-id="${p.id}" data-kind="withdrawn"`) : ""}${p.status === "sent" ? btn("sign", "Record signed agreement", "", "primary") : ""}</div>` : ""}</article>`).join("") || "<p>No proposal versions yet.</p>"}</section>`;
    return `<section class="sales-panel"><h2>Agreement & operations</h2>${d.agreement ? `${link(d.agreement.document_url, "Open signed agreement")}${pair("Signed", date(d.agreement.signed_at))}${pair("Effective", d.agreement.effective_date)}${pair("Signed properties", d.agreement.scope.properties.map((p) => p.name).join(", "))}${pair("Signatories", d.agreement.signatories.map((c) => c.name).join(", "))}${pair("Handoff status", d.handoff.status)}${pair("Operations owner", state.boot.users.find((u) => u.id === d.handoff.assigned_user_id)?.name)}${pair("Target launch", d.handoff.target_date)}${pair("Commitments", d.handoff.commitments)}${d.handoff.checklist.map((c) => `<p>${c.done ? "☑" : "☐"} ${esc(c.title)}</p>`).join("")}${d.handoff.operational_links.map((u) => `<p>${link(u, "Operational task")}</p>`).join("")}<div class="sales-actions">${btn("handoff", "Update handoff")}${d.handoff.status === "pending" ? btn("accept-handoff", "Accept handoff", "", "primary") : ""}${d.handoff.status === "in_progress" ? btn("complete-handoff", "Complete onboarding", "", "primary") : ""}</div>` : "<p>A signed agreement creates a handoff with an operations owner. Signed and onboarding are tracked separately.</p>"}</section>`;
  }
  function activityHTML(a) {
    return `<article><strong>${esc(a.kind.replaceAll("_", " "))}</strong> <small>${esc(a.actor_name)} · ${date(a.occurred_at)}</small><p>${esc(a.body || "")}</p>${a.outcome ? badge(a.outcome) : ""}${a.source_url ? link(a.source_url, "Evidence") : ""}${
      Object.keys(a.changes || {}).length
        ? `<details><summary>View recorded changes</summary>${Object.entries(
            a.changes,
          )
            .map(([k, val]) =>
              pair(
                k.replaceAll("_", " "),
                typeof val === "object" ? JSON.stringify(val) : val,
              ),
            )
            .join("")}</details>`
        : ""
    }</article>`;
  }
  function tasksPanel(d) {
    return `<section class="sales-panel"><div class="sales-between"><h3>Tasks & appointments</h3>${btn("task", "＋ Add task")}</div>${d.tasks.map((t) => `<div class="sales-task"><strong>${esc(t.title)}</strong> ${badge(t.is_primary ? "Primary next action" : t.kind)} ${badge(t.status, t.overdue ? "danger" : "")}<small>${date(t.due_at)} · ${esc(t.assigned_name)}</small>${t.status === "open" ? `${btn("complete-task", "Complete", `data-id="${t.id}"`)} ${btn("reschedule", "Reschedule", `data-id="${t.id}"`)} ${btn("cancel-task", "Cancel", `data-id="${t.id}"`)}` : ""}${t.outcome ? `<p>${esc(t.outcome)}</p>` : ""}</div>`).join("") || "<p>No tasks yet.</p>"}</section>`;
  }
  function taskList(r) {
    return `<div class="sales-toolbar"><span>${r.total} open tasks and appointments · ${esc(state.boot.settings.timezone)}</span></div><section class="sales-panel">${r.tasks.map((t) => `<div class="sales-task"><div class="sales-between"><strong>${esc(t.title)}</strong>${badge(t.kind, t.overdue ? "danger" : "")}</div><p><a href="/sales/leads/${t.deal_id}" data-deal="${t.deal_id}">${esc(t.deal_title)}</a> · ${esc(t.assigned_name)}</p><small>${date(t.due_at)} ${t.overdue ? " · Overdue" : ""} ${t.is_primary ? " · Primary next action" : ""}</small></div>`).join("") || "<p>No open tasks in this view.</p>"}</section><div class="sales-toolbar">${pager(r.page, r.total, r.page_size)}</div>`;
  }
  function reports(r) {
    const metric = (label, value, sub = "") =>
      `<div class="sales-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`;
    return `<p class="sales-subtle">Created-date cohort · internal tests excluded · all amounts USD</p><div class="sales-stats">${metric("Opportunities", r.opportunities, `${r.accounts} owners · ${r.properties} properties`)}${metric("Qualified", r.qualified, "Human-reviewed qualification")}${metric("Won / signed", r.signed_agreements, `${r.signed_properties} signed properties`)}${metric("Win rate", r.win_rate === null ? "—" : r.win_rate + "%", r.win_rate_denominator)}${metric("Assessed annual management", money(r.assessed_management_revenue), `${r.assessed_properties} assessed property scopes`)}${metric("Won annual management", money(r.won_management_revenue), `${r.unknown_won_properties} unassessed signed properties`)}${metric("Median first human response", r.first_human_response_median_hours === null ? "—" : r.first_human_response_median_hours + "h", `${r.response_sample_size} recorded responses`)}${metric("Needs follow-through", r.overdue_opportunities, `${r.awaiting_launch} agreements awaiting launch`)}</div><div class="sales-metrics"><section class="sales-panel"><h2>Stage progression</h2><p>Distinct opportunities that entered each stage. Duration is the median visit, including current visits.</p>${r.stages.map((s) => `<div class="sales-between"><span>${esc(s.label)}</span><small>${s.ever_entered} · ${s.median_visit_days ?? "—"} days</small></div><div class="sales-progress"><span style="width:${r.opportunities ? (s.ever_entered / r.opportunities) * 100 : 0}%"></span></div>`).join("")}</section><section class="sales-panel"><h2>Acquisition sources</h2>${r.sources.map((s) => `<div class="sales-property"><strong>${esc(s.name)}</strong>${pair("Opportunities / wins", s.leads + " / " + s.won)}${pair("Recorded acquisition cost", money(s.recorded_cost))}${pair("Cost per win", money(s.acquisition_cost_per_win))}<small>${s.cost_records} of ${s.leads} records have cost data.</small></div>`).join("") || "<p>No source data yet.</p>"}</section></div><p class="sales-subtle">Response time: ${esc(r.response_time_basis)}. Estimates and won amounts use different evidence; unassessed amounts are never counted as zero.</p>`;
  }
  function settingsView() {
    const s = state.boot.settings;
    return `<section class="sales-panel"><div class="sales-between"><h2>Sales operating rules</h2>${btn("settings", "Edit settings", "", "primary")}</div><p>Timing and business rules stay unset until the team agrees them. Proposal approval is always required.</p>${pair("Timezone", s.timezone)}${pair("Reminder delay after due", s.reminder_minutes === null ? "Not configured" : s.reminder_minutes + " minutes")}${pair("Escalation delay", s.escalation_minutes === null ? "Not configured" : s.escalation_minutes + " minutes")}${pair("Unassigned delay", s.unassigned_minutes === null ? "Not configured" : s.unassigned_minutes + " minutes")}${pair("Qualification", s.qualification_guidance)}${pair("Assignment", s.assignment_guidance)}${pair("Commercial", s.commercial_guidance)}${pair("Onboarding", s.onboarding_guidance)}</section><section class="sales-panel"><div class="sales-between"><h2>Acquisition sources</h2>${btn("source", "＋ Add source")}</div>${state.boot.sources.map((s) => `<div class="sales-task"><div class="sales-between"><span>${esc(s.name)} · ${esc(s.kind)} ${badge(s.active ? "Active" : "Retired")}</span>${btn("source", "Edit", `data-id="${s.id}"`)}</div></div>`).join("")}</section><section class="sales-panel"><h2>Intake review</h2><p>Failed imports retain their original source material for review and retry.</p>${btn("failures", "Review failed intake")}</section>`;
  }
  function contactFields(c = {}) {
    return (
      field("name", "Full name", "text", c.name, {
        required: true,
        wide: true,
      }) +
      field("email", "Email", "email", c.email) +
      field("phone", "Phone with country code", "text", c.phone) +
      field(
        "preferred_channel",
        "Preferred channel",
        "select",
        c.preferred_channel,
        { items: [["", "Unknown"], ...["email", "phone", "sms", "whatsapp"]] },
      )
    );
  }
  function propertyFields(p = {}) {
    return (
      field("name", "Property name", "text", p.name, {
        required: true,
        wide: true,
      }) +
      field("address", "Street address", "text", p.address, { wide: true }) +
      field("market", "Market", "text", p.market, {
        hint: state.boot.markets.join(" · "),
        wide: true,
      }) +
      field("property_type", "Property type", "text", p.property_type) +
      field("bedrooms", "Bedrooms", "number", p.bedrooms, {
        min: 0,
        max: 100,
        step: 1,
      }) +
      field("bathrooms", "Bathrooms", "number", p.bathrooms, {
        min: 0,
        max: 100,
        step: 0.5,
      }) +
      field("rental_status", "Rental status", "select", p.rental_status, {
        items: [
          ["", "Unknown"],
          ["self_managed", "Self managed"],
          ["professionally_managed", "Professionally managed"],
          ["not_rented", "Not rented"],
        ],
      }) +
      field(
        "amenities",
        "Amenities (one per line)",
        "textarea",
        (p.amenities || []).join("\n"),
        { wide: true },
      )
    );
  }
  const lines = (value) =>
    String(value || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  function contactData(fd) {
    return {
      name: fd.get("name"),
      email: fd.get("email"),
      phone: fd.get("phone"),
      preferred_channel: fd.get("preferred_channel") || null,
    };
  }
  function propertyData(fd) {
    return {
      name: fd.get("name"),
      address: fd.get("address"),
      market: fd.get("market"),
      property_type: fd.get("property_type"),
      bedrooms: fd.get("bedrooms") === "" ? null : Number(fd.get("bedrooms")),
      bathrooms: fd.get("bathrooms"),
      rental_status: fd.get("rental_status") || null,
      amenities: lines(fd.get("amenities")),
    };
  }
  const dealMutation = (path, data, method = "POST") =>
    mutation(
      `leads/${state.deal.id}${path ? "/" + path : ""}`,
      { version: state.deal.version, ...data },
      method,
    );
  async function handle(action, el) {
    const d = state.deal;
    if (action === "close-dialog") {
      closeModal();
      return;
    }
    if (action === "reload") {
      if (state.view === "detail") await openDeal(d.id, false);
      else await render();
      return;
    }
    if (action === "back") {
      state.view = "leads";
      state.deal = null;
      history.pushState({}, "", "/sales/");
      await render();
      return;
    }
    if (action === "detail-tab") {
      state.tab = el.dataset.tab;
      await render();
      return;
    }
    if (action === "queue") {
      state.filters.queue = el.dataset.value;
      state.page = 1;
      state.boardPages = {};
      await render();
      return;
    }
    if (action === "clear-filters") {
      state.filters = { queue: "all" };
      state.page = 1;
      await render();
      return;
    }
    if (action === "page") {
      if (el.dataset.stage !== undefined)
        state.boardPages["page_" + el.dataset.stage] = el.dataset.page;
      else state.page = Number(el.dataset.page);
      await render();
      return;
    }
    if (action === "save-view") {
      localStorage.setItem(
        "sales-view:" + state.boot.current_user_id,
        JSON.stringify(state.filters),
      );
      note("This filter view is saved in this browser.");
      return;
    }
    if (action === "load-view") {
      try {
        state.filters = JSON.parse(
          localStorage.getItem("sales-view:" + state.boot.current_user_id) ||
            '{"queue":"all"}',
        );
      } catch {
        state.filters = { queue: "all" };
      }
      state.page = 1;
      await render();
      return;
    }
    if (action === "export") {
      window.location.assign("/sales/api/export.csv?" + query({}));
      return;
    }
    if (action === "create") {
      modal(
        "New opportunity",
        field("title", "Opportunity title", "text", "", {
          required: true,
          wide: true,
        }) +
          field("owner_name", "Owner / household / company", "text", "", {
            wide: true,
          }) +
          field(
            "assigned_user_id",
            "Salesperson",
            "select",
            state.boot.current_user_id,
            { items: personOptions() },
          ) +
          field("source_id", "Original source", "select", "", {
            items: [["", "Manual entry"], ...sources()],
          }) +
          field("source_reference", "Source evidence link", "url", "", {
            wide: true,
          }) +
          field("summary", "Relationship context", "textarea", "", {
            wide: true,
          }) +
          section("Initial contact (optional)") +
          field("contact_name", "Contact name") +
          field("email", "Email", "email") +
          field("phone", "Phone with country code") +
          section("Initial property (optional)") +
          field("property_name", "Property name") +
          field("market", "Market", "select", "", {
            items: [["", "To confirm"], ...state.boot.markets],
          }) +
          field("address", "Address", "text", "", { wide: true }),
        (fd) =>
          mutation("leads", {
            title: fd.get("title"),
            account: fd.get("owner_name")
              ? { name: fd.get("owner_name") }
              : null,
            assigned_user_id: fd.get("assigned_user_id")
              ? Number(fd.get("assigned_user_id"))
              : null,
            ...(fd.get("source_id")
              ? { source_id: Number(fd.get("source_id")) }
              : {}),
            source_reference: fd.get("source_reference"),
            summary: fd.get("summary"),
            contacts:
              fd.get("contact_name") || fd.get("email") || fd.get("phone")
                ? [
                    {
                      name: fd.get("contact_name"),
                      email: fd.get("email"),
                      phone: fd.get("phone"),
                    },
                  ]
                : [],
            properties:
              fd.get("property_name") || fd.get("address")
                ? [
                    {
                      name: fd.get("property_name"),
                      address: fd.get("address"),
                      market: fd.get("market"),
                    },
                  ]
                : [],
          }),
        "Create opportunity",
      );
      return;
    }
    if (action === "edit") {
      modal(
        "Edit opportunity",
        field("title", "Opportunity title", "text", d.title, {
          required: true,
          wide: true,
        }) +
          field(
            "assigned_user_id",
            "Salesperson",
            "select",
            d.assigned_user_id,
            { items: personOptions() },
          ) +
          field("priority", "Priority", "select", d.priority, {
            items: ["low", "normal", "high"],
          }) +
          field(
            "expected_close_date",
            "Expected close",
            "date",
            d.expected_close_date,
          ) +
          field(
            "acquisition_cost",
            "Acquisition cost · USD",
            "number",
            d.acquisition_cost,
            { min: 0, step: 0.01 },
          ) +
          field("summary", "Relationship context", "textarea", d.summary, {
            wide: true,
          }) +
          field(
            "discovery_notes",
            "Discovery notes",
            "textarea",
            d.discovery_notes,
            { wide: true },
          ),
        (fd) =>
          dealMutation(
            "",
            {
              title: fd.get("title"),
              assigned_user_id: fd.get("assigned_user_id")
                ? Number(fd.get("assigned_user_id"))
                : null,
              priority: fd.get("priority"),
              expected_close_date: fd.get("expected_close_date"),
              acquisition_cost: fd.get("acquisition_cost"),
              summary: fd.get("summary"),
              discovery_notes: fd.get("discovery_notes"),
            },
            "PATCH",
          ),
      );
      return;
    }
    if (action === "next-action") {
      modal("Primary next action", actionFields(d.primary_task), (fd) =>
        dealMutation("", { next_action: actionData(fd) }, "PATCH"),
      );
      return;
    }
    if (action === "acknowledge") {
      const result = await dealMutation(
        "",
        { acknowledge_reply: true },
        "PATCH",
      );
      await openDeal(result.id, false);
      return;
    }
    if (action === "task") {
      modal(
        "Task or appointment",
        field("kind", "Kind", "select", "followup", {
          items: [
            ["followup", "Follow-up"],
            ["meeting", "Appointment / meeting"],
            ["research", "Research"],
          ],
        }) + actionFields(),
        (fd) => {
          const a = actionData(fd);
          return dealMutation("tasks", {
            action: "create",
            kind: fd.get("kind"),
            title: a.title,
            assigned_user_id: a.assigned_user_id,
            due_local: a.due_local,
            ...(a.fold !== undefined ? { fold: a.fold } : {}),
          });
        },
      );
      return;
    }
    if (["complete-task", "cancel-task", "reschedule"].includes(action)) {
      const t = d.tasks.find((t) => t.id === Number(el.dataset.id));
      if (action === "reschedule") {
        modal(
          "Reschedule task",
          field(
            "due_local",
            "Due time",
            "datetime-local",
            inputDate(t.due_at),
            { required: true, hint: state.boot.settings.timezone, wide: true },
          ) +
            field("fold", "Repeated DST hour", "select", "", {
              items: [
                ["", "Not repeated"],
                ["0", "First occurrence"],
                ["1", "Second occurrence"],
              ],
            }),
          (fd) =>
            dealMutation("tasks", {
              task_id: t.id,
              action: "reschedule",
              due_local: fd.get("due_local"),
              ...(fd.get("fold") !== ""
                ? { fold: Number(fd.get("fold")) }
                : {}),
            }),
        );
      } else {
        const replace = t.is_primary && d.outcome === "open" && d.stage > 0;
        modal(
          action === "complete-task" ? "Complete task" : "Cancel task",
          field("outcome", "Outcome / reason", "textarea", "", {
            required: true,
            wide: true,
          }) +
            (replace
              ? section("Replace the primary next action") + actionFields()
              : ""),
          (fd) =>
            dealMutation("tasks", {
              task_id: t.id,
              action: action === "complete-task" ? "complete" : "cancel",
              outcome: fd.get("outcome"),
              ...(replace ? { next_action: actionData(fd) } : {}),
            }),
        );
      }
      return;
    }
    if (action === "activity") {
      modal(
        "Log activity",
        field("kind", "Activity type", "select", "note", {
          items: [
            "note",
            "research",
            "call",
            "email",
            "meeting",
            "whatsapp",
            "sms",
          ],
        }) +
          field("direction", "Direction", "select", "internal", {
            items: ["internal", "inbound", "outbound"],
          }) +
          field("contact_id", "Contact", "select", "", {
            items: [
              ["", "No contact"],
              ...d.contacts.map((c) => [c.id, c.name]),
            ],
          }) +
          field("channel", "Channel", "select", "", {
            items: [
              ["", "Infer from activity type"],
              ...["email", "phone", "sms", "whatsapp"],
            ],
          }) +
          field("outcome", "Outcome") +
          field("source_url", "Evidence / thread link", "url") +
          field("body", "Details", "textarea", "", {
            required: true,
            wide: true,
            hint: "Outbound communication requires a reviewed permission for the chosen contact and channel.",
          }),
        (fd) =>
          dealMutation("activities", {
            kind: fd.get("kind"),
            direction: fd.get("direction"),
            contact_id: fd.get("contact_id")
              ? Number(fd.get("contact_id"))
              : null,
            channel: fd.get("channel") || null,
            outcome: fd.get("outcome"),
            source_url: fd.get("source_url"),
            body: fd.get("body"),
          }),
      );
      return;
    }
    if (action === "more-activity") {
      const r = await api(
        `leads/${d.id}/activities?before=${el.dataset.before}`,
      );
      $("#sales-timeline").insertAdjacentHTML(
        "beforeend",
        r.activities.map(activityHTML).join(""),
      );
      if (r.next_before) el.dataset.before = r.next_before;
      else el.remove();
      return;
    }
    if (action === "add-contact" || action === "edit-contact") {
      const c =
        action === "edit-contact"
          ? d.contacts.find((c) => c.id === Number(el.dataset.id))
          : {};
      modal(
        c.id ? "Edit shared contact" : "Add contact",
        contactFields(c) +
          (!c.id
            ? field("role", "Relationship role", "text", "decision-maker") +
              checks("property_ids", d.properties)
            : '<p class="sales-subtle">This shared contact is updated across its linked opportunities.</p>'),
        async (fd) => {
          if (c.id) {
            await mutation(
              "records/contact/" + c.id,
              { version: c.version, ...contactData(fd) },
              "PATCH",
            );
            return { id: d.id };
          }
          return dealMutation("relationships", {
            kind: "contact",
            record: {
              ...contactData(fd),
              role: fd.get("role"),
              property_ids: fd.getAll("property_ids").map(Number),
            },
          });
        },
      );
      return;
    }
    if (action === "add-property" || action === "edit-property") {
      const p =
        action === "edit-property"
          ? d.properties.find((p) => p.id === Number(el.dataset.id))
          : {};
      modal(
        p.id ? "Edit shared property" : "Add property",
        propertyFields(p),
        async (fd) => {
          if (p.id) {
            await mutation(
              "records/property/" + p.id,
              { version: p.version, ...propertyData(fd) },
              "PATCH",
            );
            return { id: d.id };
          }
          return dealMutation("relationships", {
            kind: "property",
            record: propertyData(fd),
          });
        },
      );
      return;
    }
    if (action === "link-record") {
      modal(
        "Link an existing record",
        field("kind", "Record type", "select", "contacts", {
          items: [
            ["contacts", "Contact"],
            ["properties", "Property"],
          ],
        }) +
          field("search", "Search by name", "text", "", { wide: true }) +
          `<div class="sales-field wide">${btn("find-records", "Find records")}<div id="sales-record-results"></div></div>` +
          field("record_id", "Selected record", "select", "", {
            required: true,
            items: [["", "Search first"]],
          }),
        (fd) =>
          dealMutation("relationships", {
            kind: fd.get("kind") === "contacts" ? "contact" : "property",
            record: { id: Number(fd.get("record_id")) },
          }),
      );
      return;
    }
    if (action === "find-records") {
      const form = $("#sales-form");
      const fd = new FormData(form);
      const r = await api(
        "directory/" +
          fd.get("kind") +
          "?search=" +
          encodeURIComponent(fd.get("search")),
      );
      form.elements.record_id.innerHTML = options(
        [
          ["", "Choose a record"],
          ...r.records.map((x) => [
            x.id,
            `${x.name} · #${x.id} · ${x.email || x.address || ""}`,
          ]),
        ],
        "",
      );
      $("#sales-record-results").textContent =
        `${r.records.length} records found (up to 50).`;
      return;
    }
    if (action === "permission") {
      const c = d.contacts.find((c) => c.id === Number(el.dataset.id));
      modal(
        "Contact permissions · " + c.name,
        field("channel", "Channel", "select", "email", {
          items: ["email", "phone", "sms", "whatsapp"],
        }) +
          field("status", "Permission", "select", "needs_review", {
            items: [
              ["needs_review", "Needs review"],
              ["allowed", "Allowed"],
              ["blocked", "Blocked"],
              ["opted_out", "Opted out"],
            ],
          }) +
          field("dnc_result", "Do-not-call review", "select", "", {
            items: [
              ["", "Not reviewed"],
              ["clear", "Clear"],
              ["listed", "Listed"],
              ["unknown", "Unknown"],
              ["not_applicable_reviewed", "Not applicable — reviewed"],
            ],
          }) +
          field("expires_at", "Review expires", "datetime-local", "", {
            hint: state.boot.settings.timezone,
          }) +
          field("source_url", "Evidence link", "url", "", { wide: true }) +
          field("basis", "Permission basis / evidence", "textarea", "", {
            required: true,
            wide: true,
            hint: "Permission applies to this contact across all opportunities. Clearing an opt-out requires management access.",
          }),
        (fd) =>
          dealMutation("permissions", {
            contact_id: c.id,
            channel: fd.get("channel"),
            status: fd.get("status"),
            dnc_result: fd.get("dnc_result") || null,
            source_url: fd.get("source_url"),
            basis: fd.get("basis"),
            ...(fd.get("expires_at")
              ? { expires_local: fd.get("expires_at") }
              : {}),
          }),
      );
      return;
    }
    if (action === "transition") {
      modal(
        "Change stage / outcome",
        field("stage", "Stage", "select", d.stage, {
          items: state.boot.stages.slice(0, 6).map((s, i) => [i, s]),
        }) +
          field(
            "outcome",
            "Outcome",
            "select",
            d.outcome === "won" ? "open" : d.outcome,
            { items: ["open", "nurture", "lost", "disqualified"] },
          ) +
          field("reason", "Reason", "textarea", "", {
            wide: true,
            hint: "Required for closing, reopening, or moving backward.",
          }) +
          field(
            "revisit_local",
            "Nurture revisit",
            "datetime-local",
            inputDate(d.revisit_at),
            { wide: true, hint: state.boot.settings.timezone },
          ) +
          `<p class="sales-subtle">Active stages require an assignee and a primary next action. Outreach needs reviewed qualification and recorded outreach; later stages require discovery, assessment, and a sent proposal. Record wins from the signed agreement.</p>`,
        (fd) =>
          dealMutation("transition", {
            stage: Number(fd.get("stage")),
            outcome: fd.get("outcome"),
            reason: fd.get("reason"),
            ...(fd.get("revisit_local")
              ? { revisit_local: fd.get("revisit_local") }
              : {}),
          }),
      );
      return;
    }
    if (action === "qualify") {
      const q = d.qualification;
      modal(
        "Review qualification",
        field("fit", "Property fit", "select", q.fit || "needs_review", {
          items: ["strong", "needs_review", "poor"],
        }) +
          field(
            "decision",
            "Human review decision",
            "select",
            q.decision || "needs_review",
            { items: ["needs_review", "approved", "rejected"] },
          ) +
          ["readiness", "owner_needs", "operational_fit", "owner_expectations"]
            .map((k) =>
              field(k, k.replaceAll("_", " "), "textarea", q[k], {
                wide: true,
              }),
            )
            .join("") +
          field(
            "sources",
            "Evidence URLs (one per line)",
            "textarea",
            (q.sources || []).join("\n"),
            { wide: true },
          ),
        (fd) =>
          dealMutation("qualification", {
            fit: fd.get("fit"),
            decision: fd.get("decision"),
            ...Object.fromEntries(
              [
                "readiness",
                "owner_needs",
                "operational_fit",
                "owner_expectations",
              ].map((k) => [k, fd.get(k)]),
            ),
            sources: lines(fd.get("sources")),
          }),
      );
      return;
    }
    if (action === "assess") {
      const a = d.assessment;
      modal(
        "Review property assessment",
        field("assumptions", "Revenue assumptions", "textarea", a.assumptions, {
          required: true,
          wide: true,
        }) +
          field(
            "service_strategy",
            "Service strategy",
            "textarea",
            a.service_strategy,
            { required: true, wide: true },
          ) +
          field(
            "sources",
            "Evidence URLs (one per line)",
            "textarea",
            (a.sources || []).join("\n"),
            { wide: true },
          ) +
          d.properties
            .map(
              (p) =>
                section(p.name) +
                field(
                  "rental_" + p.id,
                  "Annual rental revenue · USD",
                  "number",
                  p.rental_revenue,
                  { min: 0, step: 0.01 },
                ) +
                field(
                  "management_" + p.id,
                  "Annual management revenue · USD",
                  "number",
                  p.management_revenue,
                  { min: 0, step: 0.01 },
                ),
            )
            .join(""),
        (fd) =>
          dealMutation("assessment", {
            assumptions: fd.get("assumptions"),
            service_strategy: fd.get("service_strategy"),
            sources: lines(fd.get("sources")),
            properties: d.properties.map((p) => ({
              property_id: p.id,
              rental_revenue: fd.get("rental_" + p.id),
              management_revenue: fd.get("management_" + p.id),
            })),
          }),
      );
      return;
    }
    if (action === "proposal") {
      const p = d.proposals[0]?.terms || {};
      modal(
        "New proposal version",
        field("document_url", "Proposal document URL", "url", "", {
          required: true,
          wide: true,
        }) +
          checks(
            "property_ids",
            d.properties,
            d.properties.map((p) => p.id),
          ) +
          field("fee_percent", "Management fee %", "number", p.fee_percent, {
            required: true,
            min: 0,
            max: 100,
            step: 0.01,
          }) +
          field(
            "one_time_fee",
            "One-time fee · USD",
            "number",
            p.one_time_fee,
            { min: 0, step: 0.01 },
          ) +
          field("fee_basis", "Fee basis", "text", p.fee_basis, {
            required: true,
            wide: true,
          }) +
          field(
            "proposed_start",
            "Proposed start date",
            "date",
            p.proposed_start,
          ) +
          ["services", "responsibilities", "open_questions", "exceptions"]
            .map((k) =>
              field(k, k.replaceAll("_", " "), "textarea", p[k], {
                required: ["services", "responsibilities"].includes(k),
                wide: true,
              }),
            )
            .join("") +
          `<p class="sales-subtle">The selected properties and their current assessment estimates are frozen in this proposal version. Editing the assessment later does not rewrite it.</p>`,
        (fd) =>
          dealMutation("proposals", {
            document_url: fd.get("document_url"),
            terms: {
              currency: "USD",
              fee_percent: fd.get("fee_percent"),
              one_time_fee: fd.get("one_time_fee"),
              fee_basis: fd.get("fee_basis"),
              proposed_start: fd.get("proposed_start"),
              ...Object.fromEntries(
                [
                  "services",
                  "responsibilities",
                  "open_questions",
                  "exceptions",
                ].map((k) => [k, fd.get(k)]),
              ),
              properties: d.properties
                .filter((p) =>
                  fd.getAll("property_ids").map(Number).includes(p.id),
                )
                .map((p) => ({
                  property_id: p.id,
                  rental_revenue: p.rental_revenue,
                  management_revenue: p.management_revenue,
                })),
            },
          }),
      );
      return;
    }
    if (action === "proposal-action") {
      const kind = el.dataset.kind;
      modal(
        kind === "sent"
          ? "Record proposal delivery"
          : kind === "approved"
            ? "Approve proposal version"
            : "Withdraw proposal",
        field("note", "Review / delivery evidence", "textarea", "", {
          required: true,
          wide: true,
        }) +
          (kind === "sent"
            ? field("contact_id", "Recipient", "select", "", {
                required: true,
                items: [
                  ["", "Choose contact"],
                  ...d.contacts.map((c) => [c.id, c.name]),
                ],
              }) +
              field("channel", "Channel", "select", "email", {
                items: ["email", "phone", "sms", "whatsapp"],
              }) +
              '<p class="sales-subtle">Record delivery after sending the document through your usual channel.</p>'
            : ""),
        (fd) =>
          dealMutation("proposal-actions", {
            proposal_id: Number(el.dataset.id),
            action: kind,
            note: fd.get("note"),
            ...(kind === "sent"
              ? {
                  contact_id: Number(fd.get("contact_id")),
                  channel: fd.get("channel"),
                }
              : {}),
          }),
      );
      return;
    }
    if (action === "sign") {
      const p = d.proposals[0];
      modal(
        "Record signed agreement",
        field("document_url", "Signed document URL", "url", "", {
          required: true,
          wide: true,
        }) +
          field("signed_local", "Signature time", "datetime-local", "", {
            required: true,
            hint: state.boot.settings.timezone,
          }) +
          field("effective_date", "Effective date", "date", "", {
            required: true,
          }) +
          checks(
            "property_ids",
            p.terms.properties.map((p) => ({
              id: p.property_id,
              name: p.name,
            })),
          ) +
          checks("signatory_ids", d.contacts) +
          field("remaining_action", "Unsigned properties", "select", "split", {
            items: [
              ["split", "Create a separate opportunity"],
              ["exclude", "Exclude with a reason"],
            ],
          }) +
          field("remaining_reason", "Unsigned scope reason", "text", "", {
            wide: true,
          }) +
          section("Operations handoff") +
          field("operations_user_id", "Operations owner", "select", "", {
            required: true,
            items: personOptions(),
          }) +
          field("target_date", "Target launch date", "date") +
          field(
            "commitments",
            "Sales commitments / handoff notes",
            "textarea",
            "",
            { required: true, wide: true },
          ),
        (fd) =>
          dealMutation("sign", {
            proposal_id: p.id,
            document_url: fd.get("document_url"),
            signed_local: fd.get("signed_local"),
            effective_date: fd.get("effective_date"),
            property_ids: fd.getAll("property_ids").map(Number),
            signatory_ids: fd.getAll("signatory_ids").map(Number),
            remaining_action: fd.get("remaining_action"),
            remaining_reason: fd.get("remaining_reason"),
            operations_user_id: Number(fd.get("operations_user_id")),
            target_date: fd.get("target_date"),
            commitments: fd.get("commitments"),
          }),
        "Record signature & create handoff",
      );
      return;
    }
    if (action === "handoff") {
      const h = d.handoff;
      modal(
        "Update operations handoff",
        field(
          "assigned_user_id",
          "Operations owner",
          "select",
          h.assigned_user_id,
          { required: true, items: personOptions() },
        ) +
          field("target_date", "Target launch date", "date", h.target_date) +
          field("commitments", "Commitments", "textarea", h.commitments, {
            required: true,
            wide: true,
          }) +
          field(
            "checklist",
            "Checklist (one per line; prefix completed items with [x])",
            "textarea",
            h.checklist
              .map((c) => (c.done ? "[x] " : "[ ] ") + c.title)
              .join("\n"),
            { wide: true },
          ) +
          field(
            "operational_links",
            "Operational task URLs (one per line)",
            "textarea",
            h.operational_links.join("\n"),
            { wide: true },
          ) +
          d.agreement.scope.properties
            .map((p) =>
              field(
                "listing_" + p.property_id,
                p.name + " → operational listing",
                "select",
                d.properties.find((x) => x.id === p.property_id)?.listing_id ||
                  "",
                {
                  wide: true,
                  items: [
                    ["", "Not linked"],
                    ...state.boot.listings.map((l) => [l.id, l.name]),
                  ],
                },
              ),
            )
            .join(""),
        (fd) =>
          dealMutation("onboarding", {
            action: "update",
            ...(state.boot.can_manage
              ? { assigned_user_id: Number(fd.get("assigned_user_id")) }
              : {}),
            target_date: fd.get("target_date"),
            commitments: fd.get("commitments"),
            checklist: lines(fd.get("checklist")).map((s) => ({
              title: s.replace(/^\[[ xX]\]\s*/, ""),
              done: /^\[[xX]\]/.test(s),
            })),
            operational_links: lines(fd.get("operational_links")),
            listing_links: d.agreement.scope.properties
              .filter((p) => fd.get("listing_" + p.property_id))
              .map((p) => ({
                property_id: p.property_id,
                listing_id: Number(fd.get("listing_" + p.property_id)),
              })),
          }),
      );
      return;
    }
    if (action === "accept-handoff" || action === "complete-handoff") {
      modal(
        action === "accept-handoff"
          ? "Accept operations handoff"
          : "Complete onboarding",
        `<p class="sales-plain">${esc(d.handoff.commitments)}</p><p>${action === "accept-handoff" ? "Accept responsibility for the onboarding checklist and target launch." : "All checklist items must be complete before marking onboarding complete."}</p>`,
        () =>
          dealMutation("onboarding", {
            action: action === "accept-handoff" ? "accept" : "complete",
          }),
        action === "accept-handoff" ? "Accept handoff" : "Complete onboarding",
      );
      return;
    }
    if (action === "duplicates") {
      const r = await api(`leads/${d.id}/duplicates`);
      modal(
        "Review possible duplicates",
        `<div class="sales-field wide">${r.candidates.map((c, i) => `<div class="sales-property"><strong>${esc(c.from_name)} → ${esc(c.name)}</strong><p>${esc(c.kind)} #${c.from_id} → #${c.id} · ${esc(c.reason)}</p><p>${esc(c.email || c.address || c.phone || "")}</p>${state.boot.can_manage ? btn("merge-review", "Review merge", `data-index="${i}"`) : ""}</div>`).join("") || "<p>No exact email, phone, address, or owner-name matches found.</p>"}</div>`,
        () => true,
        "Done",
      );
      root._duplicates = r.candidates;
      return;
    }
    if (action === "merge-review") {
      const c = root._duplicates[Number(el.dataset.index)];
      closeModal();
      modal(
        "Confirm reviewed identity",
        `<p>Merge ${esc(c.kind)} #${c.from_id} (${esc(c.from_name)}) into #${c.id} (${esc(c.name)}). Relationships move to the surviving record; restrictive permissions and history are preserved.</p>` +
          field(
            "reason",
            "Why these records represent the same identity",
            "textarea",
            "",
            { required: true, wide: true },
          ),
        async (fd) => {
          await mutation("merge", {
            kind: c.kind,
            from_id: c.from_id,
            into_id: c.id,
            from_version: c.from_version,
            into_version: c.version,
            reason: fd.get("reason"),
          });
          return { id: d.id };
        },
        "Merge reviewed records",
      );
      return;
    }
    if (action === "settings") {
      const s = state.boot.settings;
      modal(
        "Sales operating rules",
        field("timezone", "IANA timezone", "text", s.timezone, {
          required: true,
          wide: true,
        }) +
          ["reminder_minutes", "escalation_minutes", "unassigned_minutes"]
            .map((k) =>
              field(k, k.replaceAll("_", " "), "number", s[k], {
                min: 0,
                step: 1,
                hint: "Leave blank to disable.",
              }),
            )
            .join("") +
          [
            "qualification_guidance",
            "assignment_guidance",
            "commercial_guidance",
            "onboarding_guidance",
          ]
            .map((k) =>
              field(k, k.replaceAll("_", " "), "textarea", s[k], {
                wide: true,
              }),
            )
            .join(""),
        (fd) =>
          mutation(
            "settings",
            {
              version: s.version,
              timezone: fd.get("timezone"),
              ...Object.fromEntries(
                [
                  "reminder_minutes",
                  "escalation_minutes",
                  "unassigned_minutes",
                ].map((k) => [k, fd.get(k) === "" ? null : Number(fd.get(k))]),
              ),
              ...Object.fromEntries(
                [
                  "qualification_guidance",
                  "assignment_guidance",
                  "commercial_guidance",
                  "onboarding_guidance",
                ].map((k) => [k, fd.get(k)]),
              ),
            },
            "PATCH",
          ),
      );
      return;
    }
    if (action === "source") {
      const s =
        state.boot.sources.find((s) => s.id === Number(el.dataset.id)) || {};
      modal(
        s.id ? "Edit acquisition source" : "Add acquisition source",
        field("name", "Source name", "text", s.name, {
          required: true,
          wide: true,
        }) +
          field("kind", "Type", "select", s.kind || "manual", {
            items: [
              "manual",
              "website",
              "referral",
              "realtor",
              "research",
              "campaign",
            ],
          }) +
          field(
            "active",
            "New intake",
            "select",
            s.active === false ? "false" : "true",
            {
              items: [
                ["true", "Enabled"],
                ["false", "Retired"],
              ],
            },
          ),
        (fd) =>
          mutation("sources", {
            ...(s.id ? { id: s.id, version: s.version } : {}),
            name: fd.get("name"),
            kind: fd.get("kind"),
            active: fd.get("active") === "true",
          }),
      );
      return;
    }
    if (action === "import") {
      importData = null;
      modal(
        "Import owner opportunities",
        `<p class="sales-subtle">Up to 200 rows. Columns: title, owner_name, contact_name, email, phone, property_name, address, market, source_reference. One opportunity per row; link additional properties afterward.</p><label class="sales-field wide">CSV file<input type="file" id="sales-csv" accept=".csv,text/csv" required></label>` +
          field("source_id", "Original acquisition source", "select", "", {
            required: true,
            items: [["", "Choose source"], ...sources()],
          }) +
          `<div class="sales-field wide">${btn("preview-import", "Preview file")}<div id="sales-import-preview" class="sales-import-preview"></div><label><input type="checkbox" name="duplicates_reviewed" required> I reviewed the rows and possible duplicates.</label></div>`,
        async (fd) => {
          if (!importData) throw Error("Preview the file first.");
          const current = await $("#sales-csv").files[0]?.text();
          if (
            current !== importData.csv ||
            Number(fd.get("source_id")) !== importData.source_id
          )
            throw Error("The file or source changed. Preview it again.");
          return mutation("import/commit", {
            ...importData,
            duplicates_reviewed: fd.get("duplicates_reviewed") === "on",
          });
        },
        "Import reviewed rows",
      );
      return;
    }
    if (action === "preview-import") {
      const file = $("#sales-csv").files[0];
      if (!file) throw Error("Choose a CSV file.");
      if (file.size > 500000) throw Error("CSV must be under 500 KB.");
      const values = {
        csv: await file.text(),
        source_id: Number($("#sales-form").elements.source_id.value),
      };
      const r = await api("import/preview", {
        method: "POST",
        body: JSON.stringify(values),
      });
      $("#sales-import-preview").innerHTML =
        `<p>${esc(r.warning)}</p><ol>${r.rows.map((row) => `<li>${esc(row.title || "Untitled")}: ${row.valid ? "Valid" : "Invalid — " + esc(row.error)}${row.possible_duplicates?.candidates.length ? ` · ${row.possible_duplicates.candidates.length} possible matches: ${row.possible_duplicates.candidates.map((c) => esc(c.name) + " #" + c.id).join(", ")}` : ""}</li>`).join("")}</ol>`;
      importData = r.valid
        ? {
            ...values,
            preview_fingerprint: r.fingerprint,
            preview_token: r.preview_token,
          }
        : null;
      return;
    }
    if (action === "failures") {
      const r = await api("failures");
      root._failures = r.failures;
      modal(
        "Failed intake review",
        `<div class="sales-field wide">${r.failures.map((f, i) => `<article class="sales-property"><strong>Intake #${f.id} · ${esc(f.external_id)}</strong><p>${esc(f.error)}</p><details><summary>Original source material</summary><p class="sales-plain">${esc(f.original.original)}</p></details>${btn("retry-intake", "Review correction", `data-index="${i}"`)}</article>`).join("") || "<p>No failed intake records.</p>"}</div>`,
        () => true,
        "Done",
      );
      return;
    }
    if (action === "retry-intake") {
      const f = root._failures[Number(el.dataset.index)],
        lead = f.original.lead;
      closeModal();
      modal(
        "Correct failed intake #" + f.id,
        field("title", "Opportunity title", "text", lead.title, {
          required: true,
          wide: true,
        }) +
          field(
            "summary",
            "Context / corrected extraction",
            "textarea",
            lead.summary,
            { wide: true },
          ) +
          field("owner", "Owner name", "text", lead.account?.name) +
          field("contact", "Contact name", "text", lead.contacts?.[0]?.name) +
          field("email", "Email", "email", lead.contacts?.[0]?.email) +
          field("phone", "Phone", "text", lead.contacts?.[0]?.phone) +
          field(
            "property",
            "Property name",
            "text",
            lead.properties?.[0]?.name,
          ) +
          field(
            "address",
            "Property address",
            "text",
            lead.properties?.[0]?.address,
          ) +
          field(
            "source_reference",
            "Source evidence URL",
            "url",
            lead.source_reference,
            { wide: true },
          ) +
          '<p class="sales-subtle">This correction creates the initial owner/contact/property. Additional original contacts and properties remain in the intake evidence and can be linked after review.</p>',
        (fd) =>
          mutation(`failures/${f.id}/retry`, {
            lead: {
              title: fd.get("title"),
              summary: fd.get("summary"),
              source_reference: fd.get("source_reference"),
              account: fd.get("owner") ? { name: fd.get("owner") } : null,
              contacts: fd.get("contact")
                ? [
                    {
                      name: fd.get("contact"),
                      email: fd.get("email"),
                      phone: fd.get("phone"),
                    },
                  ]
                : [],
              properties: fd.get("property")
                ? [{ name: fd.get("property"), address: fd.get("address") }]
                : [],
            },
          }),
      );
      return;
    }
  }
  root.addEventListener("click", async (event) => {
    const deal = event.target.closest("[data-deal]"),
      view = event.target.closest("[data-view]"),
      action = event.target.closest("[data-action]");
    try {
      if (deal) {
        event.preventDefault();
        state.tab = "overview";
        await openDeal(Number(deal.dataset.deal));
      } else if (view) {
        state.view = view.dataset.view;
        state.page = 1;
        history.pushState({}, "", "/sales/");
        await render();
      } else if (action) {
        event.preventDefault();
        await handle(action.dataset.action, action);
      }
    } catch (e) {
      if ($("#sales-dialog").open) {
        $("#sales-form-error").textContent = e.message;
        $("#sales-form-error").hidden = false;
      } else note(e.message, true);
    }
  });
  root.addEventListener("change", (event) => {
    if (event.target.matches("[data-filter]")) {
      const k = event.target.dataset.filter;
      if (event.target.value) state.filters[k] = event.target.value;
      else delete state.filters[k];
      state.page = 1;
      state.boardPages = {};
      render();
    }
  });
  let searchTimer;
  root.addEventListener("input", (event) => {
    if (event.target.id === "sales-search") {
      clearTimeout(searchTimer);
      const value = event.target.value;
      searchTimer = setTimeout(() => {
        state.filters.search = value;
        state.page = 1;
        render();
      }, 450);
    }
  });
  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/^\/sales\/leads\/(\d+)/);
    if (match) openDeal(Number(match[1]), false);
    else {
      state.view = "leads";
      render();
    }
  });
  (async () => {
    try {
      await refreshBootstrap();
      if (root.dataset.initialDeal)
        await openDeal(Number(root.dataset.initialDeal), false);
      else await render();
    } catch (e) {
      note(e.message, true);
      $("#sales-content").innerHTML =
        '<p class="sales-empty">Sales CRM is unavailable. Ask an administrator to verify the database migration and access.</p>';
    }
  })();
})();
