import time
import pandas as pd
import numpy as np
from src.analysis.ict import ICTAnalyzer

def generate_mock_candles(n=500):
    np.random.seed(42)
    close = 10000 + np.cumsum(np.random.randn(n) * 10)
    open_p = close + np.random.randn(n) * 5
    high = np.maximum(open_p, close) + np.random.rand(n) * 10
    low = np.minimum(open_p, close) - np.random.rand(n) * 10
    volume = np.random.rand(n) * 1000
    
    df = pd.DataFrame({
        'open': open_p,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    })
    df.index = pd.date_range(start='2024-01-01', periods=n, freq='1min')
    return df

def test_ict_speed():
    candles = generate_mock_candles(500)
    analyzer = ICTAnalyzer()
    
    print(f"🧪 Testing ICT Optimization with {len(candles)} candles...")
    
    # Measure find_order_blocks
    start = time.perf_counter()
    obs = analyzer.find_order_blocks(candles)
    end = time.perf_counter()
    print(f"⏱️ find_order_blocks: {(end - start) * 1000:.2f}ms")
    print(f"   Found {len(obs['bullish'])} bullish, {len(obs['bearish'])} bearish OBs")
    
    # Measure find_fvg
    start = time.perf_counter()
    fvgs = analyzer.find_fvg(candles)
    end = time.perf_counter()
    print(f"⏱️ find_fvg: {(end - start) * 1000:.2f}ms")
    print(f"   Found {len(fvgs['bullish'])} bullish, {len(fvgs['bearish'])} bearish FVGs")
    
    # Measure total summary (which calls both)
    start = time.perf_counter()
    summary = analyzer.get_ict_summary(candles, current_price=10000)
    end = time.perf_counter()
    print(f"⏱️ Total ICT Summary: {(end - start) * 1000:.2f}ms")
    
    print("\n✅ Optimization check complete.")

if __name__ == "__main__":
    test_ict_speed()
