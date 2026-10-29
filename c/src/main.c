/* main.c — stitchc, the stitch-c command-line harness.
 *
 * Read-side subcommands print a stable key=value report so the
 * conformance driver can diff them against the Python reference's
 * verdicts field-for-field:
 *   stitchc audit  <file> [budget]
 *   stitchc verify <file>
 *   stitchc agree  <a> <b> <xchg> [budget]
 *   stitchc state  <a> <b> [budget]
 *   stitchc anchor <file> <head|-> <count>
 *   stitchc phash            (JSON payload on stdin)
 *   stitchc canon            (JSON value on stdin -> canonical text)
 *   stitchc fmtbits          (hex uint64 bit patterns, one per line,
 *                             on stdin -> CPython repr of the double)
 *   stitchc sha256           (bytes on stdin -> hex digest)
 *
 * Write-side demonstrations with deterministic timestamps:
 *   stitchc append-ref <file>   (the tests/cross_lang.py vectors)
 *   stitchc scenario  <path>    (the cross_lang canonical sequence)
 *   stitchc scenario2 <dir>     (rich alice/bob pair; Python twin in
 *                                conformance/gen_fixtures.py)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"
#include "sha256.h"
#include "stitch.h"

static char *read_stdin_all(size_t *len_out)
{
    sbuf b;
    char chunk[8192];
    size_t n;
    sbuf_init(&b);
    while ((n = fread(chunk, 1, sizeof chunk, stdin)) > 0) sbuf_putn(&b, chunk, n);
    if (!b.buf) return xstrdup("");
    if (len_out) *len_out = b.len;
    return b.buf;
}

static const char *bool_s(int x) { return x ? "true" : "false"; }

static int cmd_sha256(void)
{
    size_t len = 0;
    char *data = read_stdin_all(&len);
    char out[65];
    sha256_hex((const uint8_t *)data, len, out);
    printf("%s\n", out);
    free(data);
    return 0;
}

static int cmd_canon(void)
{
    size_t len = 0;
    char *data = read_stdin_all(&len);
    const char *err = NULL;
    jval *v = jv_parse(data, len, &err);
    char *canon;
    if (!v) { fprintf(stderr, "parse error: %s\n", err ? err : "?"); free(data); return 2; }
    canon = jv_canonical(v);
    printf("%s\n", canon ? canon : "");
    free(canon);
    jv_free(v);
    free(data);
    return 0;
}

static int cmd_phash(void)
{
    size_t len = 0;
    char *data = read_stdin_all(&len);
    const char *err = NULL;
    jval *v = jv_parse(data, len, &err);
    char *ph;
    if (!v) { fprintf(stderr, "parse error: %s\n", err ? err : "?"); free(data); return 2; }
    /* Python payload_hash does NOT refuse non-finite values (the
     * read side is unpoliced); it hashes the canonical text, and so
     * do we. The refusal lives in the booking helpers. */
    ph = sc_payload_hash(v);
    printf("%s\n", ph ? ph : "");
    free(ph);
    jv_free(v);
    free(data);
    return 0;
}

static int cmd_fmtbits(void)
{
    char line[128];
    while (fgets(line, sizeof line, stdin)) {
        char *end = NULL;
        uint64_t bits;
        double d;
        char buf[48];
        if (line[0] == '\n' || line[0] == '\0') continue;
        bits = strtoull(line, &end, 16);
        if (end == line) continue;
        memcpy(&d, &bits, 8);
        py_float_repr(d, buf, sizeof buf);
        printf("%s\n", buf);
    }
    return 0;
}

