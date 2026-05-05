# Submission Checklist — BetterBasket Cross-Retailer Product Matching

A reviewer-facing checklist of the gates the deliverable must clear. Each item is verifiable from a clean checkout with the commands shown.

## Output contract

- [x] `matches.csv` exists in the repo root.
  - `ls -l matches.csv`
- [x] `matches.csv` has at least 4,000 data rows.
  - `wc -l matches.csv` → `4395 matches.csv` (1 header + 4,394 data rows).
- [x] Header is exactly `item_id_A,item_id_B`.
  - `head -1 matches.csv` → `item_id_A,item_id_B`.
- [x] All IDs are numeric (`^\d+$`) and present in the validated source CSVs.
  - Enforced by `betterbasket_matcher.io.read_products` (5 malformed A rows are quarantined before normalization). The pipeline output is never written from non-validated rows.
- [x] No duplicate `item_id_A`.
  - `awk -F, 'NR>1 {print $1}' matches.csv | sort | uniq -d | wc -l` → 0.
- [x] PDF example A `2197626` → B `92544`.
  - `grep -E '^2197626,' matches.csv` → `2197626,92544`.
- [x] PDF example A `1929544` → B `105624`.
  - `grep -E '^1929544,' matches.csv` → `1929544,105624` (not the 15 oz B `103620` or the 29 oz B `1086860`).

## Tests

- [x] `python3 -m pytest -q` passes; final count **412**.
  - 23 arbiter unit tests use a FakeClient (no network, no credentials read). The deterministic CLI path never imports `openai`.

## Security

- [x] `openai_creds.yaml` is not in the repo.
  - `find . -name "openai_creds*" -not -path "./.git/*" -not -path "./node_modules/*"` → empty.
  - `git ls-files | grep -E "openai_creds\.ya?ml$"` → empty.
- [x] `.gitignore` excludes credentials and caches.
  - Patterns include: `.env*`, `openai_creds.yaml`, `openai_creds.yml`, `*creds*.yaml`, `*credentials*.yaml`, `.cache/`, the source CSVs (`*_items_final.csv`), and `matches_audit.csv`.
- [x] No tracked file contains a literal API key, secret, or bearer token.
  - `git ls-files | xargs grep -nE 'sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9_\-]{20,}'` → empty.
  - References to the *string* `api_key` exist only in `betterbasket_matcher/llm_arbiter.py`, `tests/test_llm_arbiter.py`, `scripts/run_pipeline.py`, `docs/HANDOFF.md`, `README.md`, and `SUBMISSION_CHECKLIST.md` — all benign code/documentation.

## Optional GPT-5.4 nano arbiter (Phase 10C)

- [x] Arbiter is implemented at `betterbasket_matcher/llm_arbiter.py` and disabled by default.
- [x] CLI flags are documented in `README.md` (`--use-llm-arbiter`, `--llm-creds`, `--llm-cache`, `--llm-max-calls`, `--llm-min-confidence`).
- [x] Credential resolution order documented: `--llm-creds` → `$BB_OPENAI_CREDS` → `/tmp/openai_artifacts/openai_creds.yaml` → `/Users/jirustaroure/Downloads/openai_creds.yaml`.
- [x] Cache path documented: `.cache/llm_arbiter.jsonl` (gitignored).
- [x] Guardrails documented: cannot override hard rules; confidence is not clamped; fail-closed on API or parse failure; `api_key` is held in `field(repr=False)` and never logged or cached.
- [x] One-call real API smoke ran and succeeded on 2026-05-04 18:35 PDT.
  - `api_call=ok`, `latency_s=2.40`, `json_parsed=valid`, `confidence=0.9500`. No prompt body, response body, endpoint, or `api_key` was printed.
  - Recorded in `docs/HANDOFF.md` (Phase 10C session block + smoke addendum).
- [x] **Final shipped `matches.csv` was produced by the deterministic command, NOT by a full LLM run.**
  - Reproduction command in `README.md` ("Reproducing the shipped match output").

## Reproduction commands tested locally

- [x] `python3 -m pytest -q` → 412 passed (verified this session).
- [x] `python3 scripts/audit_data.py ...` produces `docs/audit_stats.json` and `docs/dataset_audit_stats.md` (Phase 0).
- [x] `python3 scripts/retrieval_probe.py ...` produces `docs/retrieval_probe_results.json` and `docs/retrieval_probe_results.md` (Phase 0).
- [x] `python3 scripts/run_pipeline.py --a-csv ... --b-csv ... --min-score 0.75 --min-margin 0.05 --top-k 50` produces the shipped `matches.csv` (4,394 data rows) and `matches_audit.csv` (one row per valid A item).

## Calibration honesty

- [x] Phase 10B labels are flagged as **AI-assisted preliminary (Codex), not human-reviewed** in `eval/manual_eval_phase10b.md`, `eval/group_breakdown.md`, `eval/phase10b_calibration.md`, and `README.md`.
- [x] Cumulative precision at `score >= 0.75` is **0.95** on the labeled subset (provisional). At `score >= 0.65` it is **0.69**, motivating the retirement of the 0.55-floor 16,218-row run in favor of the 0.75-floor 4,394-row run.
- [x] A human review pass over `eval/manual_eval_phase10b.csv` is the next calibration step. The sampler preserves prior labels and notes across reruns.
