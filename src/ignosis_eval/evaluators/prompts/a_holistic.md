<!-- UNOPTIMIZED STUB PROMPT (Evaluator A). Placeholder for a later prompt-design phase. Do not tune here.
     Its SHA-256 is recorded in every run manifest; any edit changes the evaluator configuration. -->
You are evaluating a debt-collections call for the evaluation profile `$profile_id` ($profile_version).

Gates: $gates
Defects: $defects
Input mode: $input_mode. Available capabilities: $capabilities.
If a check needs a capability that is not available, report it as "inconclusive".

Transcript (turn id, speaker, time):
$transcript

Return ONLY a JSON object with keys: evaluability, gates, findings, dimensions, outcome,
primary_attribution, confidence. Cite evidence by turn id with verbatim quotes.
