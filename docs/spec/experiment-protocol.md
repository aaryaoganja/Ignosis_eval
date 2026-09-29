# Experiment Protocol — FROZEN (contract `1.2.0-frozen`)

This protocol governs every benchmark run of K0, A, A+ and B. Metric definitions are in `scoring-spec.md`. Items marked `PENDING_HUMAN_SIGNOFF` must be resolved and recorded in the run manifest before the corresponding step can run.

---

## P-1. Data separation

| Split | Location | Who or what may read it | Used for |
|---|---|---|---|
| **dev** | `bench/dev/` inside the repository | Everyone, including the implementing agent | Tuning, debugging, threshold tuning (e.g., on G-02-N5) |
| **holdout** | `$BENCH_PRIVATE_DIR/holdout/`, outside the repository | Runner and scorer in `--locked` mode only; labelers | One locked run per frozen evaluator version |
| **red team** | `$BENCH_PRIVATE_DIR/redteam/` | Red-team author, labelers; runner and scorer in `--locked` mode | One run after the holdout run |
| **gold** | `$BENCH_PRIVATE_DIR/gold/` (holdout and red team); `bench/dev/gold/` (dev) | Scorer; labelers; adjudicator | Scoring |

**Rules:**
1. **Pairs, twins and audio renderings never cross splits.**
2. **Hidden from tuning:** holdout and red-team items, their gold labels, per-item outputs, **and aggregate holdout metrics**.
3. **The implementing agent must never create, draft or paraphrase benchmark content.** Unit-test fixtures must be minimal, obviously synthetic stubs, never realistic calls.
4. **Phrasings that appear anywhere in the spec documents or the design conversation are public.** They must not be reused verbatim or near-verbatim in holdout or red-team transcripts.
5. **Private files are hash-listed in the repository**, and the hashes are verified before every locked run.

## P-2. Shared front end and fixed components

- **L0–L3 are shared by all architectures:** intake, ASR/diarization, normalization, role assignment, evaluability and pre-checks.
- **ASR runs once per audio file and is cached** by file hash. The cache key includes the ASR model, version and parameters.
- **The evidence verifier, rule engine, attribution rules, confidence ceiling and verdict engine** are single implementations. A+ and B use the same modules.
- **The rubric text inside prompts is generated** from `rubric.yaml` + `profile.yaml` by a versioned template. Hand-written instruction text around the generated section is the tunable part.
- **During tuning, `rubric.yaml` and `profile.yaml` values are frozen.** Changing them needs a version bump and gold re-derivation. Bug fixes in deterministic code are allowed until freeze and apply to every architecture.

## P-3. LLM settings

| Setting | Value |
|---|---|
| Model | One pinned snapshot ID, the same for every LLM call in every architecture. **Aliases like "latest" are forbidden.** Identity: `PENDING_HUMAN_SIGNOFF` (B-05) |
| Temperature | 0 |
| Seed | Set if the API supports it; recorded |
| max_tokens | Implementation config, recorded in the manifest |
| Structured output | On (schema-constrained where supported) |
| Transport errors | Up to 3 retries with backoff; logged; not counted as retries |
| Schema-invalid output | 1 retry; if still invalid → `EVALUATION_FAILED` |
| Consistency re-run | **Disabled for all architectures** (R-05) |
| Free-text fields in outputs | English only (needed by the external-truth scan) |

## P-4. Architectures under test

| System | Execution |
|---|---|
| K0 | Deterministic. Runs once per unit; the result is replicated as reps 1–5 for metric computation. |
| A | One LLM call per unit per rep. Emits self-reported confidence; only the schema validation and the shared front-end merge are applied (`rubric.yaml › architecture_application.A`). |
| A+ | **Derived from A's stored raw output of the same rep** by the 8 ordered steps in `rubric.yaml › architecture_application.A_plus`. No LLM call. |
| B | Extraction call, then the rule engine, then a batched judgment call if any judgment was triggered. |

