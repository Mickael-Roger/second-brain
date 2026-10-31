{ pkgs ? import <nixpkgs> { } }:

let
  frontend = pkgs.callPackage ./nix/frontend.nix { };
in {
  frontend = frontend;
  server = pkgs.callPackage ./nix/server.nix { inherit frontend; };
  tui = pkgs.callPackage ./nix/tui.nix { };
}
