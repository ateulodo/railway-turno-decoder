"use strict";

// Delegation keeps interactions available after an HTMX error-page swap.
function updateFile(input) {
  const file = input.files[0];
  const label = document.getElementById("file-name");
  const error = document.getElementById("network-error");
  if (error) error.hidden = true;
  if (label) label.textContent = file ? file.name : "Nessun file selezionato";
  input.setCustomValidity("");
  if (file && !file.name.toLowerCase().endsWith(".pdf")) {
    input.setCustomValidity("Seleziona un file con estensione .pdf.");
  } else if (file && file.size > Number(input.dataset.maxSize)) {
    input.setCustomValidity("Il PDF supera la dimensione massima consentita.");
  }
}

document.addEventListener("change", (event) => {
  if (event.target.id === "pdf-file") updateFile(event.target);
});

for (const name of ["dragenter", "dragover", "dragleave", "drop"]) {
  document.addEventListener(name, (event) => {
    const zone = event.target instanceof Element ? event.target.closest("#drop-zone") : null;
    if (!zone) return;
    event.preventDefault();
    zone.classList.toggle("is-dragging", name === "dragenter" || name === "dragover");
    if (name === "drop") {
      const input = document.getElementById("pdf-file");
      const files = event.dataTransfer.files;
      if (files.length !== 1) {
        const error = document.getElementById("network-error");
        error.textContent = "Trascina un solo PDF alla volta.";
        error.hidden = false;
        return;
      }
      input.files = files;
      updateFile(input);
    }
  });
}

document.addEventListener("htmx:beforeSwap", (event) => {
  if (event.detail.xhr.status >= 400 && event.detail.xhr.status <= 599) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});

document.addEventListener("htmx:afterSwap", () => {
  document.getElementById("page-content")?.focus({ preventScroll: true });
});

document.addEventListener("htmx:sendError", () => {
  const error = document.getElementById("network-error");
  if (error) {
    error.textContent = "Connessione interrotta. Verifica la connessione e riprova.";
    error.hidden = false;
  }
});
