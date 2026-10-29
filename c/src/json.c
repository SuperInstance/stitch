/* json.c — see json.h. */
#include "json.h"

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ================= sbuf ================= */

void sbuf_init(sbuf *b) { b->buf = NULL; b->len = 0; b->cap = 0; b->oom = 0; }
void sbuf_free(sbuf *b) { free(b->buf); b->buf = NULL; b->len = b->cap = 0; }

static void sbuf_grow(sbuf *b, size_t need)
{
    size_t nc;
    char *nb;
    if (b->oom) return;
    if (b->len + need + 1 <= b->cap) return;
    nc = b->cap ? b->cap : 64;
    while (nc < b->len + need + 1) nc *= 2;
    nb = (char *)realloc(b->buf, nc);
    if (!nb) { b->oom = 1; return; }
    b->buf = nb; b->cap = nc;
}

void sbuf_putn(sbuf *b, const void *data, size_t n)
{
    if (b->oom || n == 0) return;
    sbuf_grow(b, n);
    if (b->oom) return;
    memcpy(b->buf + b->len, data, n);
    b->len += n;
    b->buf[b->len] = '\0';
}
void sbuf_puts(sbuf *b, const char *s) { if (s) sbuf_putn(b, s, strlen(s)); }
void sbuf_putc(sbuf *b, char c) { sbuf_putn(b, &c, 1); }

void sbuf_printf(sbuf *b, const char *fmt, ...)
{
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    sbuf_puts(b, tmp);
}

char *sbuf_steal(sbuf *b)
{
    char *r;
    if (!b->buf) { r = xstrdup(""); }
    else r = b->buf;
    b->buf = NULL; b->len = b->cap = 0;
    return r;
}

char *xstrndup(const char *s, size_t n)
{
    char *r = (char *)malloc(n + 1);
    if (!r) return NULL;
    memcpy(r, s, n);
    r[n] = '\0';
    return r;
}
char *xstrdup(const char *s) { return xstrndup(s, strlen(s)); }

/* ================= value basics ================= */

static jval *jv_alloc(jv_type t)
{
    jval *v = (jval *)calloc(1, sizeof(jval));
    if (v) v->type = t;
    return v;
}
jval *jv_new_null(void) { return jv_alloc(JV_NULL); }
jval *jv_new_bool(int b) { jval *v = jv_alloc(JV_BOOL); if (v) v->v.boolean = !!b; return v; }
jval *jv_new_int(int64_t i) { jval *v = jv_alloc(JV_INT); if (v) v->v.i = i; return v; }
jval *jv_new_uint(uint64_t u) { jval *v = jv_alloc(JV_UINT); if (v) v->v.u = u; return v; }
jval *jv_new_dbl(double d) { jval *v = jv_alloc(JV_DBL); if (v) v->v.d = d; return v; }
jval *jv_new_strn(const char *s, size_t len)
{
    jval *v = jv_alloc(JV_STR);
    if (!v) return NULL;
    v->v.str.s = xstrndup(s, len);
    v->v.str.len = len;
    return v;
}
jval *jv_new_str(const char *s) { return jv_new_strn(s, strlen(s)); }
jval *jv_new_arr(void) { return jv_alloc(JV_ARR); }
jval *jv_new_obj(void) { return jv_alloc(JV_OBJ); }

void jv_free(jval *v)
{
    size_t i;
    if (!v) return;
    switch (v->type) {
    case JV_BIGINT: free(v->v.bigint); break;
    case JV_STR: free(v->v.str.s); break;
    case JV_ARR:
        for (i = 0; i < v->v.arr.len; ++i) jv_free(v->v.arr.items[i]);
        free(v->v.arr.items);
        break;
    case JV_OBJ:
        for (i = 0; i < v->v.obj.len; ++i) {
            free(v->v.obj.keys[i]);
            jv_free(v->v.obj.vals[i]);
        }
        free(v->v.obj.keys); free(v->v.obj.klens); free(v->v.obj.vals);
        break;
    default: break;
    }
    free(v);
}

