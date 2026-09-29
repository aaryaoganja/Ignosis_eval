# Interview Q&A: Ignosis Voice AI Quality Evaluator

Short answers, grounded in decisions actually made in this build. File references point to the repository. Nothing
here claims a measured accuracy for the model evaluator: none exists yet.

---

### 1. Why not just use sentiment?

Because sentiment measures the customer's mood, not the agent's conduct. The two often point in opposite directions.

- In the settlement demo, the borrower is delighted ("Sach mein? Theek hai…") because the agent just promised a
  waiver it had no authority to give. Sentiment is positive; the call is a critical G4 failure.
- In the fraud-dispute demo, a distressed borrower is handled exactly right. Sentiment is negative; the call Meets
  Bar.

The rubric also says **tone is not evaluated** in any input mode (contract §8): it isn't reliably observable from a
transcript, and it isn't the compliance question.

### 2. Why isn't outcome enough?

Outcome tells you the column. Conduct is the row. An agent optimised on outcome learns to over-promise, which is the
exact behaviour a lender must prevent. That's why the evaluator has two explicit tags:

- **Dangerous Win**: a positive outcome obtained while a hard rule fired, or after a major inducement error made
  before the commitment.
- **Clean Loss**: no positive outcome, but the agent met the bar and the customer or policy drove the result.

Outcome *verification* (did they actually pay?) needs ledger data, so it is always out of scope. Only the observable
outcome in the call is used.

### 3. Why use an LLM at all?

The defects are semantic, and the calls are code-mixed Hinglish:

- a soft promise ("salary aane ke baad dekhti hoon, shayad agle hafte") versus a firm one;
- an implicit waiver;
- a hardship statement inside a longer sentence.

No keyword list reads those reliably. But the LLM's job is narrowed to two things:

- reading: extracting events with verbatim quotes;
- answering bounded questions, only when a rule asks.

It never sets the verdict.

### 4. What should be deterministic?

Everything that can be: the LLM proposes, code disposes. In this build, code decides:

- evaluability pre-checks;
- calling hours, from the transcript header;
- prohibited phrases;
- quote verification (fuzzy match of at least 90/100, plus the correct speaker);
- confidence computation;
- attribution by rubric class;
- the repair allowlist (only ACC-05);
- the verdict tree, the Dangerous Win and Clean Loss tags, and routing tiers.

Every threshold lives in a versioned profile. A value marked `PENDING_HUMAN_SIGNOFF` raises an error rather than
defaulting (18 such values today).

### 5. Why scenario-aware evaluation?

The same sentence is right in one situation and wrong in another. "When can you make the payment?" is the correct
ask on a routine overdue call. After "this loan isn't mine, it's fraud", the same ask is a missed dispute (UND-01).
After "I lost my job", it is over-asking after inability (TRT-01).

So the rubric codes are conditioned on registered events: disputes, hardship, rights requests, third parties,
vulnerability. The profile defines the expected path for each scenario. Without scenario conditioning you either
flag every payment ask or miss the ones that matter.

### 6. How do you prevent evaluator hallucination?

Structurally, not by prompting harder.

- **Quotes are verified.** Every cited quote is matched against the transcript and the speaker's role.
  - An unverifiable non-gate finding is dropped.
  - A gate finding gets one re-judgement, then becomes SUSPECTED with `evidence_unverified`.
- **Confidence is computed.** The model can lower confidence, never raise it.
- **No external-truth language.** Output templates cannot express ledger, payment or authority truth (H1).
- **Capability filter.** No timing claims from a plain transcript (H6).
- **Schema validation.** Invalid output gets one retry, then EVALUATION_FAILED, never a pass.
- **Family guard.** The DEV transcripts were drafted with Claude's help, so the code refuses a Claude evaluator on
  them. The DEV evaluator is Gemini, to avoid same-family bias.

Two real defects found during the build show the fail-closed posture:

- The audio path was silently dropping evidence. It was fixed, and a regression test was added.
- A citation to a turn outside the call crashed the rule engine. It now returns "evaluation failed", not a verdict.

### 7. What happens when evidence is missing?

It is never a pass (contract §9, rule 1). The response is graded by where the evidence is missing:

- **Call level:** NOT_EVALUABLE, for example a voicemail or uncertain speaker roles.
- **Check level:** INCONCLUSIVE; the call becomes *partially evaluable*.
- **Needs external data:** OUT_OF_SCOPE.

