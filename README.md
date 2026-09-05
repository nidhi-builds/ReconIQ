# ReconIQ

ReconIQ reconciles internal ledger, gateway settlement, and bank-statement data using a rules-first pipeline, with LLM assistance only for unresolved records.

## Local setup

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Generate the deterministic design and holdout CSVs with:

```powershell
python -c "from data.generator import generate_dataset, write_dataset; write_dataset(generate_dataset(), 'data/generated')"
```

Generated data is written under `data/generated/` and is intentionally ignored by Git. Matching, API, dashboard, and deployment follow in later slices.

For the opt-in Stage 6 Gemini design-set gate, set `GEMINI_API_KEY` in the
ignored `.env` file and run:

```powershell
python -m pytest -m live tests/test_llm_match.py -s
```

The live gate never sends ground truth to Gemini and does not evaluate the
holdout partition.
