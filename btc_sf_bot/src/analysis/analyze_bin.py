import sys
with open(r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py', 'rb') as f:
    data = f.read()
    print("Length:", len(data))
    print("Start:", repr(data[:200]))
    idx = data.find(b'\xef\xbb\xbf')
    print("BOM found at:", idx)
    if idx != -1:
        print("After BOM:", repr(data[idx:idx+200]))
