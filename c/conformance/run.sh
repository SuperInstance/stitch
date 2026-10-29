#!/bin/sh
# run.sh — stitch-c conformance driver.
#
# Builds stitchc, regenerates the Python-reference fixtures, and
# diffs the C tool's output against the Python verdicts. Verdict
# details embed file paths, so everything runs from inside the
# fixtures dir with the same relative paths the generator used.
#
# Usage: sh conformance/run.sh
set -u
C_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN="$C_DIR/stitchc"
CONF="$C_DIR/conformance"
FX="$CONF/fixtures"
OUT="$CONF/out"
mkdir -p "$OUT"
PASS=0; FAIL=0

check() { # name expected actual
    if cmp -s "$2" "$3"; then
        PASS=$((PASS + 1)); echo "PASS $1"
    else
        FAIL=$((FAIL + 1)); echo "FAIL $1"
        diff "$2" "$3" 2>/dev/null | head -24
    fi
}

echo "== build =="
make -C "$C_DIR" -j2 >/dev/null || { echo "BUILD FAILED"; exit 1; }
echo "build ok (gcc -std=c99 -Wall -Wextra -Werror)"

echo "== generate fixtures (Python reference) =="
rm -rf "$FX"   # the Python Ledger appends; regenerate from scratch
python3 "$CONF/gen_fixtures.py" || { echo "GEN FAILED"; exit 1; }

cd "$FX" || exit 1

echo "== reference vectors (tests/cross_lang.py) =="
rm -f "$OUT/ref.jsonl"
"$BIN" append-ref "$OUT/ref.jsonl" > "$OUT/ref_hashes.txt" 2>"$OUT/ref.err"
printf 'b513cabe780e2894\n24c56ec21ef20816\n' > "$OUT/ref_hashes.expected"
check "append-ref hashes" "$OUT/ref_hashes.expected" "$OUT/ref_hashes.txt"

echo "== canonical scenario byte-compare =="
rm -f "$OUT/c_scenario.jsonl" "$OUT/py_scenario.jsonl"
"$BIN" scenario "$OUT/c_scenario.jsonl"
REPO=$(dirname "$C_DIR")
python3 - "$REPO" "$OUT/py_scenario.jsonl" <<'PYEOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "cross_lang", sys.argv[1] + "/tests/cross_lang.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)
cl.run_canonical_sequence(sys.argv[2])
PYEOF
check "scenario bytes" "$OUT/py_scenario.jsonl" "$OUT/c_scenario.jsonl"

echo "== scenario2 byte-compare (rich pair, both directions written) =="
mkdir -p "$OUT/s2c"
rm -f "$OUT/s2c/alice.jsonl" "$OUT/s2c/bob.jsonl"
"$BIN" scenario2 "$OUT/s2c"
check "scenario2 alice bytes" "scenario2-py/alice.jsonl" "$OUT/s2c/alice.jsonl"
check "scenario2 bob bytes" "scenario2-py/bob.jsonl" "$OUT/s2c/bob.jsonl"