jval *jv_copy(const jval *v)
{
    jval *r;
    size_t i;
    if (!v) return NULL;
    switch (v->type) {
    case JV_NULL: return jv_new_null();
    case JV_BOOL: return jv_new_bool(v->v.boolean);
    case JV_INT: return jv_new_int(v->v.i);
    case JV_UINT: return jv_new_uint(v->v.u);
    case JV_DBL: return jv_new_dbl(v->v.d);
    case JV_BIGINT:
        r = jv_alloc(JV_BIGINT);
        if (r) r->v.bigint = xstrdup(v->v.bigint);
        return r;
    case JV_STR: return jv_new_strn(v->v.str.s, v->v.str.len);
    case JV_ARR:
        r = jv_new_arr();
        if (!r) return NULL;
        for (i = 0; i < v->v.arr.len; ++i) jv_arr_push(r, jv_copy(v->v.arr.items[i]));
        return r;
    case JV_OBJ:
        r = jv_new_obj();
        if (!r) return NULL;
        for (i = 0; i < v->v.obj.len; ++i)
            jv_obj_setn(r, v->v.obj.keys[i], v->v.obj.klens[i], jv_copy(v->v.obj.vals[i]));
        return r;
    }
    return NULL;
}

void jv_arr_push(jval *arr, jval *item)
{
    if (!arr || arr->type != JV_ARR || !item) { jv_free(item); return; }
    if (arr->v.arr.len == arr->v.arr.cap) {
        size_t nc = arr->v.arr.cap ? arr->v.arr.cap * 2 : 8;
        jval **ni = (jval **)realloc(arr->v.arr.items, nc * sizeof(jval *));
        if (!ni) { jv_free(item); return; }
        arr->v.arr.items = ni; arr->v.arr.cap = nc;
    }
    arr->v.arr.items[arr->v.arr.len++] = item;
}

/* Key identity follows Python dict semantics: code point order,
 * which for UTF-8 bytes is memcmp order with shorter-prefix-first. */
static int keycmp(const char *a, size_t alen, const char *b, size_t blen)
{
    size_t m = alen < blen ? alen : blen;
    int c = m ? memcmp(a, b, m) : 0;
    if (c) return c;
    if (alen < blen) return -1;
    if (alen > blen) return 1;
    return 0;
}

void jv_obj_setn(jval *obj, const char *key, size_t klen, jval *val)
{
    size_t i;
    if (!obj || obj->type != JV_OBJ || !val) { jv_free(val); return; }
    for (i = 0; i < obj->v.obj.len; ++i) {
        if (keycmp(obj->v.obj.keys[i], obj->v.obj.klens[i], key, klen) == 0) {
            jv_free(obj->v.obj.vals[i]);
            obj->v.obj.vals[i] = val;
            return;
        }
    }
    if (obj->v.obj.len == obj->v.obj.cap) {
        size_t nc = obj->v.obj.cap ? obj->v.obj.cap * 2 : 8;
        char **nk = (char **)realloc(obj->v.obj.keys, nc * sizeof(char *));
        size_t *nl = (size_t *)realloc(obj->v.obj.klens, nc * sizeof(size_t));
        jval **nv = (jval **)realloc(obj->v.obj.vals, nc * sizeof(jval *));
        if (!nk || !nl || !nv) { jv_free(val); return; }
        obj->v.obj.keys = nk; obj->v.obj.klens = nl; obj->v.obj.vals = nv;
        obj->v.obj.cap = nc;
    }
    obj->v.obj.keys[obj->v.obj.len] = xstrndup(key, klen);
    obj->v.obj.klens[obj->v.obj.len] = klen;
    obj->v.obj.vals[obj->v.obj.len] = val;
    obj->v.obj.len++;
}
void jv_obj_set(jval *obj, const char *key, jval *val)
{ jv_obj_setn(obj, key, strlen(key), val); }

jval *jv_obj_getn(const jval *obj, const char *key, size_t klen)
{
    size_t i;
    if (!obj || obj->type != JV_OBJ) return NULL;
    for (i = 0; i < obj->v.obj.len; ++i)
        if (keycmp(obj->v.obj.keys[i], obj->v.obj.klens[i], key, klen) == 0)
            return obj->v.obj.vals[i];
    return NULL;
}
jval *jv_obj_get(const jval *obj, const char *key)
{ return jv_obj_getn(obj, key, strlen(key)); }

int jv_obj_del(jval *obj, const char *key)
{
    size_t i, klen = strlen(key);
    if (!obj || obj->type != JV_OBJ) return 0;
    for (i = 0; i < obj->v.obj.len; ++i) {
        if (keycmp(obj->v.obj.keys[i], obj->v.obj.klens[i], key, klen) == 0) {
            free(obj->v.obj.keys[i]);
            jv_free(obj->v.obj.vals[i]);
            memmove(&obj->v.obj.keys[i], &obj->v.obj.keys[i + 1],
                    (obj->v.obj.len - i - 1) * sizeof(char *));
            memmove(&obj->v.obj.klens[i], &obj->v.obj.klens[i + 1],
                    (obj->v.obj.len - i - 1) * sizeof(size_t));
            memmove(&obj->v.obj.vals[i], &obj->v.obj.vals[i + 1],
                    (obj->v.obj.len - i - 1) * sizeof(jval *));
            obj->v.obj.len--;
            return 1;
        }
    }
    return 0;
}

