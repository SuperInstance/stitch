/* stitch.c — see stitch.h. A close semantic mirror of the Python
 * reference; where Python would raise on pathological foreign input
 * (e.g. float(None) inside the law replay) and C must return a
 * verdict, the divergence is noted inline and in CONFORMANCE.md. */
#include "stitch.h"
#include "sha256.h"

#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

const double SC_LOG2_3 = 1.584962500721156;
#define SC_CHARGE_EPS 1e-12   /* core._EPSILON (write-time afford) */
#define SC_AUDIT_EPS  1e-9    /* agreement._EPS (law replay) */
#define SC_G_TOL      1e-6    /* recorded-g vs replayed-gamma tolerance */

/* ================= small helpers ================= */

uint64_t sc_fnv1a(const void *data, size_t len, uint64_t seed)
{
    const uint8_t *b = (const uint8_t *)data;
    uint64_t h = seed;
    size_t i;
    for (i = 0; i < len; ++i) {
        h ^= b[i];
        h *= SC_FNV_PRIME;
    }
    return h;
}

void sc_hex64(uint64_t h, char out[17])
{
    static const char hexd[] = "0123456789abcdef";
    int i;
    for (i = 15; i >= 0; --i) { out[i] = hexd[h & 0xf]; h >>= 4; }
    out[16] = '\0';
}

static char *dup_or_null(const char *s) { return s ? xstrdup(s) : NULL; }

/* Display form of a JSON value the way Python f-strings would print
 * it for the scalar cases verdict details actually carry. */
static char *display_str(const jval *v)
{
    char numbuf[48];
    if (!v) return xstrdup("None");
    switch (v->type) {
    case JV_STR: return xstrndup(v->v.str.s, v->v.str.len);
    case JV_NULL: return xstrdup("None");
    case JV_BOOL: return xstrdup(v->v.boolean ? "True" : "False");
    case JV_INT: snprintf(numbuf, sizeof numbuf, "%lld", (long long)v->v.i); return xstrdup(numbuf);
    case JV_UINT: snprintf(numbuf, sizeof numbuf, "%llu", (unsigned long long)v->v.u); return xstrdup(numbuf);
    case JV_BIGINT: return xstrdup(v->v.bigint);
    case JV_DBL: py_float_repr(v->v.d, numbuf, sizeof numbuf); return xstrdup(numbuf);
    default: return jv_canonical(v);
    }
}

static int is_str_eq(const jval *v, const char *s)
{
    size_t n;
    if (!v || v->type != JV_STR) return 0;
    n = strlen(s);
    return v->v.str.len == n && (n == 0 || memcmp(v->v.str.s, s, n) == 0);
}

/* Read a whole file (pure C99). NULL on open failure. */
static char *read_file(const char *path, size_t *len_out)
{
    FILE *f = fopen(path, "rb");
    sbuf b;
    char chunk[8192];
    size_t n;
    if (!f) return NULL;
    sbuf_init(&b);
    while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) sbuf_putn(&b, chunk, n);
    fclose(f);
    if (b.oom) { sbuf_free(&b); return NULL; }
    if (!b.buf) { b.buf = xstrdup(""); b.len = 0; }
    if (len_out) *len_out = b.len;
    return b.buf;   /* NUL-terminated; may embed NULs — use len */
}

/* Split into lines like Python file iteration: lines end at \n; a
 * final unterminated line still counts. Returns (ptr,len) spans
 * into the buffer (not copies). */
typedef struct { const char *ptr; size_t len; } line_t;
static line_t *split_lines(const char *buf, size_t len, long *count_out)
{
    long cap = 16, n = 0;
    line_t *lines = (line_t *)malloc((size_t)cap * sizeof(line_t));
    size_t start = 0, i;
    if (!lines) return NULL;
    for (i = 0; i <= len; ++i) {
        if (i == len || buf[i] == '\n') {
            if (n == cap) {
                line_t *nl;
                cap *= 2;
                nl = (line_t *)realloc(lines, (size_t)cap * sizeof(line_t));
                if (!nl) { free(lines); return NULL; }
                lines = nl;
            }
            lines[n].ptr = buf + start;
            lines[n].len = i - start;
            n++;
            start = i + 1;
        }
    }
    /* A trailing "\n" produces a final empty segment in the loop
     * above; Python iteration would not yield it. Drop it. */
    if (n > 0 && lines[n - 1].len == 0 && len > 0 && buf[len - 1] == '\n') n--;
    *count_out = n;
    return lines;
}

/* Python str.strip() for the ASCII whitespace set. */
static void strip_span(const char **ptr, size_t *len)
{
    const char *s = *ptr;
    size_t n = *len;
    while (n > 0 && (s[0] == ' ' || s[0] == '\t' || s[0] == '\n' ||
                     s[0] == '\r' || s[0] == '\v' || s[0] == '\f')) { s++; n--; }
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\n' ||
                     s[n - 1] == '\r' || s[n - 1] == '\v' || s[n - 1] == '\f')) n--;
    *ptr = s; *len = n;
}

/* ================= payload hash / cell id ================= */

char *sc_payload_hash(const jval *payload)
{
    char *canon = jv_canonical(payload);
    char *out = (char *)malloc(65);
    if (!canon || !out) { free(canon); free(out); return NULL; }
    sha256_hex((const uint8_t *)canon, strlen(canon), out);
    free(canon);
    return out;
}

char *sc_cell_id(const char *tenant, const char *kind, const jval *payload,
                 const char *parent, int64_t ts)
{
    char *canon = jv_canonical(payload);
    sbuf b;
    char hex[65];
    char *out;
    if (!canon) return NULL;
    sbuf_init(&b);
    sbuf_puts(&b, tenant);
    sbuf_putc(&b, ':');
    sbuf_puts(&b, kind);
    sbuf_putc(&b, ':');
    sbuf_puts(&b, canon);
    sbuf_putc(&b, ':');
    if (parent) sbuf_puts(&b, parent);
    sbuf_printf(&b, ":%lld", (long long)ts);
    free(canon);
    if (b.oom) { sbuf_free(&b); return NULL; }
    sha256_hex((const uint8_t *)b.buf, b.len, hex);
    sbuf_free(&b);
    out = (char *)malloc(17);
    if (!out) return NULL;
    memcpy(out, hex, 16);
    out[16] = '\0';
    return out;
}

