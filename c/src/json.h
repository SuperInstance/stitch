/* json.h — a minimal JSON value model for stitch-c.
 *
 * The shapes stitch payloads use, with the two properties the SPEC
 * byte-exactness pin needs:
 *   - number text survives parsing: integers stay exact (int64 /
 *     uint64, or verbatim text beyond that range — Python ints are
 *     arbitrary precision and dump their full digits); doubles are
 *     reformatted with an exact emulation of CPython float repr.
 *   - canonical serialisation is json.dumps(sort_keys=True,
 *     separators=(",", ":"), ensure_ascii=False): keys sorted by
 *     code point, no whitespace, non-ASCII emitted as raw UTF-8,
 *     only control chars, quote and backslash escaped.
 *
 * The parser mirrors Python json.loads where the audit path depends
 * on it: duplicate object keys are last-wins, NaN / Infinity tokens
 * are accepted as doubles (Python-foreign ledgers may contain them;
 * the read side is unpoliced per SPEC), \u escapes incl. surrogate
 * pairs are decoded to UTF-8.
 */
#ifndef STITCHC_JSON_H
#define STITCHC_JSON_H

#include <stddef.h>
#include <stdint.h>

/* ---- growable byte buffer ---------------------------------------- */
typedef struct {
    char  *buf;
    size_t len;
    size_t cap;
    int    oom;          /* set on allocation failure; ops become no-ops */
} sbuf;

void sbuf_init(sbuf *b);
void sbuf_free(sbuf *b);
void sbuf_putn(sbuf *b, const void *data, size_t n);
void sbuf_puts(sbuf *b, const char *s);
void sbuf_putc(sbuf *b, char c);
void sbuf_printf(sbuf *b, const char *fmt, ...);
char *sbuf_steal(sbuf *b);            /* NUL-terminated result; caller frees */
char *xstrdup(const char *s);
char *xstrndup(const char *s, size_t n);

/* ---- JSON values -------------------------------------------------- */
typedef enum {
    JV_NULL, JV_BOOL, JV_INT, JV_UINT, JV_DBL, JV_BIGINT,
    JV_STR, JV_ARR, JV_OBJ
} jv_type;

typedef struct jval jval;
struct jval {
    jv_type type;
    union {
        int          boolean;
        int64_t      i;
        uint64_t     u;
        double       d;
        char        *bigint;   /* verbatim decimal text, out-of-range ints */
        struct { char *s; size_t len; } str;
        struct { jval **items; size_t len, cap; } arr;
        struct {
            char  **keys;      /* NUL-terminated UTF-8 byte strings */
            size_t *klens;     /* explicit lengths (keys may embed NUL) */
            jval  **vals;
            size_t  len, cap;
        } obj;
    } v;
};

jval *jv_new_null(void);
jval *jv_new_bool(int b);
jval *jv_new_int(int64_t i);
jval *jv_new_uint(uint64_t u);
jval *jv_new_dbl(double d);
jval *jv_new_strn(const char *s, size_t len);
jval *jv_new_str(const char *s);
jval *jv_new_arr(void);
jval *jv_new_obj(void);
void  jv_free(jval *v);
jval *jv_copy(const jval *v);

void  jv_arr_push(jval *arr, jval *item);            /* takes ownership */
/* Set key (copies key bytes); replaces existing (Python dict last-wins);
 * takes ownership of val. */
void  jv_obj_setn(jval *obj, const char *key, size_t klen, jval *val);
void  jv_obj_set(jval *obj, const char *key, jval *val);
jval *jv_obj_getn(const jval *obj, const char *key, size_t klen);
jval *jv_obj_get(const jval *obj, const char *key);
int   jv_obj_del(jval *obj, const char *key);        /* 1 if removed */

/* Parse JSON text (pass explicit length; input need not be
 * NUL-terminated). Returns NULL on parse error and sets *err
 * (static string) when err != NULL. */
jval *jv_parse(const char *text, size_t len, const char **err);

/* Canonical serialisation: json.dumps(sort_keys, separators,
 * ensure_ascii=False) byte-for-byte. Appends to b (no NUL). */
void jv_write_canonical(const jval *v, sbuf *b);
char *jv_canonical(const jval *v);                    /* caller frees */

/* CPython repr(float) emulation (also json.dumps float output):
 * shortest round-trip digits, scientific iff decpt <= -4 or
 * decpt >= 17, ".0" for integral fixed forms, NaN / Infinity /
 * -Infinity spelled as Python. Writes into buf (>= 40 bytes). */
void py_float_repr(double v, char *buf, size_t bufsz);

/* CPython repr(str) for short strings used in verdict details.
 * Returns malloc'd string; caller frees. Non-ASCII passes through
 * raw (Python keeps printable unicode raw in repr). */
char *py_str_repr(const char *s, size_t len);

/* Python == over JSON values: numbers compare numerically across
 * int/uint/double (bool counts as 0/1), containers recursively,
 * NaN is unequal to everything including itself. */
int jv_equal(const jval *a, const jval *b);

/* Numeric coercion mirroring Python float(value) for numbers and
 * numeric strings; *ok set 0 when Python would raise (null, garbage
 * string, containers). Caller decides what a raise means. */
double jv_as_double(const jval *v, int *ok);

/* 1 if the value contains a non-finite double anywhere (JSON object
 * keys are strings, so only values are checked). */
int jv_contains_nonfinite(const jval *v);

#endif
