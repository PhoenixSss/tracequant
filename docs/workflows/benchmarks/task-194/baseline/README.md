# Baseline replay material

The acceptance test runs each frozen fixture twice through the current
production-shaped `Inspect → Reason → Judge → Report` protocol and writes four
bounded `review-benchmark-run-receipt` JSON documents into its temporary test
output directory. The receipt writer is public so a maintainer can preserve a
run outside the source tree when a provider-backed baseline is executed.

`expected-results.json` records the stable semantic baseline without random Run
IDs or machine-dependent wall-clock values. Every actual receipt still records
fixture identity, Harness SHA, protocol version, model/config, token usage,
wall-clock, candidate findings, verdict, and cleanup/isolation proofs.

Receipts are evaluation evidence, not LCK lifecycle authority. Raw model
transcripts, credentials, and unbounded environment data are excluded.