/* ================= ledger writer ================= */

/* Python Ledger._compute_last_hash: walk the file; a parseable entry
 * with an "h" sets last = int(h, 16); an entry without "h" sets
 * last = fnv(stripped raw line, last); unparseable lines skipped.
 * Divergence (documented): a non-string or non-hex "h" makes Python
 * raise; C leaves last unchanged. */
static uint64_t compute_last_hash(const char *path)
{
    char *buf;
    size_t len = 0;
    line_t *lines;
    long nlines = 0, i;
    uint64_t last = SC_FNV_OFFSET;
    buf = read_file(path, &len);
    if (!buf) return SC_FNV_OFFSET;
    lines = split_lines(buf, len, &nlines);
    if (!lines) { free(buf); return SC_FNV_OFFSET; }
    for (i = 0; i < nlines; ++i) {
        const char *s = lines[i].ptr;
        size_t n = lines[i].len;
        jval *e;
        jval *h;
        strip_span(&s, &n);
        if (n == 0) continue;
        e = jv_parse(s, n, NULL);
        if (!e) continue;
        if (e->type != JV_OBJ) { jv_free(e); continue; }
        h = jv_obj_get(e, "h");
        if (h && h->type == JV_STR) {
            char *end = NULL;
            uint64_t v = strtoull(h->v.str.s, &end, 16);
            if (end && *end == '\0') last = v;
        } else if (!h) {
            last = sc_fnv1a(s, n, last);
        }
        jv_free(e);
    }
    free(lines);
    free(buf);
    return last;
}

int cledger_open(cledger *L, const char *path)
{
    L->path = xstrdup(path);
    L->last = compute_last_hash(path);
    L->f = fopen(path, "a");
    if (!L->f) { free(L->path); L->path = NULL; return SC_ERR_IO; }
    return SC_OK;
}

int cledger_append(cledger *L, const jval *entry, char out_h[17])
{
    jval *copy;
    char *canon_no_h, *canon_full;
    uint64_t h;
    char hbuf[17];
    if (!entry || entry->type != JV_OBJ) return SC_ERR_TYPE;
    if (jv_obj_get(entry, "h")) return SC_ERR_LEDGER;
    /* SPEC numeric policy, enforced on this write path: non-finite
     * floats are refused (Python's book_* helpers do the same; raw
     * Python Ledger.append would write them — see CONFORMANCE.md). */
    if (jv_contains_nonfinite(entry)) return SC_ERR_NONFINITE;
    canon_no_h = jv_canonical(entry);
    if (!canon_no_h) return SC_ERR_NOMEM;
    h = sc_fnv1a(canon_no_h, strlen(canon_no_h), L->last);
    free(canon_no_h);
    sc_hex64(h, hbuf);
    copy = jv_copy(entry);
    if (!copy) return SC_ERR_NOMEM;
    jv_obj_set(copy, "h", jv_new_str(hbuf));
    canon_full = jv_canonical(copy);
    jv_free(copy);
    if (!canon_full) return SC_ERR_NOMEM;
    if (fputs(canon_full, L->f) == EOF || fputc('\n', L->f) == EOF) {
        free(canon_full);
        return SC_ERR_IO;
    }
    free(canon_full);
    if (fflush(L->f) != 0) return SC_ERR_IO;
    L->last = h;
    if (out_h) memcpy(out_h, hbuf, 17);
    return SC_OK;
}

void cledger_close(cledger *L)
{
    if (L->f) fclose(L->f);
    L->f = NULL;
    free(L->path);
    L->path = NULL;
}

int cledger_verify(const char *path)
{
    caudit v;
    jval **entries = NULL;
    long n = 0;
    FILE *probe = fopen(path, "rb");
    int ok;
    if (!probe) return 1;   /* Python verify(): missing file is valid */
    fclose(probe);
    if (caudit_run(path, SC_LOG2_3, &v, &entries, &n) != SC_OK) return 0;
    ok = v.chain_ok;
    caudit_free(&v, entries, n);
    return ok;
}

/* ================= audit ================= */

/* tiny string->double map (tenants are few) */
typedef struct { char **keys; double *vals; long len, cap; } dmap;
static double dmap_get(const dmap *m, const char *key, double dflt)
{
    long i;
    for (i = 0; i < m->len; ++i)
        if (strcmp(m->keys[i], key) == 0) return m->vals[i];
    return dflt;
}
static int dmap_has(const dmap *m, const char *key)
{
    long i;
    for (i = 0; i < m->len; ++i)
        if (strcmp(m->keys[i], key) == 0) return 1;
    return 0;
}
static void dmap_set(dmap *m, const char *key, double val)
{
    long i;
    for (i = 0; i < m->len; ++i)
        if (strcmp(m->keys[i], key) == 0) { m->vals[i] = val; return; }
    if (m->len == m->cap) {
        long nc = m->cap ? m->cap * 2 : 8;
        m->keys = (char **)realloc(m->keys, (size_t)nc * sizeof(char *));
        m->vals = (double *)realloc(m->vals, (size_t)nc * sizeof(double));
        m->cap = nc;
    }
    m->keys[m->len] = xstrdup(key);
    m->vals[m->len] = val;
    m->len++;
}
static void dmap_free(dmap *m)
{
    long i;
    for (i = 0; i < m->len; ++i) free(m->keys[i]);
    free(m->keys); free(m->vals);
    m->keys = NULL; m->vals = NULL; m->len = m->cap = 0;
}

static void add_violation(caudit *v, const char *msg)
{
    v->violations = (char **)realloc(v->violations,
                                     (size_t)(v->nviolations + 1) * sizeof(char *));
    v->violations[v->nviolations++] = xstrdup(msg);
}

/* The map key for a tenant: the string itself, else "?" for missing,
 * else the canonical text (Python keys its dicts by the value; sane
 * ledgers always carry string ns). */
