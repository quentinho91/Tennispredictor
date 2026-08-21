"""
Entrypoint pour Hugging Face Spaces et serveurs cloud.
"""
from src.app import app

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
