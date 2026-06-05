# Build Dwarf fuzz-target shims.
#
#   make amaru          - build retained Amaru M2 decoder shims via cargo
#   make cardano-node   - build retained cardano-node M2 decoder shims via cabal
#   make all            - build everything available
#   make clean          - remove build artifacts
#   make update         - after rebuilding, bump upstream_commit in manifests
#
# Output paths are the `binary:` fields in dwarf/targets/manifests/*.yaml.

.PHONY: all amaru cardano-node clean update

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(ROOT)/../..)

all: amaru cardano-node

amaru:
	cd $(ROOT)/amaru && cargo build --release

cardano-node:
	cd $(ROOT)/cardano-node && cabal update && cabal build all
	@echo ""
	@echo "Binaries built. Copy or symlink into expected paths:"
	@for shim in tx-body block-header certificate auxiliary-data block; do \
	  bin=$$(cd $(ROOT)/cardano-node && cabal list-bin cardano-node-cbor-decode-$$shim 2>/dev/null); \
	  echo "  $$bin"; \
	done
	@for shim in keep-alive handshake chainsync blockfetch txsubmission peersharing localstatequery localtxmonitor localtxsubmission; do \
	  bin=$$(cd $(ROOT)/cardano-node && cabal list-bin cardano-node-mini-protocol-decode-$$shim 2>/dev/null); \
	  echo "  $$bin"; \
	done

# Stage the cabal-built binaries into a flat directory so manifests can
# reference a stable path instead of dist-newstyle/... with its GHC-version
# and architecture subdirs.
cardano-node-install: cardano-node
	mkdir -p $(ROOT)/cardano-node/bin
	for shim in tx-body block-header certificate auxiliary-data block; do \
	  bin=$$(cd $(ROOT)/cardano-node && cabal list-bin cardano-node-cbor-decode-$$shim); \
	  cp "$$bin" "$(ROOT)/cardano-node/bin/cardano-node-cbor-decode-$$shim"; \
	done
	for shim in keep-alive handshake chainsync blockfetch txsubmission peersharing localstatequery localtxmonitor localtxsubmission; do \
	  bin=$$(cd $(ROOT)/cardano-node && cabal list-bin cardano-node-mini-protocol-decode-$$shim); \
	  cp "$$bin" "$(ROOT)/cardano-node/bin/cardano-node-mini-protocol-decode-$$shim"; \
	done

clean:
	cd $(ROOT)/amaru && cargo clean || true
	rm -rf $(ROOT)/cardano-node/dist-newstyle $(ROOT)/cardano-node/bin || true

# Refresh upstream_commit + binary path + status across every manifest after
# a successful rebuild. Safe to run repeatedly.
update:
	@amaru_sha=$$(git -C $(REPO_ROOT)/codebases/amaru rev-parse --short HEAD); \
	cardano_sha=$$(git -C $(REPO_ROOT)/codebases/cardano-node rev-parse --short HEAD); \
	echo "Amaru upstream_commit:        $$amaru_sha"; \
	echo "cardano-node upstream_commit: $$cardano_sha"; \
	for m in $(ROOT)/manifests/amaru-cbor-decode-*.yaml; do \
	  python3 -c "import json, sys; p=sys.argv[1]; d=json.load(open(p)); d['upstream_commit']=sys.argv[2]; d.pop('status', None); open(p,'w').write(json.dumps(d, indent=2) + '\n')" "$$m" "$$amaru_sha"; \
	done; \
	for m in $(ROOT)/manifests/amaru-mini-protocol-decode-*.yaml; do \
	  python3 -c "import json, sys; p=sys.argv[1]; d=json.load(open(p)); d['upstream_commit']=sys.argv[2]; d.pop('status', None); open(p,'w').write(json.dumps(d, indent=2) + '\n')" "$$m" "$$amaru_sha"; \
	done; \
	for m in $(ROOT)/manifests/cardano-node-cbor-decode-*.yaml; do \
	  shim=$$(basename "$$m" .yaml); \
	  bin="dwarf/targets/cardano-node/bin/$$shim"; \
	  python3 -c "import json, sys; p=sys.argv[1]; d=json.load(open(p)); d['upstream_commit']=sys.argv[2]; d['binary']=sys.argv[3]; d.pop('status', None); open(p,'w').write(json.dumps(d, indent=2) + '\n')" "$$m" "$$cardano_sha" "$$bin"; \
	done; \
	for m in $(ROOT)/manifests/cardano-node-mini-protocol-decode-*.yaml; do \
	  shim=$$(basename "$$m" .yaml); \
	  bin="dwarf/targets/cardano-node/bin/$$shim"; \
	  python3 -c "import json, sys; p=sys.argv[1]; d=json.load(open(p)); d['upstream_commit']=sys.argv[2]; d['binary']=sys.argv[3]; d.pop('status', None); open(p,'w').write(json.dumps(d, indent=2) + '\n')" "$$m" "$$cardano_sha" "$$bin"; \
	done
