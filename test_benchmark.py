import time
from transformer_gui import SourceReader, ConflictResolver

def test_benchmark():
    print("Loading data...")
    with open("mock_data_2000.csv", "r", encoding="utf-8") as f:
        csv_content = f.read()
    with open("mock_data_2000.json", "r", encoding="utf-8") as f:
        json_content = f.read()

    print("Parsing records...")
    raw_csv = SourceReader.read_csv_content(csv_content, "mock_data_2000.csv")
    raw_json = SourceReader.read_json_content(json_content, "mock_data_2000.json")
    
    all_raw = raw_csv + raw_json
    print(f"Total raw records to process: {len(all_raw)}")

    print("Starting deduplication...")
    start_time = time.time()
    consolidated = ConflictResolver.deduplicate(all_raw)
    end_time = time.time()

    print(f"Deduplication finished in {end_time - start_time:.4f} seconds.")
    print(f"Total consolidated records: {len(consolidated)}")

if __name__ == "__main__":
    test_benchmark()
