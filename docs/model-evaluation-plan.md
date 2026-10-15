# Credentialed model evaluation plan

Verified against official OpenAI documentation on 2026-08-08.

## Selected target

Use `gpt-5.6-terra` for all three benchmark variants. OpenAI positions Terra as
the GPT-5.6 tier that balances intelligence and cost, and it supports the
Responses API plus Structured Outputs required by this runner. Keeping one
model fixed across `single_prompt`, `rag`, and `validated_agent` isolates the
effect of orchestration and grounding instead of confounding it with a model
change.

Current standard API pricing below 272K input tokens is:

- input: **$2.50 per 1M tokens**;
- cached input: **$0.25 per 1M tokens**;
- output: **$15.00 per 1M tokens**.

Sources:

- [OpenAI latest-model guide](https://developers.openai.com/api/docs/guides/latest-model.md)
- [GPT-5.6 Terra model page](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [OpenAI API pricing](https://openai.com/api/pricing/)

Pricing is time-sensitive and is therefore passed explicitly to the runner,
not embedded as a code default.

## Safe execution sequence

1. Configure `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-5.6-terra` locally.
2. Preview three calls without network or cost:

   ```bash
   python evals/run_model_benchmark.py \
     --plan \
     --limit 3 \
     --variants single_prompt \
     --input-usd-per-million 2.50 \
     --output-usd-per-million 15.00
   ```

3. After explicit cost confirmation, run the three-call smoke test:

   ```bash
   python evals/run_model_benchmark.py \
     --confirm-api-cost \
     --limit 3 \
     --variants single_prompt \
     --input-usd-per-million 2.50 \
     --output-usd-per-million 15.00
   ```

4. Rerun `--plan --limit 150` with the same prices. The planner uses measured
   smoke-test token usage to estimate the remaining 447 calls.
5. Run all three 150-case variants only after separately approving that
   measured estimate.

The runner stores `store: false`, response IDs, token usage, latency, per-case
predictions, model ID, dataset/corpus hashes, and a revision-bound checkpoint.
It will not reuse checkpoints from another model or benchmark revision.

## Completed run — 2026-08-08

The approved run completed all 450 calls (`150 cases × 3 variants`) with
`gpt-5.6-terra`. Results are checked in at
[`evals/results/model_benchmark.json`](../evals/results/model_benchmark.json)
and the per-case checkpoints at
[`evals/results/model_benchmark_cases.jsonl`](../evals/results/model_benchmark_cases.jsonl).

| Variant | Root-cause Top-1 | Top-3 | Grounded | Citation faithful | Mean request latency | Cost / incident |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Single prompt | 19.3% | 46.0% | 0.0% | 0.0% | 2.633 s | $0.00318 |
| RAG | 64.0% | 99.3% | 99.3% | 100% | 2.407 s | $0.00389 |
| Validated Agent | 81.3% | 98.7% | 100% | 100% | 2.785 s | $0.00468 |

Total measured token cost was **$1.76228**. Every variant recorded a 100%
destructive-action guardrail rate and zero unguarded destructive actions. The
validated Agent gained 62.0 percentage points of Top-1 accuracy over the
single-prompt baseline. It did not reduce latency or cost, so no optimization
claim is made.

The original runner measured latency before acquiring its concurrency-2 FIFO
semaphore, which included batch queue wait. The raw value remains in each case
as `usage.queued_batch_latency_ms`; `usage.latency_ms` is reconstructed from
the measured common-origin completion times and FIFO schedule. The aggregate
file records this provenance. Future calls measure elapsed time directly inside
the semaphore.
