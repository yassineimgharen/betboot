"""Import local captures, retain provenance and validate each imported league offline."""
import argparse,json
from datetime import UTC,datetime
from pathlib import Path
from betboot.config import Settings
from betboot.db import engine_for
from betboot.sofascore_dataset import import_files,records,coverage
from betboot.models.imported_xg import validate
from betboot.store import save
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('files',nargs='+',type=Path)
args=parser.parse_args()
engine=engine_for(Settings().database_url)
try:
    result=import_files(engine,args.files)
    rows=records(engine)
    for league in sorted({r['league_id'] for r in rows}):
        report=validate(rows,league,datetime.now(UTC));save(engine,'sofa_validation',report)
    print(json.dumps({'import':result,'coverage':coverage(engine)},indent=2))
    if result['rejected']:
        parser.exit(1,'One or more files failed validation; see the rejection report above.\n')
finally:engine.dispose()
