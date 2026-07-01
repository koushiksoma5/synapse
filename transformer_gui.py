import sys
import os
import re
import json
import csv
import io
import http.server
import socketserver
import webbrowser
import hashlib
from typing import List, Dict, Any, Optional

# =====================================================================
# 1. Normalization & Extract Helpers
# =====================================================================

def clean_full_name(name: str) -> str:
    if not name: return ""
    name = re.sub(r'^\s*(?:mr|ms|mrs|dr|prof)\.?\s+', '', name, flags=re.IGNORECASE)
    return " ".join(w.capitalize() for w in re.sub(r'\s+', ' ', name).strip().split())

def normalize_phone(phone: str) -> str:
    if not phone: return ""
    digits = "".join(c for c in phone if c.isdigit())
    if phone.startswith('+'): return "+" + digits
    if len(digits) == 10: return "+91" + digits
    if len(digits) == 11 and digits.startswith('1'): return "+" + digits
    if len(digits) == 7: return "+1" + digits
    return "+" + digits if digits else phone

def parse_location(loc) -> Dict[str, Optional[str]]:
    res = {"city": None, "state": None, "country": None}
    if isinstance(loc, dict):
        res.update({k: loc.get(k) for k in res})
        return res
    if not isinstance(loc, str) or not loc.strip(): return res
    parts = [p.strip() for p in loc.split(',')]
    if len(parts) >= 3:
        return {"city": parts[0], "state": parts[1], "country": ", ".join(parts[2:])}
    if len(parts) == 2:
        p1, p2 = parts[0], parts[1]
        is_us_state = len(p2) == 2 and p2.isupper()
        return {"city": p1, "state": p2 if is_us_state else None, "country": "USA" if is_us_state else p2}
    return {"city": parts[0], "state": None, "country": None}

def extract_years_experience(text: str) -> Optional[float]:
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:\+)?\s*years?(?:\s*of)?\s*experience', text, re.IGNORECASE)
    return float(m.group(1)) if m else None

def extract_headline(text: str) -> Optional[str]:
    m = re.search(r'(?:headline|tagline|title|current role):\s*([^\n\.]+)', text, re.IGNORECASE)
    return m.group(1).strip() if m else None

def extract_links(text: str) -> List[Dict[str, str]]:
    links = []
    for url in re.findall(r'https?://[^\s,\(\)\[\]\}]+', text):
        url_lower = url.lower()
        platform = "GitHub" if "github.com" in url_lower else ("LinkedIn" if "linkedin.com" in url_lower else "Website")
        links.append({"platform": platform, "url": url.strip()})
    return links

def extract_experience(text: str) -> List[Dict[str, Any]]:
    exp = []
    for m in re.finditer(r'([A-Za-z\s]{3,})\s+at\s+([A-Za-z0-9\s]{3,})\s*\(([^)]+)\)', text):
        title, company, duration = m.groups()
        parts = re.split(r'[-–to]', duration)
        exp.append({
            "job_title": title.strip(), "company": company.strip(),
            "start_date": parts[0].strip(), "end_date": parts[1].strip() if len(parts) == 2 else None,
            "description": None
        })
    return exp

def extract_education(text: str) -> List[Dict[str, Any]]:
    edu = []
    for m in re.finditer(r'([A-Za-z\.\s]{2,})\s+in\s+([A-Za-z\s]{3,})\s+from\s+([A-Za-z0-9\s]{3,})\s*(?:\(([^)]+)\))?', text):
        degree, major, institution, grad_info = m.groups()
        year = re.search(r'\b(19\d{2}|20\d{2})\b', grad_info).group(1) if grad_info else None
        edu.append({"degree": degree.strip(), "major": major.strip(), "institution": institution.strip(), "graduation_year": year})
    return edu

# =====================================================================
# 2. In-Memory Content Ingestion Engine
# =====================================================================