static int cmd_audit(const char *path, double budget)
{
    caudit v;
    jval **entries = NULL;
    long n = 0, i;
    char numbuf[48];
    int rc = caudit_run(path, budget, &v, &entries, &n);
    if (rc != SC_OK) { fprintf(stderr, "audit: cannot read %s\n", path); return 2; }
    printf("entries=%ld\n", v.entries);
    printf("chain_ok=%s\n", bool_s(v.chain_ok));
    if (v.chain_ok) printf("chain_broken_at=-\n");
    else printf("chain_broken_at=%ld\n", v.chain_broken_at);
    printf("law_ok=%s\n", bool_s(v.law_ok));
    py_float_repr(v.law_excess, numbuf, sizeof numbuf);
    printf("law_excess=%s\n", numbuf);
    printf("head=%s\n", v.head_hash ? v.head_hash : "-");
    printf("tenant=%s\n", v.tenant ? v.tenant : "-");
    printf("budgets=");
    if (v.nbudgets == 0) {
        printf("-\n");
    } else {
        /* sorted by tenant for a stable report */
        long a, b2;
        for (a = 1; a < v.nbudgets; ++a) {
            char *tk = v.budget_keys[a];
            double tv = v.budget_vals[a];
            b2 = a;
            while (b2 > 0 && strcmp(v.budget_keys[b2 - 1], tk) > 0) {
                v.budget_keys[b2] = v.budget_keys[b2 - 1];
                v.budget_vals[b2] = v.budget_vals[b2 - 1];
                b2--;
            }
            v.budget_keys[b2] = tk;
            v.budget_vals[b2] = tv;
        }
        for (i = 0; i < v.nbudgets; ++i) {
            if (i) printf(",");
            py_float_repr(v.budget_vals[i], numbuf, sizeof numbuf);
            printf("%s=%s", v.budget_keys[i], numbuf);
        }
        printf("\n");
    }
    printf("violations=%ld\n", v.nviolations);
    for (i = 0; i < v.nviolations; ++i) printf("violation=%s\n", v.violations[i]);
    printf("ok=%s\n", bool_s(caudit_ok(&v)));
    caudit_free(&v, entries, n);
    return 0;
}

static int cmd_verify(const char *path)
{
    printf("chain_ok=%s\n", bool_s(cledger_verify(path)));
    return 0;
}

static void print_agreement(const cagreement *a)
{
    printf("agreed=%s\n", bool_s(a->agreed));
    printf("reason=%s\n", a->reason ? a->reason : "-");
    printf("sender=%s\n", a->sender ? a->sender : "-");
    printf("receiver=%s\n", a->receiver ? a->receiver : "-");
    printf("payload_hash=%s\n", a->payload_hash ? a->payload_hash : "-");
    printf("head_a=%s\n", a->head_a ? a->head_a : "-");
    printf("head_b=%s\n", a->head_b ? a->head_b : "-");
    printf("entries_a=%ld\n", a->entries_a);
    printf("entries_b=%ld\n", a->entries_b);
    printf("detail=%s\n", a->detail ? a->detail : "-");
}

static int cmd_agree(const char *pa, const char *pb, const char *xchg, double budget)
{
    cagreement a;
    int rc = cagree(pa, pb, xchg, budget, &a);
    if (rc != SC_OK) { fprintf(stderr, "agree: audit failed\n"); return 2; }
    print_agreement(&a);
    cagreement_free(&a);
    return 0;
}

static int cmd_state(const char *pa, const char *pb, double budget)
{
    cagreement *agr = NULL, *ref = NULL;
    long nag = 0, nref = 0, i;
    int rc = cagreed_state(pa, pb, budget, &agr, &nag, &ref, &nref);
    if (rc != SC_OK) { fprintf(stderr, "state: audit failed\n"); return 2; }
    for (i = 0; i < nag; ++i)
        printf("%s agreed %s %s\n", agr[i].xchg, agr[i].reason,
               agr[i].payload_hash ? agr[i].payload_hash : "-");
    for (i = 0; i < nref; ++i)
        printf("%s refused %s -\n", ref[i].xchg, ref[i].reason);
    for (i = 0; i < nag; ++i) cagreement_free(&agr[i]);
    for (i = 0; i < nref; ++i) cagreement_free(&ref[i]);
    free(agr); free(ref);
    return 0;
}

