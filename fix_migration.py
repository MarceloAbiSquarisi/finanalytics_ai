import pathlib
f = pathlib.Path('migrations/versions/0012_ml_features.py')
t = f.read_text(encoding='utf-8')
t = t.replace('down_revision = "0011_event_records"', 'down_revision = "0010_financial_agents"')
f.write_text(t, encoding='utf-8', newline='\n')
print('OK' if '0010_financial_agents' in t else 'FALHOU')
