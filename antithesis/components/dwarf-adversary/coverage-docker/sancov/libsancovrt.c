/* Inert SanitizerCoverage runtime for BUILD-TIME use only: LD_PRELOADed so
 * GHC's TH dlopen of instrumented dependency .so files resolves the sancov
 * symbols. The final fuzz executable links the real afl-compiler-rt.o instead;
 * this lib never participates in fuzzing coverage. */
#include <stdint.h>
static volatile uint64_t hits;
void __sanitizer_cov_trace_pc_guard_init(uint32_t *a, uint32_t *b){ (void)a;(void)b; }
void __sanitizer_cov_trace_pc_guard(uint32_t *g){ (void)g; hits++; }
void __sanitizer_cov_pcs_init(const uintptr_t *a, const uintptr_t *b){ (void)a;(void)b; }
void __sanitizer_cov_trace_pc(void){}
