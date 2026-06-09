{ pkgs, project, version, ... }:
let
  # Bake the antithesis composer scripts under
  # /opt/antithesis/test/v1/. The composer discovers
  # parallel_driver_*, eventually_*, finally_* by walking this tree.
  antithesis-assets = pkgs.runCommand "antithesis-assets" { } ''
    mkdir -p $out/opt/antithesis/test/v1/
    cp -r ${../composer}/. $out/opt/antithesis/test/v1
    chmod 0755 $out/opt/antithesis/test/v1/*/parallel_driver_*.sh
    chmod 0755 $out/opt/antithesis/test/v1/*/finally_*.sh
  '';

  # Sleep-forever entrypoint: this container hosts the binary +
  # driver scripts but does no work itself. The Antithesis composer
  # `docker exec`s into it per tick to run a driver.
  sleep-forever = pkgs.writeShellScriptBin "sleep-forever" ''
    #!/bin/sh
    tail -f /dev/null
  '';

  # /usr/bin/env so shebangs in driver scripts resolve.
  usrBinEnv = pkgs.runCommand "usr-bin-env" { } ''
    mkdir -p $out/usr/bin
    ln -s ${pkgs.coreutils}/bin/env $out/usr/bin/env
  '';
in
pkgs.dockerTools.buildImage {
  name = "ghcr.io/cardano-foundation/cardano-node-antithesis/adversary";
  tag = version;
  config = { EntryPoint = [ "/bin/sleep-forever" ]; };
  copyToRoot = pkgs.buildEnv {
    name = "image-root";
    paths = [
      pkgs.coreutils
      pkgs.bash
      pkgs.jq
      usrBinEnv
      sleep-forever
      antithesis-assets
      project.packages.cardano-adversary.package.components.exes.cardano-adversary
    ];
  };
}