static char *ns_key(const jval *entry)
{
    jval *ns = jv_obj_get(entry, "ns");
    if (!ns) return xstrdup("?");
    if (ns->type == JV_STR) return xstrndup(ns->v.str.s, ns->v.str.len);
    return jv_canonical(ns);
}

int caudit_ok(const caudit *v) { return v->chain_ok && v->law_ok; }

int caudit_run(const char *path, double budget, caudit *out,
               jval ***entries_out, long *n_entries_out)
{
    char *buf;
    size_t len = 0;
    line_t *lines;
    long nlines = 0, i;
    jval **entries = NULL;
    long n_entries = 0, cap_entries = 0;
    uint64_t last = SC_FNV_OFFSET;
    long broken_at = -1;
    char *head = NULL;

    memset(out, 0, sizeof *out);
    out->chain_broken_at = -1;
    out->budget = budget;
    out->path = xstrdup(path);

    buf = read_file(path, &len);
    if (!buf) return SC_ERR_IO;
    lines = split_lines(buf, len, &nlines);
    if (!lines) { free(buf); return SC_ERR_NOMEM; }

    /* Phase 1: chain verification over non-blank stripped lines.
     * Entry indices count non-blank lines only, as in Python's
     * raw_lines enumeration. */
    {
        long idx = -1;
        for (i = 0; i < nlines; ++i) {
            const char *s = lines[i].ptr;
            size_t n = lines[i].len;
            jval *e, *h;
            char *canon;
            uint64_t expected;
            char exphex[17];
            strip_span(&s, &n);
            if (n == 0) continue;
            idx++;
            e = jv_parse(s, n, NULL);
            if (!e || e->type != JV_OBJ) {
                /* Python: JSON error -> broken; non-dict -> raises
                 * (AttributeError on .pop). C returns a verdict. */
                jv_free(e);
                broken_at = idx;
                break;
            }
            h = jv_obj_get(e, "h");
            if (!h) { jv_free(e); broken_at = idx; break; }
            {
                jval *hcopy = jv_copy(h);
                jv_obj_del(e, "h");
                canon = jv_canonical(e);
                expected = sc_fnv1a(canon, strlen(canon), last);
                free(canon);
                sc_hex64(expected, exphex);
                if (!hcopy || hcopy->type != JV_STR ||
                    hcopy->v.str.len != 16 ||
                    memcmp(hcopy->v.str.s, exphex, 16) != 0) {
                    jv_free(hcopy);
                    jv_free(e);
                    broken_at = idx;
                    break;
                }
                jv_free(hcopy);
            }
            if (n_entries == cap_entries) {
                long nc = cap_entries ? cap_entries * 2 : 16;
                entries = (jval **)realloc(entries, (size_t)nc * sizeof(jval *));
                cap_entries = nc;
            }
            entries[n_entries++] = e;
            /* last = int(h_str, 16); the stored h was just verified
             * equal to exphex, so parsing exphex is identical. */
            last = strtoull(exphex, NULL, 16);
            free(head);
            head = xstrdup(exphex);
        }
    }

    out->entries = n_entries;
    out->chain_ok = broken_at < 0;
    out->chain_broken_at = broken_at;
    out->head_hash = head;

    /* Phase 2: conservation-law replay over the valid prefix. */
    {
        dmap gamma, declared, active;
        double excess = 0.0;
        memset(&gamma, 0, sizeof gamma);
        memset(&declared, 0, sizeof declared);
        memset(&active, 0, sizeof active);
        for (i = 0; i < n_entries; ++i) {
            jval *e = entries[i];
            char *key = ns_key(e);
            jval *op = jv_obj_get(e, "op");
            jval *kind = jv_obj_get(e, "kind");
            jval *payload = jv_obj_get(e, "payload");
            double tb, g, recorded;
            /* Budget declaration, effective at its own position;
             * first valid declaration per tenant wins. */
            if (is_str_eq(op, "BIND") && is_str_eq(kind, "tenant") &&
                payload && payload->type == JV_OBJ && !dmap_has(&declared, key)) {
                jval *b = jv_obj_get(payload, "budget");
                int ok = 0;
                double bv = 0.0;
                if (b) bv = jv_as_double(b, &ok);
                if (b && ok && (b->type == JV_INT || b->type == JV_UINT ||
                                b->type == JV_DBL || b->type == JV_BIGINT ||
                                b->type == JV_BOOL) && bv > 0) {
                    dmap_set(&declared, key, bv);
                    dmap_set(&active, key, bv);
                }
            }
            tb = dmap_get(&active, key, budget);
            g = dmap_get(&gamma, key, 0.0);
            if (is_str_eq(op, "EFFECT")) {
                jval *s = jv_obj_get(e, "s");
                int ok = 1;
                double sv = s ? jv_as_double(s, &ok) : 0.0;
                if (!ok) sv = 0.0;   /* Python raises; C verdicts (documented) */
                g += sv;
            } else if (is_str_eq(op, "TICK")) {
                g = 0.0;
            }
            dmap_set(&gamma, key, g);
            {
                jval *gv = jv_obj_get(e, "g");
                int ok = 1;
                recorded = gv ? jv_as_double(gv, &ok) : 0.0;
                if (!ok) recorded = 0.0;   /* Python raises; documented */
            }
            if (fabs(recorded - g) > SC_G_TOL) {
                char *rop = display_str(op);
                char rbuf[48];
                sbuf msg; sbuf_init(&msg);
                py_float_repr(recorded, rbuf, sizeof rbuf);
                sbuf_printf(&msg, "entry %ld: recorded g=%s != replayed gamma=%.4f (tenant %s, op %s)",
                            i, rbuf, g, key, rop);
                add_violation(out, msg.buf ? msg.buf : "");
                sbuf_free(&msg);
                free(rop);
            }
            if (g > tb + SC_AUDIT_EPS) {
                char *rop = display_str(op);
                sbuf msg; sbuf_init(&msg);
                if (g - tb > excess) excess = g - tb;
                sbuf_printf(&msg, "entry %ld: gamma=%.4f exceeds budget %.4f by %.4f (tenant %s, op %s)",
                            i, g, tb, g - tb, key, rop);
                add_violation(out, msg.buf ? msg.buf : "");
                sbuf_free(&msg);
                free(rop);
            }
            free(key);
        }
        out->law_excess = excess;
        out->law_ok = out->nviolations == 0;
        /* declared budgets are part of the verdict (claims map) */
        out->nbudgets = declared.len;
        if (declared.len > 0) {
            long j;
            out->budget_keys = (char **)malloc((size_t)declared.len * sizeof(char *));
            out->budget_vals = (double *)malloc((size_t)declared.len * sizeof(double));
            for (j = 0; j < declared.len; ++j) {
                out->budget_keys[j] = xstrdup(declared.keys[j]);
                out->budget_vals[j] = declared.vals[j];
            }
        }
        dmap_free(&gamma); dmap_free(&declared); dmap_free(&active);
    }

    /* Single-tenant detection over the valid prefix. */
    {
        dmap seen;
        long distinct = 0;
        char *only = NULL;
        memset(&seen, 0, sizeof seen);
        for (i = 0; i < n_entries; ++i) {
            jval *ns = jv_obj_get(entries[i], "ns");
            char *k = ns_key(entries[i]);
            if (!dmap_has(&seen, k)) {
                dmap_set(&seen, k, 1.0);
                distinct++;
                free(only);
                only = (ns && ns->type == JV_STR)
                           ? xstrndup(ns->v.str.s, ns->v.str.len) : NULL;
            }
            free(k);
        }
        out->tenant = (distinct == 1) ? only : NULL;
        if (distinct != 1) free(only);
        dmap_free(&seen);
    }

    free(lines);
    free(buf);
    if (entries_out) {
        *entries_out = entries;
        if (n_entries_out) *n_entries_out = n_entries;
    } else {
        for (i = 0; i < n_entries; ++i) jv_free(entries[i]);
        free(entries);
    }
    return SC_OK;
}

