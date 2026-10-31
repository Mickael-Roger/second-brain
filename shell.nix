{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = [ pkgs.go_1_26 pkgs.nodejs_22 pkgs.python312 pkgs.uv ];
}
