# Multi-Source Candidate Data Transformer & Dashboard

A python profile pipeline that ingests, deduplicates, and resolves conflicts among candidate profile data across multiple sources (structured CSV/JSON files, copy-paste inputs, and unstructured recruiter notes/PDF resumes), then projects and normalizes them dynamically according to runtime rules.



---

## 1. How to Run the Program

### Prerequisites
* Python 3.8+
* Optional: `pypdf` (if you want to parse PDF resumes; falls back to standard text parsing if not present).

### Start the Dashboard Web Interface
Start the local server by running:
```bash
python transformer_gui.py
```
This will:
1. Boot up a local web server (default port `8000`).
2. Automatically launch your default web browser to the dashboard at `http://localhost:8000`.

From the dashboard, you can:
* Upload CSV and JSON files, or PDF/TXT resumes.
* Copy-paste structured rows/JSONs or raw text notes.
* Toggle active projection fields, custom rename targets, phone normalization, skills casing, and missing field fallback policies (`null`, `omit`, or raise a pipeline validation `error`).
* Export consolidated profiles as formatted CSV or raw JSON.

---

## 2. Example Output (sample_output.json)

Running the pipeline on the sample inputs ([sample_recruiter.csv](sample_recruiter.csv) and [sample_notes.txt](sample_notes.txt)) merges the profiles, normalizes fields (E.164 phone formats and lowercase skills), and generates deterministic candidate IDs, producing the following output ([sample_output.json](sample_output.json)):

```json
[
  {
    "candidate_id": "cand-f0464467-79b3-a14b-22ec-5a7b8cb89fbf",
    "full_name": "Amit Sharma",
    "emails": [
      "amit.sharma@example.com"
    ],
    "phones": [
      "+919876543210"
    ],
    "location": {
      "city": "Bangalore",
      "state": null,
      "country": "India"
    },
    "skills": [
      "python",
      "go",
      "kubernetes",
      "system design"
    ]
  },
  {
    "candidate_id": "cand-0cba00ca-3da1-b283-a572-87bcceb17e35",
    "full_name": "Jane Doe",
    "emails": [
      "jane.doe@example.com"
    ],
    "phones": [
      "+15550199"
    ],
    "location": {
      "city": "San Francisco",
      "state": "CA",
      "country": "USA"
    },
    "skills": [
      "product strategy",
      "agile",
      "roadmap"
    ]
  }
]
```
