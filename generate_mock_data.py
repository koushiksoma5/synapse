import csv
import json
import random

def generate_mock_data(num_candidates, csv_filename, json_filename):
    first_names = ["John", "Jane", "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Heidi"]
    last_names = ["Doe", "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Martinez"]
    cities = ["San Francisco", "New York", "Austin", "Seattle", "Chicago"]
    skills_pool = ["Python", "Java", "C++", "Go", "Rust", "React", "Node.js", "SQL", "Docker", "AWS"]

    csv_records = []
    json_records = []

    for i in range(num_candidates):
        # Base attributes
        first_name = random.choice(first_names)
        last_name = random.choice(last_names)
        full_name = f"{first_name} {last_name}"
        email1 = f"{first_name.lower()}.{last_name.lower()}{i}@example.com"
        email2 = f"dev.{first_name.lower()}{i}@gmail.com"
        phone = f"+1555000{i:04d}"
        city = random.choice(cities)
        skills = random.sample(skills_pool, k=random.randint(2, 5))
        github_url = f"https://github.com/{first_name.lower()}{i}"
        linkedin_url = f"https://linkedin.com/in/{first_name.lower()}-{last_name.lower()}-{i}"

        # We will create 2-3 fragments for this candidate to force deduplication
        # Fragment 1: Basic info (CSV)
        csv_records.append({
            "name": full_name,
            "email": email1,
            "phone": phone,
            "location": f"{city}, USA",
            "skills": ", ".join(skills[:2]),
            "link": ""
        })

        # Fragment 2: Professional info (JSON) - shares phone and different email
        json_records.append({
            "full_name": full_name,
            "emails": [email2],
            "phones": [phone],
            "location": city,
            "skills": skills,
            "links": [{"platform": "LinkedIn", "url": linkedin_url}],
            "years_experience": random.randint(1, 10)
        })

        # Fragment 3: Code info (CSV) - shares email2
        csv_records.append({
            "name": f"{first_name.upper()} {last_name.upper()}", # Case variation
            "email": email2,
            "phone": "",
            "location": "",
            "skills": "",
            "link": github_url
        })

    # Shuffle to test order independence
    random.shuffle(csv_records)
    random.shuffle(json_records)

    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=["name", "email", "phone", "location", "skills", "link"])
        writer.writeheader()
        writer.writerows(csv_records)
        
    with open(json_filename, 'w', encoding='utf-8') as f:
        json.dump(json_records, f, indent=2)

    print(f"Generated {len(csv_records)} CSV records and {len(json_records)} JSON records.")
    print(f"Total raw records: {len(csv_records) + len(json_records)}")
    print(f"Expected unique candidates: {num_candidates}")

if __name__ == "__main__":
    generate_mock_data(2000, "mock_data_2000.csv", "mock_data_2000.json")
