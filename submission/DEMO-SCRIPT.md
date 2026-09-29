# Demo script: Ignosis Voice AI Quality Evaluator (6–7 minutes)

A walkthrough in the presenter's own voice. **Say** is the script, **Show** is the screen. Every result on screen is
labelled in the app, either **DEMO / REPLAY** or **LIVE EVALUATION**. Never call a replay a live result.

## Before you start (2 minutes, off camera)

- Open `https://ignosiseval-production.up.railway.app/` and read the badge at the top right:
  - **Live evaluation on · Google Gemini · gemini-3.8-flash**: the live branch at 3:40 is available.
  - **Demo mode · live evaluation off**: skip the live branch. Everything else works unchanged.
- In a second tab, open `/#/reliability`.
- Close the "How it works" panel if it is open.
- Keep the PDF open at page 2 (the Dangerous Win / Clean Loss matrix) and page 4 (the pipeline).

---

## 0:00–0:45 · The problem and the thesis

**Show:** PDF page 1.

**Say:**
> "Voice AI agents place collections calls faster than any QA team can listen to them. Any one of those calls can
> break a hard rule: disclose a debt to the wrong person, promise a waiver nobody authorised, or push someone who
> just said they lost their job. Whatever QA doesn't sample is never judged.
>
> My thesis: Ignosis already listens to every call. This layer makes it capable of judging whether the AI agent
> behaved correctly.
>
> The hard part isn't transcription. It's trustworthy judgement: a verdict a reviewer can check against the call,
> from an evaluator that says when it can't tell."

## 0:45–1:30 · What good and bad look like

**Show:** PDF page 2.

**Say:**
> "The first design decision: the customer paying is not the same as the agent behaving correctly.
>
> We judge in a fixed order:
> - Can this call be judged at all?
> - Did the agent break a hard rule?
> - Did it handle the situation correctly?
> - Only then: what was the outcome, and who drove it?
>
> A good outcome never buys back a broken rule.
>
> That gives two cells that outcome dashboards get exactly wrong.
> - A **Dangerous Win**: the customer promised to pay, but the agent broke a rule to get it. It looks like success.
> - A **Clean Loss**: no payment, but the agent did everything right, for example it stopped and escalated a fraud
>   claim. It looks like failure.
>
> Outcome metrics see the columns; this evaluator sees the rows."

## 1:30–2:00 · How it works

**Show:** PDF page 4.

**Say:**
> "The architecture is simple: the model reads, the rules decide.
> - Gemini extracts what happened, with verbatim quotes.
> - Deterministic code checks every quote against the transcript and the right speaker. Anything it can't find is
>   dropped, or for a hard rule, downgraded to suspected.
> - A versioned rule engine maps the verified events to the rubric.
> - A fixed decision tree produces the verdict.
>
> The model never sets the verdict. Confidence is computed from the evidence; the model can lower it, never raise
> it."

## 2:00–4:30 · Live walkthrough

**Show:** the app, Evaluate a call.

**Say (2:00):**
> "This is what a QA reviewer sees. Five steps along the top. Step 1 is five fictional collections calls, each
> built to show a different right answer."

**Do:** on *Borrower asks for a reduction; agent settles on the spot*, click **View evaluation**.

**Say (2:15):**
> "The purple banner says DEMO / REPLAY: a recorded evaluation of a fictional call, replayed through the real rules
> engine. It is not a live model result, and the app never lets you confuse the two.
>
> The verdict comes first: **Critical Fail, confirmed.** Then why: hard rule G4, unauthorised commitment. And the
> evidence: turn 7, where the agent says *'I will settle the account and waive the remaining amount.'*
>
> Now read the four facts under it:
> - partially evaluable;
> - **Dangerous Win: yes**;
> - Clean Loss: no;
> - three findings."

**Do:** scroll to **Findings**, then to **Evidence: the transcript** (turn 7 is highlighted).

**Say (2:45):**
> "Each finding has a severity and a confidence. The confidence is computed from the evidence, not self-reported by
> the model. Each finding is also attributed, here to agent behaviour. Selecting a finding highlights its lines in
> the transcript, so a reviewer checks the call, not the model.
>
> Two more findings came out of the same call: the commitment was never read back, and the agent missed an
> escalation trigger when the borrower asked for a reduction.
>
> Note the 'Agent promises to verify' list. 'I will note the settlement' can only be checked in a CRM. We don't have
> CRM data, so the evaluator lists the promise for follow-up instead of pretending to judge it."

**Do:** scroll to **Confidence and what could not be checked**, then **Outcome** and **Next step**.

**Say (3:10):**
> "This is the part I care most about: what could *not* be checked. Some checks couldn't be decided from this call,
> and some need payment or account records. None of them is counted as a pass.
>
> Outcome: a firm promise, agent-driven, while a hard rule fired. That's a critical Dangerous Win. So the next step
> is Tier 1, immediate review: check the customer impact and fix the agent before it repeats."

