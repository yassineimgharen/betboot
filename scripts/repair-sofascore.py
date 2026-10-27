"""Validate a local legacy capture without network access or modifying its original."""
import argparse
import json
from pathlib import Path
from betboot.sofascore_import import repair_legacy,historical_summary

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('input',type=Path)
parser.add_argument('--output-dir',type=Path,default=Path('artifacts/sofascore'))
args=parser.parse_args()
raw=args.input.read_bytes()
capture=repair_legacy(json.loads(raw),raw)
report=historical_summary(capture)
args.output_dir.mkdir(parents=True,exist_ok=True)
stem=f"event-{capture['event_id']}-{capture['original_sha256'][:12]}"
for suffix,data in [('repaired',capture),('report',report)]:
    path=args.output_dir/f'{stem}.{suffix}.json'
    # Do not replace an earlier repair or provenance record.
    if path.exists():
        print('Preserved:',path);continue
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,indent=2,ensure_ascii=False)
    path.chmod(0o600)
    print('Saved:',path)
print('Target responses:',len(capture['responses']),'Excluded:',capture['excluded_response_count'])
print('Pre-match eligible:',report['pre_match_eligible'])