/* ================= Python float repr ================= */

void py_float_repr(double v, char *buf, size_t bufsz)
{
    if (isnan(v)) { snprintf(buf, bufsz, "NaN"); return; }
    if (isinf(v)) { snprintf(buf, bufsz, "%sInfinity", signbit(v) ? "-" : ""); return; }
    if (v == 0.0) { snprintf(buf, bufsz, "%s0.0", signbit(v) ? "-" : ""); return; }
    {
        int neg = signbit(v) != 0;
        double a = fabs(v);
        char digits[24];
        int ndig = 0, decpt = 0, prec, found = 0;
        char tmp[64];
        /* Shortest round-trip digits: the smallest precision whose
         * correctly-rounded decimal reads back as exactly v. glibc
         * printf is correctly rounded, as is CPython's dtoa, so the
         * digit strings agree. */
        /* Shortest round-trip digits. The correctly-rounded %.*e
         * candidate can fall just outside the round-trip interval
         * (the interval is asymmetric — e.g. below a power of two
         * the ulp halves) while a decimal neighbour inside it
         * exists, so test the candidate and both neighbours; among
         * round-trippers choose the closest to the true value,
         * ties to an even last digit — CPython's dtoa rule. */
        for (prec = 1; prec <= 17 && !found; ++prec) {
            char base[24];
            int bn = 0, bdecpt = 0, ci, best = -1;
            long double bestdist = 0.0L;
            const char *p;
            int exp10;
            snprintf(tmp, sizeof tmp, "%.*e", prec - 1, a);
            p = tmp;
            while (*p && *p != 'e' && *p != 'E') {
                if (isdigit((unsigned char)*p)) base[bn++] = *p;
                p++;
            }
            exp10 = atoi(p + 1);
            bdecpt = exp10 + 1;
            base[bn] = '\0';
            for (ci = -1; ci <= 1; ++ci) {
                char cd[24];
                int cn = bn, cdecpt = bdecpt, i;
                memcpy(cd, base, (size_t)bn + 1);
                if (ci < 0) {          /* decrement the digit string */
                    i = cn - 1;
                    while (i >= 0 && cd[i] == '0') { cd[i] = '9'; i--; }
                    if (i < 0) { /* 1000.. -> 999.. with decpt-1 */
                        for (i = 0; i < cn; ++i) cd[i] = '9';
                        cdecpt--;
                    } else cd[i]--;
                } else if (ci > 0) {   /* increment the digit string */
                    i = cn - 1;
                    while (i >= 0 && cd[i] == '9') { cd[i] = '0'; i--; }
                    if (i < 0) { /* 999.. -> 1 with decpt+1 */
                        cd[0] = '1'; cn = 1; cd[1] = '\0';
                        cdecpt++;
                    } else cd[i]++;
                }
                {
                    /* test string d.ddddde±XX for this candidate */
                    char test[64];
                    int w = 0;
                    long double dist;
                    test[w++] = cd[0];
                    if (cn > 1) {
                        test[w++] = '.';
                        for (i = 1; i < cn; ++i) test[w++] = cd[i];
                    }
                    w += snprintf(test + w, sizeof test - (size_t)w,
                                  "e%+d", cdecpt - 1);
                    (void)w;
                    if (strtod(test, NULL) != a) continue;
                    dist = strtold(test, NULL) - (long double)a;
                    if (dist < 0) dist = -dist;
                    if (best < 0 || dist < bestdist ||
                        (dist == bestdist &&
                         ((cd[cn - 1] - '0') % 2 == 0) &&
                         ((digits[ndig - 1] - '0') % 2 != 0))) {
                        best = ci;
                        bestdist = dist;
                        memcpy(digits, cd, (size_t)cn + 1);
                        ndig = cn; decpt = cdecpt;
                    }
                }
            }
            if (best >= 0) {
                /* strip trailing zeros (same value, shorter form) */
                while (ndig > 1 && digits[ndig - 1] == '0') digits[--ndig] = '\0';
                found = 1;
            }
        }
        if (!found) { /* unreachable for IEEE doubles; be safe */
            const char *p;
            int n = 0, exp10;
            snprintf(tmp, sizeof tmp, "%.17e", a);
            p = tmp;
            while (*p && *p != 'e' && *p != 'E') {
                if (isdigit((unsigned char)*p)) digits[n++] = *p;
                p++;
            }
            exp10 = atoi(p + 1);
            ndig = n; decpt = exp10 + 1;
            while (ndig > 1 && digits[ndig - 1] == '0') ndig--;
        }
        {
            sbuf b; sbuf_init(&b);
            if (neg) sbuf_putc(&b, '-');
            if (decpt <= -4 || decpt >= 17) {
                /* scientific: d[.ddd]e±XX, exponent at least 2 digits */
                int e = decpt - 1, i;
                sbuf_putc(&b, digits[0]);
                if (ndig > 1) {
                    sbuf_putc(&b, '.');
                    for (i = 1; i < ndig; ++i) sbuf_putc(&b, digits[i]);
                }
                sbuf_printf(&b, "e%+03d", e);
            } else if (decpt <= 0) {
                int i;
                sbuf_puts(&b, "0.");
                for (i = 0; i < -decpt; ++i) sbuf_putc(&b, '0');
                for (i = 0; i < ndig; ++i) sbuf_putc(&b, digits[i]);
            } else if (decpt >= ndig) {
                int i;
                for (i = 0; i < ndig; ++i) sbuf_putc(&b, digits[i]);
                for (i = ndig; i < decpt; ++i) sbuf_putc(&b, '0');
                sbuf_puts(&b, ".0");
            } else {
                int i;
                for (i = 0; i < decpt; ++i) sbuf_putc(&b, digits[i]);
                sbuf_putc(&b, '.');
                for (i = decpt; i < ndig; ++i) sbuf_putc(&b, digits[i]);
            }
            snprintf(buf, bufsz, "%s", b.buf ? b.buf : "");
            sbuf_free(&b);
        }
    }
}

