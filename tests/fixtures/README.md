# Test fixtures

`benchmark_smoke/` is a **test fixture**. It is not benchmark data, and its `gold/` files are not
benchmark gold. It contains 10 tiny synthetic cases. The transcripts were written by hand in
`make_smoke_fixture.py`, and the "gold" files were written by hand alongside them. No evaluator output
was ever used to produce them. The audio files are near-silent placeholder WAVs (1 kHz, 8-bit) and carry
no speech.

Purpose: to exercise every code path of the infrastructure end-to-end. That covers the three input
modes, OUT_OF_SCOPE, INCONCLUSIVE, a minimal pair, an attribution pair, judge bait with prompt
injection, a dangerous win, a clean loss, two languages and all four splits.

These cases must never be used to report evaluator quality.

## Regenerating

```bash
python tests/fixtures/make_smoke_fixture.py
ignosis-eval benchmark register-holdout --benchmark-root tests/fixtures/benchmark_smoke
ignosis-eval manifest build --benchmark-root tests/fixtures/benchmark_smoke \
    --dataset-name smoke-fixture --dataset-version fixture-1 --created-by fixture
ignosis-eval gold freeze --benchmark-root tests/fixtures/benchmark_smoke \
    --gold-version fixture-gold-1 --frozen-by fixture --labeling-protocol-version fixture-protocol/0
ignosis-eval benchmark check --benchmark-root tests/fixtures/benchmark_smoke --require-gold
```
