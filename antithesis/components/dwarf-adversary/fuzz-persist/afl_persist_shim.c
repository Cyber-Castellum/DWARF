/* Clean accessors over the AFL++ (afl.rs 4.40c) persistent-mode + shared-memory
 * test-case runtime symbols, so the Haskell harness can FFI-import simple
 * functions instead of poking C globals. These externs are provided at link
 * time by afl-compiler-rt.o (added via the SanCov linkwrap). */
#include <stddef.h>

extern int __afl_sharedmem_fuzzing;
extern unsigned char *__afl_fuzz_ptr;
extern unsigned int *__afl_fuzz_len;
void __afl_manual_init(void);
int  __afl_persistent_loop(unsigned int);

/* Must be set BEFORE dwarf_afl_init so the forkserver delivers test cases via
 * shared memory (no per-exec file read). */
void dwarf_afl_enable_shmem(void) { __afl_sharedmem_fuzzing = 1; }

/* Deferred forkserver: fork happens here, after one-time harness setup. */
void dwarf_afl_init(void) { __afl_manual_init(); }

/* __AFL_LOOP(n): nonzero => run another iteration; 0 => child should exit. */
int dwarf_afl_loop(unsigned int n) { return __afl_persistent_loop(n); }

unsigned char *dwarf_afl_buf(void) { return __afl_fuzz_ptr; }
unsigned int dwarf_afl_len(void) { return __afl_fuzz_len ? *__afl_fuzz_len : 0; }
