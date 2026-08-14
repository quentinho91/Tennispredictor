import datetime
import importlib.util
import sys
from pathlib import Path

p = Path('src/05_predict_match.py')
spec = importlib.util.spec_from_file_location('pred', p)
m = importlib.util.module_from_spec(spec)
sys.modules['pred'] = m
spec.loader.exec_module(m)

model, state, fc = m.load_resources()
feat = m.compute_features('Rafael Nadal', 'Roger Federer', 'Clay', 'G', 'F', 5, 0, datetime.datetime.now(), state, tourney_name='Roland Garros')
print('Features computed successfully! Number of features:', len(feat))
