const sampleTranscript = "I've been charged twice for the same order and the first amount still hasn't been refunded. I contacted support last week but nothing has changed. Please check the duplicate payment and tell me when the refund will arrive.";
const $ = function (selector) { return document.querySelector(selector); };

let metadata = null;
let selectedFile = null;
let originalTranscript = "";
let transcriptSource = "manual";
let currentCase = null;
let currentValidation = null;
let caseWasManuallyEdited = false;
let validationTimer = null;
let validationSequence = 0;
let toastTimer = null;
const toast = $("#toast");

async function requestJson(url, options) {
  const response = await fetch(url, options || { cache: "no-store" });
  const payload = await response.json().catch(function () { return {}; });
  if (!response.ok) throw new Error(payload.error || ("Request failed (" + response.status + ")."));
  return payload;
}

function postJson(url, payload) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

function showToast(message, isError) {
  toast.textContent = message;
  toast.classList.toggle("error", Boolean(isError));
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toast.classList.remove("visible"); }, 3000);
}

function showInlineError(elementId, message) {
  const element = $("#" + elementId);
  element.textContent = message;
  element.hidden = false;
}

function clearInlineError(elementId) {
  const element = $("#" + elementId);
  element.hidden = true;
  element.textContent = "";
}

async function initialize() {
  try {
    const values = await Promise.all([requestJson("/api/health"), requestJson("/api/meta")]);
    const health = values[0];
    metadata = values[1];
    $("#connection-badge").classList.toggle("connected", health.configured);
    $("#connection-text").textContent = health.configured ? "Gnani API ready" : "Add API key";
    if (!health.configured) $("#connection-badge").title = "Copy .env.example to .env, add your Gnani API key, then restart BoliDesk.";
    renderCategoryChoices();
    updateWorkflow();
  } catch (error) {
    $("#connection-text").textContent = "App unavailable";
    showToast(error.message, true);
  }
}

function renderCategoryChoices() {
  const select = $("#case-category");
  select.replaceChildren();
  Object.keys(metadata.categories).forEach(function (id) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = metadata.categories[id].label;
    select.appendChild(option);
  });
}

function setWorkflow(current, completed) {
  document.querySelectorAll(".workflow-step").forEach(function (step) {
    step.classList.toggle("current", step.dataset.step === current);
    step.classList.toggle("complete", (completed || []).indexOf(step.dataset.step) !== -1);
  });
}

function updateWorkflow() {
  if (!currentCase) {
    setWorkflow(selectedFile ? "audio" : "audio", []);
    return;
  }
  if (currentCase.review.status === "approved") {
    setWorkflow("export", ["audio", "transcript", "case", "review", "approval"]);
  } else if (currentValidation && currentValidation.can_approve) {
    setWorkflow("approval", ["audio", "transcript", "case", "review"]);
  } else if (currentValidation) {
    setWorkflow("review", ["audio", "transcript", "case"]);
  } else {
    setWorkflow("case", ["audio", "transcript"]);
  }
}

function setTranscript(text, source, preserveOriginal) {
  $("#transcript").value = text;
  $("#transcript-source").textContent = source;
  $("#copy-button").disabled = !text;
  $("#listen-button").disabled = !text;
  $("#draft-button").disabled = !text.trim();
  $("#transcript-count").textContent = text.length.toLocaleString() + " characters";
  if (!preserveOriginal) {
    originalTranscript = text;
    const details = $("#original-details");
    details.hidden = false;
    $("#original-transcript").textContent = text;
  }
  syncDraftButtonLabel();
}

function syncDraftButtonLabel() {
  const button = $("#draft-button");
  if (!currentCase) {
    button.innerHTML = "Generate case draft <span>→</span>";
    return;
  }
  const stale = $("#transcript").value !== currentCase.transcript.last_drafted_from;
  button.innerHTML = stale ? "Refresh case draft <span>→</span>" : "Generate case draft <span>→</span>";
}

function resetApproval() {
  if (!currentCase) return;
  currentCase.review.status = "draft";
  currentCase.review.approved_at = null;
  currentCase.review.approval_fingerprint = null;
}

