// GHC SanitizerCoverage plugin: registers a new-PM module pass "ghc-sancov"
// that runs ModuleSanitizerCoveragePass with trace-pc-guard edge coverage.
// Loaded via: opt -load-pass-plugin=GhcSancov.so -passes=ghc-sancov
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Transforms/Instrumentation.h"
#include "llvm/Transforms/Instrumentation/SanitizerCoverage.h"
using namespace llvm;

extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
  return {LLVM_PLUGIN_API_VERSION, "GhcSancov", "0.1",
    [](PassBuilder &PB) {
      PB.registerPipelineParsingCallback(
        [](StringRef Name, ModulePassManager &MPM,
           ArrayRef<PassBuilder::PipelineElement>) {
          if (Name == "ghc-sancov") {
            SanitizerCoverageOptions Opts;
            Opts.CoverageType = SanitizerCoverageOptions::SCK_Edge;
            Opts.TracePCGuard = true;
            Opts.PCTable = false;
            Opts.NoPrune = false;
            MPM.addPass(ModuleSanitizerCoveragePass(Opts));
            return true;
          }
          return false;
        });
    }};
}
