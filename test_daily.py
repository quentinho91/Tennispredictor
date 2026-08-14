import sys
from pathlib import Path
import importlib.util

def main():
    p = Path('src/05_predict_match.py')
    spec = importlib.util.spec_from_file_location('pred', p)
    pred = importlib.util.module_from_spec(spec)
    sys.modules['pred'] = pred
    spec.loader.exec_module(pred)
    
    state, model, fc = pred.load_resources()
    
    from src.daily_matches import get_daily_matches_with_odds
    print("Fetching daily matches...")
    res = get_daily_matches_with_odds(state, model, fc, pred)
    print(res)

if __name__ == "__main__":
    main()