## P-5. Units and repetitions

- **Unit** = (item_id, mode).
- **Textual items** (core, micro, abstention, twins, red team) run as `TRANSCRIPT` units.
- **Audio items** run as `T-gold`, `T-asr`, `A`, `A+T`, plus `A+T(platform)` for P-01, P-02 and S-02.
  - `T-asr` supplies our cached ASR output *as* a transcript, with provenance `offline_asr`.
  - `A+T(platform)` supplies a transcript produced by the **weaker ASR** (B-07), with provenance `platform_live_asr`.
- **Snippets are component tests, not an architecture comparison.**
  - Amount and date snippets go through the shared deterministic normalizer (text), and through ASR + normalizer (the four audio snippets).
  - Polarity snippets go through B's extraction component (firmness/polarity fields) only.
- **k = 5 reps** for A and B. A+ inherits A's 5 reps. K0 runs once.

## P-6. Ordering and interleaving (seeded)

```
for r in 1..5:
    order_r = seeded_shuffle(units, seed = BASE_SEED + r)
    arch_order_r = rotate([A, B], r - 1)          # r=1: A,B ; r=2: B,A ; ...
    for u in order_r:
        for s in arch_order_r:
            run(s, u, rep=r)
            if s == A: derive(A+, u, rep=r)        # immediately after A's output is stored
K0: run once per unit before rep 1
```

- `BASE_SEED` is recorded in the manifest.
- A locked run must finish within **24 hours** of starting. Otherwise it is abandoned and restarted under a new `run_id`.

## P-7. Tuning (dev only)

- **Budget per architecture:** 3 revision rounds **and** an equal wall-clock timebox. The timebox value is `PENDING_HUMAN_SIGNOFF` (B-12; proposed: 4 hours).
- **A round** = one batch of changes, followed by a full dev run (k may be 1 during tuning) and a dev score.
- **Round log** (required): the diff, the rationale, dev metrics before and after, and time spent.
- **Tunable per architecture:**
  - A: its prompt instruction text.
  - B: its extraction and judgment prompt instruction text.
  - A+: nothing of its own; it inherits A's final prompt and the shared modules.
- **Not tunable:** rubric or profile values, lexicon `terms` (these change only by sign-off and a version bump), scorer, gold.
- **Publish the dev-metric trajectory** of every architecture across its rounds. If a trajectory is still rising steeply at round 3, say so in the report. Extra rounds after freeze are not allowed.

## P-8. Freeze and holdout lock

1. Tag the evaluator commit (for example `eval-freeze-v1`).
2. Write the manifest (fields listed in `frozen-contract.md` §17) and set `locked: true`.
3. Verify the hashes of private data, gold, rubric, profile and lexicons.
4. The runner refuses `--locked` unless steps 1–3 hold (H7).
5. Run the holdout per P-5 and P-6.
6. **No changes after the run.** Any change → new evaluator version; the current holdout is demoted to dev.

## P-9. Red-team sequencing

1. The red-team author writes RT items and intent cards **after** P-8 step 1 (the evaluator freeze). The author has seen only `rubric.yaml`, `profile.yaml` and `frozen-contract.md`: no prompts, no dev items, no case cards.
2. **Gold** = the author's intent card + a blind label from Labeler X, adjudicated per the labeling protocol. The red-team author never labels their own items.
3. Freeze the red-team gold (hash) → one run with the **same** frozen evaluators and manifest settings (new `run_id`) → score.
4. Red-team results are **reported separately** and never pooled with the holdout.

## P-10. Blind scoring

1. Before scoring, a seeded shuffle maps {K0, A, A+, B} → {SYS-1..SYS-4}. The mapping file is hashed, and its hash is written to the manifest. The file itself is stored outside the scorer's input path.
2. The scorer and all human checks (evidence support, contested-tier review, confirmation of external-truth regex hits) operate on aliases only.
3. The mapping is revealed only after the score report is written and its hash committed.

