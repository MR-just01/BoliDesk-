const sampleTranscript = "I've been charged twice for the same order and the first amount still hasn't been refunded. I contacted support last week but nothing has changed. Please check the duplicate payment and tell me when the refund will arrive.";
const $ = (selector) => document.querySelector(selector);
const fileInput = $("#audio-file");
const dropZone = $("#drop-zone");
const transcribeButton = $("#transcribe-button");
const transcriptField = $("#transcript");
const toast = $("#toast");
let selectedFile = null;
let latestAnalysis = null;
let latestModel = "Local keyword rules";
let toastTimer = null;

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2700);
}

function showError(message) {
  const box = $("#upload-error");
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  $("#upload-error").hidden = true;
  $("#upload-error").textContent = "";
}

async function loadConnection() {
  const badge = $("#connection-badge");
  const label = $("#connection-text");
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const status = await readJson(response);
    badge.classList.toggle("connected", status.configured);
    label.textContent = status.configured ? "Gnani API ready" : "Add API key";
    if (!status.configured) badge.title = "Copy .env.example to .env, add your Gnani API key, then restart BoliDesk.";
  } catch {
    label.textContent = "API unavailable";
  }
}

function setFile(file) {
  if (!file) return;
  const allowed = /\.(wav|mp3|ogg|flac|aac|m4a)$/i.test(file.name);
  if (!allowed) {
    showError("Choose a WAV, MP3, OGG, FLAC, AAC, or M4A audio file.");
    return;
  }
  if (file.size > 24 * 1024 * 1024) {
    showError("This file is larger than 24 MB. Choose a smaller audio clip.");
    return;
  }
  clearError();
  selectedFile = file;
  $("#file-title").textContent = file.name;
  const size = file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  $("#file-subtitle").textContent = `${size} · ready to transcribe`;
  transcribeButton.disabled = false;
}

fileInput.addEventListener("change", (event) => setFile(event.target.files[0]));
for (const name of ["dragenter", "dragover"]) dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.add("dragover"); });
for (const name of ["dragleave", "drop"]) dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.remove("dragover"); });
dropZone.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0]));

function setTranscript(text, source) {
  transcriptField.value = text;
  $("#transcript-source").textContent = source;
  $("#copy-button").disabled = !text;
  $("#listen-button").disabled = !text;
  $("#analyze-button").disabled = !text;
  $("#transcript-count").textContent = `${text.length.toLocaleString()} characters`;
}

async function analyzeCurrentTranscript() {
  const transcript = transcriptField.value.trim();
  if (!transcript) return;
  const button = $("#analyze-button");
  button.disabled = true;
  button.textContent = "Updating…";
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript }),
    });
    const result = await readJson(response);
    renderAnalysis(result.analysis, "Local keyword rules");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = 'Update case brief <span>→</span>';
  }
}

function renderAnalysis(analysis, model) {
  latestAnalysis = analysis;
  latestModel = model;
  $("#case-empty").hidden = true;
  $("#case-results").hidden = false;
  $("#case-category").textContent = analysis.category.toUpperCase();
  $("#case-priority").innerHTML = `<i></i> ${escapeHtml(analysis.priority.toUpperCase())}`;
  $("#case-priority").classList.toggle("urgent", analysis.priority.toLowerCase().includes("review"));
  $("#case-summary").textContent = analysis.summary;
  $("#case-tone").textContent = analysis.tone;
  $("#review-note-text").textContent = analysis.review_note;
  const list = $("#action-list");
  list.replaceChildren(...analysis.actions.map((action) => {
    const item = document.createElement("li");
    item.textContent = action;
    return item;
  }));
  $("#export-button").disabled = false;
  $("#case-heading").textContent = "Suggested case brief";
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

transcribeButton.addEventListener("click", async () => {
  if (!selectedFile) return;
  clearError();
  transcribeButton.disabled = true;
  transcribeButton.querySelector(".button-label").textContent = "Listening…";
  try {
    const form = new FormData();
    form.append("audio_file", selectedFile);
    form.append("language_code", $("#language-select").value);
    const response = await fetch("/api/transcribe", { method: "POST", body: form });
    const result = await readJson(response);
    setTranscript(result.transcript, "GNANI PRISMA · TRANSCRIBED");
    $("#case-heading").textContent = "Suggested case brief";
    await analyzeCurrentTranscript();
    showToast("Transcript ready. Review the suggested case brief.");
  } catch (error) {
    showError(error.message);
  } finally {
    transcribeButton.querySelector(".button-label").textContent = "Transcribe call";
    transcribeButton.disabled = !selectedFile;
  }
});

$("#demo-button").addEventListener("click", async () => {
  $("#language-select").value = "en-IN";
  setTranscript(sampleTranscript, "SYNTHETIC EXAMPLE · NOT A REAL CALL");
  $("#case-heading").textContent = "Suggested case brief";
  await analyzeCurrentTranscript();
  showToast("Synthetic sample loaded. No API credits used.");
});

transcriptField.addEventListener("input", () => {
  const text = transcriptField.value;
  $("#transcript-count").textContent = `${text.length.toLocaleString()} characters`;
  $("#copy-button").disabled = !text.trim();
  $("#listen-button").disabled = !text.trim();
  $("#analyze-button").disabled = !text.trim();
});
$("#analyze-button").addEventListener("click", analyzeCurrentTranscript);

$("#copy-button").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(transcriptField.value);
    showToast("Transcript copied.");
  } catch {
    transcriptField.select();
    document.execCommand("copy");
    showToast("Transcript copied.");
  }
});

$("#listen-button").addEventListener("click", async () => {
  const button = $("#listen-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: transcriptField.value.trim(), language: $("#language-select").value }),
    });
    const result = await readJson(response);
    const raw = atob(result.audio_base64);
    const bytes = Uint8Array.from(raw, (character) => character.charCodeAt(0));
    const player = $("#audio-playback");
    if (player.dataset.objectUrl) URL.revokeObjectURL(player.dataset.objectUrl);
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: result.content_type || "audio/wav" }));
    player.src = objectUrl;
    player.dataset.objectUrl = objectUrl;
    player.hidden = false;
    await player.play();
    showToast(`Playing ${result.voice} · Timbre v2.5`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#export-button").addEventListener("click", () => {
  if (!latestAnalysis) return;
  const report = {
    application: "BoliDesk",
    generated_at: new Date().toISOString(),
    transcription_model: $("#transcript-source").textContent.startsWith("GNANI PRISMA") ? "Gnani Prisma v2.5" : "Synthetic demo transcript",
    triage_method: latestModel,
    language: $("#language-select").value,
    transcript: transcriptField.value.trim(),
    case_brief: latestAnalysis,
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "bolidesk-case-brief.json";
  link.click();
  URL.revokeObjectURL(url);
  showToast("Case brief exported as JSON.");
});

loadConnection();
