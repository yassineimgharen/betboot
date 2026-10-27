"""Football betting slip generator: python main.py ["Give me 5 matches"] [--details]"""
import sys

if __name__ == '__main__':
    try:
        from slipbot.app import main
    except ModuleNotFoundError as exc:
        print(f'Missing dependency: {exc.name}. Run: python -m pip install -r requirements.txt', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main())