function setCaseDirty(manualEdit) {
  if (!currentCase) return;
  resetApproval();
  if (manualEdit) caseWasManuallyEdited = true;
  renderStatus();
  updateWorkflow();
}

function renderStatus() {
  if (!currentCase) return;
  const status = currentCase.review.status || "draft";
  $("#review-status").textContent = status.toUpperCase();
  $("#review-status").classList.toggle("approved", status === "approved");
  $("#case-footer-note").textContent = status === "approved"
    ? "Approved. Any later edit requires approval again."
    : "Approval is required before JSON export.";
  $("#export-button").disabled = status !== "approved";
  if (status !== "approved") $("#approve-button").textContent = "Approve case →";
}

function markExistingQuestions(validation) {
  const review = currentCase.review;
  const oldQuestions = Array.isArray(review.follow_up_questions) ? review.follow_up_questions : [];
  const oldByField = new Map(oldQuestions.map(function (question) { return [question.field, question]; }));
  const missingQuestions = validation.suggested_questions || [];
  const missingFields = new Set(missingQuestions.map(function (question) { return question.field; }));
  const merged = missingQuestions.map(function (suggestion) {
    const previous = oldByField.get(suggestion.field);
    if (!previous) return suggestion;
    return Object.assign({}, suggestion, {
      text: previous.text || suggestion.text,
      status: previous.status === "dismissed" ? "dismissed" : "open"
    });
  });
  oldQuestions.forEach(function (question) {
    if (!missingFields.has(question.field)) {
      merged.push(Object.assign({}, question, {
        status: question.status === "dismissed" ? "dismissed" : "resolved"
      }));
    }
  });
  review.follow_up_questions = merged;
}

async function refreshValidation() {
  if (!currentCase) return;
  const caseAtRequest = currentCase;
  const caseSnapshot = JSON.stringify(caseAtRequest);
  const requestSequence = ++validationSequence;
  try {
    const result = await postJson("/api/validate", { case: caseAtRequest });
    if (requestSequence !== validationSequence || caseAtRequest !== currentCase || JSON.stringify(currentCase) !== caseSnapshot) return;
    currentValidation = result.validation;
    markExistingQuestions(currentValidation);
    renderReview();
    refreshRequiredMarks();
    renderStatus();
    updateWorkflow();
  } catch (error) {
    showInlineError("approval-error", error.message);
  }
}

function refreshRequiredMarks() {
  document.querySelectorAll(".case-field-control").forEach(function (control) {
    const wrapper = control.closest(".case-field");
    const label = wrapper && wrapper.querySelector("label");
    if (!label) return;
    const existing = label.querySelector(".required-mark");
    const missing = findMissingGroup(control.dataset.field);
    if (missing && !existing) {
      const mark = document.createElement("span");
      mark.className = "required-mark";
      mark.textContent = missing.fields.length > 1 ? " · one required" : " · required";
      label.appendChild(mark);
    } else if (!missing && existing) {
      existing.remove();
    }
  });
}

function scheduleValidation() {
  validationSequence += 1;
  clearTimeout(validationTimer);
  validationTimer = setTimeout(refreshValidation, 220);
}

function findMissingGroup(field) {
  if (!currentValidation) return null;
  return currentValidation.missing_fields.find(function (item) {
    return item.fields.indexOf(field) !== -1;
  }) || null;
}

