#include <sodium.h>
#include <stdint.h>
#include <stddef.h>
static int inited=0;
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(!inited){ sodium_init(); inited=1; }
  if(n < 96) return 0;                 /* 64 sig + 32 pk + msg */
  crypto_sign_ed25519_verify_detached(d, d+96, n-96, d+64);
  return 0;
}
