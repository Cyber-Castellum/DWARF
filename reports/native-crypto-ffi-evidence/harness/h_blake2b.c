#include <sodium.h>
#include <stdint.h>
#include <stddef.h>
static int inited=0;
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(!inited){ sodium_init(); inited=1; }
  unsigned char out[crypto_generichash_blake2b_BYTES_MAX];
  size_t outlen = crypto_generichash_blake2b_BYTES_MIN + (n? d[0] % (crypto_generichash_blake2b_BYTES_MAX-crypto_generichash_blake2b_BYTES_MIN) : 0);
  size_t klen = (n>1)? d[1] % (crypto_generichash_blake2b_KEYBYTES_MAX+1) : 0;
  const unsigned char *key = (klen && n>2)? d+2 : NULL;
  if (key && (size_t)(2+klen) > n) klen = (n>2)? n-2 : 0;
  crypto_generichash_blake2b(out, outlen, d, n, key, key?klen:0);
  return 0;
}