function renderCaseFields() {
  if (!currentCase || !metadata) return;
  $("#case-empty").hidden = true;
  $("#case-editor").hidden = false;
  $("#review-panel").hidden = false;
  $("#case-category").value = currentCase.category;
  $("#category-confirmed").checked = Boolean(currentCase.category_confirmed);
  $("#routing-confirmed").checked = Boolean(currentCase.review.routing_confirmed);
  $("#action-confirmed").checked = Boolean(currentCase.review.action_confirmed);

  const fieldsContainer = $("#case-fields");
  fieldsContainer.replaceChildren();
  const config = metadata.categories[currentCase.category];
  config.visible_fields.forEach(function (fieldId) {
    const definition = metadata.fields[fieldId];
    const wrapper = document.createElement("div");
    wrapper.className = "case-field";

    const label = document.createElement("label");
    label.htmlFor = "field-" + fieldId;
    label.textContent = definition.label;
    const missing = findMissingGroup(fieldId);
    if (missing) {
      const mark = document.createElement("span");
      mark.className = "required-mark";
      mark.textContent = missing.fields.length > 1 ? " · one required" : " · required";
      label.appendChild(mark);
    }
    wrapper.appendChild(label);

    const control = document.createElement(definition.kind === "textarea" ? "textarea" : "input");
    control.id = "field-" + fieldId;
    control.className = "case-field-control";
    control.dataset.field = fieldId;
    control.value = currentCase.fields[fieldId] || "";
    control.placeholder = definition.placeholder || "";
    control.setAttribute("aria-label", definition.label);
    if (definition.kind === "textarea") control.rows = fieldId === "reported_problem" ? 4 : 2;
    wrapper.appendChild(control);

    const origin = document.createElement("small");
    origin.className = "case-field-source";
    origin.dataset.sourceFor = fieldId;
    origin.textContent = sourceLabel(currentCase.field_sources[fieldId]);
    wrapper.appendChild(origin);

    if (definition.hint) {
      const hint = document.createElement("small");
      hint.className = "case-field-hint";
      hint.textContent = definition.hint;
      wrapper.appendChild(hint);
    }
    fieldsContainer.appendChild(wrapper);
  });
  renderStatus();
}

function sourceLabel(source) {
  const labels = {
    transcript: "FROM TRANSCRIPT · CUSTOMER-REPORTED",
    local_rule: "LOCAL RULE · SUGGESTED",
    "human-edited": "AGENT-EDITED",
    missing: "NOT STATED · CONFIRM OR COMPLETE"
  };
  return labels[source] || "NOT STATED";
}

function renderReview() {
  if (!currentCase || !currentValidation) return;
  const missingFields = currentValidation.missing_fields || [];
  const reviewSteps = currentValidation.review_steps || [];
  const missingTotal = missingFields.length + reviewSteps.length;
  $("#required-count").textContent = missingTotal + (missingTotal === 1 ? " REQUIRED GAP" : " REQUIRED GAPS");
  $("#required-count").classList.toggle("review-count", missingTotal > 0);
  $("#required-count").classList.toggle("complete", missingTotal === 0);

  const summary = $("#validation-summary");
  summary.replaceChildren();
  summary.classList.toggle("ready", currentValidation.can_approve);
  const headline = document.createElement("strong");
  headline.textContent = currentValidation.can_approve
    ? "Required details and review steps are complete."
    : "Complete these items before approval.";
  summary.appendChild(headline);

  if (missingFields.length) {
    const list = document.createElement("ul");
    missingFields.forEach(function (item) {
      const li = document.createElement("li");
      li.textContent = item.label;
      list.appendChild(li);
    });
    summary.appendChild(list);
  }
  if (reviewSteps.length) {
    const list = document.createElement("ul");
    reviewSteps.forEach(function (item) {
      const li = document.createElement("li");
      li.textContent = item.label;
      list.appendChild(li);
    });
    summary.appendChild(list);
  }

  const optional = $("#optional-summary");
  if (currentValidation.optional_missing.length) {
    optional.hidden = false;
    optional.textContent = "Optional and not provided: " + currentValidation.optional_missing.map(function (field) { return field.label; }).join(", ") + ". These do not block approval.";
  } else {
    optional.hidden = true;
  }

  renderFlags();
  renderQuestions();
  $("#approve-button").disabled = !currentValidation.can_approve || currentCase.review.status === "approved";
}

