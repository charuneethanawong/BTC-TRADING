import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

# Read as latin-1 to see every byte
with open(file_path, 'r', encoding='latin-1') as f:
    data = f.read()

# Look for 'class IOFAnalyzer'
# It starts at the beginning usually
index = data.find('class IOFAnalyzer')
if index != -1:
    print(f"Found class at {index}")
    # Print 500 chars around it
    print("--- START ---")
    print(data[index:index+500])
    print("--- END ---")
else:
    print("Class not found in latin-1 read.")

# Look forGate 2b implementation
sub_search = "elif m5_overbought:"
index2 = data.find(sub_search)
if index2 != -1:
    print(f"Found Gate 2b at {index2}")
    print(data[index2:index2+500])
else:
    print("Gate 2b not found.")
