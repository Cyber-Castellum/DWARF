#include <secp256k1.h>
#include <secp256k1_schnorrsig.h>
#include <secp256k1_extrakeys.h>
#include <stdint.h>
#include <stddef.h>
static secp256k1_context *ctx=NULL;
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(!ctx) ctx=secp256k1_context_create(SECP256K1_CONTEXT_VERIFY);
  if(n < 32+64) return 0;
  secp256k1_xonly_pubkey xpk;
  if (secp256k1_xonly_pubkey_parse(ctx, &xpk, d))                  /* parse attacker x-only pk */
    secp256k1_schnorrsig_verify(ctx, d+32, d+96, n-96, &xpk);      /* sig64=d+32, msg=d+96 */
  return 0;
}