function renderFlags() {
  const container = $("#review-flags");
  container.replaceChildren();
  const flags = currentCase.review.flags || [];
  if (!flags.length) {
    const note = document.createElement("p");
    note.className = "review-empty-note";
    note.textContent = "No uncertainty flags were raised by the implemented checks.";
    container.appendChild(note);
    return;
  }
  flags.forEach(function (flag) {
    const item = document.createElement("article");
    item.className = "review-item" + (flag.status !== "open" ? " resolved" : "");

    const head = document.createElement("div");
    head.className = "review-item-head";
    const title = document.createElement("strong");
    title.textContent = flag.title;
    const state = document.createElement("span");
    state.className = "flag-state";
    state.textContent = flag.status === "open" ? "Needs review" : flag.status;
    head.append(title, state);
    item.appendChild(head);

    const reason = document.createElement("p");
    reason.textContent = flag.reason;
    item.appendChild(reason);
    if (flag.evidence && flag.evidence.length) {
      const evidence = document.createElement("p");
      evidence.className = "review-evidence";
      evidence.textContent = "Transcript excerpt: “" + flag.evidence.join("” · “") + "”";
      item.appendChild(evidence);
    }
    const actions = document.createElement("div");
    actions.className = "review-actions";
    if (flag.status === "open") {
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.dataset.flagAction = "confirmed";
      confirmButton.dataset.flagId = flag.id;
      confirmButton.textContent = "Reviewed against source";
      const dismissButton = document.createElement("button");
      dismissButton.type = "button";
      dismissButton.className = "dismiss-action";
      dismissButton.dataset.flagAction = "dismissed";
      dismissButton.dataset.flagId = flag.id;
      dismissButton.textContent = "Dismiss cue";
      actions.append(confirmButton, dismissButton);
    } else {
      const reopenButton = document.createElement("button");
      reopenButton.type = "button";
      reopenButton.dataset.flagAction = "open";
      reopenButton.dataset.flagId = flag.id;
      reopenButton.textContent = "Reopen review";
      actions.appendChild(reopenButton);
    }
    item.appendChild(actions);
    container.appendChild(item);
  });
}

function renderQuestions() {
  const container = $("#followup-list");
  container.replaceChildren();
  const questions = currentCase.review.follow_up_questions || [];
  if (!questions.length) {
    const note = document.createElement("p");
    note.className = "review-empty-note";
    note.textContent = "No category-specific follow-up question is needed at this point.";
    container.appendChild(note);
    return;
  }
  questions.forEach(function (question) {
    const item = document.createElement("article");
    item.className = "review-item" + (question.status !== "open" ? " resolved" : "");
    const head = document.createElement("div");
    head.className = "review-item-head";
    const title = document.createElement("strong");
    title.textContent = question.status === "resolved" ? "Answered in the draft" : "For: " + question.field.replaceAll("_", " ");
    const state = document.createElement("span");
    state.className = "flag-state";
    state.textContent = question.status;
    head.append(title, state);
    item.appendChild(head);

    const editor = document.createElement("textarea");
    editor.className = "question-editor";
    editor.value = question.text;
    editor.setAttribute("aria-label", "Editable suggested follow-up question for " + question.field);
    editor.disabled = question.status !== "open";
    editor.dataset.questionField = question.field;
    item.appendChild(editor);

    if (question.status === "open") {
      const actions = document.createElement("div");
      actions.className = "review-actions";
      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.className = "dismiss-action";
      dismiss.dataset.dismissQuestion = question.field;
      dismiss.textContent = "Dismiss question";
      actions.appendChild(dismiss);
      item.appendChild(actions);
    } else if (question.status === "dismissed") {
      const actions = document.createElement("div");
      actions.className = "review-actions";
      const reopen = document.createElement("button");
      reopen.type = "button";
      reopen.dataset.reopenQuestion = question.field;
      reopen.textContent = "Reopen question";
      actions.appendChild(reopen);
      item.appendChild(actions);
    }
    container.appendChild(item);
  });
}

async function generateCaseDraft() {
  if (!metadata) return;
  const text = $("#transcript").value.trim();
  if (!text) return;
  const stale = currentCase && text !== currentCase.transcript.last_drafted_from;
  if (stale && caseWasManuallyEdited) {
    const accepted = window.confirm("Refreshing the case draft replaces edited case fields and review choices. Continue?");
    if (!accepted) return;
  }
  clearInlineError("approval-error");
  const button = $("#draft-button");
  button.disabled = true;
  button.textContent = "Preparing draft…";
  try {
    const result = await postJson("/api/analyze", {
      transcript: text,
      source: transcriptSource,
      original_transcript: originalTranscript || text
    });
    if ($("#transcript").value.trim() !== text) {
      showToast("The transcript changed while the draft was prepared. Refresh it again.", true);
      return;
    }
    currentCase = result.case;
    currentValidation = result.validation;
    caseWasManuallyEdited = false;
    $("#transcript-stale-note").hidden = true;
    $("#case-heading").textContent = "Case draft · review before approval";
    renderCaseFields();
    renderReview();
    renderStatus();
    updateWorkflow();
    showToast("Structured draft ready. Review the evidence and required details.");
  } catch (error) {
    showInlineError("approval-error", error.message);
  } finally {
    button.disabled = !$("#transcript").value.trim();
    syncDraftButtonLabel();
  }
}

