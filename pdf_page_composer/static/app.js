"use strict";

const state = {
  documents: [],
  selected: new Set(),
  order: [],
  orderDirty: false,
  active: null,
  thumbnailCache: new Map(),
  previewToken: 0,
};

const $ = (selector) => document.querySelector(selector);
const refs = {
  add: $("#addPdfButton"), emptyAdd: $("#emptyAddButton"), save: $("#saveButton"),
  resetOrder: $("#resetOrderButton"), sourceEmpty: $("#sourceEmpty"),
  documentList: $("#documentList"), documentCount: $("#documentCount"),
  resultEmpty: $("#resultEmpty"), resultList: $("#resultList"), pageCount: $("#pageCount"),
  selectionSummary: $("#selectionSummary"), previewEmpty: $("#previewEmpty"),
  previewImage: $("#previewImage"), previewLoader: $("#previewLoader"),
  previewEyebrow: $("#previewEyebrow"), previewHeading: $("#previewHeading"),
  previewMeta: $("#previewMeta"), busy: $("#busyOverlay"), busyText: $("#busyText"),
  toastRegion: $("#toastRegion"),
};

const pageKey = (docId, index) => `${docId}:${index}`;
const refKey = (ref) => pageKey(ref.document_id, ref.page_index);
const api = () => window.pywebview?.api;

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

async function loadImage(docId, pageIndex, kind = "thumbnail") {
  const key = `${docId}:${pageIndex}:${kind}`;
  if (state.thumbnailCache.has(key)) return state.thumbnailCache.get(key);
  const response = await api().page_image(docId, pageIndex, kind);
  if (!response.ok) throw new Error(response.error);
  state.thumbnailCache.set(key, response.image);
  return response.image;
}

function makeThumbnail(doc, page, className = "") {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = `page-tile ${className}`;
  tile.dataset.key = pageKey(doc.id, page.index);
  tile.title = `${doc.name} · ${page.number}쪽`;
  tile.innerHTML = `<span class="image-placeholder"></span><span class="selection-check">✓</span><span class="page-number">${page.number}</span>`;
  requestAnimationFrame(async () => {
    try {
      const src = await loadImage(doc.id, page.index);
      if (!tile.isConnected) return;
      const image = new Image();
      image.alt = `${page.number}쪽`;
      image.src = src;
      tile.querySelector(".image-placeholder").replaceWith(image);
    } catch (error) { toast(error.message, "error"); }
  });
  return tile;
}