static int cmd_anchor(const char *path, const char *head, long count)
{
    canchor a;
    int rc = cverify_anchor(path, strcmp(head, "-") == 0 ? NULL : head, count, &a);
    if (rc != SC_OK) { fprintf(stderr, "anchor: failed\n"); return 2; }
    printf("status=%s\n", a.status);
    printf("entries=%ld\n", a.entries);
    printf("pinned_entries=%ld\n", a.pinned_entries);
    printf("head=%s\n", a.head_hash ? a.head_hash : "-");
    printf("pinned_head=%s\n", a.pinned_head ? a.pinned_head : "-");
    canchor_free(&a);
    return 0;
}

/* ---- deterministic write scenarios ---- */

static int cmd_append_ref(const char *path)
{
    cledger L;
    jval *e1, *e2;
    char h1[17], h2[17];
    int rc = cledger_open(&L, path);
    if (rc != SC_OK) { fprintf(stderr, "open failed\n"); return 2; }
    e1 = jv_new_obj();
    jv_obj_set(e1, "t", jv_new_int(1000));
    jv_obj_set(e1, "ns", jv_new_str("alice"));
    jv_obj_set(e1, "op", jv_new_str("BIND"));
    jv_obj_set(e1, "cell", jv_new_str("abc"));
    jv_obj_set(e1, "g", jv_new_dbl(0.0));
    e2 = jv_new_obj();
    jv_obj_set(e2, "t", jv_new_int(2000));
    jv_obj_set(e2, "ns", jv_new_str("alice"));
    jv_obj_set(e2, "op", jv_new_str("TICK"));
    jv_obj_set(e2, "g", jv_new_dbl(0.0));
    rc = cledger_append(&L, e1, h1);
    if (rc == SC_OK) rc = cledger_append(&L, e2, h2);
    jv_free(e1); jv_free(e2);
    cledger_close(&L);
    if (rc != SC_OK) { fprintf(stderr, "append failed rc=%d\n", rc); return 2; }
    printf("%s\n%s\n", h1, h2);
    return 0;
}

/* The tests/cross_lang.py canonical sequence: timestamps are
 * counter * 1_000_000_000 with the counter starting at 0 and
 * pre-incremented per stitch. */
static long long g_ts_counter = 0;
static int64_t next_ts(void)
{
    g_ts_counter += 1;
    return (int64_t)(g_ts_counter * 1000000000LL);
}

static int cmd_scenario(const char *path)
{
    cledger L;
    ctenant alice;
    ccellset set;
    ccell *cell = NULL;
    jval *payload, *pred, *obs;
    int committed = 0, rc;

    rc = cledger_open(&L, path);
    if (rc != SC_OK) { fprintf(stderr, "open failed\n"); return 2; }
    ctenant_init(&alice, "alice", SC_LOG2_3);
    ccellset_init(&set);
    g_ts_counter = 0;

    payload = jv_new_obj();
    jv_obj_set(payload, "goal", jv_new_str("summarize"));
    rc = cstitch_bind(&alice, &L, &set, "task", payload, NULL, 0.0, next_ts(), &cell);
    jv_free(payload);
    if (rc != SC_OK || !cell) { fprintf(stderr, "bind failed\n"); return 2; }

    pred = jv_new_obj(); jv_obj_set(pred, "status", jv_new_str("done"));
    obs = jv_new_obj(); jv_obj_set(obs, "status", jv_new_str("error"));
    rc = cstitch_effect(&alice, &L, cell, "status", pred, obs, next_ts(), &committed);
    jv_free(pred); jv_free(obs);
    if (rc != SC_OK || !committed) { fprintf(stderr, "effect failed\n"); return 2; }

    {
        char sig[17];
        rc = cstitch_attest(&alice, &L, cell, "completed", next_ts(), sig);
        if (rc != SC_OK) { fprintf(stderr, "attest failed\n"); return 2; }
    }
    rc = cstitch_tick(&alice, &L, next_ts());
    if (rc != SC_OK) { fprintf(stderr, "tick failed\n"); return 2; }

    cledger_close(&L);
    ccellset_free(&set);
    ctenant_free(&alice);
    return 0;
}

/* Rich pair scenario. The Python twin (conformance/gen_fixtures.py)
 * performs the identical sequence with identical timestamps; the
 * resulting files must be byte-identical. */
static int64_t g2_counter = 0;
static int64_t next_ts2(void)
{
    g2_counter += 1;
    return (int64_t)(g2_counter * 1000000007LL);
}

