# Write-up: AI proposal triage

Flow: [diagram/process-flow.png](diagram/process-flow.png) · Prompt: [PROMPTS.md](PROMPTS.md) · Result: [ranking.json](ranking.json)

**Core idea:** AI extracts quote-backed facts from each proposal. Code verifies, scores and ranks them against the admin's criteria. A person clears flags and makes the award.

## 1. AI vs. rules

| Step | AI or rule |
| --- | --- |
| PDF to text, file hash, unreadable-file check | Rule |
| Scan raw text for instructions aimed at AI | Rule (regex), plus the AI's own report |
| Read the proposal and fill each criterion's fields (value, exact quote, confidence) | **AI** |
| Check each quote appears in the source; check types and units | Rule |
| Mandatory pass/fail, budget and 16-week limits | Rule |
| Weighted scores, total, ranking, tie-breaks, reason text | Rule |
| Clear flags, override, award | Human |

**Why the line is here:** proposals are free text. Reading "licensed subcontractor TBC" correctly needs language understanding, which AI is good at. Everything after that is arithmetic and yes/no logic, where code is exact, free and auditable. The AI never sees the weights, never scores, and never ranks.

## 2. Consistency and explainability

- **Same facts give the same score.** Scoring uses fixed formulas stored with the criteria. For example, price earns full marks at 25% or more under the ceiling and 0 at it; team and approach is a 4-item checklist. Ties break on lower price, then proposal letter.
- **Same file gives the same facts.** Extraction is cached by file hash, prompt version, criteria version and model, so ranking the same proposals twice gives an identical result at no extra cost. Even a fresh extraction rarely changes, because the output is narrow, schema-checked, quote-backed facts; any change shows up as a difference in the audit log.
- **Explainable:** for each proposal the reviewer sees the gate results, the points per criterion, the extracted fact behind each point, and the exact quote from the proposal. The one-line reason is generated from those same numbers, so it can't disagree with the score.

## 3. Human in the loop

1. **Admin** publishes criteria; the system checks weights total 100% before saving a version.
2. **Reviewer** confirms the category and its criteria version before the run.
3. **Flag queue:** any injection hit, a mandatory fact that is unknown or has an unverified quote, or an unreadable file sends the proposal to a person instead of the ranking. A held-back "shadow score" is shown to the reviewer only.
4. **Override:** the reviewer can correct any extracted fact, gate or rank. A reason is required, and code recomputes the score so the arithmetic stays consistent.
5. **Award** is always a human decision, recorded against the run.

**Audit log (append-only), one record per run:** user, time, file hashes, criteria version and hash, prompt version and hash, model ID, raw AI output, quote-check results, gates, per-criterion points, status, reason. Every override (who, when, old value to new value, why) and the final award are also logged, so any result can be reproduced.

## 4. Changing criteria

Criteria are **versioned, never edited in place**. Saving an edit creates v2; past results stay tied to v1, so the audit record never changes. For proposals already scored:

- **Weight or formula change:** the facts don't change, so code re-scores instantly from cached extractions. There is no AI cost.
- **New or reworded criterion (new fields to extract):** only the affected fields need a new extraction.
- The reviewer sees "scored on v1, v2 available" with a side-by-side of rank changes. A batch is never ranked on mixed versions, and once an award is under way, a re-score needs the admin's confirmation.

## 5. Risks and safeguards

| Risk | Safeguard |
| --- | --- |
| **Wrong extraction** (misread number, wrong unit) | Schema-constrained output, unit rules, range checks. Low-confidence fields are highlighted, with the quote shown next to every value. |
| **Made-up facts** | Every value needs an exact quote that code finds in the source. If it isn't found, the value is discarded. A missing value counts as "not evidenced": 0 points, or "unknown" for a mandatory criterion, never an assumed pass. |
| **Manipulation / prompt injection** (Proposal C) | The proposal is fenced as data in the prompt. A regex pre-scan and an AI report run independently, and either one flags the proposal. The AI cannot rank, so a successful injection could only alter facts, which still need verified quotes. |
| **Persuasive wording** (B's price claims) | Only facts are extracted; adjectives earn no points. A subcontracted, unconfirmed licence fails the gate. |
| **Bias** (favouring polished writing or big firms) | One proposal per call, no comparison between bidders inside the AI, explicit formulas, and bidder names aren't used in scoring. Override patterns are reviewed. |
| **Missing information** (D's timeline) | Scores 0 on that criterion with a "request clarification" note. Not assumed. |
| **Stale evidence** (C counts projects from 2020; the window is 5 years) | A rule checks the year against the evaluation date and adds a "verify" note. |
| **Cost** | One call per proposal, cached; weight changes need no AI call; token use is logged. |
| **Model change** | The model ID is logged, and a regression set of proposals with known answers is re-run before any switch. |

## Sample result and assumptions

A (73.5) > E (60.9) > D (58.6). C is flagged (would score 52.4). B is ineligible. Details are in [ranking.json](ranking.json).

**Assumptions:** "Last 5 years" is measured from the evaluation date. A missing timeline scores 0 rather than making the bid ineligible, because the brief only says that over 16 weeks fails. A "named" lead means designated, even if no personal name appears. C's score is withheld, not deducted: a person decides whether the embedded instruction disqualifies the bid.
