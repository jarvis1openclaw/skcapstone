#!/bin/bash
# P0 blocking gate (spec 4.7). Validates the meter against a known load.
# Read-only: runs inferences and reads counters, changes nothing.
set -euo pipefail

METER="${1:-http://192.168.0.100:9420/energy}"
GATEWAY="${2:-http://localhost:18780}"
N="${3:-20}"

echo "=== 1. meter is alive and monotonic ==="
A=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
sleep 2
B=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
python3 -c "
a,b=$A,$B
assert b>=a, f'counter went BACKWARDS: {a} -> {b}'
print(f'  ok: {a:.1f} -> {b:.1f} J')
"

echo
echo "=== 2. repeatability: $N identical local inferences ==="
: > /tmp/skmeter-runs.txt
for i in $(seq 1 "$N"); do
  BEFORE=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
  curl -s --max-time 120 "$GATEWAY/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d '{"model":"ornith-1.0-9b","max_tokens":200,"temperature":0,
         "messages":[{"role":"user","content":"Count from 1 to 100."}]}' >/dev/null
  AFTER=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
  python3 -c "print(f'{$AFTER-$BEFORE:.1f}')" >> /tmp/skmeter-runs.txt
  printf '.'
done
echo
python3 - <<'PY'
vals=[float(x) for x in open('/tmp/skmeter-runs.txt') if x.strip()]
mean=sum(vals)/len(vals)
sd=(sum((v-mean)**2 for v in vals)/len(vals))**0.5
cv=sd/mean if mean else float('inf')
print(f'  n={len(vals)} mean={mean:.1f} J  sd={sd:.1f}  cv={cv:.1%}')
assert mean > 0, 'FAIL: identical local inferences measured zero joules'
assert cv < 0.25, f'FAIL: variance too high ({cv:.1%}), meter is not repeatable'
print('  ok: repeatable')
PY

echo
echo "=== 3. NEGATIVE CONTROL: a cloud-routed request must measure ~zero ==="
BEFORE=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
curl -s --max-time 120 "$GATEWAY/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"openai/gpt-oss-20b","max_tokens":200,
       "messages":[{"role":"user","content":"Count from 1 to 100."}]}' >/dev/null
AFTER=$(curl -s --max-time 3 "$METER" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counter_j"])')
python3 -c "
d=$AFTER-$BEFORE
print(f'  cloud request moved the local counter by {d:.1f} J')
assert d < 50, f'FAIL: a cloud request registered {d:.1f} J of LOCAL energy'
print('  ok: local GPU correctly measured near-zero')
"

echo
echo '=== ALL CHECKS PASSED. The meter may be trusted. ==='
