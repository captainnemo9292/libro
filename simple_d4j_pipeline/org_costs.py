"""Query the OpenAI Organization Costs API for actual billed cost.

This reconciles the token-based estimates from cost.py against what OpenAI
actually billed. It hits GET /v1/organization/costs, which requires an
*admin* API key (set OPENAI_ADMIN_KEY), not a regular project key.

Docs: https://developers.openai.com/api/reference/python/resources/admin/subresources/organization/subresources/usage/methods/costs

Usage:
    OPENAI_ADMIN_KEY=sk-admin-... python org_costs.py --days 1
"""

import os
import json
import time
import argparse
import urllib.parse
import urllib.request


def fetch_costs(admin_key, start_time, end_time=None, bucket_width='1d', limit=31):
    params = {'start_time': int(start_time), 'bucket_width': bucket_width, 'limit': limit}
    if end_time:
        params['end_time'] = int(end_time)
    url = 'https://api.openai.com/v1/organization/costs?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {admin_key}'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=1,
                    help='look back this many days (default 1)')
    ap.add_argument('--start-time', type=int,
                    help='explicit start unix time (overrides --days)')
    ap.add_argument('--bucket-width', default='1d', choices=['1d'],
                    help='cost bucket granularity (API currently supports 1d)')
    args = ap.parse_args()

    admin_key = os.environ.get('OPENAI_ADMIN_KEY')
    if not admin_key:
        raise SystemExit('Set OPENAI_ADMIN_KEY (an organization admin key).')

    start = args.start_time or int(time.time() - args.days * 86400)
    data = fetch_costs(admin_key, start)

    total = 0.0
    currency = 'usd'
    for bucket in data.get('data', []):
        bstart = bucket.get('start_time')
        for res in bucket.get('results', []):
            amount = (res.get('amount') or {})
            val = amount.get('value', 0.0) or 0.0
            currency = amount.get('currency', currency)
            total += val
            ts = time.strftime('%Y-%m-%d', time.gmtime(bstart)) if bstart else '?'
            line = res.get('line_item') or res.get('project_id') or 'all'
            print(f'  {ts}  {line}: {val:.4f} {currency}')
    print(f'TOTAL billed since {time.strftime("%Y-%m-%d %H:%M", time.gmtime(start))} '
          f'UTC: {total:.4f} {currency}')


if __name__ == '__main__':
    main()
