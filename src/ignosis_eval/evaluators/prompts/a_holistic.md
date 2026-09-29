<!-- UNOPTIMIZED STUB PROMPT (Evaluator A; template 0.4.0 adds the generated output contract). Placeholder for a later prompt-design phase. Do not tune here.
     Its SHA-256 is recorded in every run manifest; any edit changes the evaluator configuration. -->
You are evaluating a debt-collections call for the evaluation profile `$profile_id` ($profile_version).

Gates: $gates
Defects: $defects
Input mode: $input_mode. Available capabilities: $capabilities.
If a check needs a capability that is not available, report it as "inconclusive".

Transcript (turn id, speaker, time):
$transcript

Return ONLY a JSON object that validates against the JSON Schema below (report every gate G1-G9, the verdict,
findings with turn-numbered evidence and verbatim quotes, and the outcome):
$output_schema
