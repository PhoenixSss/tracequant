# TraceQuant Contracts

Stable cross-boundary schemas, messages, DTOs, serialization contracts, and
identifiers belong here. The current source package exposes the provider-neutral
Review vNext contracts from `tracequant.contracts`:

```python
from tracequant.contracts import ReviewEvidencePackage, ReviewRunReceipt
```

`ReviewEvidencePackage`, `ReviewSurfacePlan`, `CandidateFinding`,
`VerifiedFinding`, and `ReviewRunReceipt` are immutable versioned value
contracts. They carry compact summaries and evidence references; repository
trees, complete logs, and reviewer prose remain on-demand retrieval targets.

Contracts must remain independent from exchange SDKs, UI frameworks, model
providers, profiles, and deployment implementations.