**Do:** **← Evaluate another call**, then **View evaluation** on *Borrower says the loan is not theirs*.

**Say (3:25):**
> "The opposite case. The borrower says, *'Yeh mera loan nahi hai, kisi ne mere naam pe fraud kiya hoga.'* The agent
> stops collecting and raises a dispute. No payment, so a dashboard calls this a loss. The evaluator says **Meets
> Bar, Clean Loss, policy-driven**. Nobody should coach this agent to push harder."

**Live branch (3:40), only if the badge says Live evaluation on:**
> **Do:** **← Evaluate another call**, then **Open in step 2** on *Hedged promise noted as a firm PTP*. Choose
> **Live evaluation with gemini-3.8-flash**, then **Evaluate call**. It takes 10–60 seconds.
>
> **Say:** "Same pipeline, now live. Look at the banner: LIVE EVALUATION, Google Gemini, gemini-3.8-flash. The live
> verdict can differ from the recorded one. Measuring how often, and in which direction, is exactly what the DEV run
> and the holdout are for."

**If demo mode, instead (3:40):** **← Evaluate another call**, then **View evaluation** on *Voicemail*.
> **Say:** "The third right answer is 'I can't judge this.' A voicemail has no conversation, so the verdict is **Not
> Evaluable**: zero findings, no review tier, and every conduct check left undecided. Abstaining is a feature. A
> false 'Meets Bar' would quietly inflate your quality numbers."

**Do (4:05):** **← Evaluate another call**, Step 2 **Audio**, **Use the sample recording**, then **Evaluate call**.

**Say:**
> "Audio. Three input types: transcript, audio, and audio plus transcript.
> - Transcript is the working path.
> - With audio plus transcript, the transcript stays the primary source.
> - Audio-only is labelled **EXPERIMENTAL** everywhere. Gemini transcribes and separates the speakers, and a simple
>   outbound-call rule names the agent. If the speakers are unclear, the call is Not Evaluable; it never guesses.
>
> This sample is a synthetic recording, replayed. Audio is kept out of every reliability claim until it's
> calibrated. That's deliberate uncertainty handling, not a missing feature."

## 4:30–5:15 · The reliability screen

**Show:** **Evaluator reliability**.

**Say:**
> "Reliability is part of the product, so it has its own screen, and it's honest. On the left is what's measured,
> labelled DEV ENGINEERING MEASUREMENT:
> - 18 draft development calls, compared with each call's design intent, not gold labels;
> - the deterministic pre-checks agree with that intent on 18 of 18;
> - K0, a keyword floor with deliberately empty keyword lists, catches nothing, by design.
>
> The model evaluators A, A+ and B show 'Not run yet'. No live DEV run has been executed, so there's no accuracy
> number, and the app refuses to invent one.
>
> On the right, in red, PENDING: native review of the transcripts, gold labels, a locked holdout run and a red-team
> run. The benchmark and the scoring machinery are built. What remains is independent execution and validation."

## 5:15–6:00 · Limitations

**Show:** PDF page 9.

**Say:**
> "What this doesn't prove yet:
> - The development transcripts are AI-assisted drafts waiting for native Hindi and Hinglish review.
> - There are no gold labels, no holdout run and no red-team run, so I make no reliability claim for the evaluator.
> - The live Gemini path is built and tested end to end against a simulated provider, but I haven't measured live
>   results.
> - Audio is experimental.
> - The rules profile is a demonstration profile, not Ignosis policy: 18 of its values wait for a human to sign off,
>   and the code refuses to default them.
> - Anything that needs payments or CRM data is out of scope, never guessed.
>
> The order of next steps matters: close the inputs, measure on DEV, validate on the holdout and red team, and only
> then connect production."

## 6:00–7:00 · Why Ignosis

**Show:** PDF page 10.

**Say:**
> "Why this belongs in Ignosis:
> - It's built for Voice AI agents, not human agents, and keeps platform problems like audio and latency apart from
>   agent conduct.
> - It speaks collections: disclosure, settlement authority, hardship, disputes, calling hours.
> - It turns QA and compliance from sampling into a verdict on every call, with evidence.
> - Because every finding names the behaviour and the turn, it closes a loop: call, evaluation, finding, coaching or
>   routing, agent improvement, re-evaluation, on the same versioned rubric.
>
> Ignosis already listens to every call. With this layer, it can continuously know which AI-agent behaviours need
> attention, with evidence a reviewer can check. Thank you."

---

**Timing guard:** if you're behind at 3:40, skip the live or voicemail branch and go straight to audio. If you're
behind at 4:30, cut the audio part to one sentence: "Audio-only is experimental, labelled, and outside every
reliability claim."