function updateCaseValue(fieldId, value) {
  if (!currentCase) return;
  currentCase.fields[fieldId] = value;
  currentCase.field_sources[fieldId] = "human-edited";
  const source = document.querySelector('[data-source-for="' + fieldId + '"]');
  if (source) source.textContent = sourceLabel("human-edited");
  if (fieldId === "proposed_queue") currentCase.review.routing_confirmed = false;
  if (fieldId === "next_action") currentCase.review.action_confirmed = false;
  setCaseDirty(true);
  scheduleValidation();
}

function updateTranscriptState() {
  const text = $("#transcript").value;
  $("#transcript-count").textContent = text.length.toLocaleString() + " characters";
  $("#copy-button").disabled = !text.trim();
  $("#listen-button").disabled = !text.trim();
  $("#draft-button").disabled = !text.trim();
  if (currentCase) {
    currentCase.transcript.edited = text;
    setCaseDirty(false);
    $("#transcript-stale-note").hidden = text === currentCase.transcript.last_drafted_from;
    scheduleValidation();
  }
  syncDraftButtonLabel();
  updateWorkflow();
}

function setAudioFile(file) {
  if (!file) return;
  const accepted = /\.(wav|mp3|ogg|flac|aac|m4a)$/i.test(file.name);
  if (!accepted) {
    showInlineError("upload-error", "Choose a WAV, MP3, OGG, FLAC, AAC, or M4A audio file.");
    return;
  }
  if (file.size > 24 * 1024 * 1024) {
    showInlineError("upload-error", "Audio file is too large. Keep it under 24 MB.");
    return;
  }
  clearInlineError("upload-error");
  selectedFile = file;
  $("#file-title").textContent = file.name;
  const size = file.size < 1024 * 1024
    ? Math.max(1, Math.round(file.size / 1024)) + " KB"
    : (file.size / (1024 * 1024)).toFixed(1) + " MB";
  $("#file-subtitle").textContent = size + " · ready for server-side format and duration checks";
  $("#transcribe-button").disabled = false;
}

async function transcribeSelectedFile() {
  if (!selectedFile) return;
  if (currentCase && caseWasManuallyEdited && !window.confirm("Transcribing a new call replaces the current case draft. Continue?")) return;
  const button = $("#transcribe-button");
  const errorBox = $("#upload-error");
  clearInlineError("upload-error");
  button.disabled = true;
  button.querySelector(".button-label").textContent = "Transcribing…";
  try {
    const form = new FormData();
    form.append("audio_file", selectedFile);
    form.append("language_code", $("#language-select").value);
    const response = await fetch("/api/transcribe", { method: "POST", body: form });
    const result = await readJson(response);
    currentCase = null;
    currentValidation = null;
    caseWasManuallyEdited = false;
    transcriptSource = "prisma";
    originalTranscript = result.transcript;
    setTranscript(result.transcript, "PRISMA OUTPUT · COMPARE WITH AUDIO", false);
    $("#original-details").hidden = false;
    $("#original-transcript").textContent = result.transcript;
    $("#case-editor").hidden = true;
    $("#case-empty").hidden = false;
    $("#review-panel").hidden = true;
    $("#case-heading").textContent = "Ready for review";
    $("#transcript-stale-note").hidden = true;
    $("#audio-playback").hidden = true;
    updateWorkflow();
    showToast("Transcript received. Review and edit it, then generate a case draft.");
  } catch (error) {
    showInlineError("upload-error", error.message);
  } finally {
    button.querySelector(".button-label").textContent = "Transcribe call";
    button.disabled = !selectedFile;
  }
}

function markFlag(flagId, status) {
  const flag = currentCase.review.flags.find(function (item) { return item.id === flagId; });
  if (!flag) return;
  flag.status = status;
  if (flagId === "category_ambiguity") {
    currentCase.category_confirmed = status === "confirmed";
    $("#category-confirmed").checked = status === "confirmed";
  }
  setCaseDirty(true);
  refreshValidation();
}

