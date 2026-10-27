#!/bin/bash
# Tutorial 02 walkthrough: a clean delivery mints a receipt; the same
# delivery with the file altered in transit refuses.
set -u
cd "$(dirname "$0")"
rm -rf out

echo "=== HAPPY PATH ==="
python3 producer.py --out out/happy/producer
mkdir -p out/happy/inbox
cp out/happy/producer/report.json out/happy/inbox/report.json   # <- the transport: a plain copy. Yours can be anything.
python3 receiver.py --inbox out/happy/inbox --out out/happy/receiver
python3 derive_receipt.py --producer out/happy/producer/producer.jsonl --receiver out/happy/receiver/receiver.jsonl
echo "derive exit code: $? (0 = minted)"

echo
echo "=== TAMPERED IN TRANSIT ==="
python3 producer.py --out out/tampered/producer
mkdir -p out/tampered/inbox
cp out/tampered/producer/report.json out/tampered/inbox/report.json
printf ' ' >> out/tampered/inbox/report.json                   # <- one byte altered after the producer hashed it
python3 receiver.py --inbox out/tampered/inbox --out out/tampered/receiver
python3 derive_receipt.py --producer out/tampered/producer/producer.jsonl --receiver out/tampered/receiver/receiver.jsonl
echo "derive exit code: $? (1 = refused)"
