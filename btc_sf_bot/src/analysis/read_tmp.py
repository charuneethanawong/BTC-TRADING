import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\tmp_lines.txt'

# Read as utf-16le
try:
    with open(file_path, 'r', encoding='utf-16le') as f:
        content = f.read()
    print("Content of tmp_lines.txt:")
    print(content[:1000]) # First 1000 chars
except Exception as e:
    print(f"Failed to read: {e}")
