#include "blst.h"
#include <stdint.h>
#include <stddef.h>
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n){
  if(n>=96){ blst_p2_affine a; if(blst_p2_uncompress(&a,d)==BLST_SUCCESS){ blst_p2_affine_on_curve(&a); blst_p2_affine_in_g2(&a);} }
  if(n>=192){ blst_p2_affine b; if(blst_p2_deserialize(&b,d)==BLST_SUCCESS){ blst_p2_affine_in_g2(&b);} }
  return 0;
}
