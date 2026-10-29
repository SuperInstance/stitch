/* sha256.h — SHA-256 (FIPS 180-4), written in-tree for stitch-c.
 * No dependencies beyond libc. Public-domain-style implementation
 * written from the FIPS 180-4 specification. */
#ifndef STITCHC_SHA256_H
#define STITCHC_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t bitlen;
    uint8_t  data[64];
    size_t   datalen;
} sha256_ctx;

void sha256_init(sha256_ctx *ctx);
void sha256_update(sha256_ctx *ctx, const uint8_t *data, size_t len);
void sha256_final(sha256_ctx *ctx, uint8_t hash[32]);

/* Convenience: one-shot hash of a byte buffer. */
void sha256(const uint8_t *data, size_t len, uint8_t out[32]);
/* Convenience: lowercase hex digest of a byte buffer (out: 65 bytes incl NUL). */
void sha256_hex(const uint8_t *data, size_t len, char out[65]);

#endif
