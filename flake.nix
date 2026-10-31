{
  description = "Second Brain server and Go terminal client";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      mkPackages = system:
        let
          pkgs = import nixpkgs { inherit system; };
          frontend = pkgs.callPackage ./nix/frontend.nix { };
          server = pkgs.callPackage ./nix/server.nix { inherit frontend; };
          tui = pkgs.callPackage ./nix/tui.nix { };
        in {
          inherit frontend server tui;
          default = server;
        };
    in {
      packages = forAllSystems mkPackages;

      apps = forAllSystems (system:
        let packages = mkPackages system;
        in {
          default = { type = "app"; program = "${packages.server}/bin/second-brain"; };
          server = { type = "app"; program = "${packages.server}/bin/second-brain"; };
          tui = { type = "app"; program = "${packages.tui}/bin/second-brain-tui"; };
        });

      overlays.default = final: prev: {
        second-brain-frontend = final.callPackage ./nix/frontend.nix { };
        second-brain = final.callPackage ./nix/server.nix {
          frontend = final.second-brain-frontend;
        };
        second-brain-tui = final.callPackage ./nix/tui.nix { };
      };

      nixosModules = {
        default = import ./nix/module.nix;
        second-brain = import ./nix/module.nix;
      };

      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [ pkgs.go_1_26 pkgs.nodejs_22 pkgs.python312 pkgs.uv ];
          };
        });
    };
}
