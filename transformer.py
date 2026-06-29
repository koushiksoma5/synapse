#!/usr/bin/env python3
"""
CandidateTransformerEngine

A deterministic, explainable, and robust pipeline that processes multi-source
candidate profile data, performs canonicalization, deduplication, conflict resolution,
and dynamic JSON projection based on a decoupled configuration.
"""

import sys
import csv
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union

# =====================================================================
# 1. Canonical State & Dataclasses
# =====================================================================

@dataclass(frozen=True)
class FieldMetadata:
    """Metadata detailing the source, method of extraction, and confidence of a value."""
    source: str
    method: str
    confidence: float

@dataclass(frozen=True)
class FieldValue:
    """Encapsulation of a field's canonical value and its associated metadata."""
    value: Any
    metadata: FieldMetadata

@dataclass
class CanonicalCandidate:
    """Internal canonical record representing a consolidated candidate profile."""
    name: Optional[FieldValue] = None
    email: Optional[FieldValue] = None
    phone: Optional[FieldValue] = None
    location: Optional[FieldValue] = None
    skills: Optional[FieldValue] = None

# =====================================================================
# 2. Text/Regex Parsers & Input Readers
# =====================================================================

class SourceReader:
    """Robust I/O layer with try/catch isolation barriers for loading raw files."""

    @staticmethod
    def read_csv(filepath: str) -> List[Dict[str, str]]:
        """Reads rows from a structured CSV file with error shielding."""
        records: List[Dict[str, str]] = []
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    print(f"Warning: CSV file {filepath} has no headers.", file=sys.stderr)
                    return []
                # Ensure we strip whitespace from keys and values
                for row in reader:
                    cleaned_row = {k.strip(): (v.strip() if v else "") for k, v in row.items()}
                    records.append(cleaned_row)
        except FileNotFoundError:
            print(f"Warning: CSV file not found at '{filepath}'. Skipping.", file=sys.stderr)
        except Exception as e:
            print(f"Error reading CSV file '{filepath}': {e}. Skipping.", file=sys.stderr)
        return records

    @staticmethod
    def read_notes(filepath: str) -> List[Dict[str, Any]]:
        """Reads unstructured notes and extracts candidate tokens using regular expressions."""
        extracted: List[Dict[str, Any]] = []
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                content = f.read()
        except FileNotFoundError:
            print(f"Warning: Notes file not found at '{filepath}'. Skipping.", file=sys.stderr)
            return []
        except Exception as e:
            print(f"Error reading notes file '{filepath}': {e}. Skipping.", file=sys.stderr)
            return []

        # Split the text file by candidate segments
        # Assuming each segment starts with 'Candidate Name:'
        segments = content.split("Candidate Name:")
        for segment in segments:
            if not segment.strip():
                continue

            # Extract fields using abstract patterns
            lines = segment.split('\n')
            name_val = lines[0].strip() if lines else ""

            # Email Regex (Standard RFC-compliant)
            email_match = re.search(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', segment)
            email_val = email_match.group(0).strip() if email_match else None

            # Phone Regex (Captures standard numbers, and simpler local extensions e.g. 555-0199)
            phone_match = re.search(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}\b', segment)
            phone_val = phone_match.group(0).strip() if phone_match else None

            # Location Regex: Looks for markers like based in/relocating to followed by city/state
            location_match = re.search(
                r'(?:based in|relocating to|lives in|located in|location is)\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)?)',
                segment,
                re.IGNORECASE
            )
            location_val = location_match.group(1).strip().rstrip('.,') if location_match else None

            # Skills Regex: Matches core skills lists and splits them by comma/and
            skills_val: List[str] = []
            skills_match = re.search(r'(?:skills include|skills:|core skills include)\s+([^.\n]+)', segment, re.IGNORECASE)
            if skills_match:
                raw_skills = skills_match.group(1)
                # Replace ' and ' or ' & ' with a comma for easier splitting
                normalized_skills = re.sub(r'\b(?:and|&)\b', ',', raw_skills, flags=re.IGNORECASE)
                for s in normalized_skills.split(','):
                    cleaned = s.strip().rstrip('.')
                    if cleaned:
                        skills_val.append(cleaned)

            extracted.append({
                "name": name_val if name_val else None,
                "email": email_val,
                "phone": phone_val,
                "location": location_val,
                "skills": skills_val if skills_val else None
            })

        return extracted

# =====================================================================
# 3. Canonicalization & Merge Rules
# =====================================================================

class ConflictResolver:
    """Handles conflict resolution and field merging using source confidence weightings."""

    @staticmethod
    def merge_field(csv_field: Optional[FieldValue], notes_field: Optional[FieldValue], is_list: bool = False) -> Optional[FieldValue]:
        """Resolves value conflict between structured CSV and unstructured notes."""
        if is_list:
            csv_list = csv_field.value if (csv_field and csv_field.value) else []
            notes_list = notes_field.value if (notes_field and notes_field.value) else []
            if not csv_list and not notes_list:
                return None
            
            # Combine items while maintaining insertion order and deduplicating
            combined = list(dict.fromkeys(csv_list + notes_list))
            
            # Metadata preference: use the metadata of the higher confidence source
            if csv_field and not notes_field:
                return FieldValue(combined, csv_field.metadata)
            elif notes_field and not csv_field:
                return FieldValue(combined, notes_field.metadata)
            elif csv_field and notes_field:
                meta = csv_field.metadata if csv_field.metadata.confidence >= notes_field.metadata.confidence else notes_field.metadata
                return FieldValue(combined, meta)
            return None
        else:
            # Single value conflict resolution
            has_csv = csv_field and csv_field.value is not None and csv_field.value != ""
            has_notes = notes_field and notes_field.value is not None and notes_field.value != ""

            if has_csv and has_notes:
                # Compare confidence scores
                if csv_field.metadata.confidence >= notes_field.metadata.confidence:
                    return csv_field
                else:
                    return notes_field
            elif has_csv:
                return csv_field
            elif has_notes:
                return notes_field
            return None

# =====================================================================
# 4. Engine & Projection Layer
# =====================================================================

class CandidateTransformerEngine:
    """Core translation engine managing ingestion, consolidation, and JSON projection."""

    def __init__(self, config_filepath: str):
        self.config = self._load_config(config_filepath)
        self.candidates: Dict[str, CanonicalCandidate] = {}

    def _load_config(self, filepath: str) -> Dict[str, Any]:
        """Safely loads and defaults the runtime config file."""
        default_cfg = {
            "projection": {
                "fields": ["name", "email", "phone", "location", "skills"],
                "phone_normalization": "E164",
                "skills_normalization": "lowercase",
                "include_provenance": True,
                "on_missing": "null"
            }
        }
        try:
            with open(filepath, mode='r', encoding='utf-8') as f:
                cfg = json.load(f)
                # Ensure schema layout is correct
                if "projection" not in cfg:
                    cfg["projection"] = default_cfg["projection"]
                else:
                    for k, v in default_cfg["projection"].items():
                        cfg["projection"].setdefault(k, v)
                return cfg
        except Exception as e:
            print(f"Warning: Failed to load config '{filepath}': {e}. Using defaults.", file=sys.stderr)
            return default_cfg

    def ingest_sources(self, csv_path: str, notes_path: str) -> None:
        """Loads and consolidates records from structured CSV and unstructured notes."""
        csv_records = SourceReader.read_csv(csv_path)
        notes_records = SourceReader.read_notes(notes_path)

        temp_csv_candidates: Dict[str, CanonicalCandidate] = {}
        temp_notes_candidates: Dict[str, CanonicalCandidate] = {}

        # 1. Process Structured CSV Records (Confidence: 0.9)
        csv_meta = FieldMetadata(source=csv_path, method="CSV Parsing", confidence=0.9)
        for row in csv_records:
            email = row.get("email", "").strip().lower()
            if not email:
                continue # Skip records missing primary identifier

            # Populate CSV candidate representations
            candidate = CanonicalCandidate()
            if row.get("name"):
                candidate.name = FieldValue(row["name"], csv_meta)
            candidate.email = FieldValue(row["email"], csv_meta)
            if row.get("phone"):
                candidate.phone = FieldValue(row["phone"], csv_meta)
            
            # Since CSV doesn't have location/skills fields in our sample schema, leave as None
            temp_csv_candidates[email] = candidate

        # 2. Process Unstructured Notes Records (Confidence: 0.6)
        notes_meta = FieldMetadata(source=notes_path, method="Regex Extraction", confidence=0.6)
        for record in notes_records:
            email = record.get("email")
            if email:
                email = email.strip().lower()
            if not email:
                continue

            candidate = CanonicalCandidate()
            if record.get("name"):
                candidate.name = FieldValue(record["name"], notes_meta)
            candidate.email = FieldValue(record["email"], notes_meta)
            if record.get("phone"):
                candidate.phone = FieldValue(record["phone"], notes_meta)
            if record.get("location"):
                candidate.location = FieldValue(record["location"], notes_meta)
            if record.get("skills"):
                candidate.skills = FieldValue(record["skills"], notes_meta)

            temp_notes_candidates[email] = candidate

        # 3. Consolidate and Deduplicate (Deterministic Merge)
        all_emails = set(temp_csv_candidates.keys()).union(temp_notes_candidates.keys())

        for email in all_emails:
            csv_cand = temp_csv_candidates.get(email)
            notes_cand = temp_notes_candidates.get(email)

            if csv_cand and notes_cand:
                merged = CanonicalCandidate(
                    name=ConflictResolver.merge_field(csv_cand.name, notes_cand.name),
                    email=ConflictResolver.merge_field(csv_cand.email, notes_cand.email),
                    phone=ConflictResolver.merge_field(csv_cand.phone, notes_cand.phone),
                    location=ConflictResolver.merge_field(csv_cand.location, notes_cand.location),
                    skills=ConflictResolver.merge_field(csv_cand.skills, notes_cand.skills, is_list=True)
                )
                self.candidates[email] = merged
            elif csv_cand:
                self.candidates[email] = csv_cand
            else:
                self.candidates[email] = notes_cand

    def _normalize_phone(self, phone_str: str) -> str:
        """Normalizes a phone number to strict E.164 formatting."""
        digits = "".join(c for c in phone_str if c.isdigit())
        if phone_str.startswith('+'):
            return "+" + digits
        
        # Format heuristics
        if len(digits) == 10:
            return "+91" + digits  # Standard fallback to Country code (+91 in Amit's case)
        elif len(digits) == 11 and digits.startswith('1'):
            return "+" + digits
        elif len(digits) == 7:
            return "+1" + digits  # USA local number fallback
        else:
            return "+" + digits if digits else phone_str

    def _normalize_skills(self, skills: List[str], casing: str) -> List[str]:
        """Normalizes skill tokens by mapping casing conventions."""
        if casing == "lowercase":
            return [s.lower() for s in skills]
        elif casing == "uppercase":
            return [s.upper() for s in skills]
        elif casing == "titlecase":
            return [s.title() for s in skills]
        return skills

    def project_profiles(self) -> List[Dict[str, Any]]:
        """Filters, normalizes, and packages canonical candidate profiles according to configuration."""
        proj_rules = self.config["projection"]
        fields_to_include = proj_rules.get("fields", [])
        phone_norm = proj_rules.get("phone_normalization", "none")
        skills_norm = proj_rules.get("skills_normalization", "none")
        include_provenance = proj_rules.get("include_provenance", False)
        on_missing = proj_rules.get("on_missing", "null")

        projected_list: List[Dict[str, Any]] = []

        # Sort emails to ensure deterministic output order
        for email in sorted(self.candidates.keys()):
            candidate = self.candidates[email]
            projected_record: Dict[str, Any] = {}
            
            for field_name in fields_to_include:
                # Retrieve the canonical field wrapper from candidate
                field_val_obj: Optional[FieldValue] = getattr(candidate, field_name, None)

                # 1. Handle missing values
                if not field_val_obj or field_val_obj.value is None:
                    if on_missing == "error":
                        raise ValueError(f"Constraint Violation: Field '{field_name}' is missing for candidate '{email}'")
                    elif on_missing == "omit":
                        continue
                    else:  # on_missing == "null" or default
                        if include_provenance:
                            projected_record[field_name] = {
                                "value": None,
                                "source": "none",
                                "confidence": 0.0
                            }
                        else:
                            projected_record[field_name] = None
                        continue

                # 2. Extract and Normalize values
                raw_value = field_val_obj.value
                normalized_value = raw_value

                if field_name == "phone" and phone_norm == "E164" and isinstance(raw_value, str):
                    normalized_value = self._normalize_phone(raw_value)
                elif field_name == "skills" and isinstance(raw_value, list):
                    normalized_value = self._normalize_skills(raw_value, skills_norm)

                # 3. Assemble record with or without provenance
                if include_provenance:
                    projected_record[field_name] = {
                        "value": normalized_value,
                        "source": field_val_obj.metadata.source,
                        "confidence": field_val_obj.metadata.confidence
                    }
                else:
                    projected_record[field_name] = normalized_value

            projected_list.append(projected_record)

        return projected_list

# =====================================================================
# 5. CLI Execution Entrypoint
# =====================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Source Candidate Data Transformer CLI")
    parser.add_argument("--config", default="config.json", help="Path to config.json file")
    parser.add_argument("--csv", default="sample_recruiter.csv", help="Path to sample recruiter CSV file")
    parser.add_argument("--notes", default="sample_notes.txt", help="Path to sample unstructured notes file")
    args = parser.parse_args()

    engine = CandidateTransformerEngine(args.config)
    
    # Isolation barrier execution
    try:
        engine.ingest_sources(args.csv, args.notes)
        output_profiles = engine.project_profiles()
        # Output clean pretty-printed JSON schema to standard output
        print(json.dumps(output_profiles, indent=2))
    except Exception as e:
        print(f"Execution Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