function handleCaseCategoryChange() {
  if (!currentCase) return;
  const category = $("#case-category").value;
  if (category === currentCase.category) return;
  const config = metadata.categories[category];
  currentCase.category = category;
  currentCase.category_candidates = [category];
  currentCase.category_confirmed = false;
  currentCase.fields.proposed_queue = config.default_queue;
  currentCase.fields.next_action = config.default_action;
  currentCase.field_sources.proposed_queue = "local_rule";
  currentCase.field_sources.next_action = "local_rule";
  currentCase.review.routing_confirmed = false;
  currentCase.review.action_confirmed = false;
  const ambiguity = currentCase.review.flags.find(function (flag) { return flag.id === "category_ambiguity"; });
  if (ambiguity) {
    ambiguity.status = "open";
    ambiguity.title = "Confirm selected issue category";
    ambiguity.reason = "The category was selected by the agent. Confirm it against the customer-reported problem before approval.";
    ambiguity.evidence = currentCase.category_evidence[category] || [];
  }
  setCaseDirty(true);
  renderCaseFields();
  refreshValidation();
}

function handleReviewCheck(id) {
  if (!currentCase) return;
  const checked = $("#" + id).checked;
  if (id === "category-confirmed") {
    currentCase.category_confirmed = checked;
    const ambiguity = currentCase.review.flags.find(function (flag) { return flag.id === "category_ambiguity"; });
    if (ambiguity) ambiguity.status = checked ? "confirmed" : "open";
  } else if (id === "routing-confirmed") {
    currentCase.review.routing_confirmed = checked;
  } else if (id === "action-confirmed") {
    currentCase.review.action_confirmed = checked;
  }
  setCaseDirty(true);
  refreshValidation();
}

async function approveCurrentCase() {
  if (!currentCase || !currentValidation || !currentValidation.can_approve) return;
  currentCase.transcript.edited = $("#transcript").value;
  const caseSnapshot = JSON.stringify(currentCase);
  clearInlineError("approval-error");
  $("#approve-button").disabled = true;
  $("#approve-button").textContent = "Approving…";
  try {
    const result = await postJson("/api/approve", { case: currentCase });
    if (JSON.stringify(currentCase) !== caseSnapshot) {
      showToast("The draft changed during approval. Review the latest version and approve again.", true);
      return;
    }
    currentCase = result.case;
    currentValidation = result.validation;
    renderCaseFields();
    renderReview();
    renderStatus();
    updateWorkflow();
    showToast("Case approved. Export is now available.");
  } catch (error) {
    showInlineError("approval-error", error.message);
  } finally {
    $("#approve-button").textContent = "Approve case →";
    $("#approve-button").disabled = !currentValidation || !currentValidation.can_approve || currentCase.review.status === "approved";
  }
}

