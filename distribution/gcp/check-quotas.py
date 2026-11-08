#!/usr/bin/env python3
"""Validate the dedicated project's regional quota before creating paid resources."""
import json
import sys

region = json.load(sys.stdin)
quotas = {q['metric']: q for q in region.get('quotas', [])}
# E2 shared-core exposes two vCPUs; reserve enough quota for both.
cpu_metric = 'E2_CPUS' if 'E2_CPUS' in quotas else 'CPUS'
required = {cpu_metric: 2, 'INSTANCES': 1, 'IN_USE_ADDRESSES': 1, 'DISKS_TOTAL_GB': 50}
failed = []
for metric, amount in required.items():
    quota = quotas.get(metric)
    if quota is None:
        failed.append(f'{metric}: quota was not reported; review the project quota manually')
        continue
    available = quota['limit'] - quota.get('usage', 0)
    print(f'{metric}: {available:g} available; {amount} required')
    if available < amount:
        failed.append(f'{metric}: insufficient quota')
if failed:
    raise SystemExit('\n'.join(failed))
print('Regional quota checks passed. This does not reserve zonal VM capacity.')
