const resolutionState = {
    stages: [],
    stageDefinitions: [],
    operators: [],
    lanes: [],
    rules: [],
    lookback: {},
    search: '',
    portfolio: '',
    draggedTicketId: null,
    activeTicketId: null,
    activeCard: null,
    activeDetail: null,
    detailFormSnapshot: '',
    detailReturnFocus: null,
};

let resolutionToastTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('resolutionSearch')?.addEventListener('input', (event) => {
        resolutionState.search = event.target.value.trim().toLowerCase();
        renderResolutionBoard();
    });
    document.getElementById('resolutionPortfolioFilter')?.addEventListener('change', (event) => {
        resolutionState.portfolio = event.target.value;
        renderResolutionBoard();
    });
    const board = document.getElementById('resolutionBoard');
    board?.addEventListener('click', handleResolutionCardClick);
    board?.addEventListener('change', handleStageSelect);
    board?.addEventListener('dragstart', handleDragStart);
    board?.addEventListener('dragend', handleDragEnd);
    board?.addEventListener('dragover', handleDragOver);
    board?.addEventListener('dragleave', handleDragLeave);
    board?.addEventListener('drop', handleDrop);
    const detail = document.getElementById('resolutionDetail');
    detail?.addEventListener('click', handleResolutionDetailClick);
    detail?.addEventListener('submit', handleResolutionDetailSubmit);
    detail?.addEventListener('input', updateResolutionDetailDirtyState);
    detail?.addEventListener('change', updateResolutionDetailDirtyState);
    document.addEventListener('keydown', handleResolutionDetailKeydown);
    document.getElementById('resolutionRuleList')?.addEventListener('change', handleRuleChange);
    loadResolutions();
});

