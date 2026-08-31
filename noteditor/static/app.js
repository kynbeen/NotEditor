"use strict";

const state = {
  documents: [],
  selected: new Set(),
  order: [],
  orderDirty: false,
  mergeOutputNameDirty: false,
  mergePlan: null,
  sourceReview: null,
  handwritingOutputNameDirty: false,
  active: null,
  thumbnailCache: new Map(),
  thumbnailCacheBytes: 0,
  imageInflight: new Map(),
  sourceThumbnailObserver: null,
  resultThumbnailObserver: null,
  previewObserver: null,
  previewScrollFrame: 0,
  bridgeReady: false,
  bridgeFailed: false,
  runtime: window.location.hash === "#desktop" || !window.location.protocol.startsWith("http")
    ? "desktop"
    : "web",
  handwriting: {
    source_name: null, source_format: null, target_name: null,
    ready: false, inspection: null, plan: [],
    analysis: { state: "waiting", stage: "waiting", message: "두 파일을 선택해 주세요.", error: null },
  },
  reviewObserver: null,
  sourceReviewObserver: null,
};

let handwritingPollTimer = 0;

const $ = (selector) => document.querySelector(selector);
const refs = {
  add: $("#addPdfButton"), emptyAdd: $("#emptyAddButton"), save: $("#saveButton"),
  mergeOutputName: $("#mergeOutputName"),
  resetOrder: $("#resetOrderButton"), sourceEmpty: $("#sourceEmpty"),
  documentList: $("#documentList"), documentCount: $("#documentCount"),
  resultEmpty: $("#resultEmpty"), resultList: $("#resultList"), pageCount: $("#pageCount"),
  selectionSummary: $("#selectionSummary"), previewEmpty: $("#previewEmpty"),
  previewStage: $("#previewStage"), previewPages: $("#previewPages"),
  previewEyebrow: $("#previewEyebrow"), previewHeading: $("#previewHeading"),
  previewMeta: $("#previewMeta"), busy: $("#busyOverlay"), busyText: $("#busyText"),
  toastRegion: $("#toastRegion"),
  sourceHelp: $("#sourceHelp"), connectionError: $("#connectionError"),
  mergeTab: $("#mergeTabButton"), handwriting: $("#handwritingButton"),
  mergeWorkspace: $("#mergeWorkspace"), handwritingWorkspace: $("#handwritingWorkspace"),
  mergeTopActions: $("#mergeTopActions"), chooseHandwritingSource: $("#chooseHandwritingSource"),
  chooseHandwritingTarget: $("#chooseHandwritingTarget"), handwritingSourceName: $("#handwritingSourceName"),
  handwritingTargetName: $("#handwritingTargetName"), handwritingCompatibility: $("#handwritingCompatibility"),
  retryHandwriting: $("#retryHandwritingButton"),
  resetHandwriting: $("#resetHandwritingButton"), saveHandwriting: $("#saveHandwritingButton"),
  handwritingOutputName: $("#handwritingOutputName"), handwritingOutputSuffix: $("#handwritingOutputSuffix"),
  resetDocuments: $("#resetDocumentsButton"),
  handwritingReview: $("#handwritingReview"),
  handwritingReviewRows: $("#handwritingReviewRows"),
  handwritingReviewSummary: $("#handwritingReviewSummary"),
  reviewInkToggle: $("#reviewInkToggle"),
  reviewReorderDialog: $("#reviewReorderDialog"),
  reviewReorderMessage: $("#reviewReorderMessage"),
  reviewReorderCancel: $("#reviewReorderCancel"),
  reviewReorderContinue: $("#reviewReorderContinue"),
  webPdfInput: $("#webPdfInput"), webHandwritingInput: $("#webHandwritingInput"),
  webTargetPdfInput: $("#webTargetPdfInput"),
  sourceReview: $("#sourceReview"), sourceReviewRows: $("#sourceReviewRows"),
  sourceReviewSummary: $("#sourceReviewSummary"),
  sourceReviewMessage: $("#sourceReviewMessage"),
  sourceReviewSkip: $("#sourceReviewSkip"), sourceReviewApply: $("#sourceReviewApply"),
};

const pageKey = (docId, index) => `${docId}:${index}`;
const refKey = (ref) => pageKey(ref.document_id, ref.page_index);
const CLIENT_IMAGE_CACHE_BYTES = 12 * 1024 * 1024;

function createRequestQueue(limit) {
  let active = 0;
  const waiting = [];
  const pump = () => {
    while (active < limit && waiting.length) {
      const entry = waiting.shift();
      active += 1;
      Promise.resolve()
        .then(entry.task)
        .then(entry.resolve, entry.reject)
        .finally(() => { active -= 1; pump(); });
    }
  };
  return (task) => new Promise((resolve, reject) => {
    waiting.push({ task, resolve, reject });
    pump();
  });
}

const queueThumbnailRequest = createRequestQueue(3);
const queuePreviewRequest = createRequestQueue(1);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ ok: false, error: `HTTP ${response.status}` }));
  if (!payload.error && payload.detail) payload.error = payload.detail;
  if (!response.ok && payload.ok !== false) payload.ok = false;
  payload.http_status = response.status;
  return payload;
}

function pickWebFiles(input) {
  return new Promise((resolve) => {
    input.value = "";
    const onChange = () => resolve([...input.files]);
    input.addEventListener("change", onChange, { once: true });
    input.click();
    window.addEventListener("focus", () => {
      setTimeout(() => {
        input.removeEventListener("change", onChange);
        resolve([...input.files]);
      }, 300);
    }, { once: true });
  });
}

function uploadFormJson(endpoint, form, progressLabel) {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.open("POST", endpoint);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable || !progressLabel) return;
      const percent = Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100)));
      refs.busyText.textContent = `${progressLabel} ${percent}%`;
    });
    request.addEventListener("load", () => {
      let payload;
      try { payload = JSON.parse(request.responseText); }
      catch (_error) { payload = { ok: false, error: `HTTP ${request.status}` }; }
      if (!payload.error && payload.detail) payload.error = payload.detail;
      if (request.status < 200 || request.status >= 300) payload.ok = false;
      payload.http_status = request.status;
      resolve(payload);
    });
    request.addEventListener("error", () => resolve({
      ok: false, error: "파일 업로드 연결이 끊겼습니다.", http_status: 0,
    }));
    request.send(form);
  });
}

async function uploadWebFiles(input, endpoint, field = "files", progressLabel = "파일 업로드 중…") {
  const files = await pickWebFiles(input);
  if (!files.length) return { ok: true, cancelled: true, added: [], sources: state.documents };
  const form = new FormData();
  files.forEach((file) => form.append(field, file, file.name));
  return uploadFormJson(endpoint, form, progressLabel);
}

async function downloadWebResult(endpoint, payload) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    return { ok: false, error: error.error || `HTTP ${response.status}` };
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const filename = encoded ? decodeURIComponent(encoded) : "NotEditor-result";
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  const warnings = JSON.parse(decodeURIComponent(response.headers.get("X-NotEditor-Warnings") || "%5B%5D"));
  return {
    ok: true,
    cancelled: false,
    result: {
      path: `다운로드 · ${filename}`,
      page_count: Number(response.headers.get("X-NotEditor-Page-Count") || 0),
      warnings,
    },
  };
}