void caudit_free(caudit *v, jval **entries, long n_entries)
{
    long i;
    free(v->path);
    free(v->tenant);
    free(v->head_hash);
    for (i = 0; i < v->nviolations; ++i) free(v->violations[i]);
    free(v->violations);
    for (i = 0; i < v->nbudgets; ++i) free(v->budget_keys[i]);
    free(v->budget_keys);
    free(v->budget_vals);
    memset(v, 0, sizeof *v);
    if (entries) {
        for (i = 0; i < n_entries; ++i) jv_free(entries[i]);
        free(entries);
    }
}

/* ================= agreement ================= */

typedef struct { const jval *ns; const jval *desc; } booking_t;

static long collect_bookings(jval **entries, long n, const char *xchg,
                             booking_t **out)
{
    long i, cnt = 0, cap = 0;
    booking_t *arr = NULL;
    for (i = 0; i < n; ++i) {
        jval *e = entries[i];
        jval *p;
        if (!is_str_eq(jv_obj_get(e, "op"), "BIND")) continue;
        if (!is_str_eq(jv_obj_get(e, "kind"), "exchange")) continue;
        p = jv_obj_get(e, "payload");
        if (!p || p->type != JV_OBJ) continue;   /* Python: payload or {} */
        {
            jval *x = jv_obj_get(p, "xchg");
            size_t xn = xchg ? strlen(xchg) : 0;
            if (!x || x->type != JV_STR) continue;
            if (xchg && (x->v.str.len != xn || memcmp(x->v.str.s, xchg, xn) != 0)) continue;
        }
        if (cnt == cap) {
            long nc = cap ? cap * 2 : 8;
            arr = (booking_t *)realloc(arr, (size_t)nc * sizeof(booking_t));
            cap = nc;
        }
        arr[cnt].ns = jv_obj_get(e, "ns");
        arr[cnt].desc = p;
        cnt++;
    }
    *out = arr;
    return cnt;
}

static void refuse_into(cagreement *out, const char *reason, const char *detail)
{
    out->agreed = 0;
    free(out->reason); out->reason = xstrdup(reason);
    free(out->detail); out->detail = xstrdup(detail);
}