async function resolutionFetch(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

async function loadResolutions() {
    const board = document.getElementById('resolutionBoard');
    if (!board) return;
    board.innerHTML = '<div class="review-ops-loading"><span class="review-ops-spinner" aria-hidden="true"></span><span>Loading resolution tickets…</span></div>';
    try {
        const data = await resolutionFetch('/reviews/api/resolutions');
        resolutionState.stages = data.stages || [];
        resolutionState.stageDefinitions = data.stage_definitions || [];
        resolutionState.operators = data.operators || [];
        resolutionState.lanes = data.lanes || [];
        resolutionState.rules = data.rules || [];
        resolutionState.lookback = data.lookback || {};
        document.getElementById('resolutionTotalCount').textContent = data.summary?.total ?? 0;
        document.getElementById('resolutionOpenCount').textContent = data.summary?.open ?? 0;
        document.getElementById('resolutionResolvedCount').textContent = data.summary?.resolved ?? 0;
        renderResolutionRules();
        renderResolutionPortfolioFilter();
        renderResolutionLookback();
        renderResolutionBoard();
    } catch (error) {
        board.innerHTML = `<div class="review-ops-error">${resolutionEscape(error.message)}</div>`;
    }
}

function renderResolutionRules() {
    const container = document.getElementById('resolutionRuleList');
    if (!container) return;
    container.innerHTML = resolutionState.rules.map((rule) => `
        <label class="review-resolution-rule">
            <span class="review-resolution-rule-copy">
                <strong>${resolutionEscape(rule.display_name || rule.portfolio)}</strong>
                <small>${rule.qualifying_count || 0} of ${rule.review_count || 0} six-month reviews qualify</small>
            </span>
            <span class="review-resolution-rule-control">
                <span>Below</span>
                <input
                    type="number"
                    min="1"
                    max="5"
                    step="0.1"
                    value="${Number(rule.bad_review_threshold || 5).toFixed(1)}"
                    data-action="change-rule"
                    data-portfolio="${resolutionEscape(rule.portfolio)}"
                    aria-label="Bad review threshold for ${resolutionEscape(rule.display_name || rule.portfolio)}"
                >
                <span>stars</span>
            </span>
        </label>
    `).join('');
}

function renderResolutionPortfolioFilter() {
    const select = document.getElementById('resolutionPortfolioFilter');
    if (!select) return;

    const counts = new Map();
    resolutionState.lanes.forEach((lane) => {
        (lane.reviews || []).forEach((review) => {
            const portfolio = review.portfolio || 'Unmapped';
            counts.set(portfolio, (counts.get(portfolio) || 0) + 1);
        });
    });
    const displayNames = new Map(
        resolutionState.rules.map((rule) => [rule.portfolio, rule.display_name || rule.portfolio])
    );
    const portfolios = [...counts.entries()].sort(([left], [right]) => {
        if (left === 'Unmapped') return 1;
        if (right === 'Unmapped') return -1;
        return String(displayNames.get(left) || left).localeCompare(String(displayNames.get(right) || right));
    });
    const total = portfolios.reduce((sum, [, count]) => sum + count, 0);

    if (resolutionState.portfolio && !counts.has(resolutionState.portfolio)) {
        resolutionState.portfolio = '';
    }
    select.innerHTML = [
        `<option value="">All portfolios (${total})</option>`,
        ...portfolios.map(([portfolio, count]) => (
            `<option value="${resolutionEscape(portfolio)}">${resolutionEscape(displayNames.get(portfolio) || portfolio)} (${count})</option>`
        )),
    ].join('');
    select.value = resolutionState.portfolio;
}

function renderResolutionLookback() {
    const element = document.getElementById('resolutionLookback');
    if (!element || !resolutionState.lookback.start_date || !resolutionState.lookback.end_date) return;
    element.textContent = `${resolutionFormatDate(resolutionState.lookback.start_date)}–${resolutionFormatDate(resolutionState.lookback.end_date)}`;
}

async function handleRuleChange(event) {
    const input = event.target.closest('[data-action="change-rule"]');
    if (!input) return;
    const threshold = Number(input.value);
    if (!Number.isFinite(threshold) || threshold < 1 || threshold > 5) {
        showResolutionToast('Choose a threshold between 1.0 and 5.0 stars.', true);
        await loadResolutions();
        return;
    }

    input.disabled = true;
    try {
        await resolutionFetch('/reviews/api/resolution-rules', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                portfolio: input.dataset.portfolio,
                bad_review_threshold: threshold,
            }),
        });
        showResolutionToast(`${input.dataset.portfolio} now treats reviews below ${threshold.toFixed(1)} stars as bad.`);
        await loadResolutions();
    } catch (error) {
        input.disabled = false;
        showResolutionToast(error.message, true);
    }
}

function renderResolutionBoard() {
    const board = document.getElementById('resolutionBoard');
    if (!board) return;
    board.style.setProperty('--resolution-stage-count', Math.max(resolutionState.stages.length, 1));
    board.innerHTML = resolutionState.stages.map((stage) => {
        const stageDefinition = resolutionState.stageDefinitions.find((definition) => definition.stage === stage) || {};
        const sourceLane = resolutionState.lanes.find((lane) => lane.stage === stage);
        const reviews = (sourceLane?.reviews || []).filter((review) => {
            if (resolutionState.portfolio && review.portfolio !== resolutionState.portfolio) return false;
            if (!resolutionState.search) return true;
            const haystack = `${review.guest_name || ''} ${review.listing_name || ''} ${review.portfolio || ''} ${review.title || ''}`.toLowerCase();
            return haystack.includes(resolutionState.search);
        });
        const emptyMessage = resolutionState.portfolio || resolutionState.search
            ? 'No matching reviews in this stage'
            : 'No reviews in this stage';
        return `
            <section class="review-resolution-lane" data-stage="${resolutionEscape(stage)}">
                <header class="review-resolution-lane-head">
                    <div class="review-resolution-lane-title">
                        ${stageDefinition.step ? `<span class="review-resolution-step" aria-label="Step ${resolutionEscape(stageDefinition.step)}">${resolutionEscape(stageDefinition.step)}</span>` : ''}
                        <div>
                            <h3>${resolutionEscape(stage)}</h3>
                            ${stageDefinition.description ? `<p>${resolutionEscape(stageDefinition.description)}</p>` : ''}
                        </div>
                    </div>
                    <span class="review-resolution-lane-count" aria-label="${reviews.length} reviews">${reviews.length}</span>
                </header>
                <div class="review-resolution-lane-list">
                    ${reviews.length ? reviews.map(createResolutionCard).join('') : `<div class="review-resolution-empty">${emptyMessage}</div>`}
                </div>
            </section>
        `;
    }).join('');
}

