"""Block accidental credentials in tracked configuration; never print values."""
import json
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
issues = []
private_key = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
sensitive = {'password', 'auth_code', 'api_key', 'secret', 'access_token'}
placeholders = {'', 'your_password', 'your_auth_code', 'your_api_key', 'your_secret', '<placeholder>', '***'}

def check_fields(value, file, trail=''):
    if isinstance(value, dict):
        for key, item in value.items():
            location = f'{trail}.{key}'
            if key.lower() in sensitive and isinstance(item, str) and item.lower() not in placeholders:
                issues.append(f'{file}: non-placeholder {location}')
            check_fields(item, file, location)
    elif isinstance(value, list):
        for item in value: check_fields(item, file, trail)

for filename in filter(None, tracked):
    path = root / filename
    if not path.is_file() or path.stat().st_size > 2_000_000: continue
    text = path.read_text(encoding='utf-8', errors='ignore')
    if private_key.search(text): issues.append(f'{filename}: private key header')
    if filename.startswith('back_end/config/') and path.suffix == '.json':
        check_fields(json.loads(text), filename)
    if 'config_production.json' in filename:
        issues.append(f'{filename}: production configuration is tracked')
if issues:
    print('\n'.join(issues))
    raise SystemExit(1)
print('Tracked configuration and private-key checks passed. Historical credentials require owner verification.')
