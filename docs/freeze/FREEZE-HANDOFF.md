# FREEZE-HANDOFF — bench-a1 design v1.0

## Canonical versions
- Contract: `1.2.0-frozen`
- Rubric: `1.2-mvp`
- Profile: `collections_default_v1` `1.1.1`
- Benchmark: `bench-a1` design v1.0 (frozen 2026-09-29)

## Files in this handoff (SHA-256)
| File | SHA-256 |
|---|---|
| `bench/public/README.md` | `9b8870d23180842a62728b5a0ded8fe5ac07e0e6a09b40c7992ed0029718f7ba` |
| `bench/public/dev-case-cards.md` | `7454c1dc9b3b3e6885a5f7237717d2677eca099c3fd05249c686da97ebf868a0` |
| `bench/public/dev-gold-blueprint.yaml` | `cc09ddc9503edadc5b3713fb3a5494f776e4452b8875a258c4fe4dbc54609298` |
| `bench/public/dev-master-matrix.csv` | `05772c939c1cdd9013488eaa0688d098de97eaf57a2bc683621f28ae5d2d63dc` |
| `bench/public/gold-blueprint-schema.yaml` | `db9f724be9e4a27c837772abf7a5e097be3c42719b9c419956a33971e0e7e163` |
| `docs/freeze/FREEZE-public.md` | `b3c2d0306335f2cf56a1bc614ad12404069ef7cba9fe985095af5d11a71110d3` |
| `docs/freeze/STAGE5-ADJUDICATION-LOG.md` | `fc3ca74f801267580504e4c70ffad1eea1a8a34c6f152e4fbd430f6682127149` |
| `docs/spec/experiment-protocol.md` | `46a0e3e869bcc38f8f721e5b8de4ed4c520dc6209b5a126a9cea1c7fad6a58c0` |
| `docs/spec/frozen-contract.md` | `8ff32ec59b1bc355189da1c331cd9b2c2941723fb6b286ff96dacfeafeba0e3a` |
| `docs/spec/implementation-blockers.md` | `9c690d17de59d60177ffd7df84db2b5d05d539ee7a65e102028bd93ad871e1e1` |
| `docs/spec/profile.yaml` | `20cbce689a40a56e1511df0d2415f7ec259b23227b70157efebc2ebd56f50109` |
| `docs/spec/rubric.yaml` | `c4a3c7af8619e5d7be362873641cc2ecb70086bc6459eca8806a8a8e01adeb8c` |
| `docs/spec/scoring-spec.md` | `70404ee7680df8a2deb7a8bc6cf0e5d1da625b8d75457a9b6cfae8be8f56eabc` |

`FREEZE-HANDOFF.md` itself is not hashed.

## Benchmark count summary
- Core full calls: 22 (10 dev, 12 holdout)
- Micro items: 32 (8 dev, 24 holdout)
- Abstention items: 10 (holdout)
- Audio-native items: 4 holdout plus 1 dev degraded variant
- Audio renderings of existing items: 3 holdout, 2 dev
- Language items: 2 holdout twins, 12 snippets (6 dev, 6 holdout)
- Red-team slots: 6
- Calibration items: 3 (never scored)
- Total registry entries: 92
- Held-out gold-FAIL gate items (T-mode): 15

## Status statements
1. **bench-a1 design v1.0 is frozen for transcript authoring.**
2. **A-01 final disposition:** corrected per BD-02. It is a single-purpose item and not a G7 positive. Its full gold intent is held only in the private registry (SC-06).
3. **There is no G7 positive in bench-a1.** G7 recall is not measured by the benchmark and is covered by deterministic unit tests only.
4. **Transcripts, audio and gold labels are NOT yet frozen.**
5. **No evaluator prompt or tuning has been produced.**
6. **Any future benchmark edit requires a BD-xx changelog entry** in `frozen-contract.md` §0 and §12.
