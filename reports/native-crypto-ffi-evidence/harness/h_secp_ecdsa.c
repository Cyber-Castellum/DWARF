#include <secp256k1.h>
#include <stdint.h>
#include <stddef.h>
static secp256k1_context *ctx=NULL;
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(!ctx) ctx=secp256k1_context_create(SECP256K1_CONTEXT_VERIFY);
  if(n < 33+64+32) return 0;
  secp256k1_pubkey pk; secp256k1_ecdsa_signature sig;
  int okpk  = secp256k1_ec_pubkey_parse(ctx, &pk, d, 33);          /* parse attacker pubkey */
  int oksig = secp256k1_ecdsa_signature_parse_compact(ctx, &sig, d+33); /* parse attacker sig */
  if (okpk && oksig) secp256k1_ecdsa_verify(ctx, &sig, d+33+64, &pk);   /* msg32 = d+97 */
  return 0;
}