int cagree(const char *path_a, const char *path_b, const char *xchg,
           double budget, cagreement *out)
{
    caudit va, vb;
    jval **ea = NULL, **eb = NULL;
    long na = 0, nb = 0;
    int rc;
    booking_t *ba = NULL, *bb = NULL;
    long nba, nbb, i, j;

    memset(out, 0, sizeof *out);
    out->xchg = xstrdup(xchg);

    rc = caudit_run(path_a, budget, &va, &ea, &na);
    if (rc != SC_OK) return rc;
    rc = caudit_run(path_b, budget, &vb, &eb, &nb);
    if (rc != SC_OK) { caudit_free(&va, ea, na); return rc; }

    out->head_a = dup_or_null(va.head_hash);
    out->head_b = dup_or_null(vb.head_hash);
    out->entries_a = va.entries;
    out->entries_b = vb.entries;

    if (!va.chain_ok || !vb.chain_ok) {
        const caudit *bad = !va.chain_ok ? &va : &vb;
        sbuf d; sbuf_init(&d);
        sbuf_printf(&d, "%s: hash chain broken at entry %ld", bad->path, bad->chain_broken_at);
        refuse_into(out, "chain_broken", d.buf ? d.buf : "");
        sbuf_free(&d);
        goto done;
    }
    if (!va.law_ok || !vb.law_ok) {
        const caudit *bad = !va.law_ok ? &va : &vb;
        sbuf d; sbuf_init(&d);
        sbuf_printf(&d, "%s: %s", bad->path,
                    bad->nviolations ? bad->violations[0] : "");
        refuse_into(out, "law_violated", d.buf ? d.buf : "");
        sbuf_free(&d);
        goto done;
    }
    /* Keyless core: no booking_keys enforcement step (Python's
     * enforcement runs here, opt-in, with Ed25519 — out of scope). */

    nba = collect_bookings(ea, na, xchg, &ba);
    nbb = collect_bookings(eb, nb, xchg, &bb);

    {
        typedef struct { const jval *ns; const jval *desc;
                         booking_t *other; long nother; } send_t;
        send_t *sends = (send_t *)malloc(
            (size_t)(nba + nbb > 0 ? nba + nbb : 1) * sizeof(send_t));
        long nsends = 0;
        cagreement first_failure;
        int have_failure = 0;
        memset(&first_failure, 0, sizeof first_failure);

        /* Sends: ledger A scanned before ledger B (Python order). */
        for (i = 0; i < nba; ++i)
            if (is_str_eq(jv_obj_get(ba[i].desc, "side"), "send")) {
                sends[nsends].ns = ba[i].ns; sends[nsends].desc = ba[i].desc;
                sends[nsends].other = bb; sends[nsends].nother = nbb;
                nsends++;
            }
        for (i = 0; i < nbb; ++i)
            if (is_str_eq(jv_obj_get(bb[i].desc, "side"), "send")) {
                sends[nsends].ns = bb[i].ns; sends[nsends].desc = bb[i].desc;
                sends[nsends].other = ba; sends[nsends].nother = nba;
                nsends++;
            }
        if (nsends == 0) {
            char *xr = py_str_repr(xchg, strlen(xchg));
            sbuf d; sbuf_init(&d);
            sbuf_printf(&d, "no SEND booking for exchange %s in either ledger", xr);
            refuse_into(out, "no_send", d.buf ? d.buf : "");
            sbuf_free(&d);
            free(xr);
            free(ba); free(bb); free(sends);
            goto done;
        }
        for (i = 0; i < nsends; ++i) {
            const jval *sender = sends[i].ns;
            const jval *p = sends[i].desc;
            const jval *peer = jv_obj_get(p, "peer");
            const jval *ph = jv_obj_get(p, "ph");
            long nrecv = 0;
            cagreement failure;
            int have_this_failure = 0;
            memset(&failure, 0, sizeof failure);
            for (j = 0; j < sends[i].nother; ++j)
                if (is_str_eq(jv_obj_get(sends[i].other[j].desc, "side"), "receive"))
                    nrecv++;
            if (nrecv == 0) {
                char *sd = display_str(sender), *pd = display_str(peer);
                char *xr = py_str_repr(xchg, strlen(xchg));
                sbuf d; sbuf_init(&d);
                sbuf_printf(&d, "%s booked SEND of %s to %s, but the counterpart ledger has no RECEIVE booking for it",
                            sd, xr, pd);
                failure.agreed = 0;
                failure.reason = xstrdup("missing_mirror");
                failure.detail = sbuf_steal(&d);
                have_this_failure = 1;
                free(sd); free(pd); free(xr);
            } else {
                for (j = 0; j < sends[i].nother; ++j) {
                    const jval *q;
                    const jval *r_ns;
                    if (!is_str_eq(jv_obj_get(sends[i].other[j].desc, "side"), "receive")) continue;
                    q = sends[i].other[j].desc;
                    r_ns = sends[i].other[j].ns;
                    if (!(r_ns && peer && jv_equal(r_ns, peer)) ||
                        !(jv_obj_get(q, "peer") && sender &&
                          jv_equal(jv_obj_get(q, "peer"), sender))) {
                        char *rd = display_str(r_ns), *qd = display_str(jv_obj_get(q, "peer"));
                        char *sd = display_str(sender), *pd = display_str(peer);
                        char *xr = py_str_repr(xchg, strlen(xchg));
                        sbuf d; sbuf_init(&d);
                        sbuf_printf(&d, "RECEIVE booking for %s is between %s and %s, not %s and %s",
                                    xr, rd, qd, sd, pd);
                        free(failure.reason); free(failure.detail);
                        failure.agreed = 0;
                        failure.reason = xstrdup("peer_mismatch");
                        failure.detail = sbuf_steal(&d);
                        have_this_failure = 1;
                        free(rd); free(qd); free(sd); free(pd); free(xr);
                    } else if (!jv_equal(jv_obj_get(q, "ph"), ph)) {
                        char *pd1 = display_str(ph), *pd2 = display_str(jv_obj_get(q, "ph"));
                        char *xr = py_str_repr(xchg, strlen(xchg));
                        sbuf d; sbuf_init(&d);
                        sbuf_printf(&d, "payload hash mismatch for %s: sender booked ph=%s, receiver booked ph=%s",
                                    xr, pd1, pd2);
                        free(failure.reason); free(failure.detail);
                        failure.agreed = 0;
                        failure.reason = xstrdup("payload_mismatch");
                        failure.detail = sbuf_steal(&d);
                        have_this_failure = 1;
                        free(pd1); free(pd2); free(xr);
                    } else {
                        char *sd = display_str(sender), *pd = display_str(peer);
                        char *phd = display_str(ph);
                        sbuf d; sbuf_init(&d);
                        sbuf_printf(&d, "%s -> %s: mirror bookings agree on payload hash %s",
                                    sd, pd, phd);
                        out->agreed = 1;
                        out->reason = xstrdup("agreed");
                        out->detail = sbuf_steal(&d);
                        out->sender = sd;
                        out->receiver = pd;
                        out->payload_hash = phd;
                        free(failure.reason); free(failure.detail);
                        free(first_failure.reason); free(first_failure.detail);
                        free(ba); free(bb); free(sends);
                        goto done;
                    }
                }
            }
            if (have_this_failure && !have_failure) {
                first_failure = failure;
                have_failure = 1;
            } else {
                free(failure.reason); free(failure.detail);
            }
        }
        if (have_failure) {
            out->agreed = 0;
            out->reason = first_failure.reason;
            out->detail = first_failure.detail;
        }
        free(ba); free(bb); free(sends);
    }

done:
    caudit_free(&va, ea, na);
    caudit_free(&vb, eb, nb);
    return SC_OK;
}

void cagreement_free(cagreement *a)
{
    free(a->xchg); free(a->reason); free(a->detail);
    free(a->sender); free(a->receiver); free(a->payload_hash);
    free(a->head_a); free(a->head_b);
    memset(a, 0, sizeof *a);
}

