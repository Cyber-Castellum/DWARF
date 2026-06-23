#include "blst.h"
#include <stdint.h>
#include <stddef.h>
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(n>=48){ blst_p1_affine a; if(blst_p1_uncompress(&a,d)==BLST_SUCCESS){ blst_p1_affine_on_curve(&a); blst_p1_affine_in_g1(&a);} }
  if(n>=96){ blst_p1_affine b; if(blst_p1_deserialize(&b,d)==BLST_SUCCESS){ blst_p1_affine_in_g1(&b);} }
  return 0;
}