/* ================= parser ================= */

typedef struct {
    const char *s;
    size_t len, pos;
    int depth;
    const char *err;
} parser;

static void p_ws(parser *p)
{
    while (p->pos < p->len) {
        char c = p->s[p->pos];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->pos++;
        else break;
    }
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void utf8_encode(sbuf *b, uint32_t cp)
{
    if (cp < 0x80) {
        sbuf_putc(b, (char)cp);
    } else if (cp < 0x800) {
        sbuf_putc(b, (char)(0xc0 | (cp >> 6)));
        sbuf_putc(b, (char)(0x80 | (cp & 0x3f)));
    } else if (cp < 0x10000) {
        sbuf_putc(b, (char)(0xe0 | (cp >> 12)));
        sbuf_putc(b, (char)(0x80 | ((cp >> 6) & 0x3f)));
        sbuf_putc(b, (char)(0x80 | (cp & 0x3f)));
    } else {
        sbuf_putc(b, (char)(0xf0 | (cp >> 18)));
        sbuf_putc(b, (char)(0x80 | ((cp >> 12) & 0x3f)));
        sbuf_putc(b, (char)(0x80 | ((cp >> 6) & 0x3f)));
        sbuf_putc(b, (char)(0x80 | (cp & 0x3f)));
    }
}

/* Parse a string starting at the opening quote. On success the
 * decoded bytes are appended to out (raw UTF-8). */
static int p_string_raw(parser *p, sbuf *out)
{
    /* p->s[p->pos] == '"' */
    p->pos++;
    while (p->pos < p->len) {
        unsigned char c = (unsigned char)p->s[p->pos];
        if (c == '"') { p->pos++; return 1; }
        if (c == '\\') {
            char e;
            p->pos++;
            if (p->pos >= p->len) break;
            e = p->s[p->pos++];
            switch (e) {
            case '"': sbuf_putc(out, '"'); break;
            case '\\': sbuf_putc(out, '\\'); break;
            case '/': sbuf_putc(out, '/'); break;
            case 'b': sbuf_putc(out, '\b'); break;
            case 'f': sbuf_putc(out, '\f'); break;
            case 'n': sbuf_putc(out, '\n'); break;
            case 'r': sbuf_putc(out, '\r'); break;
            case 't': sbuf_putc(out, '\t'); break;
            case 'u': {
                uint32_t cp = 0;
                int i;
                if (p->pos + 4 > p->len) { p->err = "bad \\u escape"; return 0; }
                for (i = 0; i < 4; ++i) {
                    int hv = hexval(p->s[p->pos++]);
                    if (hv < 0) { p->err = "bad \\u escape"; return 0; }
                    cp = (cp << 4) | (uint32_t)hv;
                }
                if (cp >= 0xd800 && cp <= 0xdbff) {
                    /* possible surrogate pair */
                    if (p->pos + 6 <= p->len && p->s[p->pos] == '\\' &&
                        p->s[p->pos + 1] == 'u') {
                        uint32_t lo = 0; int ok = 1;
                        for (i = 0; i < 4; ++i) {
                            int hv = hexval(p->s[p->pos + 2 + i]);
                            if (hv < 0) { ok = 0; break; }
                            lo = (lo << 4) | (uint32_t)hv;
                        }
                        if (ok && lo >= 0xdc00 && lo <= 0xdfff) {
                            p->pos += 6;
                            cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00);
                        }
                        /* else: lone surrogate — encode the code point
                         * itself (WTF-8). Python would carry a lone
                         * surrogate str and fail at .encode("utf-8")
                         * during hashing: a documented divergence. */
                    }
                }
                utf8_encode(out, cp);
                break;
            }
            default: p->err = "bad escape"; return 0;
            }
            continue;
        }
        if (c < 0x20) { p->err = "raw control char in string"; return 0; }
        sbuf_putc(out, (char)c);
        p->pos++;
    }
    p->err = "unterminated string";
    return 0;
}

