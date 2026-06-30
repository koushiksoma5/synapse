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
import hashlib
from typing import List, Dict, Any, Optional

# =====================================================================
# 1. Canonical State & Helper Parsers
# =====================================================================

def clean_full_name(name_str: str) -> str:
    """Strips titles and formats names to Capitalized First Last."""
    if not name_str:
        return ""
    # Strip titles case-insensitively
    name_str = re.sub(r'^\s*(?:mr|ms|mrs|dr|prof)\.?\s+', '', name_str, flags=re.IGNORECASE)
    # Strip standard leading/trailing whitespace and double spaces
    name_str = re.sub(r'\s+', ' ', name_str).strip()
    words = [w.capitalize() for w in name_str.split()]
    return " ".join(words)

def normalize_phone(phone_str: str) -> str:
    """Normalizes phone string to E.164 format."""
    if not phone_str:
        return ""
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

def parse_location(loc_val: Any) -> Dict[str, Optional[str]]:
    """Normalizes location values into structured city, state, country objects."""
    if isinstance(loc_val, dict):
        return {
            "city": loc_val.get("city") or None,
            "state": loc_val.get("state") or None,
            "country": loc_val.get("country") or None
        }
    if not isinstance(loc_val, str) or not loc_val.strip():
        return {"city": None, "state": None, "country": None}
    
    parts = [p.strip() for p in loc_val.split(',')]
    if len(parts) >= 3:
        return {
            "city": parts[0],
            "state": parts[1],
            "country": ", ".join(parts[2:])
        }
    elif len(parts) == 2:
        p1, p2 = parts[0], parts[1]
        # Check for US state abbreviation
        if len(p2) == 2 and p2.isupper():
            return {"city": p1, "state": p2, "country": "USA"}
        return {"city": p1, "state": None, "country": p2}
    else:
        return {"city": parts[0], "state": None, "country": None}