int cagreed_state(const char *path_a, const char *path_b, double budget,
                  cagreement **agreed_out, long *n_agreed,
                  cagreement **refused_out, long *n_refused)
{
    caudit va, vb;
    jval **ea = NULL, **eb = NULL;
    long na = 0, nb = 0, i;
    int rc;
    char **ids = NULL;
    long nids = 0, capids = 0;
    cagreement *agr = NULL, *ref = NULL;
    long nag = 0, capag = 0, nref = 0, capref = 0;

    *agreed_out = NULL; *n_agreed = 0;
    *refused_out = NULL; *n_refused = 0;

    rc = caudit_run(path_a, budget, &va, &ea, &na);
    if (rc != SC_OK) return rc;
    rc = caudit_run(path_b, budget, &vb, &eb, &nb);
    if (rc != SC_OK) { caudit_free(&va, ea, na); return rc; }

    {
        jval **sets[2];
        long ns[2], s;
        sets[0] = ea; sets[1] = eb; ns[0] = na; ns[1] = nb;
        for (s = 0; s < 2; ++s) {
            for (i = 0; i < ns[s]; ++i) {
                jval *e = sets[s][i];
                jval *p, *x;
                if (!is_str_eq(jv_obj_get(e, "op"), "BIND")) continue;
                if (!is_str_eq(jv_obj_get(e, "kind"), "exchange")) continue;
                p = jv_obj_get(e, "payload");
                if (!p || p->type != JV_OBJ) continue;
                x = jv_obj_get(p, "xchg");
                if (!x || x->type != JV_STR) continue;
                {
                    long j2;
                    int dup = 0;
                    char *xs = xstrndup(x->v.str.s, x->v.str.len);
                    for (j2 = 0; j2 < nids; ++j2)
                        if (strcmp(ids[j2], xs) == 0) { dup = 1; break; }
                    if (dup) { free(xs); continue; }
                    if (nids == capids) {
                        long nc = capids ? capids * 2 : 8;
                        ids = (char **)realloc(ids, (size_t)nc * sizeof(char *));
                        capids = nc;
                    }
                    ids[nids++] = xs;
                }
            }
        }
    }
    /* Sort ids by code point order (Python sorted over str). */
    {
        long a, b2;
        for (a = 1; a < nids; ++a) {
            char *t = ids[a];
            b2 = a;
            while (b2 > 0) {
                const char *x = ids[b2 - 1], *y = t;
                size_t xl = strlen(x), yl = strlen(y), m = xl < yl ? xl : yl;
                int c = m ? memcmp(x, y, m) : 0;
                if (c == 0) c = (xl < yl) ? -1 : (xl > yl ? 1 : 0);
                if (c <= 0) break;
                ids[b2] = ids[b2 - 1];
                b2--;
            }
            ids[b2] = t;
        }
    }
    for (i = 0; i < nids; ++i) {
        cagreement v;
        memset(&v, 0, sizeof v);
        rc = cagree(path_a, path_b, ids[i], budget, &v);
        if (rc == SC_OK) {
            if (v.agreed) {
                if (nag == capag) { long nc = capag ? capag * 2 : 8; agr = (cagreement *)realloc(agr, (size_t)nc * sizeof(cagreement)); capag = nc; }
                agr[nag++] = v;
            } else {
                if (nref == capref) { long nc = capref ? capref * 2 : 8; ref = (cagreement *)realloc(ref, (size_t)nc * sizeof(cagreement)); capref = nc; }
                ref[nref++] = v;
            }
        }
        free(ids[i]);
    }
    free(ids);
    *agreed_out = agr; *n_agreed = nag;
    *refused_out = ref; *n_refused = nref;
    caudit_free(&va, ea, na);
    caudit_free(&vb, eb, nb);
    return SC_OK;
}

/* ================= anchor ================= */

int cverify_anchor(const char *path, const char *pinned_head,
                   long pinned_entries, canchor *out)
{
    FILE *probe;
    caudit v;
    jval **entries = NULL;
    long n = 0;
    const char *status;

    memset(out, 0, sizeof *out);
    out->pinned_entries = pinned_entries;
    out->pinned_head = dup_or_null(pinned_head);

    probe = fopen(path, "rb");
    if (!probe) {
        out->status = xstrdup("missing");
        out->entries = 0;
        out->head_hash = NULL;
        return SC_OK;
    }
    fclose(probe);
    if (caudit_run(path, SC_LOG2_3, &v, &entries, &n) != SC_OK)
        return SC_ERR_IO;
    out->entries = v.entries;
    out->head_hash = dup_or_null(v.head_hash);

    if (pinned_entries == 0) {
        status = pinned_head == NULL ? "anchored" : "rewritten";
    } else if (!v.chain_ok && v.chain_broken_at < pinned_entries) {
        status = "chain_broken";
    } else if (v.entries < pinned_entries) {
        status = "truncated";
    } else {
        /* Stored h of the entry at the pinned position, raw file. */
        char *buf;
        size_t len = 0;
        line_t *lines;
        long nlines = 0, i, idx = -1;
        char *pinned_hash = NULL;
        buf = read_file(path, &len);
        lines = buf ? split_lines(buf, len, &nlines) : NULL;
        if (lines) {
            for (i = 0; i < nlines; ++i) {
                const char *s = lines[i].ptr;
                size_t m = lines[i].len;
                strip_span(&s, &m);
                if (m == 0) continue;
                idx++;
                if (idx == pinned_entries - 1) {
                    jval *e = jv_parse(s, m, NULL);
                    if (e && e->type == JV_OBJ) {
                        jval *h = jv_obj_get(e, "h");
                        if (h && h->type == JV_STR)
                            pinned_hash = xstrndup(h->v.str.s, h->v.str.len);
                    }
                    jv_free(e);
                    break;
                }
            }
            free(lines);
        }
        free(buf);
        status = (pinned_hash && pinned_head &&
                  strcmp(pinned_hash, pinned_head) == 0)
                     ? "anchored" : "rewritten";
        free(pinned_hash);
    }
    out->status = xstrdup(status);
    caudit_free(&v, entries, n);
    return SC_OK;
}