const webApi = {
  health: () => fetchJson("/api/health"),
  startup_plan: async () => ({ ok: true, plan: null }),
  log_client_error: (message) => fetchJson("/api/client-error", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }),
  }),
  choose_pdfs: () => uploadWebFiles(refs.webPdfInput, "/api/documents", "files", "PDF 업로드 중…"),
  remove_document: (documentId) => fetchJson(`/api/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" }),
  page_image: (documentId, pageIndex, kind) => fetchJson(`/api/documents/${encodeURIComponent(documentId)}/pages/${pageIndex}?kind=${encodeURIComponent(kind)}`),
  parse_range: (value, pageCount) => fetchJson("/api/ranges", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value, page_count: pageCount }),
  }),
  save_result: (order, suggestedName) => downloadWebResult("/api/documents/export", { order, suggested_name: suggestedName }),
  choose_handwriting_source: () => uploadWebFiles(refs.webHandwritingInput, "/api/handwriting/source", "file", "필기 파일 업로드 중…"),
  choose_handwriting_target: () => uploadWebFiles(refs.webTargetPdfInput, "/api/handwriting/target", "file", "대상 PDF 업로드 중…"),
  handwriting_status: () => fetchJson("/api/handwriting/status"),
  retry_handwriting_analysis: () => fetchJson("/api/handwriting/retry", { method: "POST" }),
  handwriting_preview: (pageIndex, sourceIndex) => fetchJson(`/api/handwriting/preview?page_index=${pageIndex}&source_index=${sourceIndex}`),
  reset_handwriting_transfer: () => fetchJson("/api/handwriting/reset", { method: "POST" }),
  reset_documents: () => fetchJson("/api/documents/reset", { method: "POST" }),
  save_handwriting_transfer: (suggestedName, pagePlan, allowUnconfirmed = false) => downloadWebResult("/api/handwriting/export", {
    suggested_name: suggestedName, page_plan: pagePlan, allow_unconfirmed: allowUnconfirmed,
  }),
};

function requireApi() {
  const bridge = window.pywebview?.api;
  if (bridge) return bridge;
  if (state.runtime === "web") return webApi;
  throw new Error("앱 내부 연결이 준비되지 않았습니다. NotEditor를 다시 실행해 주세요.");
}

async function callApi(method, ...args) {
  const bridge = requireApi();
  if (typeof bridge[method] !== "function") {
    throw new Error(`앱 기능을 찾을 수 없습니다: ${method}. 앱을 다시 설치해 주세요.`);
  }
  return bridge[method](...args);
}

function toast(message, type = "") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  refs.toastRegion.append(node);
  setTimeout(() => node.remove(), 4800);
}

function setBusy(on, text = "처리 중…") {
  refs.busyText.textContent = text;
  refs.busy.hidden = !on;
  document.body.setAttribute("aria-busy", String(on));
}

function setBridgeState(ready, failed = false) {
  state.bridgeReady = ready;
  state.bridgeFailed = failed;
  refs.add.disabled = !ready;
  refs.emptyAdd.disabled = !ready;
  refs.chooseHandwritingSource.disabled = !ready;
  refs.chooseHandwritingTarget.disabled = !ready;
  refs.emptyAdd.textContent = ready ? "파일 선택" : (failed ? "앱으로 다시 실행하세요" : "준비 중…");
  refs.sourceHelp.textContent = ready
    ? (state.runtime === "web" ? "PDF는 이 브라우저 세션에서만 임시 처리됩니다." : "여러 파일을 한 번에 선택할 수 있습니다.")
    : (failed ? "NotEditor 연결을 확인할 수 없습니다." : "앱 내부 연결을 준비하는 중입니다.");
  refs.connectionError.hidden = !failed;
  renderSummary();
}

async function initializeBridge() {
  if (state.bridgeReady) return;
  try {
    const response = await callApi("health");
    if (!response?.ok) throw new Error(response?.error || "앱 응답이 올바르지 않습니다.");
    setBridgeState(true, false);
    const startup = await callApi("startup_plan");
    if (!startup?.ok) throw new Error(startup?.error || "합치기 시작 계획을 열 수 없습니다.");
    if (startup.plan) applyStartupPlan(startup.plan);
  } catch (error) {
    console.error(error);
    setBridgeState(false, true);
    toast(error.message, "error");
    reportClientError(error);
  }
}

function applyStartupPlan(plan) {
  state.mergePlan = plan;
  state.sourceReview = plan.mode === "review" ? plan.comparison : null;
  state.documents = plan.sources || [];
  state.order = (plan.order || []).map((item) => ({ ...item }));
  state.selected = new Set(state.order.map(refKey));
  state.orderDirty = true;
  state.mergeOutputNameDirty = true;
  refs.mergeOutputName.value = withoutKnownExtension(plan.output_name || "merged.pdf");
  refs.mergeOutputName.title = `summary.ai 지정 저장 경로: ${plan.output_path}`;
  refs.mergeWorkspace.classList.toggle("review-mode", plan.mode === "review");
  refs.sourceReview.hidden = plan.mode !== "review";
  if (plan.mode === "review") {
    refs.add.hidden = true;
    refs.resetDocuments.hidden = true;
    refs.mergeOutputName.parentElement.hidden = true;
    refs.save.hidden = true;
    refs.sourceReviewApply.textContent = plan.origin === "merged" ? "합쳐서 갱신" : "전체 갱신";
    renderSourceReview();
  }
  if (plan.title) document.title = `NotEditor — ${plan.title}`;
  render();
  const first = state.order[0];
  if (first) showPreview(first.document_id, first.page_index, "인계 계획 미리보기");
  toast(plan.mode === "review"
    ? "실제 사용 파일과 현재 수집함 파일을 비교했습니다. 같은 쪽과 다른 쪽을 확인해 주세요."
    : "summary.ai 합치기 계획을 불러왔습니다. 쪽 선택과 순서를 확인해 주세요.", "success");
  if (plan.auto_choose) setTimeout(() => { void addPdfs(); }, 0);
}

function sourceReviewStatus(pair) {
  if (!pair.source_ref) return { label: "현재 파일에 새로 생김", same: false };
  if (!pair.target_ref) return { label: "현재 파일에서 빠짐", same: false };
  if (pair.confident) return { label: "동일 쪽", same: true };
  return { label: "내용 다름 · 확인 필요", same: false };
}

async function loadSourceReviewRow(row, pair) {
  if (row.dataset.loaded === "true") return;
  row.dataset.loaded = "true";
  const requests = [];
  for (const [name, pageRef] of [["source", pair.source_ref], ["target", pair.target_ref]]) {
    if (!pageRef) continue;
    requests.push(queuePreviewRequest(async () => {
      const response = await callApi("page_image", pageRef.document_id, pageRef.page_index, "preview");
      if (!response.ok) throw new Error(response.error);
      const page = row.querySelector(`.${name}-cell .review-page`);
      const image = page.querySelector(".review-background");
      image.src = response.image;
      page.classList.remove("loading");
      page.classList.add("loaded");
      await image.decode().catch(() => {});
    }));
  }
  try { await Promise.all(requests); }
  catch (error) {
    row.dataset.loaded = "false";
    row.querySelectorAll(".review-page:not(.empty)").forEach((page) => {
      page.classList.remove("loading"); page.classList.add("error");
      const placeholder = page.querySelector(".review-placeholder");
      placeholder.textContent = "미리보기 실패 · 눌러서 다시 시도";
      placeholder.onclick = () => loadSourceReviewRow(row, pair);
    });
    reportClientError(error);
  }
}

function sourceReviewCell(kind, pageRef, emptyMessage) {
  const cell = document.createElement("div");
  cell.className = `review-cell ${kind}-cell`;
  const page = makeReviewPage(pageRef ? "" : emptyMessage, pageRef ? `${pageRef.document_name} ${pageRef.page_index + 1}쪽` : "");
  const ink = page.querySelector(".review-ink");
  if (ink) ink.hidden = true;
  cell.append(page);
  const meta = document.createElement("div");
  meta.className = "review-meta";
  meta.innerHTML = pageRef
    ? `<span class="review-meta-copy"><strong>${escapeHtml(pageRef.document_name)}</strong><span>${pageRef.page_index + 1}쪽</span></span>`
    : `<span>${escapeHtml(emptyMessage)}</span>`;
  cell.append(meta);
  return cell;
}

function renderSourceReview() {
  const comparison = state.sourceReview;
  refs.sourceReviewRows.replaceChildren();
  state.sourceReviewObserver?.disconnect();
  if (!comparison) return;
  refs.sourceReviewSummary.textContent = [
    `동일 ${comparison.matched_count - comparison.uncertain_count}쪽`,
    `확인 필요 ${comparison.uncertain_count}쌍`,
    `현재 전용 ${comparison.target_only_count}쪽`,
    `사용본 전용 ${comparison.source_only_count}쪽`,
  ].join(" · ");
  state.sourceReviewObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      state.sourceReviewObserver.unobserve(entry.target);
      const pair = comparison.pairs[Number(entry.target.dataset.pairIndex)];
      if (pair) void loadSourceReviewRow(entry.target, pair);
    });
  }, { root: refs.sourceReviewRows, rootMargin: "500px 0px" });
  comparison.pairs.forEach((pair, index) => {
    const status = sourceReviewStatus(pair);
    const row = document.createElement("article");
    row.className = `review-row ${status.same ? "same" : "different"}`;
    row.dataset.pairIndex = String(index);
    const source = sourceReviewCell("source", pair.source_ref, "현재 파일에서 빠진 사용본 쪽");
    const target = sourceReviewCell("target", pair.target_ref, "사용본에 없던 새 쪽");
    const badge = document.createElement("span");
    badge.className = `review-badge${status.same ? " done" : ""}`;
    badge.textContent = status.label;
    target.querySelector(".review-meta").append(badge);
    row.append(source, target);
    refs.sourceReviewRows.append(row);
    state.sourceReviewObserver.observe(row);
  });
}

async function finishSourceReview(decision) {
  if (!state.mergePlan || state.mergePlan.mode !== "review") return;
  refs.sourceReviewMessage.className = "source-review-message";
  refs.sourceReviewMessage.textContent = "summary.ai에 결정을 전달하는 중…";
  refs.sourceReviewApply.disabled = true;
  refs.sourceReviewSkip.disabled = true;
  try {
    const response = await callApi("finish_review", decision, state.order);
    if (!response.ok) throw new Error(response.error);
    refs.sourceReviewMessage.classList.add("success");
    refs.sourceReviewMessage.textContent = decision === "skip"
      ? "현재 수집함 버전을 확인 처리했습니다. summary.ai로 돌아가세요."
      : "갱신 결정을 전달했습니다. summary.ai가 결과를 반영합니다.";
    toast(refs.sourceReviewMessage.textContent, "success");
  } catch (error) {
    refs.sourceReviewMessage.classList.add("error");
    refs.sourceReviewMessage.textContent = error.message;
    refs.sourceReviewApply.disabled = false;
    refs.sourceReviewSkip.disabled = false;
  }
}

async function reportClientError(value) {
  const message = value instanceof Error ? `${value.message}\n${value.stack || ""}` : String(value);
  try {
    const api = requireApi();
    if (typeof api.log_client_error === "function") await api.log_client_error(message);
  } catch (_) { /* Logging must never hide the original UI error. */ }
}

function showTool(tool) {
  const merge = tool === "merge";
  refs.mergeWorkspace.hidden = !merge;
  refs.handwritingWorkspace.hidden = merge;
  refs.mergeTopActions.hidden = !merge;
  refs.mergeTab.classList.toggle("active", merge);
  refs.handwriting.classList.toggle("active", !merge);
  refs.mergeTab.setAttribute("aria-selected", String(merge));
  refs.handwriting.setAttribute("aria-selected", String(!merge));
  if (!merge) {
    renderHandwritingStatus();
    if (state.handwriting.ready) renderPageReview();
  }
}

function renderHandwritingStatus(error = "") {
  const status = state.handwriting;
  const analysis = status.analysis || {};
  refs.handwritingSourceName.textContent = status.source_name || ".sdocx 또는 .notewise 파일 선택";
  refs.handwritingTargetName.textContent = status.target_name || ".pdf 파일 선택";
  refs.saveHandwriting.disabled = !state.bridgeReady || !status.ready;
  refs.retryHandwriting.hidden = analysis.state !== "error";
  const card = refs.handwritingCompatibility;
  card.classList.remove("waiting", "ready", "error");
  const icon = card.querySelector(".compatibility-icon");
  const heading = card.querySelector("strong");
  const detail = card.querySelector("p");
  if (error) {
    card.classList.add("error");
    icon.textContent = "!";
    heading.textContent = "필기 문서를 분석하지 못했습니다.";
    detail.textContent = error;
    return;
  }
  if (analysis.state === "error") {
    card.classList.add("error");
    icon.textContent = "!";
    heading.textContent = "필기 문서를 분석하지 못했습니다.";
    detail.textContent = `${analysis.error || analysis.message}\n올린 파일은 유지됩니다. 분석을 다시 시도할 수 있습니다.`;
    return;
  }
  if (analysis.state === "running") {
    card.classList.add("waiting");
    icon.innerHTML = '<span class="loader compact"></span>';
    heading.textContent = analysis.message || "필기 문서를 분석하는 중…";
    detail.textContent = "업로드는 끝났습니다. 이 화면을 계속 사용할 수 있으며 분석이 끝나면 자동으로 갱신됩니다.";
    return;
  }
  if (status.ready && status.inspection) {
    const info = status.inspection;
    card.classList.add("ready");
    icon.textContent = "✓";
    const common = `필기 데이터가 있는 페이지 ${info.annotated_page_count}쪽 · Samsung 펜 캐시 ${info.stroke_cache_count}개`;
    if (info.mode === "rebuild" && info.match) {
      const match = info.match;
      heading.textContent = `공통 ${match.matched_count}쪽을 찾아 새 PDF 기준으로 재조립합니다.`;
      detail.textContent = [
        `새 PDF 전용 ${match.target_only.length}쪽 추가 · 구판 전용 ${match.source_only.length}쪽 검사 · 불확실 ${match.uncertain_count}쌍`,
        info.alignment ? `본문 배율 ${info.alignment.scale.toFixed(3)}배 · 이동 ${info.alignment.offset_x_mm}, ${info.alignment.offset_y_mm}mm` : "공통 쪽의 페이지 좌표가 일치합니다.",
        common,
      ].join("\n");
    } else if (info.mode === "aligned" && info.alignment) {
      const fit = info.alignment;
      heading.textContent = `본문 기준으로 ${fit.scale.toFixed(3)}배 맞춰서 ${info.page_count}쪽을 옮깁니다.`;
      detail.textContent = [
        `이동 ${fit.offset_x_mm}, ${fit.offset_y_mm}mm · 본문 오차 최대 ${fit.residual_mm}mm (${fit.sampled_pages}쪽 표본)`,
        ...alignmentWarnings(fit),
        common,
      ].join("\n");
    } else {
      heading.textContent = `${info.page_count}쪽의 페이지 좌표가 모두 일치합니다.`;
      detail.textContent = `${common} · 대상 PDF를 그대로 넣습니다.`;
    }
    return;
  }
  card.classList.add("waiting");
  icon.textContent = "···";
  heading.textContent = "두 파일을 선택하면 자동으로 맞춤 여부를 확인합니다.";
  detail.textContent = "쪽이 추가·삭제됐으면 공통 쪽을 자동으로 찾고, 크기나 여백이 달라지면 본문을 기준으로 자동 정렬합니다.";
}

function clonePagePlan(plan) {
  return (plan?.slots || []).map((slot) => ({
    source_index: slot.source_index,
    target_index: slot.target_index,
    confirmed: Boolean(slot.confirmed),
    manual: Boolean(slot.manual),
    attention: Boolean(slot.needs_confirmation || slot.kind !== "matched"),
  }));
}

function reviewBadge(slot) {
  if (slot.source_index === null) return "새 쪽";
  if (slot.target_index === null) return "원본에만 있음";
  if (slot.manual) return "수동 정렬";
  if (slot.attention && slot.confirmed) return "확인 완료";
  return slot.confirmed ? "자동 매칭" : "확인 필요";
}

function renderReviewSummary() {
  const plan = state.handwriting.plan;
  const unconfirmed = plan.filter((slot) => !slot.confirmed).length;
  const automatic = plan.filter((slot) => (
    slot.source_index !== null && slot.target_index !== null && !slot.manual
  )).length;
  const targetOnly = plan.filter((slot) => slot.source_index === null).length;
  const sourceOnly = plan.filter((slot) => slot.target_index === null).length;
  refs.handwritingReviewSummary.textContent = [
    `전체 ${plan.length}행`,
    `자동 연결 ${automatic}`,
    `새 전용 ${targetOnly}`,
    `옛 전용 ${sourceOnly}`,
    unconfirmed ? `확인 필요 ${unconfirmed}` : "모두 확인됨",
  ].join(" · ");
  refs.saveHandwriting.disabled = !state.bridgeReady || !state.handwriting.ready;
}

function setReviewRowState(row, slot) {
  row.classList.toggle("needs-confirmation", !slot.confirmed);
  row.classList.toggle("confirmed", slot.confirmed);
  row.classList.toggle(
    "special-row",
    slot.attention || slot.manual || slot.source_index === null || slot.target_index === null,
  );
  const button = row.querySelector(".review-confirm");
  if (!button) return;
  button.textContent = slot.confirmed ? "확인됨" : "이 대응 확인";
  button.classList.toggle("done", slot.confirmed);
}

function makeReviewPage(emptyMessage, alt) {
  const page = document.createElement("div");
  page.className = `review-page${emptyMessage ? " empty" : " loading"}`;
  page.innerHTML = emptyMessage
    ? `<div class="review-placeholder">${emptyMessage}</div>`
    : `<img class="review-background" alt="${alt}" /><img class="review-ink" alt="필기 레이어" /><div class="review-placeholder"><div class="loader"></div><span>보이면 미리보기를 불러옵니다…</span></div>`;
  return page;
}

function describeChangedPages(before, after) {
  const changedTargets = new Set();
  const changedSources = new Set();
  const beforeTargetSource = new Map(before.filter((slot) => slot.target_index !== null)
    .map((slot) => [slot.target_index, slot.source_index]));
  const beforeSourceTarget = new Map(before.filter((slot) => slot.source_index !== null)
    .map((slot) => [slot.source_index, slot.target_index]));
  after.forEach((slot, index) => {
    const prior = before[index];
    if (slot.target_index !== null && (
      beforeTargetSource.get(slot.target_index) !== slot.source_index
      || prior?.target_index !== slot.target_index
    )) changedTargets.add(slot.target_index + 1);
    if (slot.source_index !== null && beforeSourceTarget.get(slot.source_index) !== slot.target_index) {
      changedSources.add(slot.source_index + 1);
    }
  });
  return {
    targetPages: [...changedTargets].sort((a, b) => a - b),
    sourcePages: [...changedSources].sort((a, b) => a - b),
  };
}

function shiftedTargetPlan(from, to) {
  const before = state.handwriting.plan;
  const targets = before.map((slot) => slot.target_index);
  const [moved] = targets.splice(from, 1);
  targets.splice(to, 0, moved);
  const priorByPair = new Map(before.map((slot) => [
    `${slot.source_index}:${slot.target_index}`, slot,
  ]));
  return before.map((prior, index) => {
    const target = targets[index];
    const same = priorByPair.get(`${prior.source_index}:${target}`);
    return {
      source_index: prior.source_index,
      target_index: target,
      confirmed: same ? same.confirmed : false,
      manual: same ? same.manual : true,
      attention: same ? same.attention : true,
    };
  }).filter((slot) => slot.source_index !== null || slot.target_index !== null);
}

function confirmReviewReorder(message) {
  refs.reviewReorderMessage.textContent = message;
  refs.reviewReorderDialog.showModal();
  return new Promise((resolve) => {
    const finish = (accepted) => {
      refs.reviewReorderDialog.removeEventListener("close", onClose);
      refs.reviewReorderDialog.removeEventListener("cancel", onCancel);
      resolve(accepted);
    };
    const onClose = () => finish(refs.reviewReorderDialog.returnValue === "continue");
    const onCancel = (event) => {
      event.preventDefault();
      refs.reviewReorderDialog.close("cancel");
    };
    refs.reviewReorderDialog.addEventListener("close", onClose);
    refs.reviewReorderDialog.addEventListener("cancel", onCancel);
  });
}

async function moveReviewTarget(from, to) {
  if (!Number.isInteger(from) || !Number.isInteger(to) || from === to) return;
  const before = state.handwriting.plan;
  if (!before[from] || before[from].target_index === null) return;
  const next = shiftedTargetPlan(from, Math.max(0, Math.min(to, before.length - 1)));
  const changed = describeChangedPages(before, next);
  const relationshipCount = next.filter((slot, index) => (
    before[index]?.source_index !== slot.source_index
    || before[index]?.target_index !== slot.target_index
  )).length;
  const details = [
    changed.targetPages.length ? `새 PDF ${changed.targetPages.join(", ")}쪽` : "",
    changed.sourcePages.length ? `원본 ${changed.sourcePages.join(", ")}쪽` : "",
  ].filter(Boolean).join("과 ");
  if (details && !await confirmReviewReorder(
    `${relationshipCount}개 대응이 달라집니다 (${details}). 변경된 행은 다시 확인해야 합니다. 계속할까요?`,
  )) return;
  state.handwriting.plan = next;
  renderPageReview();
}

async function loadReviewRow(row, slotIndex) {
  if (row.dataset.loaded === "true") return;
  row.dataset.loaded = "true";
  const slot = state.handwriting.plan[slotIndex];
  if (!slot) return;
  const targetIndex = slot.target_index === null ? 0 : slot.target_index;
  const sourceIndex = slot.source_index === null ? -1 : slot.source_index;
  try {
    const response = await queuePreviewRequest(() => callApi(
      "handwriting_preview", targetIndex, sourceIndex,
    ));
    if (!response.ok) throw new Error(response.error);
    const sourcePage = row.querySelector(".source-cell .review-page");
    const targetPage = row.querySelector(".target-cell .review-page");
    const decodes = [];
    if (slot.source_index !== null) {
      sourcePage.querySelector(".review-background").src = response.before;
      sourcePage.querySelector(".review-ink").src = response.ink;
      decodes.push(...sourcePage.querySelectorAll("img"));
      sourcePage.classList.remove("loading");
      sourcePage.classList.add("loaded");
    }
    if (slot.target_index !== null) {
      targetPage.querySelector(".review-background").src = response.after;
      targetPage.querySelector(".review-ink").src = response.ink;
      decodes.push(...targetPage.querySelectorAll("img"));
      targetPage.classList.remove("loading");
      targetPage.classList.add("loaded");
    }
    await Promise.allSettled(decodes.map((image) => image.decode()));
    const inkLabel = row.querySelector(".review-ink-count");
    if (inkLabel) {
      inkLabel.textContent = response.stroke_count
        ? `필기 ${response.stroke_count}획`
        : "필기 없음";
    }
  } catch (error) {
    row.dataset.loaded = "false";
    row.querySelectorAll(".review-page:not(.empty)").forEach((page) => {
      page.classList.remove("loading");
      page.classList.add("error");
      const placeholder = page.querySelector(".review-placeholder");
      placeholder.textContent = "미리보기 실패 · 눌러서 다시 시도";
      placeholder.onclick = () => loadReviewRow(row, slotIndex);
    });
    reportClientError(error);
  }
}

function renderPageReview() {
  const visible = state.handwriting.ready && state.handwriting.plan.length > 0;
  refs.handwritingReview.hidden = !visible;
  refs.handwritingReviewRows.replaceChildren();
  state.reviewObserver?.disconnect();
  if (!visible) return;
  state.reviewObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      state.reviewObserver.unobserve(entry.target);
      loadReviewRow(entry.target, Number(entry.target.dataset.slotIndex));
    });
  }, { root: null, rootMargin: "600px 0px" });

  state.handwriting.plan.forEach((slot, index) => {
    const row = document.createElement("article");
    row.className = "review-row";
    row.dataset.slotIndex = String(index);
    const source = document.createElement("div");
    source.className = "review-cell source-cell";
    source.append(makeReviewPage(
      slot.source_index === null ? "새 PDF에만 있는 쪽" : "",
      slot.source_index === null ? "" : `원본 ${slot.source_index + 1}쪽`,
    ));
    const sourceMeta = document.createElement("div");
    sourceMeta.className = "review-meta";
    sourceMeta.innerHTML = slot.source_index === null
      ? `<span>대응할 원본 없음</span>`
      : `<span class="review-meta-copy"><strong>원본 ${slot.source_index + 1}쪽</strong><span class="review-ink-count"></span></span>`;
    source.append(sourceMeta);

    const target = document.createElement("div");
    target.className = "review-cell target-cell";
    target.draggable = slot.target_index !== null;
    target.append(makeReviewPage(
      slot.target_index === null ? "새 PDF에서 빠진 쪽 · 원본 필기와 배경을 보존" : "",
      slot.target_index === null ? "" : `새 PDF ${slot.target_index + 1}쪽`,
    ));
    const targetMeta = document.createElement("div");
    targetMeta.className = "review-meta";
    targetMeta.innerHTML = `<span class="review-meta-copy">${
      slot.target_index === null
        ? "<strong>새 PDF 쪽 없음</strong>"
        : `<span class="review-drag-handle" aria-hidden="true">⠿</span><strong>새 PDF ${slot.target_index + 1}쪽</strong>`
    }<span class="review-badge${slot.manual ? " manual" : ""}">${reviewBadge(slot)}</span></span>`;
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "review-confirm";
    confirm.addEventListener("click", () => {
      slot.confirmed = true;
      setReviewRowState(row, slot);
      renderReviewSummary();
    });
    targetMeta.append(confirm);
    target.append(targetMeta);

    if (slot.target_index !== null) {
      target.addEventListener("dragstart", (event) => {
        target.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(index));
      });
      target.addEventListener("dragend", () => {
        target.classList.remove("dragging");
        document.querySelectorAll(".review-row.drop-before, .review-row.drop-after")
          .forEach((node) => node.classList.remove("drop-before", "drop-after"));
      });
    }
    row.addEventListener("dragover", (event) => {
      event.preventDefault();
      const before = event.clientY < row.getBoundingClientRect().top + row.offsetHeight / 2;
      row.classList.toggle("drop-before", before);
      row.classList.toggle("drop-after", !before);
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
    row.addEventListener("drop", (event) => {
      event.preventDefault();
      const from = Number(event.dataTransfer.getData("text/plain"));
      let to = index + (row.classList.contains("drop-after") ? 1 : 0);
      row.classList.remove("drop-before", "drop-after");
      if (from < to) to -= 1;
      void moveReviewTarget(from, to);
    });
    row.append(source, target);
    setReviewRowState(row, slot);
    refs.handwritingReviewRows.append(row);
    state.reviewObserver.observe(row);
  });
  renderReviewSummary();
}

function alignmentWarnings(fit) {
  const warnings = [];
  if (!fit.axes_agree) {
    warnings.push(`가로세로 비율이 달라 폭 기준으로 맞췄습니다. 세로로 최대 ${fit.aspect_drift_mm}mm 어긋날 수 있습니다.`);
  }
  if (fit.clipped_mm > 0.5) {
    warnings.push(`새 배경의 본문이 원본 쪽 밖으로 최대 ${fit.clipped_mm}mm 잘립니다.`);
  }
  if (fit.residual_mm > 2) {
    warnings.push("쪽마다 본문 위치가 달라 자동 정렬이 정확하지 않을 수 있습니다. 미리보기를 꼭 확인하세요.");
  }
  return warnings;
}

function withoutKnownExtension(name) {
  return String(name || "").replace(/\.(pdf|sdocx|notewise)$/i, "");
}

function updateHandwritingOutputName(force = false) {
  const extension = state.handwriting.source_format === "notewise" ? ".notewise" : ".sdocx";
  refs.handwritingOutputSuffix.textContent = extension;
  if (force || !state.handwritingOutputNameDirty) {
    const base = withoutKnownExtension(state.handwriting.target_name || "새-문서");
    refs.handwritingOutputName.value = `${base}-필기`;
    state.handwritingOutputNameDirty = false;
  }
}

function updateMergeOutputName(force = false) {
  if (!state.documents.length) {
    if (force || !state.mergeOutputNameDirty) refs.mergeOutputName.value = "";
    return;
  }
  if (force || !state.mergeOutputNameDirty) {
    refs.mergeOutputName.value = `${withoutKnownExtension(state.documents[0].name)}-편집본`;
    state.mergeOutputNameDirty = false;
  }
}

function applyHandwritingResponse(response) {
  if (!response.ok) {
    renderHandwritingStatus(response.error || "파일을 확인할 수 없습니다.");
    return false;
  }
  const previous = state.handwriting;
  const selectionChanged = previous.source_name !== (response.source_name || null)
    || previous.target_name !== (response.target_name || null);
  const becameReady = !previous.ready && Boolean(response.ready);
  state.handwriting = {
    source_name: response.source_name || null,
    // 저장할 파일의 확장자를 이 값으로 정한다. 빠뜨리면 Notewise를 옮겨도 .sdocx로 나간다.
    source_format: response.source_format || null,
    target_name: response.target_name || null,
    ready: Boolean(response.ready),
    inspection: response.inspection || null,
    plan: (selectionChanged || becameReady || !previous.plan?.length)
      ? clonePagePlan(response.inspection?.plan)
      : previous.plan,
    analysis: response.analysis || {
      state: response.ready ? "ready" : "waiting",
      stage: response.ready ? "ready" : "waiting",
      message: "",
      error: null,
    },
  };
  if (selectionChanged) updateHandwritingOutputName(!state.handwritingOutputNameDirty);
  else updateHandwritingOutputName(false);
  renderHandwritingStatus();
  renderPageReview();
  scheduleHandwritingPoll();
  return true;
}

function scheduleHandwritingPoll() {
  clearTimeout(handwritingPollTimer);
  if (state.handwriting.analysis?.state !== "running") return;
  handwritingPollTimer = setTimeout(async () => {
    try {
      const response = await callApi("handwriting_status");
      applyHandwritingResponse(response);
    } catch (error) {
      renderHandwritingStatus(error.message);
      reportClientError(error);
    }
  }, 450);
}

async function retryHandwritingAnalysis() {
  refs.retryHandwriting.disabled = true;
  try {
    const response = await callApi("retry_handwriting_analysis");
    if (!applyHandwritingResponse(response)) toast(response.error, "error");
  } catch (error) {
    renderHandwritingStatus(error.message);
    reportClientError(error);
  } finally {
    refs.retryHandwriting.disabled = false;
  }
}

async function chooseHandwriting(kind) {
  setBusy(true, kind === "source" ? "필기 파일 업로드 중…" : "대상 PDF 업로드 중…");
  try {
    const method = kind === "source" ? "choose_handwriting_source" : "choose_handwriting_target";
    const response = await callApi(method);
    applyHandwritingResponse(response);
  } catch (error) {
    renderHandwritingStatus(error.message);
    reportClientError(error);
  } finally { setBusy(false); }
}

async function resetHandwritingTransfer() {
  try {
    const response = await callApi("reset_handwriting_transfer");
    state.handwritingOutputNameDirty = false;
    if (!applyHandwritingResponse(response)) toast(response.error, "error");
  } catch (error) {
    toast(error.message, "error");
    reportClientError(error);
  }
}

async function resetDocuments() {
  if (!state.documents.length) return;
  if (!window.confirm("문서 합치기에 올린 PDF를 모두 지웁니다. 필기 옮기기는 그대로 둡니다. 계속할까요?")) return;
  setBusy(true, "올린 문서를 지우는 중…");
  try {
    const response = await callApi("reset_documents");
    if (!response.ok) throw new Error(response.error);
    state.documents = [];
    state.selected.clear();
    state.order = [];
    state.orderDirty = false;
    state.mergeOutputNameDirty = false;
    state.active = null;
    state.thumbnailCache.clear();
    state.thumbnailCacheBytes = 0;
    updateMergeOutputName(true);
    syncOrder();
    render();
    toast("올린 문서를 모두 지웠습니다.", "success");
  } catch (error) {
    toast(error.message, "error");
    reportClientError(error);
  } finally { setBusy(false); }
}

async function saveHandwritingTransfer() {
  if (!state.handwriting.ready) return;
  const base = (state.handwriting.target_name || "새-문서.pdf").replace(/\.pdf$/i, "");
  const outputExtension = state.handwriting.source_format === "notewise" ? ".notewise" : ".sdocx";
  const requestedName = withoutKnownExtension(refs.handwritingOutputName.value.trim()) || `${base}-필기`;
  const unconfirmedSlots = state.handwriting.plan.filter((slot) => !slot.confirmed);
  const unconfirmed = unconfirmedSlots.length;
  const unconfirmedPages = unconfirmedSlots.map((slot) => [
    slot.source_index === null ? "" : `원본 ${slot.source_index + 1}쪽`,
    slot.target_index === null ? "" : `새 PDF ${slot.target_index + 1}쪽`,
  ].filter(Boolean).join(" ↔ ")).join(", ");
  if (unconfirmed && !window.confirm(
    `확인하지 않은 쪽 대응이 ${unconfirmed}개 남아 있습니다 (${unconfirmedPages}). 그대로 필기를 옮길까요?`,
  )) return;
  setBusy(true, "필기와 형광펜을 새 PDF로 옮기는 중…");
  try {
    const response = await callApi("save_handwriting_transfer",
      `${requestedName}${outputExtension}`,
      state.handwriting.plan,
      unconfirmed > 0,
    );
    if (!response.ok) throw new Error(response.error);
    if (response.cancelled) return;
    toast(`필기 문서를 저장했습니다.\n${response.result.path}`, "success");
  } catch (error) {
    renderHandwritingStatus(error.message);
    reportClientError(error);
  } finally { setBusy(false); }
}

function documentById(id) { return state.documents.find((doc) => doc.id === id); }
function defaultOrder() {
  return state.documents.flatMap((doc) => doc.pages
    .filter((page) => state.selected.has(pageKey(doc.id, page.index)))
    .map((page) => ({ document_id: doc.id, page_index: page.index })));
}

function syncOrder() {
  const valid = new Set(defaultOrder().map(refKey));
  state.order = state.order.filter((ref) => valid.has(refKey(ref)));
  if (!state.orderDirty) {
    state.order = defaultOrder();
  } else {
    const present = new Set(state.order.map(refKey));
    defaultOrder().forEach((ref) => { if (!present.has(refKey(ref))) state.order.push(ref); });
  }
}

function formatRanges(indices) {
  const pages = [...new Set(indices)].sort((a, b) => a - b).map((value) => value + 1);
  if (!pages.length) return "";
  const chunks = [];
  let start = pages[0], previous = pages[0];
  pages.slice(1).forEach((page) => {
    if (page === previous + 1) { previous = page; return; }
    chunks.push(start === previous ? `${start}` : `${start}-${previous}`);
    start = previous = page;
  });
  chunks.push(start === previous ? `${start}` : `${start}-${previous}`);
  return chunks.join(", ");
}

function cachedImage(key) {
  const value = state.thumbnailCache.get(key);
  if (!value) return null;
  state.thumbnailCache.delete(key);
  state.thumbnailCache.set(key, value);
  return value;
}

function cacheImage(key, value) {
  const previous = state.thumbnailCache.get(key);
  if (previous) state.thumbnailCacheBytes -= previous.length;
  state.thumbnailCache.delete(key);
  state.thumbnailCache.set(key, value);
  state.thumbnailCacheBytes += value.length;
  while (state.thumbnailCacheBytes > CLIENT_IMAGE_CACHE_BYTES && state.thumbnailCache.size) {
    const oldest = state.thumbnailCache.keys().next().value;
    const removed = state.thumbnailCache.get(oldest);
    state.thumbnailCache.delete(oldest);
    state.thumbnailCacheBytes -= removed?.length || 0;
  }
}

function dropCachedImage(key) {
  const value = state.thumbnailCache.get(key);
  if (value) state.thumbnailCacheBytes -= value.length;
  state.thumbnailCache.delete(key);
}

async function requestImage(docId, pageIndex, kind) {
  const delays = [0, 350, 900];
  let lastError = null;
  for (let attempt = 0; attempt < delays.length; attempt += 1) {
    if (delays[attempt]) await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
    const response = await callApi("page_image", docId, pageIndex, kind);
    if (response.ok) return response.image;
    lastError = new Error(response.error || `HTTP ${response.http_status || "오류"}`);
    const retryable = state.runtime === "web" && [502, 503, 504].includes(response.http_status);
    if (!retryable) break;
  }
  throw lastError || new Error("미리보기를 불러오지 못했습니다.");
}

async function loadImage(docId, pageIndex, kind = "thumbnail") {
  const key = `${docId}:${pageIndex}:${kind}`;
  const cached = cachedImage(key);
  if (cached) return cached;
  if (state.imageInflight.has(key)) return state.imageInflight.get(key);
  const enqueue = kind === "preview" ? queuePreviewRequest : queueThumbnailRequest;
  const pending = enqueue(() => requestImage(docId, pageIndex, kind))
    .then((image) => {
      if (documentById(docId)) cacheImage(key, image);
      return image;
    })
    .finally(() => state.imageInflight.delete(key));
  state.imageInflight.set(key, pending);
  return pending;
}

function makeThumbnail(doc, page, className = "") {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = `page-tile ${className}`;
  tile.dataset.key = pageKey(doc.id, page.index);
  tile.dataset.documentId = doc.id;
  tile.dataset.pageIndex = String(page.index);
  tile.title = `${doc.name} · ${page.number}쪽`;
  tile.innerHTML = `<span class="image-placeholder"></span><span class="selection-check">✓</span><span class="page-number">${page.number}</span>`;
  return tile;
}

function showInlineImageError(node, placeholder, retry) {
  placeholder.classList.add("image-load-error");
  placeholder.textContent = "미리보기 실패 · 다시 시도";
  placeholder.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    placeholder.classList.remove("image-load-error");
    placeholder.textContent = "미리보기 준비 중…";
    retry();
  }, { once: true });
}

async function loadThumbnailNode(node) {
  if (!node?.isConnected || node.dataset.imageLoaded === "true" || node.dataset.imageLoading === "true") return;
  node.dataset.imageLoading = "true";
  const placeholder = node.querySelector(".image-placeholder, .result-image-placeholder");
  try {
    const pageIndex = Number(node.dataset.pageIndex);
    const src = await loadImage(node.dataset.documentId, pageIndex, "thumbnail");
    if (!node.isConnected) return;
    const image = new Image();
    image.className = node.classList.contains("result-item") ? "result-thumb" : "";
    image.alt = node.classList.contains("result-item") ? "" : `${pageIndex + 1}쪽`;
    image.src = src;
    placeholder?.replaceWith(image);
    node.dataset.imageLoaded = "true";
  } catch (_error) {
    if (placeholder?.isConnected) {
      showInlineImageError(node, placeholder, () => loadThumbnailNode(node));
    }
  } finally {
    delete node.dataset.imageLoading;
  }
}

function lazyImageObserver(root) {
  return new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      loadThumbnailNode(entry.target);
    });
  }, { root, rootMargin: "350px 0px" });
}

function renderDocuments() {
  state.sourceThumbnailObserver?.disconnect();
  state.sourceThumbnailObserver = lazyImageObserver(refs.documentList);
  refs.documentCount.textContent = state.documents.length;
  refs.sourceEmpty.hidden = state.documents.length > 0;
  refs.documentList.hidden = state.documents.length === 0;
  refs.documentList.replaceChildren();

  state.documents.forEach((doc) => {
    const card = document.createElement("article");
    card.className = "document-card";
    card.dataset.documentId = doc.id;
    const selectedIndices = doc.pages.filter((page) => state.selected.has(pageKey(doc.id, page.index))).map((page) => page.index);
    card.innerHTML = `
      <div class="document-card-header">
        <div class="document-title"><strong title="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</strong><span class="document-stats">${doc.page_count}쪽 · ${selectedIndices.length}쪽 선택</span></div>
        <button class="icon-button remove-document" type="button" title="문서 제거">×</button>
        <div class="range-row">
          <input class="range-input" value="${formatRanges(selectedIndices)}" aria-label="쪽 범위" placeholder="예: -3, 7, 10-" />
          <button class="mini-button all-pages" type="button">전체</button>
          <button class="mini-button no-pages" type="button">해제</button>
        </div>
        <div class="range-error"></div>
      </div>
      <div class="thumbnail-grid"></div>`;
    card.querySelector(".remove-document").addEventListener("click", () => removeDocument(doc.id));
    card.querySelector(".all-pages").addEventListener("click", () => setDocumentSelection(doc, doc.pages.map((page) => page.index)));
    card.querySelector(".no-pages").addEventListener("click", () => setDocumentSelection(doc, []));
    const input = card.querySelector(".range-input");
    input.addEventListener("change", () => applyRange(doc, input, card.querySelector(".range-error")));
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") input.blur(); });
    const grid = card.querySelector(".thumbnail-grid");
    doc.pages.forEach((page) => {
      const tile = makeThumbnail(doc, page, state.selected.has(pageKey(doc.id, page.index)) ? "selected" : "");
      tile.addEventListener("click", () => togglePage(doc, page, tile));
      grid.append(tile);
      state.sourceThumbnailObserver.observe(tile);
    });
    refs.documentList.append(card);
  });
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value;
  return node.innerHTML;
}

function setDocumentSelection(doc, indices) {
  doc.pages.forEach((page) => state.selected.delete(pageKey(doc.id, page.index)));
  indices.forEach((index) => state.selected.add(pageKey(doc.id, index)));
  syncOrder();
  updateDocumentSelectionUi(doc);
  updatePreviewSelection();
  renderResult();
  renderSummary();
}

function updateDocumentSelectionUi(doc) {
  const card = [...refs.documentList.querySelectorAll(".document-card")]
    .find((node) => node.dataset.documentId === doc.id);
  if (!card) return;
  const selectedIndices = doc.pages
    .filter((page) => state.selected.has(pageKey(doc.id, page.index)))
    .map((page) => page.index);
  card.querySelector(".document-stats").textContent = `${doc.page_count}쪽 · ${selectedIndices.length}쪽 선택`;
  card.querySelector(".range-input").value = formatRanges(selectedIndices);
  card.querySelectorAll(".page-tile").forEach((tile) => {
    tile.classList.toggle("selected", state.selected.has(tile.dataset.key));
  });
}

async function applyRange(doc, input, errorNode) {
  try {
    const response = await callApi("parse_range", input.value, doc.page_count);
    if (!response.ok) {
      errorNode.textContent = response.error;
      input.focus();
      return;
    }
    errorNode.textContent = "";
    setDocumentSelection(doc, response.indices);
  } catch (error) {
    errorNode.textContent = error.message;
    reportClientError(error);
  }
}

function togglePage(doc, page, tile) {
  const key = pageKey(doc.id, page.index);
  if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
  syncOrder();
  tile.classList.toggle("selected", state.selected.has(key));
  updateDocumentSelectionUi(doc);
  updatePreviewSelection(key);
  showPreview(doc.id, page.index, "원본 미리보기");
  renderResult();
  renderSummary();
}

function previewNode(docId, pageIndex) {
  const key = pageKey(docId, pageIndex);
  return [...refs.previewPages.querySelectorAll(".preview-page")]
    .find((node) => node.dataset.key === key);
}

function setActivePreview(docId, pageIndex, origin = "전체 페이지 미리보기") {
  const doc = documentById(docId);
  if (!doc) return;
  state.active = { document_id: docId, page_index: pageIndex };
  refs.previewEyebrow.textContent = origin === "결과 미리보기" ? "RESULT PREVIEW" : "ALL PAGES";
  refs.previewHeading.textContent = "전체 페이지 미리보기";
  refs.previewMeta.textContent = `${doc.name} · ${pageIndex + 1}쪽`;
  refs.previewPages.querySelectorAll(".preview-page.active").forEach((node) => node.classList.remove("active"));
  previewNode(docId, pageIndex)?.classList.add("active");
  document.querySelectorAll(".result-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.key === pageKey(docId, pageIndex));
  });
}

async function loadPreviewPage(node) {
  if (!node || node.dataset.loaded === "true" || node.dataset.loading === "true") return;
  node.dataset.loading = "true";
  const docId = node.dataset.documentId;
  const pageIndex = Number(node.dataset.pageIndex);
  try {
    const src = await loadImage(docId, pageIndex, "preview");
    if (!node.isConnected || node.dataset.previewNear !== "true") {
      dropCachedImage(`${docId}:${pageIndex}:preview`);
      return;
    }
    const image = new Image();
    image.alt = `${pageIndex + 1}쪽 미리보기`;
    image.src = src;
    node.querySelector(".preview-page-placeholder").replaceWith(image);
    node.dataset.loaded = "true";
  } catch (error) {
    const placeholder = node.querySelector(".preview-page-placeholder");
    if (placeholder) {
      placeholder.classList.add("error");
      placeholder.textContent = "미리보기를 불러오지 못했습니다 · 다시 시도";
      placeholder.addEventListener("click", () => {
        placeholder.classList.remove("error");
        placeholder.textContent = "미리보기 준비 중…";
        loadPreviewPage(node);
      }, { once: true });
    }
  } finally {
    delete node.dataset.loading;
  }
}

function unloadPreviewPage(node) {
  if (node.dataset.loaded !== "true") return;
  const image = node.querySelector("img");
  if (image) {
    const placeholder = document.createElement("span");
    placeholder.className = "preview-page-placeholder";
    placeholder.textContent = "미리보기 준비 중…";
    image.replaceWith(placeholder);
  }
  dropCachedImage(`${node.dataset.documentId}:${node.dataset.pageIndex}:preview`);
  delete node.dataset.loaded;
}

function updatePreviewSelection(onlyKey = null) {
  refs.previewPages.querySelectorAll(".preview-page").forEach((node) => {
    if (onlyKey && node.dataset.key !== onlyKey) return;
    const selected = state.selected.has(node.dataset.key);
    node.classList.toggle("selected", selected);
    node.querySelector(".preview-page-state").textContent = selected ? "출력 포함" : "출력 제외";
  });
}

function updatePreviewFromScroll() {
  state.previewScrollFrame = 0;
  const stageRect = refs.previewStage.getBoundingClientRect();
  const pages = [...refs.previewPages.querySelectorAll(".preview-page")];
  let nearest = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  pages.forEach((node) => {
    const rect = node.getBoundingClientRect();
    if (rect.bottom < stageRect.top || rect.top > stageRect.bottom) return;
    const distance = Math.abs(rect.top - stageRect.top - 34);
    if (distance < nearestDistance) { nearest = node; nearestDistance = distance; }
  });
  if (nearest) setActivePreview(nearest.dataset.documentId, Number(nearest.dataset.pageIndex));
}

function renderPreviewPages() {
  state.previewObserver?.disconnect();
  refs.previewPages.replaceChildren();
  const hasPages = state.documents.some((doc) => doc.pages.length);
  refs.previewEmpty.hidden = hasPages;
  refs.previewPages.hidden = !hasPages;
  if (!hasPages) {
    refs.previewHeading.textContent = "미리보기";
    refs.previewMeta.textContent = "";
    return;
  }

  state.documents.forEach((doc) => {
    const label = document.createElement("div");
    label.className = "preview-document-label";
    label.textContent = `${doc.name} · ${doc.page_count}쪽`;
    refs.previewPages.append(label);
    doc.pages.forEach((page) => {
      const node = document.createElement("article");
      const key = pageKey(doc.id, page.index);
      node.className = `preview-page${state.selected.has(key) ? " selected" : ""}`;
      node.dataset.key = key;
      node.dataset.documentId = doc.id;
      node.dataset.pageIndex = page.index;
      node.innerHTML = `
        <div class="preview-page-header">
          <strong>${escapeHtml(doc.name)} · ${page.number}쪽</strong>
          <span class="preview-page-state">${state.selected.has(key) ? "출력 포함" : "출력 제외"}</span>
        </div>
        <div class="preview-sheet"><span class="preview-page-placeholder">미리보기 준비 중…</span></div>`;
      node.querySelector(".preview-sheet").style.aspectRatio = `${page.width} / ${page.height}`;
      refs.previewPages.append(node);
    });
  });

  state.previewObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      entry.target.dataset.previewNear = entry.isIntersecting ? "true" : "false";
      if (entry.isIntersecting) loadPreviewPage(entry.target);
      else unloadPreviewPage(entry.target);
    });
  }, { root: refs.previewStage, rootMargin: "900px 0px" });
  refs.previewPages.querySelectorAll(".preview-page").forEach((node) => state.previewObserver.observe(node));
  requestAnimationFrame(updatePreviewFromScroll);
}

function showPreview(docId, pageIndex, origin = "원본 미리보기") {
  const node = previewNode(docId, pageIndex);
  if (!node) return;
  setActivePreview(docId, pageIndex, origin);
  node.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderResult() {
  state.resultThumbnailObserver?.disconnect();
  state.resultThumbnailObserver = lazyImageObserver(refs.resultList);
  refs.pageCount.textContent = `${state.order.length}쪽`;
  refs.resultEmpty.hidden = state.order.length > 0;
  refs.resultList.hidden = state.order.length === 0;
  refs.resultList.replaceChildren();
  refs.resetOrder.disabled = !state.orderDirty || state.order.length < 2;

  state.order.forEach((ref, index) => {
    const doc = documentById(ref.document_id);
    if (!doc) return;
    const item = document.createElement("li");
    item.className = `result-item${state.active?.document_id === ref.document_id && state.active?.page_index === ref.page_index ? " active" : ""}`;
    item.draggable = true;
    item.dataset.index = index;
    item.dataset.key = refKey(ref);
    item.dataset.documentId = ref.document_id;
    item.dataset.pageIndex = String(ref.page_index);
    item.innerHTML = `<span class="result-image-placeholder"></span><div class="result-label"><strong>${escapeHtml(doc.name)}</strong><span>원본 ${ref.page_index + 1}쪽</span></div><span class="drag-handle" aria-hidden="true">⠿</span>`;
    item.addEventListener("click", () => {
      document.querySelectorAll(".result-item.active").forEach((node) => node.classList.remove("active"));
      item.classList.add("active");
      showPreview(ref.document_id, ref.page_index, "결과 미리보기");
    });
    item.addEventListener("dragstart", (event) => {
      item.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", `${index}`);
    });
    item.addEventListener("dragend", () => {
      item.classList.remove("dragging");
      document.querySelectorAll(".drop-before, .drop-after").forEach((node) => node.classList.remove("drop-before", "drop-after"));
    });
    item.addEventListener("dragover", (event) => {
      event.preventDefault();
      const before = event.offsetY < item.offsetHeight / 2;
      item.classList.toggle("drop-before", before);
      item.classList.toggle("drop-after", !before);
    });
    item.addEventListener("dragleave", () => item.classList.remove("drop-before", "drop-after"));
    item.addEventListener("drop", (event) => {
      event.preventDefault();
      const from = Number(event.dataTransfer.getData("text/plain"));
      let to = index + (item.classList.contains("drop-after") ? 1 : 0);
      item.classList.remove("drop-before", "drop-after");
      if (!Number.isInteger(from) || from < 0 || from >= state.order.length) return;
      const [moved] = state.order.splice(from, 1);
      if (from < to) to -= 1;
      state.order.splice(Math.max(0, Math.min(to, state.order.length)), 0, moved);
      state.orderDirty = true;
      renderResult();
    });
    refs.resultList.append(item);
    state.resultThumbnailObserver.observe(item);
  });
}

function renderSummary() {
  refs.selectionSummary.textContent = !state.bridgeReady
    ? (state.bridgeFailed ? "바로가기로 다시 실행해 주세요" : "앱 연결 중…")
    : (state.documents.length
      ? `${state.mergePlan?.title ? `${state.mergePlan.title} · ` : ""}${state.documents.length}개 문서에서 ${state.order.length}쪽 선택`
      : "PDF를 추가해 시작하세요");
  refs.save.disabled = !state.bridgeReady || state.order.length === 0 || state.mergePlan?.mode === "review";
  refs.mergeOutputName.disabled = Boolean(state.mergePlan) || state.documents.length === 0;
  refs.resetDocuments.disabled = !state.bridgeReady || state.documents.length === 0 || state.mergePlan?.mode === "review";
}

function render() { renderDocuments(); renderPreviewPages(); renderResult(); renderSummary(); }

async function addPdfs() {
  setBusy(true, "PDF를 확인하는 중…");
  try {
    const response = await callApi("choose_pdfs");
    if (!response.ok) throw new Error(response.error);
    if (!response.added.length) return;
    response.added.forEach((doc) => doc.pages.forEach((page) => state.selected.add(pageKey(doc.id, page.index))));
    state.documents = response.sources;
    updateMergeOutputName(false);
    syncOrder();
    render();
    const first = response.added[0];
    showPreview(first.id, 0, "원본 미리보기");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(false); }
}

async function removeDocument(id) {
  try {
    const response = await callApi("remove_document", id);
    if (!response.ok) { toast(response.error, "error"); return; }
    state.documents = state.documents.filter((doc) => doc.id !== id);
    updateMergeOutputName(false);
    [...state.selected].filter((key) => key.startsWith(`${id}:`)).forEach((key) => state.selected.delete(key));
    state.order = state.order.filter((ref) => ref.document_id !== id);
    [...state.thumbnailCache.keys()]
      .filter((key) => key.startsWith(`${id}:`))
      .forEach(dropCachedImage);
    if (state.active?.document_id === id) {
      state.active = null;
    }
    syncOrder(); render();
  } catch (error) {
    toast(error.message, "error");
    reportClientError(error);
  }
}

async function saveResult() {
  if (!state.order.length) return;
  const firstDoc = documentById(state.order[0].document_id);
  const fallback = firstDoc ? `${withoutKnownExtension(firstDoc.name)}-편집본` : "조합된 문서";
  const suggestion = `${withoutKnownExtension(refs.mergeOutputName.value.trim()) || fallback}.pdf`;
  setBusy(true, "원본 품질로 PDF를 저장하는 중…");
  try {
    const response = await callApi("save_result", state.order, suggestion);
    if (!response.ok) throw new Error(response.error);
    if (response.cancelled) return;
    const warnings = response.result.warnings || [];
    toast(`${response.result.page_count}쪽을 저장했습니다.\n${response.result.path}`, "success");
    warnings.forEach((warning) => toast(warning));
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(false); }
}

refs.add.addEventListener("click", addPdfs);
refs.emptyAdd.addEventListener("click", addPdfs);
refs.save.addEventListener("click", saveResult);
refs.sourceReviewApply.addEventListener("click", () => {
  const decision = state.mergePlan?.origin === "merged" ? "merge" : "refresh";
  void finishSourceReview(decision);
});
refs.sourceReviewSkip.addEventListener("click", () => { void finishSourceReview("skip"); });
refs.mergeOutputName.addEventListener("input", () => { state.mergeOutputNameDirty = true; });
refs.handwritingOutputName.addEventListener("input", () => { state.handwritingOutputNameDirty = true; });
refs.mergeTab.addEventListener("click", () => showTool("merge"));
refs.handwriting.addEventListener("click", () => showTool("handwriting"));
refs.reviewInkToggle.addEventListener("change", () => {
  refs.handwritingReview.classList.toggle("hide-ink", !refs.reviewInkToggle.checked);
});
refs.chooseHandwritingSource.addEventListener("click", () => chooseHandwriting("source"));
refs.chooseHandwritingTarget.addEventListener("click", () => chooseHandwriting("target"));
refs.retryHandwriting.addEventListener("click", retryHandwritingAnalysis);
refs.resetHandwriting.addEventListener("click", resetHandwritingTransfer);
refs.resetDocuments.addEventListener("click", resetDocuments);
refs.saveHandwriting.addEventListener("click", saveHandwritingTransfer);
refs.resetOrder.addEventListener("click", () => { state.orderDirty = false; state.order = defaultOrder(); renderResult(); });
refs.previewStage.addEventListener("scroll", () => {
  if (state.previewScrollFrame) return;
  state.previewScrollFrame = requestAnimationFrame(updatePreviewFromScroll);
}, { passive: true });

document.addEventListener("keydown", async (event) => {
  if (event.key !== "F11" || state.runtime !== "desktop") return;
  event.preventDefault();
  const response = await callApi("toggle_fullscreen");
  if (!response.ok) toast(response.error, "error");
});

window.addEventListener("error", (event) => reportClientError(event.error || event.message));
window.addEventListener("unhandledrejection", (event) => reportClientError(event.reason));
window.addEventListener("pywebviewready", initializeBridge);

setBridgeState(false, false);
showTool("merge");
renderHandwritingStatus();
if (window.pywebview?.api || state.runtime === "web") initializeBridge();
setTimeout(() => {
  if (!state.bridgeReady) setBridgeState(false, true);
}, 6000);

if (window.location.protocol.startsWith("http") && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((error) => {
      console.warn("NotEditor PWA service worker registration failed", error);
    });
  });
}
