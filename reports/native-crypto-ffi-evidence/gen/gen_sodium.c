#include <sodium.h>
#include <stdio.h>
#include <string.h>
static void wr(const char*dir,int i,const unsigned char*b,size_t n){char p[256];snprintf(p,sizeof p,"%s/seed%03d",dir,i);FILE*f=fopen(p,"wb");fwrite(b,1,n,f);fclose(f);}
int main(int c,char**v){
  sodium_init();
  for(int i=0;i<80;i++){
    /* ed25519: sig(64)||pk(32)||msg */
    unsigned char pk[32],sk[64],sig[64],m[48]; size_t ml=8+i%40;
    randombytes_buf(m,ml); crypto_sign_keypair(pk,sk); crypto_sign_detached(sig,NULL,m,ml,sk);
    unsigned char buf[200]; memcpy(buf,sig,64); memcpy(buf+64,pk,32); memcpy(buf+96,m,ml);
    wr("corpus/ed25519_verify",i,buf,96+ml);
    /* vrf03: pk(32)||proof(80)||msg */
    unsigned char vpk[32],vsk[64],proof[80]; 
    unsigned char vseed[32]; randombytes_buf(vseed,32); crypto_vrf_ietfdraft03_keypair_from_seed(vpk,vsk,vseed); crypto_vrf_ietfdraft03_prove(proof,vsk,m,ml);
    unsigned char vb[200]; memcpy(vb,vpk,32); memcpy(vb+32,proof,80); memcpy(vb+112,m,ml);
    wr("corpus/vrf03_verify",i,vb,112+ml);
    /* blake2b: any bytes */
    wr("corpus/blake2b",i,m,ml);
  }
  printf("sodium seeds done\n"); return 0;
}