async function exportCurrentCase() {
  if (!currentCase || currentCase.review.status !== "approved") return;
  currentCase.transcript.edited = $("#transcript").value;
  const caseSnapshot = JSON.stringify(currentCase);
  try {
    const result = await postJson("/api/export", { case: currentCase });
    if (JSON.stringify(currentCase) !== caseSnapshot) {
      throw new Error("The case changed during export. Approve the latest version before exporting again.");
    }
    const blob = new Blob([JSON.stringify(result.export, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "bolidesk-case.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    setWorkflow("export", ["audio", "transcript", "case", "review", "approval"]);
    showToast("Approved case exported as JSON.");
  } catch (error) {
    showInlineError("approval-error", error.message);
    await refreshValidation();
  }
}

async function loadSyntheticExample() {
  transcriptSource = "synthetic";
  originalTranscript = sampleTranscript;
  setTranscript(sampleTranscript, "SYNTHETIC EXAMPLE · NOT A REAL CALL", false);
  $("#language-select").value = "en-IN";
  $("#original-details").hidden = false;
  $("#original-transcript").textContent = sampleTranscript;
  $("#audio-playback").hidden = true;
  currentCase = null;
  currentValidation = null;
  caseWasManuallyEdited = false;
  await generateCaseDraft();
  showToast("Synthetic example loaded. No Gnani API calls or credits used.");
}

async function readJson(response) {
  const payload = await response.json().catch(function () { return {}; });
  if (!response.ok) throw new Error(payload.error || ("Request failed (" + response.status + ")."));
  return payload;
}

async function copyTranscript() {
  try {
    await navigator.clipboard.writeText($("#transcript").value);
    showToast("Transcript copied.");
  } catch (error) {
    $("#transcript").select();
    document.execCommand("copy");
    showToast("Transcript copied.");
  }
}

async function speakTranscript() {
  const text = $("#transcript").value.trim();
  if (!text) return;
  if (text.length > 5000) {
    showToast("Timbre playback is limited to 5,000 characters.", true);
    return;
  }
  const button = $("#listen-button");
  button.disabled = true;
  try {
    const result = await postJson("/api/speak", { text: text, language: $("#language-select").value });
    const raw = atob(result.audio_base64);
    const bytes = Uint8Array.from(raw, function (character) { return character.charCodeAt(0); });
    const player = $("#audio-playback");
    if (player.dataset.objectUrl) URL.revokeObjectURL(player.dataset.objectUrl);
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: result.content_type }));
    player.src = objectUrl;
    player.dataset.objectUrl = objectUrl;
    player.hidden = false;
    await player.play();
    showToast("Playing with Timbre · " + result.voice);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = !$("#transcript").value.trim();
  }
}

$("#audio-file").addEventListener("change", function (event) { setAudioFile(event.target.files[0]); });
const dropZone = $("#drop-zone");
["dragenter", "dragover"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) {
    event.preventDefault();
    dropZone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) {
    event.preventDefault();
    dropZone.classList.remove("dragover");
  });
});
dropZone.addEventListener("drop", function (event) { setAudioFile(event.dataTransfer.files[0]); });
$("#transcribe-button").addEventListener("click", transcribeSelectedFile);
$("#draft-button").addEventListener("click", generateCaseDraft);
$("#transcript").addEventListener("input", updateTranscriptState);
$("#case-category").addEventListener("change", handleCaseCategoryChange);
$("#category-confirmed").addEventListener("change", function () { handleReviewCheck("category-confirmed"); });
$("#routing-confirmed").addEventListener("change", function () { handleReviewCheck("routing-confirmed"); });
$("#action-confirmed").addEventListener("change", function () { handleReviewCheck("action-confirmed"); });
$("#case-fields").addEventListener("input", function (event) {
  if (!event.target.matches(".case-field-control")) return;
  updateCaseValue(event.target.dataset.field, event.target.value);
});
$("#review-flags").addEventListener("click", function (event) {
  const button = event.target.closest("[data-flag-action]");
  if (button) markFlag(button.dataset.flagId, button.dataset.flagAction);
});
$("#followup-list").addEventListener("input", function (event) {
  const field = event.target.dataset.questionField;
  if (!field || !currentCase) return;
  const question = currentCase.review.follow_up_questions.find(function (item) { return item.field === field; });
  if (question) {
    question.text = event.target.value;
    setCaseDirty(true);
  }
});
$("#followup-list").addEventListener("click", function (event) {
  const dismiss = event.target.closest("[data-dismiss-question]");
  const reopen = event.target.closest("[data-reopen-question]");
  const target = dismiss || reopen;
  if (!target || !currentCase) return;
  const field = target.dataset.dismissQuestion || target.dataset.reopenQuestion;
  const question = currentCase.review.follow_up_questions.find(function (item) { return item.field === field; });
  if (!question) return;
  question.status = dismiss ? "dismissed" : "open";
  setCaseDirty(true);
  renderQuestions();
  scheduleValidation();
});
$("#demo-button").addEventListener("click", loadSyntheticExample);
$("#copy-button").addEventListener("click", copyTranscript);
$("#listen-button").addEventListener("click", speakTranscript);
$("#approve-button").addEventListener("click", approveCurrentCase);
$("#export-button").addEventListener("click", exportCurrentCase);
window.addEventListener("beforeunload", function () {
  const player = $("#audio-playback");
  if (player.dataset.objectUrl) URL.revokeObjectURL(player.dataset.objectUrl);
});

initialize();