def extract_years_experience(text: str) -> Optional[float]:
    """Regex searches for candidate years of experience."""
    patterns = [
        r'(\d+(?:\.\d+)?)\s*(?:\+)?\s*years?(?:\s*of)?\s*experience',
        r'experience:\s*(\d+(?:\.\d+)?)\s*years?',
        r'(\d+(?:\.\d+)?)\+?\s*years?\s+(?:in\s+)?(?:industry|professional|work)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except:
                pass
    return None

def extract_links(text: str) -> List[Dict[str, str]]:
    """Regex extracts URLs and catalogs them by source platform."""
    links = []
    urls = re.findall(r'https?://[^\s\)]+', text)
    for url in urls:
        url_clean = url.rstrip('.,;:')
        url_lower = url_clean.lower()
        platform = "Website"
        if "linkedin.com" in url_lower:
            platform = "LinkedIn"
        elif "github.com" in url_lower:
            platform = "GitHub"
        elif "twitter.com" in url_lower or "x.com" in url_lower:
            platform = "Twitter"
        links.append({"platform": platform, "url": url_clean})
    return links

def extract_headline(text: str) -> Optional[str]:
    """Regex extracts headline or job tagline summaries."""
    match = re.search(r'(?:headline|tagline|title):\s*(.+)', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'looking for\s+(.+?)\s+roles', text, re.IGNORECASE)
    if match:
        return f"Seeking {match.group(1).strip()} Roles"
    return None

def parse_duration(duration_str: str) -> tuple:
    parts = re.split(r'[-–to]', duration_str)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return duration_str.strip(), None

def extract_experience(text: str) -> List[Dict[str, Any]]:
    """Regex extracts experience (job history) from free-form text notes."""
    experience_list = []
    lines = text.split('\n')
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
        # Pattern 1: Title at Company (Start - End)
        m1 = re.search(r'([A-Za-z\s]{3,})\s+at\s+([A-Za-z0-9\s]{3,})\s*\(([^)]+)\)', line_strip)
        if m1:
            title, company, duration = m1.groups()
            start_date, end_date = parse_duration(duration)
            experience_list.append({
                "job_title": title.strip(),
                "company": company.strip(),
                "start_date": start_date,
                "end_date": end_date,
                "description": None
            })
            continue
        # Pattern 2: Worked as Title at Company
        m2 = re.search(r'(?:worked as|was a|employed as)\s+([A-Za-z\s]{3,})\s+at\s+([A-Za-z0-9\s]{3,})(?:\s+from\s+(.*?)\s+to\s+(.*))?', line_strip, re.IGNORECASE)
        if m2:
            title = m2.group(1).strip()
            company = m2.group(2).strip()
            start_date = m2.group(3).strip() if m2.group(3) else None
            end_date = m2.group(4).strip() if m2.group(4) else None
            if end_date and '.' in end_date:
                end_date = end_date.split('.')[0].strip()
            experience_list.append({
                "job_title": title,
                "company": company,
                "start_date": start_date,
                "end_date": end_date,
                "description": None
            })
            continue
    return experience_list

def extract_education(text: str) -> List[Dict[str, Any]]:
    """Regex extracts education credentials from text notes."""
    education_list = []
    lines = text.split('\n')
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
        # Pattern 1: Degree in Major from Institution
        m1 = re.search(r'([A-Za-z\.\s]{2,})\s+in\s+([A-Za-z\s]{3,})\s+from\s+([A-Za-z0-9\s]{3,})\s*(?:\(([^)]+)\))?', line_strip)
        if m1:
            degree, major, institution, grad_info = m1.groups()
            year = None
            if grad_info:
                year_match = re.search(r'\b(19\d{2}|20\d{2})\b', grad_info)
                if year_match:
                    year = year_match.group(1)
            education_list.append({
                "degree": degree.strip(),
                "major": major.strip(),
                "institution": institution.strip(),
                "graduation_year": year
            })
            continue
        # Pattern 2: Graduated from Institution with a Degree in Major
        m2 = re.search(r'graduated from\s+([A-Za-z0-9\s]{3,})\s+with\s+(?:a|an)?\s*([A-Za-z\.\s]{2,})\s+in\s+([A-Za-z\s]{3,})(?:\s+in\s+(\d{4}))?', line_strip, re.IGNORECASE)
        if m2:
            institution, degree, major, year = m2.groups()
            education_list.append({
                "degree": degree.strip(),
                "major": major.strip(),
                "institution": institution.strip(),
                "graduation_year": year
            })
            continue
    return education_list

# =====================================================================
# 2. In-Memory Content Ingestion Engine
# =====================================================================

class SourceReader:
    @staticmethod
    def read_csv_content(content: str, filename: str = "CSV Source") -> List[Dict[str, Any]]:
        records = []
        try:
            f = io.StringIO(content)
            reader = csv.DictReader(f)
            for row in reader:
                clean_row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k is not None}
                records.append(SourceReader.parse_csv_row(clean_row, filename))
        except Exception as e:
            print(f"CSV read error ({filename}): {e}", file=sys.stderr)
        return records

    @staticmethod
    def parse_csv_row(row: Dict[str, str], source_name: str) -> Dict[str, Any]:
        norm_row = {}
        for k, v in row.items():
            if k and v:
                clean_k = k.lower().replace("_", "").replace(" ", "")
                norm_row[clean_k] = v.strip()
                
        raw_name = norm_row.get("fullname") or norm_row.get("name") or ""
        full_name = clean_full_name(raw_name) if raw_name else None
        
        # Emails
        raw_email = norm_row.get("emails") or norm_row.get("email") or ""
        emails = []
        if raw_email:
            if raw_email.startswith("[") and raw_email.endswith("]"):
                try:
                    emails = json.loads(raw_email)
                except:
                    pass
            if not emails:
                emails = [e.strip() for e in re.split(r'[,;|\s]+', raw_email) if e.strip()]
                
        # Phones
        raw_phone = norm_row.get("phones") or norm_row.get("phone") or ""
        phones = []
        if raw_phone:
            if raw_phone.startswith("[") and raw_phone.endswith("]"):
                try:
                    phones = json.loads(raw_phone)
                except:
                    pass
            if not phones:
                phones = [p.strip() for p in re.split(r'[,;|\s]+', raw_phone) if p.strip()]
                
        # Location
        raw_loc = norm_row.get("location") or ""
        location = parse_location(raw_loc) if raw_loc else None
        
        # Links
        raw_links = norm_row.get("links") or norm_row.get("link") or ""
        links = []
        if raw_links:
            if raw_links.startswith("[") and raw_links.endswith("]"):
                try:
                    links = json.loads(raw_links)
                except:
                    pass
            if not links:
                links = extract_links(raw_links)
                
        headline = norm_row.get("headline") or norm_row.get("tagline") or norm_row.get("title")
        
        # Years experience
        raw_years = norm_row.get("yearsexperience") or norm_row.get("experienceyears") or norm_row.get("years") or ""
        years_experience = None
        if raw_years:
            try:
                years_experience = float(raw_years)
            except:
                years_experience = extract_years_experience(raw_years)
                
        # Skills
        raw_skills = norm_row.get("skills") or norm_row.get("skill") or ""
        skills = []
        if raw_skills:
            if raw_skills.startswith("[") and raw_skills.endswith("]"):
                try:
                    skills = json.loads(raw_skills)
                except:
                    pass
            if not skills:
                skills = [s.strip() for s in re.split(r'[,;\|]+', raw_skills) if s.strip()]
                
        # Experience
        raw_exp = norm_row.get("experience") or norm_row.get("jobs") or norm_row.get("workhistory") or ""
        experience = []
        if raw_exp:
            if raw_exp.startswith("[") and raw_exp.endswith("]"):
                try:
                    experience = json.loads(raw_exp)
                except:
                    pass
            if not experience:
                experience = extract_experience(raw_exp)
                
        # Education
        raw_edu = norm_row.get("education") or norm_row.get("degrees") or ""
        education = []
        if raw_edu:
            if raw_edu.startswith("[") and raw_edu.endswith("]"):
                try:
                    education = json.loads(raw_edu)
                except:
                    pass
            if not education:
                education = extract_education(raw_edu)
                
        candidate_id = norm_row.get("candidateid") or norm_row.get("id")
        
        return {
            "candidate_id": candidate_id,
            "full_name": full_name,
            "emails": emails,
            "phones": phones,
            "location": location,
            "links": links,
            "headline": headline,
            "years_experience": years_experience,
            "skills": skills,
            "experience": experience,
            "education": education,
            "provenance_source": source_name,
            "confidence_score": 0.9
        }

    @staticmethod
    def read_json_content(content: str, filename: str = "JSON Source") -> List[Dict[str, Any]]:
        records = []
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                for item in data:
                    records.append(SourceReader.parse_json_item(item, filename))
        except Exception as e:
            print(f"JSON read error ({filename}): {e}", file=sys.stderr)
        return records

    @staticmethod
    def parse_json_item(item: Dict[str, Any], source_name: str) -> Dict[str, Any]:
        name_val = item.get("full_name") or item.get("name")
        full_name = clean_full_name(name_val) if name_val else None
        
        emails_val = item.get("emails") or item.get("email")
        if isinstance(emails_val, str):
            emails = [emails_val]
        elif isinstance(emails_val, list):
            emails = list(emails_val)
        else:
            emails = []
            
        phones_val = item.get("phones") or item.get("phone")
        if isinstance(phones_val, str):
            phones = [phones_val]
        elif isinstance(phones_val, list):
            phones = list(phones_val)
        else:
            phones = []
            
        location = parse_location(item.get("location"))
        
        links_val = item.get("links") or item.get("link") or []
        links = []
        if isinstance(links_val, str):
            links = [{"platform": "Website", "url": links_val}]
        elif isinstance(links_val, list):
            for l in links_val:
                if isinstance(l, dict):
                    links.append({"platform": l.get("platform", "Website"), "url": l.get("url")})
                elif isinstance(l, str):
                    links.append({"platform": "Website", "url": l})
                    
        skills_val = item.get("skills") or item.get("skill") or []
        if isinstance(skills_val, str):
            skills = [skills_val]
        elif isinstance(skills_val, list):
            skills = list(skills_val)
        else:
            skills = []
            
        exp_val = item.get("experience") or item.get("jobs") or []
        experience = []
        if isinstance(exp_val, list):
            for job in exp_val:
                if isinstance(job, dict):
                    experience.append({
                        "job_title": job.get("job_title") or job.get("title"),
                        "company": job.get("company"),
                        "start_date": job.get("start_date") or job.get("start"),
                        "end_date": job.get("end_date") or job.get("end"),
                        "description": job.get("description") or job.get("desc")
                    })
                elif isinstance(job, str):
                    experience.append({
                        "job_title": job,
                        "company": None,
                        "start_date": None,
                        "end_date": None,
                        "description": None
                    })
                    
        edu_val = item.get("education") or item.get("degrees") or []
        education = []
        if isinstance(edu_val, list):
            for edu in edu_val:
                if isinstance(edu, dict):
                    education.append({
                        "degree": edu.get("degree"),
                        "major": edu.get("major") or edu.get("subject"),
                        "institution": edu.get("institution") or edu.get("school") or edu.get("university"),
                        "graduation_year": str(edu.get("graduation_year") or edu.get("year") or "")
                    })
                elif isinstance(edu, str):
                    education.append({
                        "degree": edu,
                        "major": None,
                        "institution": None,
                        "graduation_year": None
                    })
                    
        years_exp = item.get("years_experience") or item.get("years_exp") or item.get("experience_years")
        years_experience = None
        if years_exp is not None:
            try:
                years_experience = float(years_exp)
            except:
                pass
                
        headline = item.get("headline") or item.get("tagline") or item.get("title")
        candidate_id = item.get("candidate_id") or item.get("id")
        
        return {
            "candidate_id": candidate_id,
            "full_name": full_name,
            "emails": emails,
            "phones": phones,
            "location": location,
            "links": links,
            "headline": headline,
            "years_experience": years_experience,
            "skills": skills,
            "experience": experience,
            "education": education,
            "provenance_source": source_name,
            "confidence_score": 0.95
        }

    @staticmethod
    def read_notes_content(content: str, filename: str = "Notes Source") -> List[Dict[str, Any]]:
        extracted = []
        # Split notes by candidate records
        segments = re.split(r'(?m)^Candidate Name:|^Name:', content)
        for segment in segments:
            if not segment.strip():
                continue
            lines = segment.split('\n')
            name_val = lines[0].strip() if lines else ""
            name_val = name_val.lstrip(':').strip()
            full_name = clean_full_name(name_val) if name_val else None
            
            emails = re.findall(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b', segment)
            emails = list(dict.fromkeys([e.strip().lower() for e in emails]))
            
            phones = re.findall(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,5}\)?(?:[-.\s]?\d{2,5})?[-.\s]?\d{4,5}\b', segment)
            phones = list(dict.fromkeys([p.strip() for p in phones]))
            
            location_match = re.search(r'(?:based in|relocating to|lives in|located in|location is|location:)\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z][a-zA-Z\s]+)?)', segment, re.IGNORECASE)
            location_val = location_match.group(1).strip().rstrip('.,') if location_match else None
            location = parse_location(location_val) if location_val else None
            
            skills = []
            skills_match = re.search(r'(?:skills include|skills|core skills include|skills list)(?:[\s:]+)\s*([^.\n]+)', segment, re.IGNORECASE)
            if skills_match:
                normalized_skills = re.sub(r'\b(and|&)\b', ',', skills_match.group(1), flags=re.IGNORECASE)
                skills = [s.strip().rstrip('.') for s in normalized_skills.split(',') if s.strip()]
                
            links = extract_links(segment)
            years_experience = extract_years_experience(segment)
            headline = extract_headline(segment)
            experience = extract_experience(segment)
            education = extract_education(segment)
            
            id_match = re.search(r'(?:candidate id|id):\s*([a-zA-Z0-9\-]+)', segment, re.IGNORECASE)
            candidate_id = id_match.group(1).strip() if id_match else None

            extracted.append({
                "candidate_id": candidate_id,
                "full_name": full_name,
                "emails": emails,
                "phones": phones,
                "location": location,
                "links": links,
                "headline": headline,
                "years_experience": years_experience,
                "skills": skills,
                "experience": experience,
                "education": education,
                "provenance_source": filename,
                "confidence_score": 0.6
            })
        return extracted

    @staticmethod
    def read_url_content(content: str, source_name: str = "Profile Link Ingest") -> List[Dict[str, Any]]:
        import urllib.request
        import urllib.error
        
        records = []
        lines = content.split('\n')
        for line in lines:
            url = line.strip()
            if not url:
                continue
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            url_lower = url.lower()
            
            extracted_name = None
            extracted_headline = None
            
            # Fetch live content to extract candidate details
            try:
                req = urllib.request.Request(
                    url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                with urllib.request.urlopen(req, timeout=3) as response:
                    html_content = response.read().decode('utf-8', errors='ignore')
                    
                    title_match = re.search(r'<title>([^<]+)</title>', html_content)
                    title = title_match.group(1).strip() if title_match else ""
                    
                    if "github.com" in url_lower:
                        name_match = re.search(r'itemprop="name">\s*([^<]+?)\s*<', html_content)
                        if name_match:
                            extracted_name = name_match.group(1).strip()
                        else:
                            paren_match = re.search(r'\(([^)]+)\)', title)
                            if paren_match:
                                extracted_name = paren_match.group(1).strip()
                    elif "linkedin.com" in url_lower:
                        if "linkedin" in title.lower():
                            clean_title = re.sub(r'\s*\|\s*LinkedIn.*$', '', title, flags=re.IGNORECASE).strip()
                            parts = clean_title.split(' - ', 1)
                            if len(parts) >= 1:
                                extracted_name = parts[0].strip()
                            if len(parts) >= 2:
                                extracted_headline = parts[1].strip()
            except Exception as e:
                print(f"URL fetch error for {url}: {e}", file=sys.stderr)
                
            if "github.com" in url_lower:
                match = re.search(r'github\.com/([^/\s\?]+)', url, re.IGNORECASE)
                if match:
                    username = match.group(1)
                    if not extracted_name:
                        parts = re.split(r'[^a-zA-Z]+', username)
                        clean_parts = [p.capitalize() for p in parts if p.strip()]
                        extracted_name = " ".join(clean_parts) or username.capitalize()
                        
                    records.append({
                        "candidate_id": None,
                        "full_name": extracted_name,
                        "emails": [f"{username.lower()}@example.com"],
                        "phones": [],
                        "location": None,
                        "links": [{"platform": "GitHub", "url": url}],
                        "headline": extracted_headline or f"GitHub Profile: {username}",
                        "years_experience": None,
                        "skills": ["Git", "Open Source", "Software Engineering"],
                        "experience": [],
                        "education": [],
                        "provenance_source": url,
                        "confidence_score": 0.8
                    })
            elif "linkedin.com" in url_lower:
                match = re.search(r'linkedin\.com/in/([^/\s\?]+)', url, re.IGNORECASE)
                if match:
                    username = match.group(1)
                    if not extracted_name:
                        parts = re.split(r'[^a-zA-Z]+', username)
                        clean_parts = [p.capitalize() for p in parts if p.strip()]
                        extracted_name = " ".join(clean_parts) or username.capitalize()
                        
                    records.append({
                        "candidate_id": None,
                        "full_name": extracted_name,
                        "emails": [f"{username.lower()}@example.com"],
                        "phones": [],
                        "location": None,
                        "links": [{"platform": "LinkedIn", "url": url}],
                        "headline": extracted_headline or f"LinkedIn Profile: {username}",
                        "years_experience": None,
                        "skills": ["Professional Network", "Industry Experience"],
                        "experience": [],
                        "education": [],
                        "provenance_source": url,
                        "confidence_score": 0.8
                    })
        return records

    @staticmethod
    def extract_text_from_pdf(base64_str: str) -> str:
        """Decodes base64 PDF and extracts text pages using pypdf."""
        import base64
        import io
        try:
            pdf_bytes = base64.b64decode(base64_str)
            from pypdf import PdfReader
            pdf_file = io.BytesIO(pdf_bytes)
            reader = PdfReader(pdf_file)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts)
        except Exception as e:
            print(f"Error parsing PDF resume: {e}", file=sys.stderr)
            return ""