static jval *parse_lit(const char *s)
{
    return jv_parse(s, strlen(s), NULL);
}

static jval *tricky_payload(void)
{
    jval *p = jv_new_obj();
    jval *inner;
    /* "Résumé — café ☕" as explicit UTF-8 bytes */
    jv_obj_set(p, "title", jv_new_str("R\xc3\xa9sum\xc3\xa9 \xe2\x80\x94 caf\xc3\xa9 \xe2\x98\x95"));
    jv_obj_set(p, "n", jv_new_int(3));
    jv_obj_set(p, "pi", jv_new_dbl(3.5));
    jv_obj_set(p, "small", jv_new_dbl(1e-05));
    jv_obj_set(p, "i64max", jv_new_int(INT64_MAX));
    jv_obj_set(p, "i64min", jv_new_int(INT64_MIN));
    jv_obj_set(p, "u64max", jv_new_uint(UINT64_MAX));
    jv_obj_set(p, "big", parse_lit("18446744073709551616")); /* 2^64 */
    jv_obj_set(p, "negzero", jv_new_dbl(-0.0));
    jv_obj_set(p, "sub", jv_new_dbl(5e-324));
    jv_obj_set(p, "max", jv_new_dbl(1.7976931348623157e308));
    jv_obj_set(p, "esc", jv_new_str("a\"b\\c\nd\te"));
    jv_obj_set(p, "t", jv_new_bool(1));
    jv_obj_set(p, "f", jv_new_bool(0));
    jv_obj_set(p, "nil", jv_new_null());
    jv_obj_set(p, "empty_obj", jv_new_obj());
    jv_obj_set(p, "empty_arr", jv_new_arr());
    inner = jv_new_obj();
    jv_obj_set(inner, "arr", parse_lit("[1,\"two\",3.25,null,[true],{\"z\":0}]"));
    jv_obj_set(p, "inner", inner);
    /* "ключ" (cyrillic) and "emoji🔑" keys */
    jv_obj_set(p, "\xd0\xba\xd0\xbb\xd1\x8e\xd1\x87", jv_new_str("cyrillic key"));
    jv_obj_set(p, "emoji\xf0\x9f\x94\x91", jv_new_str("key"));
    return p;
}

