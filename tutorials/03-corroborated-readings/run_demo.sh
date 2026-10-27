#!/bin/bash
# Tutorial 03 walkthrough: four observers record a harbour temperature.
# obs-a and obs-b see 21.5. obs-c also saw 21.5, but its ledger file is
# altered after the fact. obs-d honestly records 99.9 (a broken sensor).
# Only 21.5 can reach quorum — from the two intact, agreeing records.
set -u
cd "$(dirname "$0")"
rm -rf out
mkdir -p out/inbox out/observers

for spec in "obs-a 21.5" "obs-b 21.5" "obs-c 21.5" "obs-d 99.9"; do
  set -- $spec
  python3 observer.py --name "$1" --reading "$2" --out "out/observers/$1"
  cp "out/observers/$1/$1.reading.json" out/inbox/     # transport: plain copy, whenever
done

# obs-c's ledger is tampered with after delivery (one digit flipped in
# the file). Its reading file was already delivered intact — watch
# which layer catches this, and which layer cannot.
sed -i 's/21.5/21.9/' out/observers/obs-c/obs-c.jsonl

python3 collector.py --inbox out/inbox --out out/collector
echo
python3 corroborate.py --dir out --quorum 2
echo "corroborate exit code: $? (0 = a reading reached quorum)"
