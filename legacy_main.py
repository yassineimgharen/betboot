"""Previous Betboot app (backend/betboot), kept for reference: python legacy_main.py."""
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'backend'))
os.chdir(ROOT)

if __name__=='__main__':
    try:
        from betboot.terminal import main
    except ModuleNotFoundError as exc:
        print(f'Missing dependency: {exc.name}. Run: python -m pip install -r requirements.lock',file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main())
