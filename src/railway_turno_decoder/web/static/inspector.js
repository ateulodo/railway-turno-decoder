"use strict";
const words = JSON.parse(document.getElementById("raw-words").textContent);
const select = document.getElementById("token-select");
function showWord(index) {
  const word = words[Number(index)];
  document.getElementById("token-details").textContent = index !== "" && word
    ? JSON.stringify(word, null, 2) : "Seleziona un token.";
  document.querySelectorAll(".word-box.selected").forEach(el => el.classList.remove("selected"));
  if (index !== "" && word) {
    document.querySelector(`[data-word="${word.word_index}"]`).classList.add("selected");
  }
}
select.addEventListener("change", () => showWord(select.value));
document.querySelectorAll("[data-word]").forEach(el => el.addEventListener("click", () => {
  select.value = el.dataset.word;
  showWord(select.value);
}));
document.querySelectorAll("[data-layer]").forEach(el => el.addEventListener("change", () => {
  document.getElementById(`layer-${el.dataset.layer}`).toggleAttribute("hidden", !el.checked);
}));

const temporal = JSON.parse(document.getElementById("temporal-data").textContent);
function temporalDetails(rowIndex, value) {
  document.getElementById("token-details").textContent = JSON.stringify({
    source_extraction_id: temporal.source_extraction_id,
    source_sha256: temporal.source_sha256,
    page_index: temporal.page_index, row_index: rowIndex, ...value
  }, null, 2);
}
document.querySelectorAll("[data-segment]").forEach(el => el.addEventListener("click", () => {
  const [rowIndex, index] = el.dataset.segment.split(":").map(Number);
  const row = temporal.rows[rowIndex];
  temporalDetails(rowIndex, {segment: row.segments[index], axis: row.axis});
}));
document.querySelectorAll("[data-hour]").forEach(el => el.addEventListener("click", () => {
  const [rowIndex, index] = el.dataset.hour.split(":").map(Number);
  temporalDetails(rowIndex, {anchor: temporal.rows[rowIndex].axis.anchors[index]});
}));
document.getElementById("temporal-row")?.addEventListener("change", event => {
  document.querySelectorAll("[data-temporal-row]").forEach(el => {
    el.toggleAttribute("hidden", event.target.value !== "all" && el.dataset.temporalRow !== event.target.value);
  });
});