static jval *p_value(parser *p);

static jval *p_number(parser *p)
{
    size_t start = p->pos;
    int is_float = 0;
    if (p->pos < p->len && p->s[p->pos] == '-') p->pos++;
    if (p->pos >= p->len || !isdigit((unsigned char)p->s[p->pos])) {
        p->err = "bad number"; return NULL;
    }
    if (p->s[p->pos] == '0') p->pos++;
    else while (p->pos < p->len && isdigit((unsigned char)p->s[p->pos])) p->pos++;
    if (p->pos < p->len && p->s[p->pos] == '.') {
        is_float = 1; p->pos++;
        if (p->pos >= p->len || !isdigit((unsigned char)p->s[p->pos])) {
            p->err = "bad number"; return NULL;
        }
        while (p->pos < p->len && isdigit((unsigned char)p->s[p->pos])) p->pos++;
    }
    if (p->pos < p->len && (p->s[p->pos] == 'e' || p->s[p->pos] == 'E')) {
        is_float = 1; p->pos++;
        if (p->pos < p->len && (p->s[p->pos] == '+' || p->s[p->pos] == '-')) p->pos++;
        if (p->pos >= p->len || !isdigit((unsigned char)p->s[p->pos])) {
            p->err = "bad number"; return NULL;
        }
        while (p->pos < p->len && isdigit((unsigned char)p->s[p->pos])) p->pos++;
    }
    {
        size_t n = p->pos - start;
        char *tok = xstrndup(p->s + start, n);
        jval *r = NULL;
        if (!tok) { p->err = "oom"; return NULL; }
        if (is_float) {
            r = jv_new_dbl(strtod(tok, NULL));
            free(tok);
        } else {
            /* Integer: exact int64, then uint64, else verbatim text
             * (Python arbitrary precision dumps the same digits). */
            const char *q = tok;
            int neg = 0;
            if (*q == '-') { neg = 1; q++; }
            {
                size_t nd = strlen(q);
                if (nd <= 19) {
                    uint64_t u = strtoull(q, NULL, 10);
                    if (!neg && u <= (uint64_t)INT64_MAX) r = jv_new_int((int64_t)u);
                    else if (!neg) r = jv_new_uint(u);
                    else if (u <= (uint64_t)INT64_MAX + 1u)
                        r = jv_new_int(u == (uint64_t)INT64_MAX + 1u ? INT64_MIN : -(int64_t)u);
                } else if (nd == 20 && !neg) {
                    char *end = NULL;
                    uint64_t u;
                    errno = 0;
                    u = strtoull(q, &end, 10);
                    /* strtoull saturates at UINT64_MAX on overflow;
                     * only ERANGE distinguishes a real UINT64_MAX. */
                    if (end && *end == '\0' && errno != ERANGE)
                        r = jv_new_uint(u);
                }
            }
            if (!r) {
                /* verify it really is all digits before keeping text */
                const char *q2 = tok + (neg ? 1 : 0);
                int alldig = *q2 != '\0';
                while (*q2) { if (!isdigit((unsigned char)*q2)) { alldig = 0; break; } q2++; }
                if (alldig) {
                    r = jv_alloc(JV_BIGINT);
                    if (r) r->v.bigint = tok; else free(tok);
                    return r;
                }
                free(tok);
                p->err = "bad number";
                return NULL;
            }
            free(tok);
        }
        return r;
    }
}

