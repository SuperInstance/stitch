/* stitch.h — pure C99 implementation of the stitch KEYLESS core.
 *
 * Mirrors the Python reference (stitch-python) semantics as pinned
 * by SPEC.md: canonical JSON, FNV-1a 64 hash chain, SHA-256 payload
 * commitments and cell ids, the conservation-law audit with
 * position-aware budget declarations, bilateral agreement/minting,
 * the head-hash anchor, and the 11 stitches on the write side.
 *
 * Keyed paths are OUT of scope here: no Ed25519 (attest writes the
 * v0.1 SHA-256 placeholder; audits skip signature checks; agree
 * performs no booking_keys enforcement). The seams are where the
 * Python code has them — attest's sig field and agree's enforcement
 * step sit in the same places — so a crypto module can be added
 * later without touching the wire format or the verdict logic.
 *
 * Error codes (negative returns): 0 ok, SC_ERR_IO, SC_ERR_LEDGER
 * (e.g. entry already has "h"), SC_ERR_CONSERVATION, SC_ERR_TYPE
 * (payload not an object where a dict is required),
 * SC_ERR_NONFINITE (SPEC numeric policy: refused on write paths),
 * SC_ERR_PARSE, SC_ERR_NOMEM.
 */
#ifndef STITCHC_STITCH_H
#define STITCHC_STITCH_H

#include <stdint.h>
#include <stdio.h>
#include "json.h"

#define SC_OK              0
#define SC_ERR_IO         -1
#define SC_ERR_LEDGER     -2
#define SC_ERR_CONSERVATION -3
#define SC_ERR_TYPE       -4
#define SC_ERR_NONFINITE  -5
#define SC_ERR_PARSE      -6
#define SC_ERR_NOMEM      -7

extern const double SC_LOG2_3;       /* 1.584962500721156 */
#define SC_FNV_OFFSET 0xcbf29ce484222325ULL
#define SC_FNV_PRIME  0x100000001b3ULL

uint64_t sc_fnv1a(const void *data, size_t len, uint64_t seed);
void sc_hex64(uint64_t h, char out[17]);       /* "%016x" */

/* SHA-256 hex of the canonical JSON of a payload (malloc'd, 65). */
char *sc_payload_hash(const jval *payload);

/* First 16 hex of SHA-256 over the cell-id material (malloc'd, 17). */
char *sc_cell_id(const char *tenant, const char *kind, const jval *payload,
                 const char *parent /* NULL for none */, int64_t ts);

/* ---- ledger writer (stitch.core.Ledger) ---- */
typedef struct {
    char    *path;
    FILE    *f;
    uint64_t last;
} cledger;

int  cledger_open(cledger *L, const char *path);
int  cledger_append(cledger *L, const jval *entry, char out_h[17]);
void cledger_close(cledger *L);
/* stitch.core.Ledger.verify: 1 chain ok, 0 not. Python verify()
 * returns True for a missing file; we mirror that (missing -> 1). */
int  cledger_verify(const char *path);

/* ---- foreign-ledger audit (stitch.agreement._audit, keyless) ---- */
typedef struct {
    char   *path;
    char   *tenant;           /* single tenant identity, else NULL */
    long    entries;          /* valid-prefix length */
    int     chain_ok;
    long    chain_broken_at;  /* -1 when chain_ok */
    int     law_ok;
    double  law_excess;
    char  **violations;
    long    nviolations;
    char   *head_hash;        /* NULL when no valid entry */
    double  budget;           /* fallback budget used */
    char  **budget_keys;      /* declared budgets (claims) */
    double *budget_vals;
    long    nbudgets;
} caudit;

int  caudit_ok(const caudit *v);
/* Runs the audit. When entries_out != NULL, the valid-prefix entries
 * (objects, "h" removed, owned by caller) are returned there.
 * Returns SC_OK or SC_ERR_IO (missing file: Python audit_ledger
 * raises FileNotFoundError). */
int  caudit_run(const char *path, double budget, caudit *out,
                jval ***entries_out, long *n_entries_out);
void caudit_free(caudit *v, jval **entries, long n_entries);

/* ---- bilateral agreement (stitch.agreement.agree, keyless) ---- */
typedef struct {
    char *xchg;
    int   agreed;
    char *reason;             /* agreed/no_send/missing_mirror/... */
    char *detail;
    char *sender;             /* NULL unless agreed */
    char *receiver;           /* NULL unless agreed */
    char *payload_hash;       /* NULL unless agreed */
    char *head_a, *head_b;    /* pins; NULL when that side is empty */
    long  entries_a, entries_b;
} cagreement;

