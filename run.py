# run.py — Production Runner

import uvicorn
import sys

if __name__ == "__main__":
    print(f"Starting server in SINGLE PROCESS mode...")
    try:
        # Standard run without reload
        uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
    except Exception as e:
        print(f"Server crashed: {e}")
        input("Press Enter to close window...")
