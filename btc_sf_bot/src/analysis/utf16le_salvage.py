import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

# Read as utf-16le
try:
    with open(file_path, 'r', encoding='utf-16le') as f:
        content = f.read()
    
    if 'class IOFAnalyzer' in content:
        print("FOUND! Salvaging...")
        # Save as clean utf-8
        with open(file_path + '.salvaged', 'w', encoding='utf-8') as f2:
            f2.write(content)
        print("Saved to .salvaged")
    else:
        print("Not found in utf-16le.")
except Exception as e:
    print(f"Read error: {e}")
