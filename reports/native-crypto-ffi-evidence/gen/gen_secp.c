#include <secp256k1.h>
#include <secp256k1_schnorrsig.h>
#include <secp256k1_extrakeys.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static void rnd(unsigned char*b,size_t n){FILE*f=fopen("/dev/urandom","rb");size_t r=fread(b,1,n,f);(void)r;fclose(f);}
static void wr(const char*dir,int i,const unsigned char*b,size_t n){char p[256];snprintf(p,sizeof p,"%s/seed%03d",dir,i);FILE*f=fopen(p,"wb");fwrite(b,1,n,f);fclose(f);}
int main(void){
  secp256k1_context*ctx=secp256k1_context_create(SECP256K1_CONTEXT_SIGN|SECP256K1_CONTEXT_VERIFY);
  for(int i=0;i<80;i++){
    unsigned char sk[32]; rnd(sk,32); while(!secp256k1_ec_seckey_verify(ctx,sk)) rnd(sk,32);
    unsigned char msg[32]; rnd(msg,32);
    /* ECDSA: pk33||sig64||msg32 */
    secp256k1_pubkey pk; secp256k1_ec_pubkey_create(ctx,&pk,sk);
    unsigned char pk33[33]; size_t l=33; secp256k1_ec_pubkey_serialize(ctx,pk33,&l,&pk,SECP256K1_EC_COMPRESSED);
    secp256k1_ecdsa_signature sig; secp256k1_ecdsa_sign(ctx,&sig,msg,sk,NULL,NULL);
    unsigned char s64[64]; secp256k1_ecdsa_signature_serialize_compact(ctx,s64,&sig);
    unsigned char eb[129]; memcpy(eb,pk33,33); memcpy(eb+33,s64,64); memcpy(eb+97,msg,32); wr("corpus/secp_ecdsa",i,eb,129);
    /* Schnorr: xpk32||sig64||msg32 */
    secp256k1_keypair kp; secp256k1_keypair_create(ctx,&kp,sk);
    secp256k1_xonly_pubkey xpk; secp256k1_keypair_xonly_pub(ctx,&xpk,NULL,&kp);
    unsigned char xp[32]; secp256k1_xonly_pubkey_serialize(ctx,xp,&xpk);
    unsigned char ss[64]; secp256k1_schnorrsig_sign32(ctx,ss,msg,&kp,NULL);
    unsigned char sb[128]; memcpy(sb,xp,32); memcpy(sb+32,ss,64); memcpy(sb+96,msg,32); wr("corpus/secp_schnorr",i,sb,128);
  }
  printf("secp seeds done\n"); return 0;
}
