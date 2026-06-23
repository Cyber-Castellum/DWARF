DWARF — native-crypto FFI sanitizer fuzzing (build provenance)
Toolchain: clang 18 (Ubuntu) + libFuzzer + AddressSanitizer + SanitizerCoverage.
Sources (cardano c-deps on build host): IOG libsodium fork, secp256k1 (bitcoin-core fork).
Instrumented build flags:
  CFLAGS = -g -O1 -fno-omit-frame-pointer -fsanitize=address,fuzzer-no-link \
           -fsanitize-coverage=inline-8bit-counters,trace-cmp,trace-div,trace-gep
  libsodium: configure --disable-shared --disable-asm
  secp256k1: configure --disable-shared --enable-module-{schnorrsig,recovery,extrakeys,ecdh}
Harness link: clang <CFLAGS w/ -fsanitize=address,fuzzer> harness.c <instrumented lib.a>
Run: ASAN_OPTIONS=detect_leaks=0 ./fuzz_X corpus/X -max_total_time=3600  (leak-detect off = only real memory corruption trips)
Seeds: gen/gen_sodium.c + gen/gen_secp.c produce 80 valid sig/key/proof inputs per harness.

blst (BLS12-381, Plutus builtins): instrumented C + native field-arith asm
  clang <CFLAGS> -c src/server.c -o blst.o     (C instrumented under ASan/SanCov)
  clang -c build/assembly.S -o asm.o           (hand-written field arithmetic, not instrumented)
  ar rcs libblst-asan.a blst.o asm.o
  harnesses: h_blst_g1.c / h_blst_g2.c = p1/p2 uncompress+deserialize + on_curve + in_g1/in_g2 (Plutus BLS parse path)
