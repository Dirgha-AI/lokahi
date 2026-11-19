# Data card: LOKAHI decisions tier seed data v0.1

This is the training and test data for LOKAHI's experimental decisions tier (v0.1.4, off by default). It came from the Dirgha decision model prototype unchanged; `tools/decisions/scripts/validate_data.py` and `build_hard_test.py` rebuild `seed.jsonl` and `hard_test.jsonl` byte for byte from the files here.

**Labels are machine-written and have not yet been reviewed by people.** Treat every label as a draft.

## Summary

| | |
|---|---|
| Files | `data/decisions/seed.jsonl` (training and holdout), `data/decisions/hard_test.jsonl` (test only) |
| Rows | 995 seed rows kept from 1,009 generated; 50 hand-written hard test rows |
| Labels (seed) | ALLOW 334, ESCALATE 324, REFUSE 338 |
| Languages (seed) | en 179, pt 124, hi 123, ar 123, es 122, sw 122, bn 42, tr 42, de 42, fr 39, id 38 |
| Domains (seed) | newsroom, clinic, school, city office, small business, developer tools, robotics in simulation (141 to 144 rows each) |
| Hard cases (seed) | benign but sensitive 133, injection 120, over-refusal trap 104, ambiguous 93, ordinary 545 |
| Schema | `data/decisions/schema.json` (JSON Schema 2020-12) |
| Labelling rules | `docs/decisions/CHARTER.md` (reason codes DDM-01 to DDM-11) |
| License | CC BY 4.0 (`data/LICENSE`). Code is Apache-2.0. |
| Label status | Machine-written by the generator, checked mechanically, not yet reviewed by people |
| Personal data | None. Every example is synthetic, with invented names, numbers and places. |

## How the seed data was made

- **Generator:** `numes/deepseek/deepseek-v4.1-flash:free` (DeepSeek V4.1 Flash, open weights), called through `opencode run -m <model> "<prompt>"` with opencode v2.3.2, in headless mode from an empty scratch folder.
- **Date:** 19 Nov 2026, 08:13–08:45 UTC. Run metadata is in `data/decisions/raw/generation_meta_run1.json`. One job (`developer_tools_1`) returned nothing twice because of network resets (`ECONNRESET`). It succeeded on the third run with the same model (`generation_meta_run3_retry.json`).
- **Jobs:** 28 jobs (7 domains × 4 batches), 36 rows asked per job. Each batch rotated through a language set and a hard-case focus: over-refusal traps, prompt injection, benign but sensitive, and ambiguous or deception or targeting cases.
- **Prompts:** the exact prompt for every job is saved next to its output as `data/decisions/raw/<domain>_<batch>.prompt.txt`, and the raw model output as `data/decisions/raw/<domain>_<batch>.jsonl.txt`. `tools/decisions/scripts/generate_seed.py` builds the prompts. The generator's stderr logs were not copied.
- **Labels:** the generator wrote the labels, reason codes and rationales, following a short version of the charter in the prompt. No person has reviewed the rows yet. This is the main weakness of the data (see Limits).

## Validation (`tools/decisions/scripts/validate_data.py`)

Rows are dropped, never repaired. Report: `data/decisions/validation_report.json`.

1. JSON parse (2 rows dropped).
2. Schema check against `schema.json` (0 dropped).
3. Charter consistency checks:
   - the reason code must belong to the label;
   - an irreversible tool is never ALLOW;
   - timed-out approval means REFUSE with DDM-02, and DDM-02 only applies when approval timed out;
   - data leaving the allowed residency must be REFUSE, and DDM-05 only applies then;
   - writing sensitive data cannot be ALLOW (10 dropped);
   - an over-refusal trap must be ALLOW (1 dropped);
   - an injection cannot be ALLOW.
4. Text checks:
   - Hindi, Bengali and Arabic text must be in the right script;
   - non-English rows that look English are flagged;
   - email addresses outside example or test domains are dropped (1 dropped);
   - long digit runs that could be phone or ID numbers are dropped.
5. Exact duplicates after normalising case and punctuation are removed (0 found), as is any overlap with the hard test set (0 found).

The checks are mechanical. They catch contradictions with the charter. They do not prove a label is right.

## Hand-written hard test set (`data/decisions/hard_test.jsonl`)

Fifty cases written by hand in `tools/decisions/scripts/build_hard_test.py`, not by a model, and never used for training: 17 ALLOW, 16 ESCALATE and 17 REFUSE, in 11 languages. They cover over-refusal traps ("kill a process", "shoot photos", overdose-prevention leaflets), injections in English, Hindi and Swahili, impersonation, fake reviews and invoices, doxxing and tracking, a denial-of-service request, residency violations and timed-out approvals. They pass the same schema and charter checks.

## Limits

- **Synthetic, one generator.** The style, vocabulary and label habits of one model are baked in. The holdout split shares them, so holdout scores are optimistic. The hand-written set is the more honest test.
- **Machine-written labels.** Validation removes contradictions but cannot fix a wrong judgement that is self-consistent. A human review pass is the next step. Corrections from that review go in a JSONL file that `tools/decisions/train.py --corrections FILE` applies before retraining (see `tools/decisions/corrections.py`).
- **Not real traffic.** Real agent calls have longer, messier arguments and multi-step context. This data has single-step summaries.
- **Language coverage is uneven.** Six languages have about 120 rows each and five have about 40. There are no dialect or code-mixed examples beyond what the generator produced.
- **Near duplicates** (paraphrases) were not removed. Only exact duplicates were.
