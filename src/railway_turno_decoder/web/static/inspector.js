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