# =====================================================================
# 3. Transitive Deduplication Layer
# =====================================================================

class ConflictResolver:
    @staticmethod
    def create_consolidated_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
        source = raw.get("provenance_source", "Unknown")
        conf = raw.get("confidence_score", 0.5)
        
        return {
            "candidate_id": raw.get("candidate_id"),
            "full_name": raw.get("full_name"),
            "emails": list(raw.get("emails", [])),
            "phones": list(raw.get("phones", [])),
            "location": raw.get("location"),
            "links": list(raw.get("links", [])),
            "headline": raw.get("headline"),
            "years_experience": raw.get("years_experience"),
            "skills": list(raw.get("skills", [])),
            "experience": list(raw.get("experience", [])),
            "education": list(raw.get("education", [])),
            "provenance": [source] if source else [],
            "_field_metadata": {
                "candidate_id": (source, conf),
                "full_name": (source, conf),
                "location": (source, conf),
                "headline": (source, conf),
                "years_experience": (source, conf),
                "emails": (source, conf),
                "phones": (source, conf),
                "skills": (source, conf),
                "links": (source, conf),
                "experience": (source, conf),
                "education": (source, conf)
            }
        }

    @staticmethod
    def merge_profiles(target: Dict[str, Any], other: Dict[str, Any]):
        # Merge lists
        target["emails"] = list(dict.fromkeys(target.get("emails", []) + other.get("emails", [])))
        target["phones"] = list(dict.fromkeys(target.get("phones", []) + other.get("phones", [])))
        target["skills"] = list(dict.fromkeys(target.get("skills", []) + other.get("skills", [])))
        
        # Merge links by unique URL
        seen_urls = {l.get("url"): l for l in target.get("links", []) if l.get("url")}
        for l in other.get("links", []):
            url = l.get("url")
            if url and url not in seen_urls:
                seen_urls[url] = l
        target["links"] = list(seen_urls.values())
        
        # Merge experience
        seen_jobs = {f"{j.get('company')}||{j.get('job_title')}": j for j in target.get("experience", []) if j.get("company") and j.get("job_title")}
        for j in other.get("experience", []):
            key = f"{j.get('company')}||{j.get('job_title')}"
            if key not in seen_jobs:
                seen_jobs[key] = j
        target["experience"] = list(seen_jobs.values())
        
        # Merge education
        seen_edu = {f"{e.get('degree')}||{e.get('institution')}": e for e in target.get("education", []) if e.get('degree') and e.get('institution')}
        for e in other.get("education", []):
            key = f"{e.get('degree')}||{e.get('institution')}"
            if key not in seen_edu:
                seen_edu[key] = e
        target["education"] = list(seen_edu.values())
        
        # Merge provenance
        target["provenance"] = list(dict.fromkeys(target.get("provenance", []) + other.get("provenance", [])))
        
        # Singular & list metadata resolutions
        meta_target = target["_field_metadata"]
        meta_other = other.get("_field_metadata", {})
        
        fields_to_resolve = ["candidate_id", "full_name", "location", "headline", "years_experience", "emails", "phones", "skills", "links", "experience", "education"]
        for field in fields_to_resolve:
            target_val = target.get(field)
            other_val = other.get(field)
            
            # If target has nothing, adopt other
            if target_val is None or target_val == "" or (isinstance(target_val, list) and not target_val):
                target[field] = other_val
                if field in meta_other:
                    meta_target[field] = meta_other[field]
            elif other_val is not None and other_val != "" and (not isinstance(other_val, list) or other_val):
                target_conf = meta_target.get(field, ("", 0.0))[1]
                other_conf = meta_other.get(field, ("", 0.0))[1]
                
                if other_conf > target_conf:
                    if not isinstance(target_val, list):
                        target[field] = other_val
                    meta_target[field] = meta_other.get(field, ("", other_conf))
                elif other_conf == target_conf:
                    # Tie-breaker for strings: length wins
                    if not isinstance(target_val, list) and len(str(other_val)) > len(str(target_val)):
                        target[field] = other_val
                        meta_target[field] = meta_other.get(field, ("", other_conf))

    @staticmethod
    def deduplicate(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        parent = {}
        
        def find(i):
            if parent.setdefault(i, i) != i:
                parent[i] = find(parent[i])
            return parent[i]
            
        def union(i, j):
            root_i = find(i)
            root_j = find(j)
            if root_i != root_j:
                parent[root_i] = root_j

        key_to_index = {}
        
        for idx, record in enumerate(raw_records):
            keys = []
            
            cand_id = record.get("candidate_id")
            if cand_id:
                keys.append(f"id:{cand_id}")
                
            emails = [e.lower().strip() for e in record.get("emails", []) if e]
            for e in emails:
                keys.append(f"email:{e}")
                
            phones = [normalize_phone(p) for p in record.get("phones", []) if p]
            name = record.get("full_name")
            cleaned_name = clean_full_name(name) if name else None
            
            if cleaned_name and phones:
                for p in phones:
                    keys.append(f"namephone:{cleaned_name}:{p}")
                    
            links = [l.get("url", "").lower().strip() for l in record.get("links", []) if l and l.get("url")]
            for l in links:
                keys.append(f"link:{l}")
                
            # Connect this record to any existing records that share keys
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
            root = find(idx)
            groups.setdefault(root, []).append(raw_records[idx])
            
        consolidated: List[Dict[str, Any]] = []
        for root, records in groups.items():
            if not records:
                continue
            
            target = ConflictResolver.create_consolidated_from_raw(records[0])
            for record in records[1:]:
                ConflictResolver.merge_profiles(target, record)
            consolidated.append(target)
                    
        # Post process to guarantee ID and compute confidence
        for c in consolidated:
            if not c.get("candidate_id"):
                email_seed = sorted(c.get("emails", []))[0] if c.get("emails") else (c.get("full_name") or "unknown")
                uuid_hash = hashlib.md5(email_seed.encode('utf-8')).hexdigest()
                c["candidate_id"] = f"cand-{uuid_hash[:8]}-{uuid_hash[8:12]}-{uuid_hash[12:16]}-{uuid_hash[16:20]}-{uuid_hash[20:32]}"
            
            # Overall system confidence is average of choose field confidences
            meta = c["_field_metadata"]
            conf_sum = 0.0
            counted = 0
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
    """Normalizes casing of skill strings."""
    if casing == "lowercase":
        return [s.lower() for s in skills]
    elif casing == "uppercase":
        return [s.upper() for s in skills]
    elif casing == "titlecase":
        return [s.title() for s in skills]
    return skills

def run_pipeline(csv_files: List[Dict[str, str]], txt_files: List[Dict[str, str]], json_files: List[Dict[str, str]], csv_text: str, notes_text: str, json_text: str, url_text: str, github_url: str, linkedin_url: str, config: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    raw_records = []
    
    # 1. Ingest CSV Files
    for file in csv_files:
        raw_records.extend(SourceReader.read_csv_content(file.get("content", ""), file.get("name", "CSV File")))
        
    # 2. Ingest CSV Paste Text
    if csv_text.strip():
        raw_records.extend(SourceReader.read_csv_content(csv_text, "Pasted CSV"))
        
    # 3. Ingest JSON Files
    for file in json_files:
        raw_records.extend(SourceReader.read_json_content(file.get("content", ""), file.get("name", "JSON File")))
        
    # 4. Ingest JSON Paste Text
    if json_text.strip():
        raw_records.extend(SourceReader.read_json_content(json_text, "Pasted JSON"))
        
    # 5. Ingest Notes & Resume Files
    for file in txt_files:
        name = file.get("name", "TXT Notes")
        content = file.get("content", "")
        if file.get("is_pdf", False):
            extracted_text = SourceReader.extract_text_from_pdf(content)
            raw_records.extend(SourceReader.read_notes_content(extracted_text, name))
        else:
            raw_records.extend(SourceReader.read_notes_content(content, name))
        
    # 6. Ingest Notes Paste Text
    if notes_text.strip():
        raw_records.extend(SourceReader.read_notes_content(notes_text, "Pasted Notes"))
        
    # 6b. Ingest URL Paste Text
    if url_text.strip():
        raw_records.extend(SourceReader.read_url_content(url_text, "Pasted URLs"))
        
    # 6c. Ingest GitHub URL
    if github_url.strip():
        raw_records.extend(SourceReader.read_url_content(github_url, "GitHub URL Ingest"))
        
    # 6d. Ingest LinkedIn URL
    if linkedin_url.strip():
        raw_records.extend(SourceReader.read_url_content(linkedin_url, "LinkedIn URL Ingest"))
        
    # 7. Run Match and Merge Deduplication
    consolidated = ConflictResolver.deduplicate(raw_records)
    
    # 8. Project fields and apply configurations
    fields_to_project = config.get('fields', ['candidate_id', 'full_name', 'emails', 'phones', 'location', 'links', 'headline', 'years_experience', 'skills', 'experience', 'education'])
    
    # Support per-field normalizations mapping
    normalizations = config.get('normalizations', {})
    phone_norm = normalizations.get('phones', config.get('phone_normalization', 'E164'))
    skills_norm = normalizations.get('skills', config.get('skills_normalization', 'lowercase'))
    
    include_provenance = config.get('include_provenance', True)
    on_missing = config.get('on_missing', 'null')
    rename_map = config.get('rename', {})
    
    master_list = []
    projected_list = []
    
    for cand in consolidated:
        meta = cand["_field_metadata"]
        
        # Apply Normalizations to Candidate Fields
        if cand.get("phones"):
            if phone_norm == "E164":
                cand["phones"] = list(dict.fromkeys([normalize_phone(p) for p in cand["phones"]]))
        if cand.get("skills"):
            cand["skills"] = list(dict.fromkeys(normalize_skills(cand["skills"], skills_norm)))
            
        # 1. Compile immutable Master record (all canonical fields, original key names)
        master_record = {}
        all_canonical_fields = ['candidate_id', 'full_name', 'emails', 'phones', 'location', 'links', 'headline', 'years_experience', 'skills', 'experience', 'education']
        for field in all_canonical_fields:
            val = cand.get(field)
            if include_provenance:
                field_meta = meta.get(field, ("Unknown", 0.5))
                master_record[field] = {
                    "value": val,
                    "source": field_meta[0],
                    "confidence": field_meta[1]
                }
            else:
                master_record[field] = val
        if include_provenance:
            master_record["provenance"] = cand["provenance"]
            master_record["overall_confidence"] = cand["overall_confidence"]
        
        # Ensure candidate_id is present as flat field in master
        if "candidate_id" not in master_record or (include_provenance and (not isinstance(master_record["candidate_id"], dict) or master_record["candidate_id"].get("value") is None)):
            if include_provenance:
                master_record["candidate_id"] = {"value": cand["candidate_id"], "source": "none", "confidence": 1.0}
            else:
                master_record["candidate_id"] = cand["candidate_id"]
        master_list.append(master_record)
            
        # 2. Compile Projected record (selected fields, renamed key names, policy checks)
        projected_record = {}
        for field in fields_to_project:
            val = cand.get(field)
            projected_name = rename_map.get(field, field)
            
            # Check missing field policy
            if val is None or val == "" or (isinstance(val, list) and not val):
                if on_missing == "error":
                    raise ValueError(f"Constraint Fault: Required property '{field}' is absent/empty for candidate ID '{cand.get('candidate_id')}'")
                elif on_missing == "omit":
                    continue
                else: # null assignment
                    if include_provenance:
                        projected_record[projected_name] = {"value": None, "source": "none", "confidence": 0.0}
                    else:
                        projected_record[projected_name] = None
                    continue
                    
            if include_provenance:
                field_meta = meta.get(field, ("Unknown", 0.5))
                projected_record[projected_name] = {
                    "value": val,
                    "source": field_meta[0],
                    "confidence": field_meta[1]
                }
            else:
                projected_record[projected_name] = val
                
        # Append structural metadata fields only if include_provenance is enabled
        if include_provenance:
            prov_name = rename_map.get("provenance", "provenance")
            conf_name = rename_map.get("overall_confidence", "overall_confidence")
            projected_record[prov_name] = cand["provenance"]
            projected_record[conf_name] = cand["overall_confidence"]
        
        # Make sure candidate_id is included as a flat field if not already projected
        target_cand_id_name = rename_map.get("candidate_id", "candidate_id")
        if target_cand_id_name not in projected_record:
            projected_record[target_cand_id_name] = cand["candidate_id"]
            
        projected_list.append(projected_record)
        
    return {
        "master": master_list,
        "projected": projected_list
    }

# =====================================================================
# 5. Local HTTP Server & API Endpoints
# =====================================================================

class TransformerHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return # Mute logs to console

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode('utf-8'))
            except Exception as e:
                self.wfile.write(f"Error loading index.html: {e}".encode('utf-8'))
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
                json_files = payload.get('json_files', [])
                csv_text = payload.get('csv_text', '')
                notes_text = payload.get('notes_text', '')
                json_text = payload.get('json_text', '')
                url_text = payload.get('url_text', '')
                github_url = payload.get('github_url', '')
                linkedin_url = payload.get('linkedin_url', '')
                config = payload.get('config', {})
                
                has_structured = len(csv_files) > 0 or len(csv_text.strip()) > 0 or len(json_files) > 0 or len(json_text.strip()) > 0
                has_unstructured = len(txt_files) > 0 or len(notes_text.strip()) > 0 or len(url_text.strip()) > 0 or len(github_url.strip()) > 0 or len(linkedin_url.strip()) > 0
                
                if not has_structured and not has_unstructured:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Validation Error: At least one structured or unstructured input source is mandatory."}).encode('utf-8'))
                    return
                
                consolidated = run_pipeline(
                    csv_files=csv_files,
                    txt_files=txt_files,
                    json_files=json_files,
                    csv_text=csv_text,
                    notes_text=notes_text,
                    json_text=json_text,
                    url_text=url_text,
                    github_url=github_url,
                    linkedin_url=linkedin_url,
                    config=config
                )
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "master_data": consolidated["master"],
                    "projected_data": consolidated["projected"]
                }).encode('utf-8'))
                
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
    
    webbrowser.open(f"http://localhost:{PORT}")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server gracefully...")
        sys.exit(0)

if __name__ == "__main__":
    main()