"use strict";
const layoutData = JSON.parse(document.getElementById("layout-data").textContent);
document.querySelectorAll("[data-layout-kind]").forEach(el => el.addEventListener("click", () => {
  const row = layoutData?.rows[Number(el.dataset.layoutRow)];
  const value = row?.[el.dataset.layoutKind]?.[Number(el.dataset.layoutIndex)];
  if (!value) return;
  const index = Number(el.dataset.layoutIndex);
  const kind = {clusters:"CLUSTER",minute_tokens:"MINUTE",explicit_pairs:"PAIR",below_axis:"BELOW"}[el.dataset.layoutKind];
  const same = (a, b) => a.kind === b.kind && a.index === b.index;
  const nodes = kind ? [{kind, index}] : [value.source, value.target].filter(Boolean);
  const relations = row.relations.filter(r => nodes.some(n => same(r.source, n) || same(r.target, n)));
  const minuteIndices = new Set(relations.flatMap(r => [r.source, r.target])
    .filter(n => n.kind === "MINUTE").map(n => n.index));
  if (el.dataset.layoutKind === "explicit_pairs") {
    minuteIndices.add(value.start_token_index);
    minuteIndices.add(value.end_token_index);
  }
  document.getElementById("token-details").textContent = JSON.stringify({
    source_extraction_id: layoutData.source_extraction_id,
    source_sha256: layoutData.source_sha256,
    temporal_identity: layoutData.temporal_identity,
    page_index: layoutData.page_index, row_index: row.row_index,
    band_group_offsets: layoutData.band_group_offsets,
    band_phase_candidates: layoutData.band_phase_candidates,
    kind: el.dataset.layoutKind, value,
    candidate_relations: relations,
    related_minute_tokens: [...minuteIndices].sort((a, b) => a - b).map(i => row.minute_tokens[i])
  }, null, 2);
}));