static jval *p_value(parser *p)
{
    char c;
    if (++p->depth > 512) { p->err = "nesting too deep"; p->depth--; return NULL; }
    p_ws(p);
    if (p->pos >= p->len) { p->err = "unexpected end"; p->depth--; return NULL; }
    c = p->s[p->pos];
    if (c == '{') {
        jval *o;
        p->pos++;
        o = jv_new_obj();
        if (!o) { p->err = "oom"; p->depth--; return NULL; }
        p_ws(p);
        if (p->pos < p->len && p->s[p->pos] == '}') { p->pos++; p->depth--; return o; }
        for (;;) {
            sbuf kb; jval *val;
            p_ws(p);
            if (p->pos >= p->len || p->s[p->pos] != '"') {
                p->err = "expected object key"; jv_free(o); p->depth--; return NULL;
            }
            sbuf_init(&kb);
            if (!p_string_raw(p, &kb)) { sbuf_free(&kb); jv_free(o); p->depth--; return NULL; }
            p_ws(p);
            if (p->pos >= p->len || p->s[p->pos] != ':') {
                p->err = "expected :"; sbuf_free(&kb); jv_free(o); p->depth--; return NULL;
            }
            p->pos++;
            val = p_value(p);
            if (!val) { sbuf_free(&kb); jv_free(o); p->depth--; return NULL; }
            jv_obj_setn(o, kb.buf ? kb.buf : "", kb.len, val);
            sbuf_free(&kb);
            p_ws(p);
            if (p->pos < p->len && p->s[p->pos] == ',') { p->pos++; continue; }
            if (p->pos < p->len && p->s[p->pos] == '}') { p->pos++; break; }
            p->err = "expected , or }"; jv_free(o); p->depth--; return NULL;
        }
        p->depth--;
        return o;
    }
    if (c == '[') {
        jval *a;
        p->pos++;
        a = jv_new_arr();
        if (!a) { p->err = "oom"; p->depth--; return NULL; }
        p_ws(p);
        if (p->pos < p->len && p->s[p->pos] == ']') { p->pos++; p->depth--; return a; }
        for (;;) {
            jval *item = p_value(p);
            if (!item) { jv_free(a); p->depth--; return NULL; }
            jv_arr_push(a, item);
            p_ws(p);
            if (p->pos < p->len && p->s[p->pos] == ',') { p->pos++; continue; }
            if (p->pos < p->len && p->s[p->pos] == ']') { p->pos++; break; }
            p->err = "expected , or ]"; jv_free(a); p->depth--; return NULL;
        }
        p->depth--;
        return a;
    }
    if (c == '"') {
        sbuf kb; jval *r;
        sbuf_init(&kb);
        if (!p_string_raw(p, &kb)) { sbuf_free(&kb); p->depth--; return NULL; }
        r = jv_new_strn(kb.buf ? kb.buf : "", kb.len);
        sbuf_free(&kb);
        p->depth--;
        return r;
    }
    if (c == 't' && p->pos + 4 <= p->len && memcmp(p->s + p->pos, "true", 4) == 0) {
        p->pos += 4; p->depth--; return jv_new_bool(1);
    }
    if (c == 'f' && p->pos + 5 <= p->len && memcmp(p->s + p->pos, "false", 5) == 0) {
        p->pos += 5; p->depth--; return jv_new_bool(0);
    }
    if (c == 'n' && p->pos + 4 <= p->len && memcmp(p->s + p->pos, "null", 4) == 0) {
        p->pos += 4; p->depth--; return jv_new_null();
    }
    /* Python json.loads accepts these non-standard constants. */
    if (c == 'N' && p->pos + 3 <= p->len && memcmp(p->s + p->pos, "NaN", 3) == 0) {
        p->pos += 3; p->depth--; return jv_new_dbl(NAN);
    }
    if (c == 'I' && p->pos + 8 <= p->len && memcmp(p->s + p->pos, "Infinity", 8) == 0) {
        p->pos += 8; p->depth--; return jv_new_dbl(INFINITY);
    }
    if (c == '-' && p->pos + 9 <= p->len && memcmp(p->s + p->pos, "-Infinity", 9) == 0) {
        p->pos += 9; p->depth--; return jv_new_dbl(-INFINITY);
    }
    if (c == '-' || isdigit((unsigned char)c)) {
        jval *r = p_number(p);
        p->depth--;
        return r;
    }
    p->err = "unexpected character";
    p->depth--;
    return NULL;
}