## P-11. Storage (append-only)

```
runs/<run_id>/manifest.json
runs/<run_id>/<system>/<item_id>__<mode>/rep_<k>/
    normalized_input.json   llm_requests.jsonl   llm_responses.jsonl
    evaluation_record.json  timing.json          usage.json   errors.json
runs/<run_id>/A+/<item_id>__<mode>/rep_<k>/derivation_log.json
scoring/<run_id>/item_scores.csv  metrics.json  discordance_tables.csv  human_checks.csv
```

Existing files are never overwritten. A re-run gets a new `run_id`. Tuning runs use `runs/dev-<round>-<timestamp>/`.

## P-12. Scorer independence

- The scorer and the gold-derivation module are **separate packages** that never import evaluator packages, and evaluator packages never import them. An import-lint test enforces this.
- **Mode-specific expected outputs** are derived from content gold + capability rules by the independent gold-derivation module, then spot-checked by a human on every audio item.
- The scorer ships with unit tests on hand-built fixtures that cover every cell of the gate mapping table (scoring-spec SD-06) before it is first used.

## P-13. Human checks during scoring

| Check | Scope |
|---|---|
| Evidence support | **Rep 1 only**: every gate finding, plus a 20% seeded random sample of Major findings (seed recorded), per system |
| External-truth regex hits | Every hit is confirmed or rejected by a human (H1 counts confirmed only) |
| Contested-tier review | Every contested item |

## P-14. Hard safety requirements

H1–H7 as defined in `frozen-contract.md` §14 and computed per `scoring-spec.md` SD-27.
- **Architecture selection:** holdout only.
- **"Meets v1 bar":** H1–H7 on both the holdout and the red team.

## P-15. Architecture decision procedure (pre-registered)

1. **Hard-requirement screen.** Any architecture that violates H1–H6 on the holdout is disqualified. If all are disqualified, report "no architecture meets the v1 bar".
2. **Primary safety, lexicographic** by tier: S0 → S1 (majority-output critical misses, then pooled per-rep misses) → S2 → S3 → S4. **Prefer the simpler architecture** (order of simplicity: A < A+ < B). A more complex architecture wins only with a **meaningful reduction**:
   - either ≥2 fewer units with S1–S3 failures, or ≥3 fewer pooled per-rep critical misses;
   - **and** no increase at any higher tier.
3. **Secondary** (only if step 2 ties): abstention correctness, verdict accuracy, evidence faithfulness and support, unjustified-attribution rate. The more complex architecture wins only if it is ≥10 percentage points better on at least two of these, with counts shown, and no more than 5 points worse on any.
4. **Tie-breakers, in order:**
   1. consistency (flip-to-pass count first);
   2. evidence completeness;
   3. cost per unit;
   4. p95 latency;
   5. implementation complexity (LLM calls, components, rules to maintain).
5. **Report** paired discordance tables for every primary metric. The exact sign test on discordant units is reported but is **not** decisive.

## P-16. Reporting rules

The statistical presentation rules of `scoring-spec.md` SD-25/SD-29 apply: counts first, whole-percent rounding, per-category tables, separate provenance strata (core, micro, red team), and scoped claims only.

## P-17. Opaque unit aliases (SC-05)

1. The runner assigns every evaluation unit a random opaque alias (e.g. `u_7f3a91c2`) per run. The alias→item mapping is stored only in `BENCH_PRIVATE_DIR` and in the run's private manifest.
2. **No bench-a1 item ID, pair ID, pack name, split name or source filename may appear in any evaluator input** (prompt text, transcript header, metadata fields, file paths passed to tools). Transcript headers carry only the frozen header fields.
3. Rendered audio files are renamed to their alias before any ASR or evaluator step.
4. A pre-run test fails the run if any evaluator-bound payload matches the item-ID pattern (`^(G|M|K|C|R|P|J|X|A|AB|E|S|MI|MC|MD|SN|RT|CAL)-`) or contains a pack/split word.