function renderDocuments() {
  refs.documentCount.textContent = state.documents.length;
  refs.sourceEmpty.hidden = state.documents.length > 0;
  refs.documentList.hidden = state.documents.length === 0;
  refs.documentList.replaceChildren();

  state.documents.forEach((doc) => {
    const card = document.createElement("article");
    card.className = "document-card";
    const selectedIndices = doc.pages.filter((page) => state.selected.has(pageKey(doc.id, page.index))).map((page) => page.index);
    card.innerHTML = `
      <div class="document-card-header">
        <div class="document-title"><strong title="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</strong><span>${doc.page_count}쪽 · ${selectedIndices.length}쪽 선택</span></div>
        <button class="icon-button remove-document" type="button" title="문서 제거">×</button>
        <div class="range-row">
          <input class="range-input" value="${formatRanges(selectedIndices)}" aria-label="쪽 범위" placeholder="예: 1-3, 7, 10-12" />
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
      tile.addEventListener("click", () => togglePage(doc, page));
      grid.append(tile);
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
  render();
}

async function applyRange(doc, input, errorNode) {
  const response = await api().parse_range(input.value, doc.page_count);
  if (!response.ok) {
    errorNode.textContent = response.error;
    input.focus();
    return;
  }
  errorNode.textContent = "";
  setDocumentSelection(doc, response.indices);
}

function togglePage(doc, page) {
  const key = pageKey(doc.id, page.index);
  if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
  syncOrder();
  showPreview(doc.id, page.index, "원본 미리보기");
  renderDocuments();
  renderResult();
  renderSummary();
}

async function showPreview(docId, pageIndex, origin = "원본 미리보기") {
  const doc = documentById(docId);
  if (!doc) return;
  const token = ++state.previewToken;
  state.active = { document_id: docId, page_index: pageIndex };
  refs.previewEmpty.hidden = true;
  refs.previewImage.hidden = true;
  refs.previewLoader.hidden = false;
  refs.previewEyebrow.textContent = origin === "결과 미리보기" ? "RESULT PREVIEW" : "SOURCE PREVIEW";
  refs.previewHeading.textContent = origin;
  refs.previewMeta.textContent = `${doc.name} · ${pageIndex + 1}쪽`;
  document.querySelectorAll(".result-item.active").forEach((node) => node.classList.remove("active"));
  try {
    const src = await loadImage(docId, pageIndex, "preview");
    if (token !== state.previewToken) return;
    refs.previewImage.src = src;
    refs.previewLoader.hidden = true;
    refs.previewImage.hidden = false;
  } catch (error) {
    if (token !== state.previewToken) return;
    refs.previewLoader.hidden = true;
    refs.previewEmpty.hidden = false;
    toast(error.message, "error");
  }
}

function renderResult() {
  refs.pageCount.textContent = `${state.order.length}쪽`;
  refs.resultEmpty.hidden = state.order.length > 0;
  refs.resultList.hidden = state.order.length === 0;
  refs.resultList.replaceChildren();
  refs.resetOrder.disabled = !state.orderDirty || state.order.length < 2;

  state.order.forEach((ref, index) => {
    const doc = documentById(ref.document_id);
    if (!doc) return;
    const item = document.createElement("li");
    item.className = "result-item";
    item.draggable = true;
    item.dataset.index = index;
    item.innerHTML = `<span class="result-image-placeholder"></span><div class="result-label"><strong>${escapeHtml(doc.name)}</strong><span>원본 ${ref.page_index + 1}쪽</span></div><span class="drag-handle" aria-hidden="true">⠿</span>`;
    requestAnimationFrame(async () => {
      try {
        const src = await loadImage(ref.document_id, ref.page_index);
        if (!item.isConnected) return;
        const image = new Image(); image.className = "result-thumb"; image.alt = ""; image.src = src;
        item.querySelector(".result-image-placeholder").replaceWith(image);
      } catch (error) { toast(error.message, "error"); }
    });
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
  });
}

function renderSummary() {
  refs.selectionSummary.textContent = state.documents.length
    ? `${state.documents.length}개 문서에서 ${state.order.length}쪽 선택`
    : "PDF를 추가해 시작하세요";
  refs.save.disabled = state.order.length === 0;
}

function render() { renderDocuments(); renderResult(); renderSummary(); }

async function addPdfs() {
  setBusy(true, "PDF를 확인하는 중…");
  try {
    const response = await api().choose_pdfs();
    if (!response.ok) throw new Error(response.error);
    if (!response.added.length) return;
    response.added.forEach((doc) => doc.pages.forEach((page) => state.selected.add(pageKey(doc.id, page.index))));
    state.documents = response.sources;
    syncOrder();
    render();
    const first = response.added[0];
    showPreview(first.id, 0, "원본 미리보기");
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(false); }
}

async function removeDocument(id) {
  const response = await api().remove_document(id);
  if (!response.ok) { toast(response.error, "error"); return; }
  state.documents = state.documents.filter((doc) => doc.id !== id);
  [...state.selected].filter((key) => key.startsWith(`${id}:`)).forEach((key) => state.selected.delete(key));
  state.order = state.order.filter((ref) => ref.document_id !== id);
  state.thumbnailCache.forEach((_, key) => { if (key.startsWith(`${id}:`)) state.thumbnailCache.delete(key); });
  if (state.active?.document_id === id) {
    state.active = null; refs.previewImage.hidden = true; refs.previewEmpty.hidden = false;
    refs.previewHeading.textContent = "미리보기"; refs.previewMeta.textContent = "";
  }
  syncOrder(); render();
}

async function saveResult() {
  if (!state.order.length) return;
  const firstDoc = documentById(state.order[0].document_id);
  const suggestion = state.documents.length === 1
    ? `${firstDoc.name.replace(/\.pdf$/i, "")}-선택.pdf`
    : "조합된 문서.pdf";
  setBusy(true, "원본 품질로 PDF를 저장하는 중…");
  try {
    const response = await api().save_result(state.order, suggestion);
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
refs.resetOrder.addEventListener("click", () => { state.orderDirty = false; state.order = defaultOrder(); renderResult(); });

window.addEventListener("pywebviewready", () => render());