void canchor_free(canchor *a)
{
    free(a->status); free(a->head_hash); free(a->pinned_head);
    memset(a, 0, sizeof *a);
}

/* ================= tenants, cells, stitches ================= */

void ctenant_init(ctenant *t, const char *identity, double budget)
{
    t->identity = xstrdup(identity);
    t->budget = budget;
    t->gamma = 0.0;
    t->pending = 0.0;
}
void ctenant_free(ctenant *t) { free(t->identity); t->identity = NULL; }
double ctenant_eta(const ctenant *t)
{
    double e = t->budget - t->gamma - t->pending;
    return e > 0.0 ? e : 0.0;
}
int ctenant_can_afford(const ctenant *t, double cost)
{
    return t->gamma + t->pending + cost <= t->budget + SC_CHARGE_EPS;
}
int ctenant_charge(ctenant *t, double cost)
{
    if (!ctenant_can_afford(t, cost)) return 0;
    t->gamma += cost;
    return 1;
}

void ccellset_init(ccellset *s) { s->cells = NULL; s->len = 0; s->cap = 0; }
void ccellset_free(ccellset *s)
{
    size_t i;
    for (i = 0; i < s->len; ++i) {
        free(s->cells[i].id); free(s->cells[i].kind); free(s->cells[i].parent);
        jv_free(s->cells[i].payload);
    }
    free(s->cells);
    s->cells = NULL; s->len = s->cap = 0;
}
ccell *ccellset_find(ccellset *s, const char *id)
{
    size_t i;
    for (i = 0; i < s->len; ++i)
        if (strcmp(s->cells[i].id, id) == 0) return &s->cells[i];
    return NULL;
}
static ccell *cellset_add(ccellset *s, char *id, const char *kind,
                          const char *parent, jval *payload_copy)
{
    ccell *c;
    if (!s) { jv_free(payload_copy); free(id); return NULL; }
    if (s->len == s->cap) {
        size_t nc = s->cap ? s->cap * 2 : 8;
        s->cells = (ccell *)realloc(s->cells, nc * sizeof(ccell));
        s->cap = nc;
    }
    c = &s->cells[s->len++];
    c->id = id;
    c->kind = xstrdup(kind);
    c->parent = dup_or_null(parent);
    c->payload = payload_copy;
    return c;
}

double sc_surprise_kl(const jval *predicted, const jval *observed)
{
    double s = 0.0;
    size_t i;
    if (!predicted || predicted->type != JV_OBJ ||
        !observed || observed->type != JV_OBJ) return 0.0;
    for (i = 0; i < predicted->v.obj.len; ++i) {
        const char *k = predicted->v.obj.keys[i];
        size_t kl = predicted->v.obj.klens[i];
        jval *ov = jv_obj_getn(observed, k, kl);
        if (ov) {
            if (!jv_equal(predicted->v.obj.vals[i], ov)) s += 1.0;
        }
        /* predicted-only: no surprise */
    }
    for (i = 0; i < observed->v.obj.len; ++i) {
        const char *k = observed->v.obj.keys[i];
        size_t kl = observed->v.obj.klens[i];
        if (!jv_obj_getn(predicted, k, kl)) s += 1.0;
    }
    return s;
}

static jval *entry_base(const ctenant *t, const char *op, int64_t ts)
{
    jval *e = jv_new_obj();
    jv_obj_set(e, "t", jv_new_int(ts));
    jv_obj_set(e, "ns", jv_new_str(t->identity));
    jv_obj_set(e, "op", jv_new_str(op));
    return e;
}

int cstitch_bind(ctenant *t, cledger *L, ccellset *set, const char *kind,
                 const jval *payload, const char *parent, double cost,
                 int64_t ts, ccell **out_cell)
{
    char *id;
    jval *e;
    char hbuf[17];
    int rc;
    if (out_cell) *out_cell = NULL;
    if (!payload || payload->type != JV_OBJ) return SC_ERR_TYPE;
    if (!ctenant_charge(t, cost)) return SC_ERR_CONSERVATION;
    id = sc_cell_id(t->identity, kind, payload, parent, ts);
    if (!id) return SC_ERR_NOMEM;
    e = entry_base(t, "BIND", ts);
    jv_obj_set(e, "cell", jv_new_str(id));
    jv_obj_set(e, "kind", jv_new_str(kind));
    jv_obj_set(e, "payload", jv_copy(payload));
    if (parent) jv_obj_set(e, "parent", jv_new_str(parent));
    else jv_obj_set(e, "parent", jv_new_null());
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    if (rc != SC_OK) { free(id); return rc; }
    if (set) {
        ccell *c = cellset_add(set, id, kind, parent, jv_copy(payload));
        if (out_cell) *out_cell = c;
    } else {
        free(id);
    }
    return SC_OK;
}

