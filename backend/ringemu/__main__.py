"""python -m ringemu  — start the Ring API emulator (default port 8090)."""
import os

import uvicorn

if __name__ == "__main__":
    print("=" * 72 + "\nClearWay RING API EMULATOR — a local stand-in built from Ring's public docs. NOT Ring.\n" + "=" * 72)
    uvicorn.run("ringemu.server:app", host=os.getenv("RINGEMU_HOST", "0.0.0.0"), port=int(os.getenv("RINGEMU_PORT", "8090")))
