"""Content identity of M3A, canonicalizing only unordered provenance lists."""

import json
from hashlib import sha256


def temporal_identity(temporal):
    def canonical(value, key=""):
        if isinstance(value, dict):
            return {k: canonical(v, k) for k, v in value.items()}
        if isinstance(value, list):
            values = [canonical(v) for v in value]
            if key in {"support_refs", "primitive_refs", "word_indices", "rejected_word_indices"}:
                values.sort(key=lambda v: json.dumps(v, sort_keys=True))
            return values
        return value

    return sha256(
        json.dumps(
            canonical(temporal.model_dump(mode="json")),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
