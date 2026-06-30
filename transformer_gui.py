#!/usr/bin/env python3
import sys
import csv
import json
import re
import os
import io
import http.server
import socketserver
import webbrowser
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# =====================================================================
# 1. Canonical State & Dataclasses
# =====================================================================

@dataclass(frozen=True)
class FieldMetadata:
    source: str
    method: str
    confidence: float

@dataclass(frozen=True)
class FieldValue:
    value: Any
    metadata: FieldMetadata

@dataclass
class CanonicalCandidate:
    name: Optional[FieldValue] = None
    email: Optional[FieldValue] = None
    phone: Optional[FieldValue] = None
    location: Optional[FieldValue] = None
    skills: Optional[FieldValue] = None

# =====================================================================
# 2. In-Memory Content Ingestion Engine
# =====================================================================

class SourceReader:
    @staticmethod
    def read_csv_content(content: str, filename: str = "CSV Source") -> List[Dict[str, str]]:
        records = []
        try:
            f = io.StringIO(content)
            reader = csv.DictReader(f)
            for row in reader:
                records.append({k.strip(): (v.strip() if v else "") for k, v in row.items()})
        except Exception as e:
            print(f"CSV read error ({filename}): {e}", file=sys.stderr)
        return records

    @staticmethod
    def read_notes_content(content: str, filename: str = "Notes Source") -> List[Dict[str, Any]]:
        extracted = []
        # Split notes by candidate records
        segments = content.split("Candidate Name:")
        for segment in segments:
            if not segment.strip():
                continue
            lines = segment.split('\n')
            name_val = lines[0].strip() if lines else ""
            
            # Regex for Email
            email_match = re.search(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', segment)
            email_val = email_match.group(0).strip() if email_match else None
            
            # Regex for Phone
            phone_match = re.search(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}\b', segment)
            phone_val = phone_match.group(0).strip() if phone_match else None
            
            # Regex for Location
            location_match = re.search(r'(?:based in|relocating to|lives in|located in|location is)\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)?)', segment, re.IGNORECASE)
            location_val = location_match.group(1).strip().rstrip('.,') if location_match else None

            # Regex for Skills
            skills_val = []
            skills_match = re.search(r'(?:skills include|skills:|core skills include)\s+([^.\n]+)', segment, re.IGNORECASE)
            if skills_match:
                normalized_skills = re.sub(r'\b(and|&)\b', ',', skills_match.group(1), flags=re.IGNORECASE)
                skills_val = [s.strip().rstrip('.') for s in normalized_skills.split(',') if s.strip()]

            extracted.append({
                "name": name_val if name_val else None, 
                "email": email_val, 
                "phone": phone_val, 
                "location": location_val, 
                "skills": skills_val if skills_val else None
            })
        return extracted

# =====================================================================
# 3. Conflict Resolution Layer
# =====================================================================

class ConflictResolver:
    @staticmethod
    def merge_field(existing_field: Optional[FieldValue], new_field: Optional[FieldValue], is_list: bool = False) -> Optional[FieldValue]:
        """Resolves field conflicts deterministically using confidence scoring."""
        if not existing_field:
            return new_field
        if not new_field:
            return existing_field

        if is_list:
            existing_list = existing_field.value if existing_field.value else []
            new_list = new_field.value if new_field.value else []
            combined = list(dict.fromkeys(existing_list + new_list))
            meta = existing_field.metadata if existing_field.metadata.confidence >= new_field.metadata.confidence else new_field.metadata
            return FieldValue(combined, meta)
        else:
            # Single value: higher confidence wins
            if new_field.metadata.confidence > existing_field.metadata.confidence:
                return new_field
            return existing_field

# =====================================================================
# 4. Engine Processing & Normalization
# =====================================================================

def normalize_phone(phone_str: str) -> str:
    """Normalizes phone string to E.164 formats."""
    digits = "".join(c for c in phone_str if c.isdigit())
    if phone_str.startswith('+'):
        return "+" + digits
    
    if len(digits) == 10:
        return "+91" + digits
    elif len(digits) == 11 and digits.startswith('1'):
        return "+" + digits
    elif len(digits) == 7:
        return "+1" + digits
    else:
        return "+" + digits if digits else phone_str

def normalize_skills(skills: List[str], casing: str) -> List[str]:
    """Normalizes case formats for skill lists."""
    if casing == "lowercase":
        return [s.lower() for s in skills]
    elif casing == "uppercase":
        return [s.upper() for s in skills]
    elif casing == "titlecase":
        return [s.title() for s in skills]
    return skills

def ingest_csv_records(candidates: Dict[str, CanonicalCandidate], records: List[Dict[str, str]], meta: FieldMetadata):
    for row in records:
        email = row.get("email", "").strip().lower()
        if not email:
            continue
        
        new_cand = CanonicalCandidate(email=FieldValue(email, meta))
        if row.get("name"): new_cand.name = FieldValue(row["name"], meta)
        if row.get("phone"): new_cand.phone = FieldValue(row["phone"], meta)
        
        if email in candidates:
            current = candidates[email]
            current.name = ConflictResolver.merge_field(current.name, new_cand.name)
            current.phone = ConflictResolver.merge_field(current.phone, new_cand.phone)
        else:
            candidates[email] = new_cand

def ingest_notes_records(candidates: Dict[str, CanonicalCandidate], records: List[Dict[str, Any]], meta: FieldMetadata):
    for rec in records:
        email = rec.get("email")
        if email:
            email = email.strip().lower()
        if not email:
            continue
        
        new_cand = CanonicalCandidate(email=FieldValue(email, meta))
        if rec.get("name"): new_cand.name = FieldValue(rec["name"], meta)
        if rec.get("phone"): new_cand.phone = FieldValue(rec["phone"], meta)
        if rec.get("location"): new_cand.location = FieldValue(rec["location"], meta)
        if rec.get("skills"): new_cand.skills = FieldValue(rec["skills"], meta)
        
        if email in candidates:
            current = candidates[email]
            current.name = ConflictResolver.merge_field(current.name, new_cand.name)
            current.phone = ConflictResolver.merge_field(current.phone, new_cand.phone)
            current.location = ConflictResolver.merge_field(current.location, new_cand.location)
            current.skills = ConflictResolver.merge_field(current.skills, new_cand.skills, is_list=True)
        else:
            candidates[email] = new_cand

def run_pipeline(csv_files: List[Dict[str, str]], txt_files: List[Dict[str, str]], csv_text: str, notes_text: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: Dict[str, CanonicalCandidate] = {}
    
    # 1. Digest all CSV file selections (Confidence: 0.9)
    for csv_file in csv_files:
        name = csv_file.get('name', 'CSV Source')
        content = csv_file.get('content', '')
        records = SourceReader.read_csv_content(content, name)
        csv_meta = FieldMetadata(name, "CSV Ingest", 0.9)
        ingest_csv_records(candidates, records, csv_meta)
        
    # 2. Digest Pasted CSV content
    if csv_text.strip():
        records = SourceReader.read_csv_content(csv_text, "Pasted CSV")
        csv_meta = FieldMetadata("Pasted CSV", "CSV Ingest", 0.9)
        ingest_csv_records(candidates, records, csv_meta)
        
    # 3. Digest all Notes file selections (Confidence: 0.6)
    for txt_file in txt_files:
        name = txt_file.get('name', 'TXT Notes Source')
        content = txt_file.get('content', '')
        records = SourceReader.read_notes_content(content, name)
        notes_meta = FieldMetadata(name, "Regex Extraction", 0.6)
        ingest_notes_records(candidates, records, notes_meta)
        
    # 4. Digest Pasted Notes content
    if notes_text.strip():
        records = SourceReader.read_notes_content(notes_text, "Pasted Notes")
        notes_meta = FieldMetadata("Pasted Notes", "Regex Extraction", 0.6)
        ingest_notes_records(candidates, records, notes_meta)
        
    # 5. Extract fields and apply config specifications
    fields_to_include = config.get('fields', ['name', 'email', 'phone', 'location', 'skills'])
    phone_norm = config.get('phone_normalization', 'E164')
    skills_norm = config.get('skills_normalization', 'lowercase')
    include_provenance = config.get('include_provenance', True)
    on_missing = config.get('on_missing', 'null')
    
    projected_list = []
    
    for email in sorted(candidates.keys()):
        cand = candidates[email]
        projected_record = {}
        total_confidence_acc = 0.0
        fields_counted = 0
        
        for field_name in fields_to_include:
            field_val_obj = getattr(cand, field_name, None)
            
            if not field_val_obj or field_val_obj.value is None:
                if on_missing == "error":
                    raise ValueError(f"Constraint Fault: Required property '{field_name}' absent for candidate key '{email}'")
                elif on_missing == "omit":
                    continue
                else: # 'null' assignment
                    if include_provenance:
                        projected_record[field_name] = {"value": None, "source": "none", "confidence": 0.0}
                    else:
                        projected_record[field_name] = None
                    continue
            
            raw_value = field_val_obj.value
            normalized_value = raw_value
            
            if field_name == "phone" and phone_norm == "E164" and isinstance(raw_value, str):
                normalized_value = normalize_phone(raw_value)
            elif field_name == "skills" and isinstance(raw_value, list):
                normalized_value = normalize_skills(raw_value, skills_norm)
                
            if include_provenance:
                projected_record[field_name] = {
                    "value": normalized_value,
                    "source": field_val_obj.metadata.source,
                    "confidence": field_val_obj.metadata.confidence
                }
            else:
                projected_record[field_name] = normalized_value
                
            total_confidence_acc += field_val_obj.metadata.confidence
            fields_counted += 1
            
        if include_provenance and fields_counted > 0:
            projected_record["overall_confidence"] = round(total_confidence_acc / fields_counted, 2)
            
        projected_list.append(projected_record)
        
    return projected_list

# =====================================================================
# 5. Local HTTP Server & API Endpoints
# =====================================================================

class TransformerHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Mute standard request logger spam in console
        return

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            
            # Read index.html from workspace directory
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            except Exception as e:
                self.wfile.write(f"Error loading index.html from local filesystem: {e}".encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == '/api/transform':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                
                csv_files = payload.get('csv_files', [])
                txt_files = payload.get('txt_files', [])
                csv_text = payload.get('csv_text', '')
                notes_text = payload.get('notes_text', '')
                config = payload.get('config', {})
                
                # Check validation: at least 1 input source in total is required
                has_structured = len(csv_files) > 0 or len(csv_text.strip()) > 0
                has_unstructured = len(txt_files) > 0 or len(notes_text.strip()) > 0
                
                if not has_structured and not has_unstructured:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Validation Error: At least one structured or unstructured input source is mandatory."}).encode('utf-8'))
                    return
                
                # Run engine
                consolidated = run_pipeline(csv_files, txt_files, csv_text, notes_text, config)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"data": consolidated}).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Internal pipeline exception: {str(e)}"}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

# =====================================================================
# 6. Execution Entry Point
# =====================================================================

def main():
    PORT = 8000
    max_retries = 10
    httpd = None
    
    # Locate open local port
    for i in range(max_retries):
        current_port = PORT + i
        try:
            server_address = ('', current_port)
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(server_address, TransformerHTTPHandler)
            PORT = current_port
            break
        except OSError:
            print(f"Port {current_port} is busy, checking next...")
            continue
            
    if not httpd:
        print("Error: Failed to bind to any local port in the range 8000-8009.", file=sys.stderr)
        sys.exit(1)
        
    print(f"================================================================")
    print(f"Candidate Profile Transformer Ingest Dashboard Ready")
    print(f"Serving Interface: http://localhost:{PORT}")
    print(f"Press Ctrl+C in this terminal to shutdown the server")
    print(f"================================================================")
    
    # Automatically open local browser window
    webbrowser.open(f"http://localhost:{PORT}")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server gracefully...")
        sys.exit(0)

if __name__ == "__main__":
    main()