If the uncertainty sits inside the observed span and would be a violation if resolved adversely, the gate is
reported as SUSPECTED, not silently passed. The app shows all of this in "Confidence and what could not be
checked".

### 8. How do you prove evaluator reliability?

With a frozen benchmark (bench-a1, 92 registry entries):

- a DEV/holdout split, with the holdout hidden from tuning;
- blind, independent gold that is never derived from evaluator output;
- minimal pairs;
- an abstention pack;
- modality and language variants;
- a red team written by an independent author *after* the evaluator is frozen.

Scoring is also independent:

- the scorer reads a blinded view;
- every call is run 5 times;
- every metric carries an interval;
- errors are ranked by safety tier (S0 to S10), so a lower-tier gain can never offset a higher-tier failure.

"Meets v1 bar" requires the hard safety requirements H1–H7 on both holdout and red team.

**Status:** the machinery is built and tested. The execution (gold, holdout, red team) is pending. Today nothing
proves the evaluator is right, and the product says so.

### 9. Why does benchmark design matter?

Because the benchmark *is* the definition of "right". Each part catches a specific way an evaluator can look good
without being good:

- **Minimal pairs:** evaluators that key on surface words.
- **The abstention pack:** over-confidence.
- **Twins and audio renderings:** dependence on transcript quality.
- **Opaque per-run aliases:** an item ID such as `MI-G4-01` literally names the gate, so no ID can leak the answer
  to the evaluator.
- **One locked holdout run per frozen version:** tuning on the test.
- **Not reusing phrasings quoted in the spec in holdout transcripts:** memorised answers.

### 10. Why A vs A+ vs B?

It's an ablation, so the architecture choice is earned, not asserted.

- **A** is the typical single-prompt "LLM judge".
- **A+** takes A's *stored raw output* through B's deterministic steps. It isolates what verification and rules add.
- **B** adds structured extraction and targeted judgements. It isolates what decomposition adds.
- **K0** is a keyword floor.

Selection happens on the holdout, by safety tiers first. The protocol prefers the simpler architecture
(A < A+ < B): a more complex one wins only with a meaningful reduction in safety errors (experiment protocol).

### 11. Why Dangerous Win and Clean Loss?

They are the two cells where outcome metrics mislead, in opposite directions.

- **Dangerous Win** surfaces risk hidden inside "successes" and routes it to review: Tier 1–2 if a gate fired,
  Tier 3 for a major inducement error.
- **Clean Loss** protects a correctly behaving agent or configuration from being "fixed" into aggressiveness.

Both are defined precisely, so they are testable. For example, a soft or conditional promise is observable but not
a positive outcome.

### 12. Why is audio experimental?

Three reasons:

- The ASR and diarization choice and the audio thresholds are pending human decisions.
- Transcription errors in Hinglish change meaning: amounts, dates, negation.
- Speaker attribution is a heuristic. With exactly two speakers, the one who opens the call and speaks most is the
  agent; otherwise the call is Not Evaluable.

There is no calibration data yet. So audio-only runs only in the review app. It is labelled EXPERIMENTAL AUDIO
EVALUATION, excluded from the benchmark, gold and metrics, and it refuses to guess speakers.

With audio + transcript, the transcript stays the primary source. This is deliberate uncertainty handling.

### 13. What would you change for production?

- Ingest the Ignosis call stream with platform ASR provenance. This enables the SAID-vs-HEARD *perception*
  attribution the spec already defines.
- Connect payment, ledger and CRM data to turn on the 13 checks that are out of scope today, such as whether a
  promised action was executed.
- Replace the demonstration profile with client policy packs signed off by the policy owner.
- Calibrate ASR and diarization.
- Enable the specified consistency re-run for Tier 1–2 and Dangerous Wins.
- Persist results; the library is in-memory today.
- Feed reviewer outcomes back into gold, with drift monitoring and cost/latency budgets.
- Add authentication and multi-tenancy; they are explicitly out of the assignment's scope.

### 14. What would you prioritise next?

In this order, because each step unblocks the next:

1. Native review of the DEV transcripts, and sign-off of the 18 pending profile values.
2. A live DEV run of K0, A, A+ and B with 5 repetitions; fix only on DEV; freeze and tag the evaluator.
3. Blind gold, one locked holdout run, and the red team.
4. Only then, production integration.

Shipping unvalidated verdicts to operations would create false confidence, which is worse than having no evaluator.