int  cagree(const char *path_a, const char *path_b, const char *xchg,
            double budget, cagreement *out);
void cagreement_free(cagreement *a);

/* The fold (stitch.agreement.agreed_state, keyless): every exchange
 * id booked in either ledger, agreed/refused partition, each sorted
 * by exchange id. Arrays of cagreement (owned); free each element
 * with cagreement_free then free the arrays. */
int  cagreed_state(const char *path_a, const char *path_b, double budget,
                   cagreement **agreed_out, long *n_agreed,
                   cagreement **refused_out, long *n_refused);

/* ---- head-hash anchor (stitch.agreement.verify_anchor) ---- */
typedef struct {
    char *status;             /* anchored/truncated/rewritten/chain_broken/missing */
    long  entries;
    long  pinned_entries;
    char *head_hash;          /* current head (valid prefix), or NULL */
    char *pinned_head;        /* the pin passed in, or NULL */
} canchor;

int  cverify_anchor(const char *path, const char *pinned_head,
                    long pinned_entries, canchor *out);
void canchor_free(canchor *a);

/* ---- tenants, cells, stitches (write side) ---- */
typedef struct {
    char  *identity;
    double budget;            /* C */
    double gamma;             /* γ */
    double pending;           /* γ_pending */
} ctenant;

void   ctenant_init(ctenant *t, const char *identity, double budget);
void   ctenant_free(ctenant *t);
double ctenant_eta(const ctenant *t);
int    ctenant_can_afford(const ctenant *t, double cost);
int    ctenant_charge(ctenant *t, double cost);   /* 1 charged, 0 refused */

typedef struct {
    char *id;
    char *kind;
    char *parent;             /* NULL when none */
    jval *payload;            /* owned object */
} ccell;

typedef struct {
    ccell *cells;
    size_t len, cap;
} ccellset;

void   ccellset_init(ccellset *s);
void   ccellset_free(ccellset *s);
ccell *ccellset_find(ccellset *s, const char *id);

/* The discrete-KL surprise oracle (stitch.core.KLSurprise). */
double sc_surprise_kl(const jval *predicted, const jval *observed);

/* All stitches take an explicit timestamp (nanoseconds) — Python
 * reads the wall clock; the wire format is identical, and explicit
 * ts is what makes cross-language byte-exact fixtures possible.
 * Payload arguments are borrowed; cells registered in `set` (when
 * non-NULL) own their payload copies. */

int cstitch_bind(ctenant *t, cledger *L, ccellset *set, const char *kind,
                 const jval *payload, const char *parent, double cost,
                 int64_t ts, ccell **out_cell);
int cstitch_link(ctenant *t, cledger *L, const ccell *a, const ccell *b,
                 const char *relation, int64_t ts);
int cstitch_effect(ctenant *t, cledger *L, ccell *cell, const char *key,
                   const jval *predicted, const jval *observed,
                   int64_t ts, int *committed);
int cstitch_tick(ctenant *t, cledger *L, int64_t ts);
int cstitch_attest(ctenant *t, cledger *L, const ccell *cell,
                   const char *claim, int64_t ts, char sig_out[17]);
int cstitch_delegate(ctenant *t, cledger *L, const ccell *cell,
                     const char *to, const char *scope, int64_t ts);
int cstitch_contest(ctenant *t, cledger *L, const ccell *cell,
                    const char *reason, int64_t ts);
int cstitch_merger(ctenant *t, cledger *L, ccellset *set,
                   const ccell *a, const ccell *b, const char *kind,
                   const jval *payload, int64_t ts_bind, int64_t ts_entry,
                   ccell **out_cell);
int cstitch_revoke(ctenant *t, cledger *L, const ccell *cell,
                   const char *reason, int64_t ts);
int cstitch_withdraw(ctenant *t, cledger *L, int has_amount, double amount,
                     int64_t ts);
int cstitch_declare_budget(ctenant *t, cledger *L, ccellset *set,
                           int has_budget, double budget, int64_t ts,
                           ccell **out_cell);

/* Exchange bookings (stitch.agreement.book_send / book_receive,
 * keyless). Refuses non-finite payloads (SC_ERR_NONFINITE) before
 * anything is written, like the Python helpers. */
int csc_book(ctenant *t, cledger *L, ccellset *set, const char *xchg,
             const char *peer, const jval *payload, int is_send,
             int64_t ts, ccell **out_cell);

#endif