static int cmd_scenario2(const char *dir)
{
    char path_a[1024], path_b[1024];
    cledger la, lb;
    ctenant alice, bob;
    ccellset sa, sb;
    ccell *cellA = NULL, *cellB = NULL, *bobCell = NULL, *tmp = NULL;
    jval *p;
    int committed = -1, rc = SC_OK;
    char sig[17];

    snprintf(path_a, sizeof path_a, "%s/alice.jsonl", dir);
    snprintf(path_b, sizeof path_b, "%s/bob.jsonl", dir);
    if (cledger_open(&la, path_a) != SC_OK) return 2;
    if (cledger_open(&lb, path_b) != SC_OK) return 2;
    ctenant_init(&alice, "alice", 3.0);
    ctenant_init(&bob, "bob", SC_LOG2_3);
    ccellset_init(&sa); ccellset_init(&sb);
    g2_counter = 0;

#define STEP(expr) do { rc = (expr); if (rc != SC_OK) { \
    fprintf(stderr, "scenario2 step failed rc=%d: %s\n", rc, #expr); \
    return 2; } } while (0)

    /* 1. alice declares budget 3.0 */
    STEP(cstitch_declare_budget(&alice, &la, &sa, 1, 3.0, next_ts2(), &tmp));
    /* 2. alice binds the tricky payload */
    p = tricky_payload();
    STEP(cstitch_bind(&alice, &la, &sa, "note", p, NULL, 0.0, next_ts2(), &cellA));
    jv_free(p);
    /* 3. alice binds a data cell */
    p = parse_lit("{\"v\":[1,2,3],\"ok\":true}");
    STEP(cstitch_bind(&alice, &la, &sa, "data", p, NULL, 0.0, next_ts2(), &cellB));
    jv_free(p);
    /* 4. link A -> B */
    STEP(cstitch_link(&alice, &la, cellA, cellB, "depends_on", next_ts2()));
    /* 5. effect: new key x=1 (s=1, g=1.0) */
    {
        jval *pred = jv_new_obj();
        jval *obs = jv_new_obj(); jv_obj_set(obs, "x", jv_new_int(1));
        STEP(cstitch_effect(&alice, &la, cellA, "x", pred, obs, next_ts2(), &committed));
        jv_free(pred); jv_free(obs);
        if (!committed) { fprintf(stderr, "effect 5 refused\n"); return 2; }
    }
    /* 6. effect: x changes 1 -> 2 (s=1, g=2.0) */
    {
        jval *pred = jv_new_obj(); jv_obj_set(pred, "x", jv_new_int(1));
        jval *obs = jv_new_obj(); jv_obj_set(obs, "x", jv_new_int(2));
        STEP(cstitch_effect(&alice, &la, cellA, "x", pred, obs, next_ts2(), &committed));
        jv_free(pred); jv_free(obs);
        if (!committed) { fprintf(stderr, "effect 6 refused\n"); return 2; }
    }
    /* 7. effect: no surprise (s=0, g=2.0) */
    {
        jval *pred = jv_new_obj();
        jval *obs = jv_new_obj();
        jv_obj_set(pred, "a", jv_new_int(1)); jv_obj_set(pred, "b", jv_new_int(2));
        jv_obj_set(obs, "a", jv_new_int(1));
        STEP(cstitch_effect(&alice, &la, cellB, "a", pred, obs, next_ts2(), &committed));
        jv_free(pred); jv_free(obs);
        if (!committed) { fprintf(stderr, "effect 7 refused\n"); return 2; }
    }
    /* 8. effect refused: s=2 would take gamma 2.0 -> 4.0 > 3.0.
     * A refusal appends nothing and consumes no timestamp. */
    {
        jval *pred = jv_new_obj();
        jval *obs = jv_new_obj();
        jv_obj_set(obs, "p", jv_new_int(1)); jv_obj_set(obs, "q", jv_new_int(2));
        rc = cstitch_effect(&alice, &la, cellB, "p", pred, obs, 0, &committed);
        jv_free(pred); jv_free(obs);
        if (rc != SC_OK || committed) { fprintf(stderr, "effect 8 was not refused\n"); return 2; }
    }
    /* 9. bob binds a note */
    p = parse_lit("{\"from\":\"bob\"}");
    STEP(cstitch_bind(&bob, &lb, &sb, "note", p, NULL, 0.0, next_ts2(), &bobCell));
    jv_free(p);
    /* 10. exchanges x1..x5 (mint / missing_mirror / payload_mismatch /
     *     reverse-direction mint / no_send) */
    {
        jval *P1 = parse_lit("{\"item\":\"ledger\",\"qty\":2}");
        jval *P2 = parse_lit("{\"item\":\"pen\",\"qty\":1}");
        jval *P3a = parse_lit("{\"item\":\"ink\",\"qty\":1}");
        jval *P3b = parse_lit("{\"item\":\"ink\",\"qty\":2}");
        jval *P4 = parse_lit("{\"item\":\"stamp\",\"qty\":5}");
        jval *P5 = parse_lit("{\"item\":\"seal\",\"qty\":1}");
        STEP(csc_book(&alice, &la, &sa, "x1", "bob", P1, 1, next_ts2(), &tmp));
        STEP(csc_book(&bob, &lb, &sb, "x1", "alice", P1, 0, next_ts2(), &tmp));
        STEP(csc_book(&alice, &la, &sa, "x2", "bob", P2, 1, next_ts2(), &tmp));
        STEP(csc_book(&alice, &la, &sa, "x3", "bob", P3a, 1, next_ts2(), &tmp));
        STEP(csc_book(&bob, &lb, &sb, "x3", "alice", P3b, 0, next_ts2(), &tmp));
        STEP(csc_book(&bob, &lb, &sb, "x4", "alice", P4, 1, next_ts2(), &tmp));
        STEP(csc_book(&alice, &la, &sa, "x4", "bob", P4, 0, next_ts2(), &tmp));
        STEP(csc_book(&bob, &lb, &sb, "x5", "alice", P5, 0, next_ts2(), &tmp));
        jv_free(P1); jv_free(P2); jv_free(P3a); jv_free(P3b); jv_free(P4); jv_free(P5);
    }
    /* 11. alice ticks */
    STEP(cstitch_tick(&alice, &la, next_ts2()));
    /* 12. effect after tick: p=1 (s=1, g=1.0) */
    {
        jval *pred = jv_new_obj();
        jval *obs = jv_new_obj(); jv_obj_set(obs, "p", jv_new_int(1));
        STEP(cstitch_effect(&alice, &la, cellB, "p", pred, obs, next_ts2(), &committed));
        jv_free(pred); jv_free(obs);
        if (!committed) { fprintf(stderr, "effect 12 refused\n"); return 2; }
    }
    /* 13. attest */
    STEP(cstitch_attest(&alice, &la, cellA, "verified", next_ts2(), sig));
    /* 14. delegate */
    STEP(cstitch_delegate(&alice, &la, cellA, "bob", "read", next_ts2()));
    /* 15. contest */
    STEP(cstitch_contest(&alice, &la, cellB, "stale", next_ts2()));
    /* 16. merger (two timestamps: bind, then the MERGER entry) */
    p = parse_lit("{\"sum\":[1,2]}");
    STEP(cstitch_merger(&alice, &la, &sa, cellA, cellB, "merged", p, next_ts2(), next_ts2(), &tmp));
    jv_free(p);
    /* 17. revoke */
    STEP(cstitch_revoke(&alice, &la, cellB, "superseded", next_ts2()));
    /* 18. withdraw (amount = eta = 3.0 - 1.0 = 2.0) */
    STEP(cstitch_withdraw(&alice, &la, 0, 0.0, next_ts2()));
    /* 19. bob ticks */
    STEP(cstitch_tick(&bob, &lb, next_ts2()));

