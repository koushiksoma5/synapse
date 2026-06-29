# Multi-Source Candidate Data Transformer

A Python pipeline that ingests, deduplicates, and resolves conflicts among candidate profile data across multiple sources (structured CSV files and unstructured text note reports), then projects them dynamically according to runtime rules.

## Core Features
1. **Explicit Data Canonicalization**: Keeps clean, internal canonical candidate state decoupled from the final view.
2. **Regex Extraction Parser**: Scans and parses text logs to extract details using abstract regex queries.
3. **Decoupled Configuration**: Filter fields, toggles provenance, sets phone (E.164) and skill casings, and enforces missing-value policies without script edits.

## Execution

Ensure python is installed on your local environment. Run the program using:

```bash
python transformer.py --config config.json --csv sample_recruiter.csv --notes sample_notes.txt
```

### Config Options
The `config.json` schema accepts the following controls:
- `fields`: List of candidate attributes to project (`name`, `email`, `phone`, `location`, `skills`).
- `phone_normalization`: Option to format phone numbers (`E164` or `none`).
- `skills_normalization`: Casing conversions (`lowercase`, `uppercase`, `titlecase`, or `none`).
- `include_provenance`: Boolean value controlling detailed metadata tracking.
- `on_missing`: Missing attribute behavior (`null`, `omit`, or `error`).
