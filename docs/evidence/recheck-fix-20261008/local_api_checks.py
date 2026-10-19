"""Read-only checks against the running canonical API and existing Vite proxy."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

checks = [
    ('http://127.0.0.1:8000/health', 200),
    ('http://127.0.0.1:8000/docs', 200),
    ('http://127.0.0.1:8000/openapi.json', 200),
    ('http://127.0.0.1:8000/backtest/strategies', 200),
    ('http://127.0.0.1:8000/auth/status', 200),
    ('http://127.0.0.1:8000/watch/tick?symbols=rb2610', 503),
    ('http://127.0.0.1:8000/trading/snapshot', 401),
    ('http://127.0.0.1:5174/api/health', 200),
]
results = []
for url, expected in checks:
    try:
        response = urlopen(url, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        content = response.read().decode('utf-8')
        result = {'url': url, 'status': status, 'expected': expected}
        if '/health' in url or '/auth/status' in url or '/watch/tick' in url or '/trading/snapshot' in url:
            result['body'] = json.loads(content)
        results.append(result)
        assert status == expected, result

request = Request('http://127.0.0.1:8000/risk/config', method='OPTIONS', headers={
    'Origin': 'http://127.0.0.1:5174', 'Access-Control-Request-Method': 'PUT',
    'Access-Control-Request-Headers': 'content-type'})
with urlopen(request, timeout=5) as response:
    result = {'url': request.full_url, 'method': 'OPTIONS', 'status': response.status,
              'allow_origin': response.headers.get('Access-Control-Allow-Origin')}
    results.append(result)
    assert response.status == 200 and result['allow_origin'] == 'http://127.0.0.1:5174'
output = json.dumps(results, ensure_ascii=False, indent=2)
Path('D:/mine/quant-recheck-fix-20261008/local-api-checks.json').write_text(output, encoding='utf-8')
print(output)