function createResolutionCard(review) {
    const stageOptions = resolutionState.stages.map((stage) => (
        `<option value="${resolutionEscape(stage)}" ${stage === review.stage ? 'selected' : ''}>${resolutionEscape(stage)}</option>`
    )).join('');
    const rating = review.rating == null ? 'N/A' : `${Number(review.rating).toFixed(1)} ★`;
    const ratingTitle = review.rating_raw == null
        ? 'Rating unavailable'
        : `Normalized from ${Number(review.rating_raw).toFixed(1)} / ${Number(review.rating_source_max || 10).toFixed(0)}`;
    const noteLabel = `${Number(review.note_count || 0)} ${Number(review.note_count || 0) === 1 ? 'note' : 'notes'}`;
    return `
        <article class="review-resolution-card" data-ticket-id="${review.ticket_id}">
            <button
                class="review-resolution-card-open"
                type="button"
                data-action="open-resolution"
                data-ticket-id="${review.ticket_id}"
                aria-label="Open resolution case for ${resolutionEscape(review.guest_name || 'guest')}"
            ></button>
            <div class="review-resolution-card-top">
                <span class="review-rating-pill" title="${resolutionEscape(ratingTitle)}">${resolutionEscape(rating)}</span>
                <span class="review-resolution-card-actions">
                    <span class="review-priority-pill">${resolutionEscape(review.priority || 'Medium')}</span>
                    <button class="review-resolution-drag-handle" type="button" draggable="true" aria-label="Drag ${resolutionEscape(review.guest_name || 'guest')} to another stage" title="Drag to another stage">
                        <span aria-hidden="true">⋮⋮</span>
                    </button>
                </span>
            </div>
            <h4>${resolutionEscape(review.guest_name || 'Guest')}</h4>
            <p class="review-resolution-meta">${resolutionEscape(review.listing_name || 'Unknown property')} · ${resolutionEscape(review.portfolio || 'Unmapped')}</p>
            <p class="review-resolution-standard">Below ${Number(review.bad_review_threshold || 5).toFixed(1)}-star standard${review.review_date ? ` · ${resolutionFormatDate(review.review_date)}` : ''}</p>
            <p class="review-resolution-excerpt">${resolutionEscape(review.review_text || 'No written review was supplied.')}</p>
            <select data-action="change-stage" data-ticket-id="${review.ticket_id}" aria-label="Resolution stage for ${resolutionEscape(review.guest_name || 'guest')}">
                ${stageOptions}
            </select>
            <div class="review-resolution-card-foot">
                <span>${resolutionEscape(noteLabel)}${review.assigned_user_name ? ` · ${resolutionEscape(review.assigned_user_name)}` : ''}</span>
                <span class="review-resolution-open-label">Open <span aria-hidden="true">→</span></span>
            </div>
        </article>
    `;
}

function handleResolutionCardClick(event) {
    const trigger = event.target.closest('[data-action="open-resolution"]');
    if (!trigger) return;
    openResolutionDetail(Number(trigger.dataset.ticketId), trigger);
}

async function handleStageSelect(event) {
    const select = event.target.closest('[data-action="change-stage"]');
    if (!select) return;
    await moveResolution(Number(select.dataset.ticketId), select.value);
}

