# MATACS-TH-2026-02: AI Proposal Triage Design

| | |
| --- | --- |
| **Candidate** | [TODO: your full name] |
| **Reference** | MATACS-TH-2026-02 |
| **Role** | Associate Software Developer |
| **Time actually spent** | [TODO: e.g. 75 minutes on the design + 30 minutes on the optional prototype] |

## The design in one paragraph

A reviewer uploads proposals and picks a category, which fixes the admin's criteria version. Code converts each PDF to text and scans it for text that tries to manipulate an automated reviewer. **AI is used for one job only:** reading each proposal and filling the fields the admin's criteria need, with an exact quote for every value. Code then checks every quote against the source, applies the pass/fail gates and hard limits, scores the weighted criteria with fixed formulas, ranks, and writes a reason from the points. Anything suspicious or unverifiable goes to a person instead of the ranking. A person always makes the award, and every step is logged.

**Sample result:** A (73.5) > E (60.9) > D (58.6). C is flagged for an embedded "rank me first" instruction. B is ineligible because the bidder doesn't hold the electrical licence.

## Files

| File | What it is |
| --- | --- |
| [WRITEUP.md](WRITEUP.md) | Answers to the five questions (under 1,000 words) |
| [diagram/process-flow.png](diagram/process-flow.png) | Process flow from upload to ranked output: AI, rule and human steps |
| [PROMPTS.md](PROMPTS.md) | The extraction prompt exactly as sent: system prompt, user template, output schema |
| [ranking.json](ranking.json) | Expected ranking of proposals A–E |
| [prototype/triage.py](prototype/triage.py) | Optional: runs the full pipeline (extract, verify, gate, score, rank, audit log) |
| [prototype/criteria.json](prototype/criteria.json) | The admin's criteria as data: weights, scoring rules, fields to extract |
| [prototype/fixtures/extractions.json](prototype/fixtures/extractions.json) | Recorded extraction output for A–E, so the prototype runs without an API key |
| [.env.example](.env.example) | Environment variable template (no real keys are committed) |

## Running the prototype (optional)

```bash
# Offline: uses the recorded extractions, standard library only (Python 3.10+)
python prototype/triage.py

# Live: calls Claude once per proposal with the prompt in PROMPTS.md
pip install -r prototype/requirements.txt
cp .env.example .env            # then put your key in .env and export it as ANTHROPIC_API_KEY
# put each proposal's extracted text in prototype/samples/A.txt ... E.txt (gitignored)
python prototype/triage.py --live
```

Output goes to `prototype/output/`: `ranking.json` and an append-only `audit_log.jsonl` with the run ID, file hashes, criteria and prompt hashes, model, per-criterion points, flags and an empty `human_decision` slot. The scores in the root [ranking.json](ranking.json) are the prototype's offline output; the reason text was rewritten by hand to read better.

The sample proposal text is not in this repository, because the brief asks candidates not to copy it. That is why the fixtures hold facts only and live mode reads from a gitignored folder.

## How I used AI tools

[TODO: rewrite in your own words, 2–3 sentences. Draft below; edit it to match what actually happened.]

I used Claude (Claude Code) as a pair designer: it drafted the flow, the extraction prompt, the scoring rules and the prototype, and I reviewed and changed them. **Where I disagreed:** [TODO: one real example. For instance, if the AI first suggested letting the model score and rank the proposals directly, you could explain why you moved scoring into plain code: the weights are admin data, arithmetic shouldn't vary between runs, and it keeps the injection in Proposal C away from the ranking.]
