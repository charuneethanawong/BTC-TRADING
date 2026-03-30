import os
import sys
from dotenv import load_dotenv

load_dotenv('config/.env')
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("No GEMINI_API_KEY found in .env")
    sys.exit(1)

try:
    from google import genai
    from google.genai import errors
    client = genai.Client(api_key=api_key)
except ImportError:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    client = None

models = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
]

print("Testing Gemini Models for Available Quota...\n")

for model in models:
    try:
        print(f"Testing {model}...", end=" ")
        if client:
            response = client.models.generate_content(
                model=model,
                contents="Hello, just testing."
            )
            print(f"✅ Success! (Code: 200)")
        else:
            model_instance = genai.GenerativeModel(model)
            response = model_instance.generate_content("Hello, just testing.")
            print(f"✅ Success! (Code: 200)")
    except Exception as e:
        err_msg = str(e).split('\n')[0]
        if "429" in err_msg or "Quota" in err_msg or "exhaustive" in err_msg.lower():
            print(f"❌ Failed (Quota Exceeded / 429) - {err_msg}")
        elif "404" in err_msg or "not found" in err_msg.lower():
            print(f"❌ Failed (Model Not Found) - {err_msg}")
        elif "400" in err_msg:
             print(f"❌ Failed (Bad Request / Limit 0) - {err_msg}")
        else:
            print(f"❌ Error: {err_msg}")