int cstitch_link(ctenant *t, cledger *L, const ccell *a, const ccell *b,
                 const char *relation, int64_t ts)
{
    jval *e = entry_base(t, "LINK", ts);
    char hbuf[17];
    int rc;
    jv_obj_set(e, "a", jv_new_str(a->id));
    jv_obj_set(e, "b", jv_new_str(b->id));
    jv_obj_set(e, "rel", jv_new_str(relation));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_effect(ctenant *t, cledger *L, ccell *cell, const char *key,
                   const jval *predicted, const jval *observed,
                   int64_t ts, int *committed)
{
    double s = sc_surprise_kl(predicted, observed);
    jval *e;
    char hbuf[17];
    int rc;
    jval *ov;
    *committed = 0;
    if (!ctenant_can_afford(t, s)) return SC_OK;   /* refused: Python False */
    if (!ctenant_charge(t, s)) return SC_OK;
    ov = (observed && observed->type == JV_OBJ) ? jv_obj_get(observed, key) : NULL;
    if (ov) {
        jv_obj_set(cell->payload, key, jv_copy(ov));
    } else if (predicted && predicted->type == JV_OBJ &&
               jv_obj_get(predicted, key)) {
        /* predicted but not observed: no mutation */
    } else {
        jv_obj_set(cell->payload, key, jv_new_null());
    }
    e = entry_base(t, "EFFECT", ts);
    jv_obj_set(e, "cell", jv_new_str(cell->id));
    jv_obj_set(e, "k", jv_new_str(key));
    jv_obj_set(e, "v", ov ? jv_copy(ov) : jv_new_null());
    jv_obj_set(e, "s", jv_new_dbl(s));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    if (rc != SC_OK) return rc;
    *committed = 1;
    return SC_OK;
}

int cstitch_tick(ctenant *t, cledger *L, int64_t ts)
{
    jval *e;
    char hbuf[17];
    int rc;
    t->gamma = 0.0;
    t->pending = 0.0;
    e = entry_base(t, "TICK", ts);
    jv_obj_set(e, "g", jv_new_dbl(0.0));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_attest(ctenant *t, cledger *L, const ccell *cell,
                   const char *claim, int64_t ts, char sig_out[17])
{
    /* Keyless placeholder (v0.1): first 16 hex of
     * SHA-256("tenant:cell:claim"). The keyed Ed25519 form is out
     * of scope for this core; the field and format are unchanged. */
    sbuf b;
    char hex[65];
    jval *e;
    char hbuf[17];
    int rc;
    sbuf_init(&b);
    sbuf_puts(&b, t->identity);
    sbuf_putc(&b, ':');
    sbuf_puts(&b, cell->id);
    sbuf_putc(&b, ':');
    sbuf_puts(&b, claim);
    sha256_hex((const uint8_t *)b.buf, b.len, hex);
    sbuf_free(&b);
    memcpy(sig_out, hex, 16);
    sig_out[16] = '\0';
    e = entry_base(t, "ATTEST", ts);
    jv_obj_set(e, "cell", jv_new_str(cell->id));
    jv_obj_set(e, "claim", jv_new_str(claim));
    jv_obj_set(e, "sig", jv_new_strn(sig_out, 16));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_delegate(ctenant *t, cledger *L, const ccell *cell,
                     const char *to, const char *scope, int64_t ts)
{
    jval *e = entry_base(t, "DELEGATE", ts);
    char hbuf[17];
    int rc;
    jv_obj_set(e, "cell", jv_new_str(cell->id));
    jv_obj_set(e, "to", jv_new_str(to));
    jv_obj_set(e, "scope", jv_new_str(scope));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_contest(ctenant *t, cledger *L, const ccell *cell,
                    const char *reason, int64_t ts)
{
    jval *e = entry_base(t, "CONTEST", ts);
    char hbuf[17];
    int rc;
    jv_obj_set(e, "cell", jv_new_str(cell->id));
    jv_obj_set(e, "reason", jv_new_str(reason));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_merger(ctenant *t, cledger *L, ccellset *set,
                   const ccell *a, const ccell *b, const char *kind,
                   const jval *payload, int64_t ts_bind, int64_t ts_entry,
                   ccell **out_cell)
{
    ccell *nc = NULL;
    jval *e;
    char hbuf[17];
    int rc = cstitch_bind(t, L, set, kind, payload, a->id, 0.0, ts_bind, &nc);
    if (rc != SC_OK) return rc;
    e = entry_base(t, "MERGER", ts_entry);
    jv_obj_set(e, "cell", jv_new_str(nc ? nc->id : ""));
    jv_obj_set(e, "a", jv_new_str(a->id));
    jv_obj_set(e, "b", jv_new_str(b->id));
    jv_obj_set(e, "kind", jv_new_str(kind));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    if (out_cell) *out_cell = nc;
    return rc;
}

int cstitch_revoke(ctenant *t, cledger *L, const ccell *cell,
                   const char *reason, int64_t ts)
{
    jval *e = entry_base(t, "REVOKE", ts);
    char hbuf[17];
    int rc;
    jv_obj_set(e, "cell", jv_new_str(cell->id));
    jv_obj_set(e, "reason", jv_new_str(reason));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_withdraw(ctenant *t, cledger *L, int has_amount, double amount,
                     int64_t ts)
{
    jval *e = entry_base(t, "WITHDRAW", ts);
    char hbuf[17];
    int rc;
    if (!has_amount) amount = ctenant_eta(t);
    jv_obj_set(e, "amount", jv_new_dbl(amount));
    jv_obj_set(e, "g", jv_new_dbl(t->gamma));
    rc = cledger_append(L, e, hbuf);
    jv_free(e);
    return rc;
}

int cstitch_declare_budget(ctenant *t, cledger *L, ccellset *set,
                           int has_budget, double budget, int64_t ts,
                           ccell **out_cell)
{
    jval *p = jv_new_obj();
    int rc;
    jv_obj_set(p, "budget", jv_new_dbl(has_budget ? budget : t->budget));
    rc = cstitch_bind(t, L, set, "tenant", p, NULL, 0.0, ts, out_cell);
    jv_free(p);
    return rc;
}

int csc_book(ctenant *t, cledger *L, ccellset *set, const char *xchg,
             const char *peer, const jval *payload, int is_send,
             int64_t ts, ccell **out_cell)
{
    jval *desc;
    char *ph;
    int rc;
    /* Python book_*: ValueError before any write on non-finite. */
    if (jv_contains_nonfinite(payload)) return SC_ERR_NONFINITE;
    ph = sc_payload_hash(payload);
    if (!ph) return SC_ERR_NOMEM;
    desc = jv_new_obj();
    jv_obj_set(desc, "xchg", jv_new_str(xchg));
    jv_obj_set(desc, "side", jv_new_str(is_send ? "send" : "receive"));
    jv_obj_set(desc, "peer", jv_new_str(peer));
    jv_obj_set(desc, "ph", jv_new_str(ph));
    free(ph);
    rc = cstitch_bind(t, L, set, "exchange", desc, NULL, 0.0, ts, out_cell);
    jv_free(desc);
    return rc;
}