#undef STEP
    cledger_close(&la);
    cledger_close(&lb);
    ccellset_free(&sa); ccellset_free(&sb);
    ctenant_free(&alice); ctenant_free(&bob);
    (void)bobCell; (void)tmp;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: stitchc <audit|verify|agree|state|anchor|phash|canon|fmtbits|sha256|append-ref|scenario|scenario2> ...\n");
        return 2;
    }
    if (strcmp(argv[1], "sha256") == 0) return cmd_sha256();
    if (strcmp(argv[1], "canon") == 0) return cmd_canon();
    if (strcmp(argv[1], "phash") == 0) return cmd_phash();
    if (strcmp(argv[1], "fmtbits") == 0) return cmd_fmtbits();
    if (strcmp(argv[1], "audit") == 0 && argc >= 3)
        return cmd_audit(argv[2], argc >= 4 ? strtod(argv[3], NULL) : SC_LOG2_3);
    if (strcmp(argv[1], "verify") == 0 && argc >= 3)
        return cmd_verify(argv[2]);
    if (strcmp(argv[1], "agree") == 0 && argc >= 5)
        return cmd_agree(argv[2], argv[3], argv[4], argc >= 6 ? strtod(argv[5], NULL) : SC_LOG2_3);
    if (strcmp(argv[1], "state") == 0 && argc >= 4)
        return cmd_state(argv[2], argv[3], argc >= 5 ? strtod(argv[4], NULL) : SC_LOG2_3);
    if (strcmp(argv[1], "anchor") == 0 && argc >= 5)
        return cmd_anchor(argv[2], argv[3], strtol(argv[4], NULL, 10));
    if (strcmp(argv[1], "append-ref") == 0 && argc >= 3)
        return cmd_append_ref(argv[2]);
    if (strcmp(argv[1], "scenario") == 0 && argc >= 3)
        return cmd_scenario(argv[2]);
    if (strcmp(argv[1], "scenario2") == 0 && argc >= 3)
        return cmd_scenario2(argv[2]);
    fprintf(stderr, "unknown or incomplete command: %s\n", argv[1]);
    return 2;
}
