{
  lib,
  buildGoModule,
  go_1_26,
}:

(buildGoModule.override { go = go_1_26; }) {
  pname = "second-brain-tui";
  version = "0.1.0";
  src = ../tui;

  # Dependencies are committed under tui/vendor, so Nix never fetches Go modules
  # during the build and no opaque vendor hash must be regenerated.
  vendorHash = null;

  ldflags = [ "-s" "-w" ];

  postInstall = ''
    mv "$out/bin/tui" "$out/bin/second-brain-tui"
  '';

  meta = {
    description = "Terminal client for Second Brain";
    homepage = "https://github.com/Mickael-Roger/second-brain";
    mainProgram = "second-brain-tui";
    platforms = lib.platforms.unix;
  };
}
