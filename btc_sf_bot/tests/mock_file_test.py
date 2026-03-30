import json
import os
import time

def test_file_signal():
    # กำหนดพาธไฟล์ (EA จะมองหาไฟล์ใน MQL5/Files)
    # เราจะลองสร้างไฟล์ในโฟลเดอร์ปัจจุบันก่อน หากคุณรัน EA โดยไม่ได้กำหนดพาธ 
    # มันจะมองหาในโฟลเดอร์หลักของ MT5
    
    signal_data = {
        "signal_id": f"FILE_TEST_{int(time.time())}",
        "direction": "LONG",
        "entry_price": 60000.0,
        "stop_loss": 59000.0,
        "take_profit": 65000.0,
        "lot_size": 0.08, # ทดสอบ Lot 0.08
        "score": 10,
        "reason": "FILE_MOCK_TEST",
        "short_reason": "S10_FILE"
    }
    
    filename = "signal.json"
    
    print(f"📄 Creating mock signal file: {filename}")
    with open(filename, "w") as f:
        json.dump(signal_data, f, indent=4)
        
    print(f"✅ File created with lot_size: 0.08")
    print("Please check MT5 Experts Tab for '📁 FILE SIGNAL' and 'Lot: 0.080'")
    
    # ลบไฟล์หลังจากผ่านไป 10 วินาทีเพื่อไม่ให้สัญญาณค้าง
    time.sleep(10)
    # os.remove(filename)

if __name__ == "__main__":
    test_file_signal()
