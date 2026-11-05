"""Cross-platform development entry point; npm invokes the project virtualenv."""
import os
from dotenv import load_dotenv
from pathlib import Path
import uvicorn

if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    load_dotenv('.env')
    uvicorn.run('app.main:app', host='127.0.0.1', port=int(os.getenv('PORT', '4000')),
                reload=True, reload_dirs=['app'])
