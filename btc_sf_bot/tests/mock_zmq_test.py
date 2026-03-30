import zmq
import json
import time
from datetime import datetime

def test_system():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    # บังคับให้เป็น Host (BIND) เพื่อให้ EA (ที่ทำหน้าที่เป็น Connect) รับข้อมูลได้
    socket.bind("tcp://127.0.0.1:5555")
    print("🚀 Mock Signal Server Started (BOUND to 5555)...")
    print("Waiting 3 seconds for ZMQ connection to stabilize...")
    time.sleep(3) 

    # 1. Test Heartbeat
    print("\n[1] Sending Heartbeat Topic...")
    hb_data = {
        "status": "OK",
        "timestamp": datetime.now().isoformat()
    }
    # ส่ง 3 ครั้งเพื่อให้ชัวร์ว่า EA ได้รับ
    for _ in range(3):
        socket.send_string(f"heartbeat {json.dumps(hb_data)}")
        time.sleep(0.5)
    print("✅ Heartbeat sent. Check EA Dashboard for 'System: 🟢 READY'")
    time.sleep(2)

    # 2. Test Trade Signal with Dynamic Lot
    print("\n[2] Sending Trade Signal (Lot: 0.08)...")
    trade_signal = {
        "signal_id": f"TEST_LOT_{int(time.time())}", # ใช้ timestamp เพื่อให้ ID ไม่ซ้ำ
        "direction": "LONG",
        "entry_price": 60000.0,
        "stop_loss": 59500.0,
        "take_profit": 62000.0,
        "lot_size": 0.08,
        "score": 9,
        "reason": "MOCK_TEST",
        "short_reason": "S9_MOCK"
    }
    # ส่ง 3 ครั้งเพื่อให้ชัวร์
    for _ in range(3):
        socket.send_string(f"signal {json.dumps(trade_signal)}")
        time.sleep(0.5)
    print(f"✅ Trade Signal {trade_signal['signal_id']} sent. Check EA Journal for 'Lot: 0.080'")
    time.sleep(5)

    # 3. Test Safety System (Simulate Python Crash/Stop)
    print("\n[3] Simulating Python Crash (Stopping Heartbeat)...")
    print("Waiting 25 seconds for Heartbeat Timeout (set to 20s in EA)...")
    
    # We just don't send anything
    for i in range(25, 0, -1):
        if i % 5 == 0:
            print(f"Time remaining: {i}s")
        time.sleep(1)
        
    print("🚨 Timeout reached. Check EA Dashboard for 'System: 🔴 DISCONNECTED'")
    
    # 4. Test Signal Blocked in Safety Mode
    print("\n[4] Sending Signal while DISCONNECTED...")
    blocked_signal = {
        "signal_id": "TEST_BLOCKED",
        "direction": "SHORT",
        "entry_price": 60000.0,
        "stop_loss": 60500.0,
        "take_profit": 58000.0,
        "lot_size": 0.05,
        "score": 8,
        "reason": "SHOULD_BE_BLOCKED"
    }
    socket.send_string(f"signal {json.dumps(blocked_signal)}")
    print("✅ Blocked Signal sent. Check EA Journal for '❌ SIGNAL REJECTED: System is in SAFE MODE'")

if __name__ == "__main__":
    try:
        test_system()
    except KeyboardInterrupt:
        print("\nStopped.")