function handleDragStart(event) {
    const handle = event.target.closest('.review-resolution-drag-handle');
    const card = handle?.closest('.review-resolution-card');
    if (!card) return;
    resolutionState.draggedTicketId = Number(card.dataset.ticketId);
    card.classList.add('is-dragging');
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}

function handleDragEnd(event) {
    event.target.closest('.review-resolution-card')?.classList.remove('is-dragging');
    document.querySelectorAll('.review-resolution-lane.is-drag-over').forEach((lane) => lane.classList.remove('is-drag-over'));
    resolutionState.draggedTicketId = null;
}

function handleDragOver(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (!lane || !resolutionState.draggedTicketId) return;
    event.preventDefault();
    lane.classList.add('is-drag-over');
}

function handleDragLeave(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (lane && !lane.contains(event.relatedTarget)) lane.classList.remove('is-drag-over');
}

async function handleDrop(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (!lane || !resolutionState.draggedTicketId) return;
    event.preventDefault();
    lane.classList.remove('is-drag-over');
    await moveResolution(resolutionState.draggedTicketId, lane.dataset.stage);
}

async function moveResolution(ticketId, stage) {
    const previous = resolutionState.lanes.map((lane) => ({ ...lane, reviews: [...lane.reviews] }));
    const card = previous.flatMap((lane) => lane.reviews).find((review) => Number(review.ticket_id) === Number(ticketId));
    if (!card || card.stage === stage) return;

    resolutionState.lanes.forEach((lane) => {
        lane.reviews = lane.reviews.filter((review) => Number(review.ticket_id) !== Number(ticketId));
    });
    card.stage = stage;
    const target = resolutionState.lanes.find((lane) => lane.stage === stage);
    if (target) target.reviews.unshift(card);
    renderResolutionBoard();

    try {
        await resolutionFetch(`/reviews/api/resolutions/${ticketId}/stage`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage }),
        });
        showResolutionToast(`Moved to ${stage}.`);
        await loadResolutions();
    } catch (error) {
        resolutionState.lanes = previous;
        renderResolutionBoard();
        showResolutionToast(error.message, true);
    }
}

function resolutionCardForTicket(ticketId) {
    return resolutionState.lanes
        .flatMap((lane) => lane.reviews || [])
        .find((review) => Number(review.ticket_id) === Number(ticketId));
}

async function openResolutionDetail(ticketId, trigger) {
    const modal = document.getElementById('resolutionDetail');
    const body = document.getElementById('resolutionDetailBody');
    const card = resolutionCardForTicket(ticketId);
    if (!modal || !body || !card) return;

    resolutionState.activeTicketId = ticketId;
    resolutionState.activeCard = card;
    resolutionState.activeDetail = null;
    resolutionState.detailFormSnapshot = '';
    resolutionState.detailReturnFocus = trigger || document.activeElement;

    document.getElementById('resolutionDetailTitle').textContent = card.guest_name || 'Guest review';
    document.getElementById('resolutionDetailSubtitle').textContent = `${card.listing_name || 'Unknown property'} · ${card.portfolio || 'Unmapped'}`;
    document.getElementById('resolutionDetailSaveStatus').textContent = '';
    body.innerHTML = '<div class="review-ops-loading"><span class="review-ops-spinner" aria-hidden="true"></span><span>Loading case details…</span></div>';
    modal.hidden = false;
    document.body.classList.add('review-modal-open');
    modal.querySelector('.review-action-close')?.focus();

    try {
        const detail = await resolutionFetch(`/reviews/api/resolutions/${ticketId}`);
        if (resolutionState.activeTicketId !== ticketId) return;
        resolutionState.activeDetail = detail;
        renderResolutionDetail();
        document.getElementById('resolutionCaseTitle')?.focus();
    } catch (error) {
        body.innerHTML = `<div class="review-ops-error">${resolutionEscape(error.message)}</div>`;
    }
}

