import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

# Attempt to read with different encodings
encodings = ['utf-16le', 'utf-8-sig', 'utf-8', 'cp1252']
content = None

for enc in encodings:
    try:
        with open(file_path, 'r', encoding=enc) as f:
            content = f.read()
            # Basic check to see if we read actual characters
            if content and 'class' in content:
                print(f"Successfully read with {enc}")
                break
    except Exception as e:
        continue

if content:
    with open(file_path, 'w', encoding='utf-8-sig') as f:
        f.write(content)
    print("Fixed encoding to utf-8-sig.")
else:
    print("Failed to read file with any tested encoding.")