jval *jv_parse(const char *text, size_t len, const char **err)
{
    parser p;
    jval *v;
    p.s = text; p.len = len; p.pos = 0; p.depth = 0; p.err = NULL;
    v = p_value(&p);
    if (!v) { if (err) *err = p.err ? p.err : "parse error"; return NULL; }
    p_ws(&p);
    if (p.pos != p.len) {
        jv_free(v);
        if (err) *err = "trailing data";
        return NULL;
    }
    return v;
}

/* ================= canonical writer ================= */

static void write_string(const char *s, size_t len, sbuf *b)
{
    size_t i;
    sbuf_putc(b, '"');
    for (i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': sbuf_puts(b, "\\\""); break;
        case '\\': sbuf_puts(b, "\\\\"); break;
        case '\b': sbuf_puts(b, "\\b"); break;
        case '\f': sbuf_puts(b, "\\f"); break;
        case '\n': sbuf_puts(b, "\\n"); break;
        case '\r': sbuf_puts(b, "\\r"); break;
        case '\t': sbuf_puts(b, "\\t"); break;
        default:
            if (c < 0x20) {
                char esc[8];
                snprintf(esc, sizeof esc, "\\u%04x", c);
                sbuf_puts(b, esc);
            } else {
                sbuf_putc(b, (char)c);   /* raw UTF-8 (v0.3 pin) */
            }
        }
    }
    sbuf_putc(b, '"');
}

/* Insertion sort of an index array by key (payload objects are
 * small; this avoids non-C99 qsort_r). */
static void sort_indices(const jval *o, size_t *idx)
{
    size_t i, j, n = o->v.obj.len;
    for (i = 0; i < n; ++i) idx[i] = i;
    for (i = 1; i < n; ++i) {
        size_t t = idx[i];
        j = i;
        while (j > 0 && keycmp(o->v.obj.keys[t], o->v.obj.klens[t],
                               o->v.obj.keys[idx[j - 1]],
                               o->v.obj.klens[idx[j - 1]]) < 0) {
            idx[j] = idx[j - 1];
            j--;
        }
        idx[j] = t;
    }
}

void jv_write_canonical(const jval *v, sbuf *b)
{
    size_t i;
    char numbuf[64];
    switch (v->type) {
    case JV_NULL: sbuf_puts(b, "null"); break;
    case JV_BOOL: sbuf_puts(b, v->v.boolean ? "true" : "false"); break;
    case JV_INT:
        snprintf(numbuf, sizeof numbuf, "%lld", (long long)v->v.i);
        sbuf_puts(b, numbuf);
        break;
    case JV_UINT:
        snprintf(numbuf, sizeof numbuf, "%llu", (unsigned long long)v->v.u);
        sbuf_puts(b, numbuf);
        break;
    case JV_BIGINT: sbuf_puts(b, v->v.bigint); break;
    case JV_DBL:
        py_float_repr(v->v.d, numbuf, sizeof numbuf);
        sbuf_puts(b, numbuf);
        break;
    case JV_STR: write_string(v->v.str.s, v->v.str.len, b); break;
    case JV_ARR:
        sbuf_putc(b, '[');
        for (i = 0; i < v->v.arr.len; ++i) {
            if (i) sbuf_putc(b, ',');
            jv_write_canonical(v->v.arr.items[i], b);
        }
        sbuf_putc(b, ']');
        break;
    case JV_OBJ: {
        size_t *idx = (size_t *)malloc((v->v.obj.len ? v->v.obj.len : 1) * sizeof(size_t));
        if (!idx) { b->oom = 1; break; }
        sort_indices(v, idx);
        sbuf_putc(b, '{');
        for (i = 0; i < v->v.obj.len; ++i) {
            size_t k = idx[i];
            if (i) sbuf_putc(b, ',');
            write_string(v->v.obj.keys[k], v->v.obj.klens[k], b);
            sbuf_putc(b, ':');
            jv_write_canonical(v->v.obj.vals[k], b);
        }
        sbuf_putc(b, '}');
        free(idx);
        break;
    }
    }
}

char *jv_canonical(const jval *v)
{
    sbuf b;
    sbuf_init(&b);
    jv_write_canonical(v, &b);
    return sbuf_steal(&b);
}

/* ================= Python str repr (for detail strings) ================= */