function renderResolutionDetail() {
    const body = document.getElementById('resolutionDetailBody');
    const detail = resolutionState.activeDetail;
    const card = resolutionState.activeCard;
    if (!body || !detail || !card) return;

    const stageOptions = resolutionState.stages.map((stage) => (
        `<option value="${resolutionEscape(stage)}" ${stage === detail.stage ? 'selected' : ''}>${resolutionEscape(stage)}</option>`
    )).join('');
    const priorityOptions = ['Low', 'Medium', 'High', 'Critical'].map((priority) => (
        `<option value="${priority}" ${priority === detail.priority ? 'selected' : ''}>${priority}</option>`
    )).join('');
    const operatorOptions = resolutionState.operators.map((operator) => (
        `<option value="${operator.user_id}" ${Number(operator.user_id) === Number(detail.assigned_user_id) ? 'selected' : ''}>${resolutionEscape(operator.name || operator.email)}</option>`
    )).join('');
    const normalizedRating = card.rating == null ? 'Rating unavailable' : `${Number(card.rating).toFixed(1)} out of 5 stars`;
    const rawRating = card.rating_raw == null ? '' : ` · Source score ${Number(card.rating_raw).toFixed(1)} / ${Number(card.rating_source_max || 10).toFixed(0)}`;

    body.innerHTML = `
        <section class="review-resolution-detail__section review-resolution-review-panel" aria-labelledby="resolutionGuestReviewHeading">
            <div class="review-resolution-detail__section-head">
                <div>
                    <span class="review-ops-eyebrow">Guest review</span>
                    <h3 id="resolutionGuestReviewHeading">${resolutionEscape(normalizedRating)}</h3>
                </div>
                ${card.review_date ? `<time datetime="${resolutionEscape(card.review_date)}">${resolutionFormatDate(card.review_date)}</time>` : ''}
            </div>
            <p class="review-resolution-detail__rating-source">${resolutionEscape(`Normalized rating${rawRating}`)}</p>
            <p class="review-resolution-detail__review-text">${resolutionEscape(card.review_text || 'No written review was supplied.')}</p>
        </section>

        <form id="resolutionDetailForm" class="review-resolution-detail__section" novalidate>
            <div class="review-resolution-detail__section-head">
                <div><span class="review-ops-eyebrow">Case details</span><h3>Resolution plan</h3></div>
                <span class="review-resolution-case-id">Case #${detail.ticket_id}</span>
            </div>
            <label class="review-resolution-field review-resolution-field--full">
                <span>Case title</span>
                <input id="resolutionCaseTitle" name="title" type="text" maxlength="240" value="${resolutionEscape(detail.title || '')}" required>
            </label>
            <div class="review-resolution-detail__form-grid">
                <label class="review-resolution-field">
                    <span>Stage</span>
                    <select name="stage">${stageOptions}</select>
                </label>
                <label class="review-resolution-field">
                    <span>Priority</span>
                    <select name="priority">${priorityOptions}</select>
                </label>
                <label class="review-resolution-field">
                    <span>Owner</span>
                    <select name="assigned_user_id">
                        <option value="">Unassigned</option>
                        ${operatorOptions}
                    </select>
                </label>
                <label class="review-resolution-field">
                    <span>Follow-up date</span>
                    <input name="due_date" type="date" value="${resolutionEscape(detail.due_date || '')}">
                </label>
            </div>
        </form>

        <section class="review-resolution-detail__section" aria-labelledby="resolutionNotesHeading">
            <div class="review-resolution-detail__section-head">
                <div><span class="review-ops-eyebrow">Notes &amp; activity</span><h3 id="resolutionNotesHeading">Progress history</h3></div>
                <span id="resolutionNoteCount" class="review-resolution-case-id">${(detail.notes || []).length} ${(detail.notes || []).length === 1 ? 'note' : 'notes'}</span>
            </div>
            <div id="resolutionNoteList" class="review-resolution-note-list"></div>
            <form id="resolutionNoteForm" class="review-resolution-note-composer">
                <label for="resolutionNoteText">Add an operator note</label>
                <textarea id="resolutionNoteText" name="note_text" rows="4" maxlength="5000" placeholder="Log calls, text outreach, guest responses, or next steps…"></textarea>
                <div>
                    <small>Notes are timestamped and stay in the case history.</small>
                    <button class="review-action-secondary" type="submit">Add note</button>
                </div>
            </form>
        </section>
    `;
    renderResolutionNotes();
    resolutionState.detailFormSnapshot = resolutionDetailFormSnapshot();
    updateResolutionDetailDirtyState();
}

