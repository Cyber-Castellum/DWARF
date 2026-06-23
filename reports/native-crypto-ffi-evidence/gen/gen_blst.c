#include "blst.h"
#include <stdio.h>
#include <string.h>
static void rnd(unsigned char*b,size_t n){FILE*f=fopen("/dev/urandom","rb");size_t r=fread(b,1,n,f);(void)r;fclose(f);}
static void wr(const char*dir,int i,const unsigned char*b,size_t n){char p[256];snprintf(p,sizeof p,"%s/seed%03d",dir,i);FILE*f=fopen(p,"wb");fwrite(b,1,n,f);fclose(f);}
int main(void){
  for(int i=0;i<80;i++){
    unsigned char ikm[32]; rnd(ikm,32); blst_scalar sk; blst_keygen(&sk,ikm,32,NULL,0);
    blst_p1 g1; blst_p1_affine g1a; unsigned char c1[48];
    blst_sk_to_pk_in_g1(&g1,&sk); blst_p1_to_affine(&g1a,&g1); blst_p1_affine_compress(c1,&g1a); wr("corpus/blst_g1",i,c1,48);
    blst_p2 g2; blst_p2_affine g2a; unsigned char c2[96];
    blst_sk_to_pk_in_g2(&g2,&sk); blst_p2_to_affine(&g2a,&g2); blst_p2_affine_compress(c2,&g2a); wr("corpus/blst_g2",i,c2,96);
  }
  printf("blst seeds done\n"); return 0;
}
