#include <sodium.h>
#include <stdint.h>
#include <stddef.h>
static int inited=0;
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(!inited){ sodium_init(); inited=1; }
  size_t pkb=crypto_vrf_ietfdraft03_publickeybytes();  /* 32 */
  size_t prb=crypto_vrf_ietfdraft03_proofbytes();      /* 80 */
  if(n < pkb+prb) return 0;
  unsigned char out[128];
  crypto_vrf_ietfdraft03_verify(out, d, d+pkb, d+pkb+prb, n-pkb-prb);
  return 0;
}