class SourceReader:
    @staticmethod
    def read_csv_content(content: str, filename: str) -> List[Dict[str, Any]]:
        f = io.StringIO(content)
        return [SourceReader.parse_csv_row({k.strip(): (v.strip() if v else "") for k, v in row.items() if k}, filename) for row in csv.DictReader(f)]

    @staticmethod
    def parse_csv_row(row: Dict[str, str], source_name: str) -> Dict[str, Any]:
        norm = {k.lower().replace("_", "").replace(" ", ""): v for k, v in row.items()}
        
        raw_email = norm.get("emails") or norm.get("email") or ""
        emails = json.loads(raw_email) if (raw_email.startswith("[") and raw_email.endswith("]")) else [e.strip() for e in re.split(r'[,;|\s]+', raw_email) if e.strip()]
        
        raw_phone = norm.get("phones") or norm.get("phone") or ""
        phones = json.loads(raw_phone) if (raw_phone.startswith("[") and raw_phone.endswith("]")) else [p.strip() for p in re.split(r'[,;|]+', raw_phone) if p.strip()]
        
        raw_links = norm.get("links") or norm.get("link") or ""
        links = json.loads(raw_links) if (raw_links.startswith("[") and raw_links.endswith("]")) else extract_links(raw_links)
        
        raw_skills = norm.get("skills") or norm.get("skill") or ""
        skills = json.loads(raw_skills) if (raw_skills.startswith("[") and raw_skills.endswith("]")) else [s.strip() for s in re.split(r'[,;\|]+', raw_skills) if s.strip()]
        
        raw_exp = norm.get("experience") or norm.get("jobs") or ""
        experience = json.loads(raw_exp) if (raw_exp.startswith("[") and raw_exp.endswith("]")) else extract_experience(raw_exp)
        
        raw_edu = norm.get("education") or norm.get("degrees") or ""
        education = json.loads(raw_edu) if (raw_edu.startswith("[") and raw_edu.endswith("]")) else extract_education(raw_edu)

        return {
            "candidate_id": norm.get("candidateid") or norm.get("id"),
            "full_name": clean_full_name(norm.get("fullname") or norm.get("name") or ""),
            "emails": emails, "phones": phones,
            "location": parse_location(norm.get("location") or ""),
            "links": links, "headline": norm.get("headline") or norm.get("title"),
            "years_experience": float(norm["years"]) if ("years" in norm and norm["years"].isdigit()) else extract_years_experience(norm.get("yearsexperience") or ""),
            "skills": skills, "experience": experience, "education": education,
            "provenance_source": source_name, "confidence_score": 0.9
        }

    @staticmethod
    def read_json_content(content: str, filename: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(content)
            items = data if isinstance(data, list) else [data]
            return [SourceReader.parse_json_item(item, filename) for item in items]
        except Exception as e:
            print(f"JSON read error: {e}", file=sys.stderr)
            return []

    @staticmethod
    def parse_json_item(item: Dict[str, Any], source: str) -> Dict[str, Any]:
        emails = item.get("emails") or item.get("email") or []
        phones = item.get("phones") or item.get("phone") or []
        links_val = item.get("links") or item.get("link") or []
        links = [{"platform": l.get("platform", "Website"), "url": l.get("url")} if isinstance(l, dict) else {"platform": "Website", "url": l} for l in (links_val if isinstance(links_val, list) else [links_val])]
        
        return {
            "candidate_id": item.get("candidate_id") or item.get("id"),
            "full_name": clean_full_name(item.get("full_name") or item.get("name") or ""),
            "emails": emails if isinstance(emails, list) else [emails],
            "phones": phones if isinstance(phones, list) else [phones],
            "location": parse_location(item.get("location")), "links": links,
            "headline": item.get("headline") or item.get("title"),
            "years_experience": float(item["years_experience"]) if item.get("years_experience") else None,
            "skills": item.get("skills") or [], "experience": item.get("experience") or [], "education": item.get("education") or [],
            "provenance_source": source, "confidence_score": 0.95
        }

    @staticmethod
    def read_notes_content(content: str, filename: str) -> List[Dict[str, Any]]:
        extracted = []
        for segment in re.split(r'(?m)^Candidate Name:|^Name:', content):
            if not segment.strip(): continue
            lines = segment.split('\n')
            full_name = clean_full_name(lines[0].lstrip(':').strip() if lines else "")
            
            emails = list(dict.fromkeys(e.strip().lower() for e in re.findall(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', segment)))
            phones = list(dict.fromkeys(p.strip() for p in re.findall(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,5}\)?(?:[-.\s]?\d{2,5})?[-.\s]?\d{4,5}\b', segment)))
            
            loc_m = re.search(r'(?:based in|relocating to|lives in|located in|location is|location:)\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)?)', segment, re.IGNORECASE)
            location = parse_location(loc_m.group(1).strip() if loc_m else "")
            
            skills = []
            skills_m = re.search(r'(?:skills include|skills|core skills include|skills list)(?:[\s:]+)\s*([^.\n]+)', segment, re.IGNORECASE)
            if skills_m:
                skills = [s.strip().rstrip('.') for s in re.sub(r'\b(and|&)\b', ',', skills_m.group(1), flags=re.IGNORECASE).split(',') if s.strip()]

            id_m = re.search(r'(?:candidate id|id):\s*([a-zA-Z0-9\-]+)', segment, re.IGNORECASE)
            
            extracted.append({
                "candidate_id": id_m.group(1).strip() if id_m else None, "full_name": full_name,
                "emails": emails, "phones": phones, "location": location, "links": extract_links(segment),
                "headline": extract_headline(segment), "years_experience": extract_years_experience(segment),
                "skills": skills, "experience": extract_experience(segment), "education": extract_education(segment),
                "provenance_source": filename, "confidence_score": 0.6
            })
        return extracted

    @staticmethod
    def extract_text_from_pdf(base64_str: str) -> str:
        import base64
        import io
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(base64.b64decode(base64_str)))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            print(f"PDF extract error: {e}", file=sys.stderr)
            return ""

# =====================================================================
# 3. Transitive Deduplication Layer
# =====================================================================

class ConflictResolver:
    @staticmethod
    def create_consolidated_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
        src, conf = raw.get("provenance_source", "Unknown"), raw.get("confidence_score", 0.5)
        return {
            "candidate_id": raw.get("candidate_id"), "full_name": raw.get("full_name"),
            "emails": list(raw.get("emails", [])), "phones": list(raw.get("phones", [])),
            "location": raw.get("location"), "links": list(raw.get("links", [])),
            "headline": raw.get("headline"), "years_experience": raw.get("years_experience"),
            "skills": list(raw.get("skills", [])), "experience": list(raw.get("experience", [])),
            "education": list(raw.get("education", [])), "provenance": [src] if src else [],
            "_field_metadata": {f: (src, conf) for f in ["candidate_id", "full_name", "location", "headline", "years_experience", "emails", "phones", "skills", "links", "experience", "education"]}
        }

    @staticmethod
    def merge_profiles(target: Dict[str, Any], other: Dict[str, Any]):
        target["emails"] = list(dict.fromkeys(target.get("emails", []) + other.get("emails", [])))
        target["phones"] = list(dict.fromkeys(target.get("phones", []) + other.get("phones", [])))
        target["skills"] = list(dict.fromkeys(target.get("skills", []) + other.get("skills", [])))
        
        seen_urls = {l.get("url"): l for l in target.get("links", []) if l.get("url")}
        for l in other.get("links", []):
            if l.get("url") and l.get("url") not in seen_urls: seen_urls[l.get("url")] = l
        target["links"] = list(seen_urls.values())
        
        seen_jobs = {f"{j.get('company')}||{j.get('job_title')}": j for j in target.get("experience", []) if j.get("company") and j.get("job_title")}
        for j in other.get("experience", []):
            if f"{j.get('company')}||{j.get('job_title')}" not in seen_jobs: seen_jobs[f"{j.get('company')}||{j.get('job_title')}"] = j
        target["experience"] = list(seen_jobs.values())
        
        seen_edu = {f"{e.get('degree')}||{e.get('institution')}": e for e in target.get("education", []) if e.get('degree') and e.get('institution')}
        for e in other.get("education", []):
            if f"{e.get('degree')}||{e.get('institution')}" not in seen_edu: seen_edu[f"{e.get('degree')}||{e.get('institution')}"] = e
        target["education"] = list(seen_edu.values())
        
        target["provenance"] = list(dict.fromkeys(target.get("provenance", []) + other.get("provenance", [])))
        
        meta_target = target["_field_metadata"]
        meta_other = other.get("_field_metadata", {})
        default_source, default_conf = other.get("provenance_source", "Unknown"), other.get("confidence_score", 0.5)
        
        for field in ["candidate_id", "full_name", "location", "headline", "years_experience", "emails", "phones", "skills", "links", "experience", "education"]:
            target_val, other_val = target.get(field), other.get(field)
            
            is_empty_target = (target_val is None or target_val == "" or (isinstance(target_val, list) and not target_val) or (isinstance(target_val, dict) and not any(target_val.values())))
            is_empty_other = (other_val is None or other_val == "" or (isinstance(other_val, list) and not other_val) or (isinstance(other_val, dict) and not any(other_val.values())))
            
            if is_empty_target:
                target[field] = other_val
                meta_target[field] = meta_other.get(field, (default_source, default_conf))
            elif not is_empty_other:
                _, target_conf = meta_target.get(field, (default_source, default_conf))
                other_src, other_conf = meta_other.get(field, (default_source, default_conf))
                
                if other_conf > target_conf:
                    if not isinstance(target_val, list): target[field] = other_val
                    meta_target[field] = (other_src, other_conf)
                elif other_conf == target_conf:
                    if not isinstance(target_val, list) and len(str(other_val)) > len(str(target_val)):
                        target[field] = other_val
                        meta_target[field] = (other_src, other_conf)

    @staticmethod
    def deduplicate(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        parent = {}
        def find(i):
            if parent.setdefault(i, i) != i: parent[i] = find(parent[i])
            return parent[i]
        def union(i, j):
            root_i, root_j = find(i), find(j)
            if root_i != root_j: parent[root_i] = root_j

        key_to_index = {}
        for idx, record in enumerate(raw_records):
            keys = []
            if record.get("candidate_id"): keys.append(f"id:{record.get('candidate_id')}")
            for e in [email.lower().strip() for email in record.get("emails", []) if email]:
                keys.append(f"email:{e}")
                
            phones = [normalize_phone(p) for p in record.get("phones", []) if p]
            cleaned_name = clean_full_name(record.get("full_name") or "")
            if cleaned_name and phones:
                for p in phones: keys.append(f"namephone:{cleaned_name}:{p}")
            for l in [link.get("url", "").lower().strip() for link in record.get("links", []) if link and link.get("url")]:
                keys.append(f"link:{l}")
                
            first_match_idx = None
            for key in keys:
                if key in key_to_index:
                    if first_match_idx is None:
                        first_match_idx = key_to_index[key]
                        union(idx, first_match_idx)
                    else:
                        union(key_to_index[key], first_match_idx)
                else:
                    key_to_index[key] = idx
                    
        groups = {}
        for idx in range(len(raw_records)):
            groups.setdefault(find(idx), []).append(raw_records[idx])
            
        consolidated = []
        for root, records in groups.items():
            if not records: continue
            target = ConflictResolver.create_consolidated_from_raw(records[0])
            for record in records[1:]: ConflictResolver.merge_profiles(target, record)
            consolidated.append(target)
            
        for c in consolidated:
            if not c.get("candidate_id"):
                email_seed = sorted(c.get("emails", []))[0] if c.get("emails") else (c.get("full_name") or "unknown")
                uuid_hash = hashlib.md5(email_seed.encode('utf-8')).hexdigest()
                c["candidate_id"] = f"cand-{uuid_hash[:8]}-{uuid_hash[8:12]}-{uuid_hash[12:16]}-{uuid_hash[16:20]}-{uuid_hash[20:32]}"
            
            meta = c["_field_metadata"]
            conf_sum, counted = 0, 0
            for field in ["candidate_id", "full_name", "location", "headline", "years_experience", "emails", "phones", "skills", "links", "experience", "education"]:
                val = c.get(field)
                if val is not None and val != "" and (not isinstance(val, list) or val):
                    conf_sum += meta.get(field, ("", 0.5))[1]
                    counted += 1
            c["overall_confidence"] = round(conf_sum / counted, 2) if counted > 0 else 0.5
            
        return consolidated

# =====================================================================
# 4. Engine Processing & Normalization
# =====================================================================

def normalize_skills(skills: List[str], casing: str) -> List[str]:
    if casing == "lowercase": return [s.lower() for s in skills]
    if casing == "uppercase": return [s.upper() for s in skills]
    if casing == "titlecase": return [s.title() for s in skills]
    return skills

def run_pipeline(csv_files, txt_files, json_files, csv_text, notes_text, json_text, config) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    has_structured = len(csv_files) > 0 or len(json_files) > 0 or bool(csv_text and csv_text.strip()) or bool(json_text and json_text.strip())
    has_unstructured = len(txt_files) > 0 or bool(notes_text and notes_text.strip())
    if not (has_structured and has_unstructured):
        return None

    raw_records = []
    for file in csv_files: raw_records.extend(SourceReader.read_csv_content(file.get("content", ""), file.get("name", "CSV File")))
    if csv_text.strip(): raw_records.extend(SourceReader.read_csv_content(csv_text, "Pasted CSV"))
    for file in json_files: raw_records.extend(SourceReader.read_json_content(file.get("content", ""), file.get("name", "JSON File")))
    if json_text.strip(): raw_records.extend(SourceReader.read_json_content(json_text, "Pasted JSON"))
    for file in txt_files:
        name, content = file.get("name", "TXT Notes"), file.get("content", "")
        raw_records.extend(SourceReader.read_notes_content(SourceReader.extract_text_from_pdf(content) if file.get("is_pdf") else content, name))
    if notes_text.strip(): raw_records.extend(SourceReader.read_notes_content(notes_text, "Pasted Notes"))
        
    consolidated = ConflictResolver.deduplicate(raw_records)
    fields = config.get('fields', ['candidate_id', 'full_name', 'emails', 'phones', 'location', 'links', 'headline', 'years_experience', 'skills', 'experience', 'education'])
    
    normalizations = config.get('normalizations', {})
    phone_norm = normalizations.get('phones', config.get('phone_normalization', 'E164'))
    skills_norm = normalizations.get('skills', config.get('skills_normalization', 'lowercase'))
    
    include_provenance = config.get('include_provenance', True)
    on_missing = config.get('on_missing', 'null')
    rename_map = config.get('rename', {})
    
    master_list, projected_list = [], []
    for cand in consolidated:
        meta = cand["_field_metadata"]
        if cand.get("phones") and phone_norm == "E164":
            cand["phones"] = list(dict.fromkeys(normalize_phone(p) for p in cand["phones"]))
        if cand.get("skills"):
            cand["skills"] = list(dict.fromkeys(normalize_skills(cand["skills"], skills_norm)))
            
        master_record = {}
        for f in ['candidate_id', 'full_name', 'emails', 'phones', 'location', 'links', 'headline', 'years_experience', 'skills', 'experience', 'education']:
            val = cand.get(f)
            if include_provenance:
                f_meta = meta.get(f, ("Unknown", 0.5))
                master_record[f] = {"value": val, "source": f_meta[0], "confidence": f_meta[1]}
            else:
                master_record[f] = val
        master_record["overall_confidence"] = cand.get("overall_confidence", 0.5)
        
        if "candidate_id" not in master_record or (include_provenance and (not isinstance(master_record["candidate_id"], dict) or master_record["candidate_id"].get("value") is None)):
            if include_provenance:
                master_record["candidate_id"] = {"value": cand["candidate_id"], "source": "none", "confidence": 1.0}
            else:
                master_record["candidate_id"] = cand["candidate_id"]
        master_list.append(master_record)
        
        projected = {}
        for f in fields:
            val = cand.get(f)
            target_key = rename_map.get(f, f)
            
            is_empty = (val is None or val == "" or (isinstance(val, list) and not val) or (isinstance(val, dict) and not any(val.values())))
            if is_empty:
                if on_missing == "error":
                    raise ValueError(f"Constraint Fault: Required property '{f}' is absent/empty for candidate ID '{cand.get('candidate_id')}'")
                elif on_missing == "omit":
                    continue
                else: # null assignment
                    if include_provenance:
                        projected[target_key] = {"value": None, "source": "none", "confidence": 0.0}
                    else:
                        projected[target_key] = None
                    continue
                    
            if include_provenance:
                f_meta = meta.get(f, ("Unknown", 0.5))
                projected[target_key] = {"value": val, "source": f_meta[0], "confidence": f_meta[1]}
            else:
                projected[target_key] = val
                
        target_cand_id_name = rename_map.get("candidate_id", "candidate_id")
        if target_cand_id_name not in projected:
            if include_provenance:
                projected[target_cand_id_name] = {"value": cand["candidate_id"], "source": "none", "confidence": 1.0}
            else:
                projected[target_cand_id_name] = cand["candidate_id"]
                
        if include_provenance and fields:
            overall_conf_key = rename_map.get("overall_confidence", "overall_confidence")
            projected[overall_conf_key] = cand.get("overall_confidence", 0.5)
        projected_list.append(projected)
        
    return {"master": master_list, "projected": projected_list}

# =====================================================================
# 5. Local Web Dashboard Server Interface
# =====================================================================

class TransformerHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            with open(html_path, 'r', encoding='utf-8') as f: self.wfile.write(f.read().encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        if self.path == '/api/transform':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            
            try:
                consolidated = run_pipeline(
                    csv_files=payload.get('csv_files', []), txt_files=payload.get('txt_files', []), json_files=payload.get('json_files', []),
                    csv_text=payload.get('csv_text', ''), notes_text=payload.get('notes_text', ''), json_text=payload.get('json_text', ''),
                    config=payload.get('config', {})
                )
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                if consolidated is None:
                    self.wfile.write(json.dumps(None).encode('utf-8'))
                else:
                    self.wfile.write(json.dumps({"master_data": consolidated["master"], "projected_data": consolidated["projected"]}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Exception: {str(e)}"}).encode('utf-8'))

def main():
    PORT = 8000
    for i in range(10):
        try:
            server_address = ('', PORT + i)
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(server_address, TransformerHTTPHandler)
            PORT += i
            break
        except OSError: continue
        
    print(f"Dashboard Serving at: http://localhost:{PORT}")
    webbrowser.open(f"http://localhost:{PORT}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: sys.exit(0)

if __name__ == "__main__":
    main()