function renderResolutionNotes() {
    const container = document.getElementById('resolutionNoteList');
    const notes = resolutionState.activeDetail?.notes || [];
    if (!container) return;
    if (!notes.length) {
        container.innerHTML = '<div class="review-resolution-note-empty"><strong>No notes yet</strong><span>Add the first update to start the case history.</span></div>';
        return;
    }
    container.innerHTML = [...notes].reverse().map((note) => {
        const author = note.user_name || note.user_email || 'Operator';
        const initial = author.trim().charAt(0).toUpperCase() || 'O';
        return `
            <article class="review-resolution-note">
                <span class="review-resolution-note__avatar" aria-hidden="true">${resolutionEscape(initial)}</span>
                <div>
                    <header><strong>${resolutionEscape(author)}</strong><time datetime="${resolutionEscape(note.created_at || '')}">${resolutionFormatDateTime(note.created_at)}</time></header>
                    <p>${resolutionEscape(note.comment_text || '')}</p>
                </div>
            </article>
        `;
    }).join('');
}

function resolutionDetailFormValues() {
    const form = document.getElementById('resolutionDetailForm');
    if (!form) return null;
    const values = Object.fromEntries(new FormData(form).entries());
    return {
        title: String(values.title || '').trim(),
        stage: values.stage || '',
        priority: values.priority || '',
        assigned_user_id: values.assigned_user_id || null,
        due_date: values.due_date || null,
    };
}

function resolutionDetailFormSnapshot() {
    return JSON.stringify(resolutionDetailFormValues() || {});
}

function resolutionDetailHasUnsavedWork() {
    const formChanged = Boolean(resolutionState.detailFormSnapshot)
        && resolutionDetailFormSnapshot() !== resolutionState.detailFormSnapshot;
    const noteDraft = Boolean(document.getElementById('resolutionNoteText')?.value.trim());
    return formChanged || noteDraft;
}

function updateResolutionDetailDirtyState() {
    const saveButton = document.getElementById('resolutionSaveButton');
    const status = document.getElementById('resolutionDetailSaveStatus');
    if (!saveButton || document.getElementById('resolutionDetail')?.hidden) return;
    const changed = Boolean(resolutionState.detailFormSnapshot)
        && resolutionDetailFormSnapshot() !== resolutionState.detailFormSnapshot;
    saveButton.disabled = !changed;
    if (status) status.textContent = changed ? 'Unsaved changes' : 'All changes saved';
}

async function handleResolutionDetailSubmit(event) {
    if (event.target.id === 'resolutionDetailForm') {
        event.preventDefault();
        await saveResolutionDetail();
    } else if (event.target.id === 'resolutionNoteForm') {
        event.preventDefault();
        await addResolutionNote(event.target);
    }
}