echo "== audits of Python-written fixtures =="
for f in audit/*.jsonl; do
    n=$(basename "$f" .jsonl)
    "$BIN" audit "$f" > "$OUT/audit-$n.txt" 2>"$OUT/audit-$n.err"
    check "audit $n" "audit/$n.expected" "$OUT/audit-$n.txt"
    "$BIN" verify "$f" > "$OUT/verify-$n.txt"
    grep -q '^chain_ok=' "audit/$n.expected" && \
        sed -n 's/^\(chain_ok=.*\)$/\1/p' "audit/$n.expected" > "$OUT/verify-$n.expected"
    check "verify $n" "$OUT/verify-$n.expected" "$OUT/verify-$n.txt"
done

echo "== rich pair: audits, agrees (both orders), state =="
for nm in alice bob; do
    "$BIN" audit "rich/$nm.jsonl" > "$OUT/rich-audit-$nm.txt"
    check "audit rich/$nm" "rich/audit-$nm.expected" "$OUT/rich-audit-$nm.txt"
done
for x in x1 x2 x3 x4 x5 never; do
    "$BIN" agree rich/alice.jsonl rich/bob.jsonl "$x" > "$OUT/rich-agree-$x.txt"
    check "agree rich $x" "rich/agree-$x.expected" "$OUT/rich-agree-$x.txt"
    "$BIN" agree rich/bob.jsonl rich/alice.jsonl "$x" > "$OUT/rich-agree-$x.rev.txt"
    check "agree rich $x (swapped)" "rich/agree-$x.rev.expected" "$OUT/rich-agree-$x.rev.txt"
done
"$BIN" state rich/alice.jsonl rich/bob.jsonl > "$OUT/rich-state.txt"
check "state rich" "rich/state.expected" "$OUT/rich-state.txt"

echo "== pairs =="
for d in pairs/*/; do
    d=${d%/}
    name=$(basename "$d")
    for ef in "$d"/agree-*.expected; do
        base=$(basename "$ef" .expected)          # agree-<x> or agree-<x>.rev
        x=${base#agree-}
        case "$x" in
            *.rev) xx=${x%.rev}
                   "$BIN" agree "$d/b.jsonl" "$d/a.jsonl" "$xx" > "$OUT/p-$name-$base.txt" ;;
            *)     "$BIN" agree "$d/a.jsonl" "$d/b.jsonl" "$x" > "$OUT/p-$name-$base.txt" ;;
        esac
        check "pairs/$name $base" "$ef" "$OUT/p-$name-$base.txt"
    done
    "$BIN" state "$d/a.jsonl" "$d/b.jsonl" > "$OUT/p-$name-state.txt"
    check "pairs/$name state" "$d/state.expected" "$OUT/p-$name-state.txt"
done

echo "== anchors =="
for af in anchors/*.args; do
    label=$(basename "$af" .args)
    set -- $(cat "$af")
    "$BIN" anchor "$1" "$2" "$3" > "$OUT/anchor-$label.txt"
    check "anchor $label" "anchors/$label.expected" "$OUT/anchor-$label.txt"
done

echo "== float repr corpus =="
"$BIN" fmtbits < floats.txt > "$OUT/floats.txt"
check "fmtbits corpus" "floats.expected" "$OUT/floats.txt"

echo "== canonicalisation corpus =="
: > "$OUT/canon.txt"
while IFS= read -r line; do
    printf '%s' "$line" | "$BIN" canon >> "$OUT/canon.txt"
done < canon.txt
check "canon corpus" "canon.expected" "$OUT/canon.txt"

echo "== payload hash corpus =="
: > "$OUT/phash.txt"
while IFS= read -r line; do
    printf '%s' "$line" | "$BIN" phash >> "$OUT/phash.txt"
done < phash.txt
check "phash corpus" "phash.expected" "$OUT/phash.txt"

echo "== python audit accepts the C-written scenario2 ledgers =="
python3 - "$REPO" "$OUT/s2c" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1] + "/stitch-python")
from stitch.agreement import audit_ledger, agree, agreed_state
d = sys.argv[2]
va = audit_ledger(d + "/alice.jsonl")
vb = audit_ledger(d + "/bob.jsonl")
assert va.ok and vb.ok, (va, vb)
a = agree(d + "/alice.jsonl", d + "/bob.jsonl", "x1")
assert a.agreed, a
ag, rf = agreed_state(d + "/alice.jsonl", d + "/bob.jsonl")
assert [x.xchg for x in ag] == ["x1", "x4"], ag
assert sorted(x.xchg for x in rf) == ["x2", "x3", "x5"], rf
print("python-side acceptance: ok")
PYEOF
if [ $? -eq 0 ]; then PASS=$((PASS + 1)); echo "PASS python accepts C ledgers";
else FAIL=$((FAIL + 1)); echo "FAIL python accepts C ledgers"; fi

echo
echo "conformance: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