char *py_str_repr(const char *s, size_t len)
{
    /* Python repr: single quotes unless the string contains a single
     * quote and no double quote (then double quotes). Escapes \\, the
     * quote, \n \r \t, and non-printables as \xXX. Printable
     * non-ASCII is kept raw. Verdict details only carry identities
     * and ids; this covers the realistic cases byte-for-byte. */
    int has_sq = 0, has_dq = 0;
    size_t i;
    char q;
    sbuf b;
    for (i = 0; i < len; ++i) {
        if (s[i] == '\'') has_sq = 1;
        if (s[i] == '"') has_dq = 1;
    }
    q = (has_sq && !has_dq) ? '"' : '\'';
    sbuf_init(&b);
    sbuf_putc(&b, q);
    for (i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)s[i];
        if (c == (unsigned char)q || c == '\\') {
            sbuf_putc(&b, '\\'); sbuf_putc(&b, (char)c);
        } else if (c == '\n') sbuf_puts(&b, "\\n");
        else if (c == '\r') sbuf_puts(&b, "\\r");
        else if (c == '\t') sbuf_puts(&b, "\\t");
        else if (c < 0x20 || c == 0x7f) sbuf_printf(&b, "\\x%02x", c);
        else sbuf_putc(&b, (char)c);
    }
    sbuf_putc(&b, q);
    return sbuf_steal(&b);
}

/* ================= equality / coercion ================= */

static int to_ld(const jval *v, long double *out)
{
    switch (v->type) {
    case JV_BOOL: *out = v->v.boolean ? 1.0L : 0.0L; return 1;
    case JV_INT: *out = (long double)v->v.i; return 1;
    case JV_UINT: *out = (long double)v->v.u; return 1;
    case JV_DBL: *out = (long double)v->v.d; return 1;
    case JV_BIGINT: *out = strtold(v->v.bigint, NULL); return 1;
    default: return 0;
    }
}

static int is_numeric(const jval *v)
{
    return v->type == JV_BOOL || v->type == JV_INT || v->type == JV_UINT ||
           v->type == JV_DBL || v->type == JV_BIGINT;
}

int jv_equal(const jval *a, const jval *b)
{
    size_t i;
    if (is_numeric(a) && is_numeric(b)) {
        long double x, y;
        /* Python: True == 1 == 1.0; bool vs bool also lands here.
         * NaN compares unequal to everything, including itself. */
        to_ld(a, &x); to_ld(b, &y);
        if (isnan(x) || isnan(y)) return 0;
        return x == y;
    }
    if (a->type != b->type) return 0;
    switch (a->type) {
    case JV_NULL: return 1;
    case JV_STR:
        return a->v.str.len == b->v.str.len &&
               (a->v.str.len == 0 || memcmp(a->v.str.s, b->v.str.s, a->v.str.len) == 0);
    case JV_ARR:
        if (a->v.arr.len != b->v.arr.len) return 0;
        for (i = 0; i < a->v.arr.len; ++i)
            if (!jv_equal(a->v.arr.items[i], b->v.arr.items[i])) return 0;
        return 1;
    case JV_OBJ:
        if (a->v.obj.len != b->v.obj.len) return 0;
        for (i = 0; i < a->v.obj.len; ++i) {
            jval *bv = jv_obj_getn(b, a->v.obj.keys[i], a->v.obj.klens[i]);
            if (!bv) return 0;
            if (!jv_equal(a->v.obj.vals[i], bv)) return 0;
        }
        return 1;
    default: return 0;
    }
}

double jv_as_double(const jval *v, int *ok)
{
    *ok = 1;
    switch (v->type) {
    case JV_BOOL: return v->v.boolean ? 1.0 : 0.0;
    case JV_INT: return (double)v->v.i;
    case JV_UINT: return (double)v->v.u;
    case JV_DBL: return v->v.d;
    case JV_BIGINT: return strtod(v->v.bigint, NULL);
    case JV_STR: {
        /* Python float(str): whole-string parse, whitespace tolerated */
        char *end = NULL;
        double d;
        if (v->v.str.len == 0) { *ok = 0; return 0.0; }
        d = strtod(v->v.str.s, &end);
        if (end == v->v.str.s) { *ok = 0; return 0.0; }
        while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r') end++;
        if (*end != '\0') { *ok = 0; return 0.0; }
        return d;
    }
    default:
        *ok = 0;
        return 0.0;
    }
}

int jv_contains_nonfinite(const jval *v)
{
    size_t i;
    switch (v->type) {
    case JV_DBL: return !isfinite(v->v.d);
    case JV_ARR:
        for (i = 0; i < v->v.arr.len; ++i)
            if (jv_contains_nonfinite(v->v.arr.items[i])) return 1;
        return 0;
    case JV_OBJ:
        for (i = 0; i < v->v.obj.len; ++i)
            if (jv_contains_nonfinite(v->v.obj.vals[i])) return 1;
        return 0;
    default: return 0;
    }
}