async function saveResolutionDetail() {
    const values = resolutionDetailFormValues();
    const button = document.getElementById('resolutionSaveButton');
    const status = document.getElementById('resolutionDetailSaveStatus');
    if (!values || !resolutionState.activeTicketId || !button) return;
    if (!values.title) {
        showResolutionToast('Case title is required.', true);
        document.getElementById('resolutionCaseTitle')?.focus();
        return;
    }

    button.disabled = true;
    if (status) status.textContent = 'Saving…';
    try {
        const detail = await resolutionFetch(`/reviews/api/resolutions/${resolutionState.activeTicketId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(values),
        });
        resolutionState.activeDetail = detail;
        const card = resolutionCardForTicket(detail.ticket_id);
        if (card) {
            card.title = detail.title;
            card.stage = detail.stage;
            card.priority = detail.priority;
            card.assigned_user_name = detail.assigned_user_name;
            card.updated_at = detail.updated_at;
            resolutionState.lanes.forEach((lane) => {
                lane.reviews = lane.reviews.filter((review) => Number(review.ticket_id) !== Number(detail.ticket_id));
            });
            resolutionState.lanes.find((lane) => lane.stage === detail.stage)?.reviews.unshift(card);
            resolutionState.activeCard = card;
        }
        resolutionState.detailFormSnapshot = resolutionDetailFormSnapshot();
        renderResolutionBoard();
        updateResolutionDetailDirtyState();
        showResolutionToast('Resolution case saved.');
    } catch (error) {
        button.disabled = false;
        if (status) status.textContent = 'Save failed';
        showResolutionToast(error.message, true);
    }
}

async function addResolutionNote(form) {
    const textarea = form.querySelector('textarea[name="note_text"]');
    const button = form.querySelector('button[type="submit"]');
    const noteText = textarea?.value.trim();
    if (!noteText || !resolutionState.activeTicketId) {
        showResolutionToast('Write a note before posting.', true);
        textarea?.focus();
        return;
    }

    button.disabled = true;
    try {
        const note = await resolutionFetch(`/reviews/api/resolutions/${resolutionState.activeTicketId}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ note_text: noteText }),
        });
        resolutionState.activeDetail.notes = [...(resolutionState.activeDetail.notes || []), note];
        textarea.value = '';
        const card = resolutionCardForTicket(resolutionState.activeTicketId);
        if (card) {
            card.note_count = Number(card.note_count || 0) + 1;
            card.updated_at = note.created_at;
        }
        renderResolutionNotes();
        document.getElementById('resolutionNoteCount').textContent = `${resolutionState.activeDetail.notes.length} ${resolutionState.activeDetail.notes.length === 1 ? 'note' : 'notes'}`;
        renderResolutionBoard();
        updateResolutionDetailDirtyState();
        showResolutionToast('Note added to the case history.');
    } catch (error) {
        showResolutionToast(error.message, true);
    } finally {
        button.disabled = false;
    }
}

function handleResolutionDetailClick(event) {
    if (event.target.closest('[data-action="close-resolution"]')) closeResolutionDetail();
}

function closeResolutionDetail(force = false) {
    const modal = document.getElementById('resolutionDetail');
    if (!modal || modal.hidden) return;
    if (!force && resolutionDetailHasUnsavedWork() && !window.confirm('Discard your unsaved case changes or note draft?')) return;

    const ticketId = resolutionState.activeTicketId;
    modal.hidden = true;
    document.body.classList.remove('review-modal-open');
    resolutionState.activeTicketId = null;
    resolutionState.activeCard = null;
    resolutionState.activeDetail = null;
    resolutionState.detailFormSnapshot = '';
    const returnFocus = document.querySelector(`[data-action="open-resolution"][data-ticket-id="${ticketId}"]`)
        || resolutionState.detailReturnFocus;
    resolutionState.detailReturnFocus = null;
    returnFocus?.focus();
}

function handleResolutionDetailKeydown(event) {
    const modal = document.getElementById('resolutionDetail');
    if (!modal || modal.hidden) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        closeResolutionDetail();
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
        .filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
}

function showResolutionToast(message, isError = false) {
    const toast = document.getElementById('reviewToast');
    if (!toast) return;
    window.clearTimeout(resolutionToastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.classList.add('is-visible');
    resolutionToastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 4200);
}

function resolutionEscape(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function resolutionFormatDate(value) {
    if (!value) return '';
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    return Number.isNaN(parsed.getTime())
        ? String(value)
        : parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function resolutionFormatDateTime(value) {
    if (!value) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
        ? String(value)
        : parsed.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
}
