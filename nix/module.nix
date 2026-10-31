{ config, lib, pkgs, ... }:

let
  inherit (lib) mkEnableOption mkIf mkOption types;
  frontend = pkgs.callPackage ./frontend.nix { };
  defaultPackage = pkgs.callPackage ./server.nix { inherit frontend; };
  cfg = config.services.second-brain;
in
{
  options.services.second-brain = {
    enable = mkEnableOption "Second Brain server";

    package = mkOption {
      type = types.package;
      default = defaultPackage;
      description = "Second Brain server package to execute.";
    };

    configFile = mkOption {
      type = types.str;
      example = "/run/secrets/second-brain/config.yml";
      description = ''
        Absolute runtime path to config.yml. This is a string rather than a Nix
        path so credentials in the file are not copied into the Nix store.
      '';
    };

    dataDir = mkOption {
      type = types.str;
      default = "/var/lib/second-brain";
      description = "Directory for SQLite data, transcripts, OAuth tokens, and cached articles.";
    };

    vaultPath = mkOption {
      type = types.str;
      example = "/var/lib/obsidian-vault";
      description = "Existing writable Obsidian vault path; it is not created by this module.";
    };

    user = mkOption {
      type = types.str;
      default = "second-brain";
      description = "System user used by the service.";
    };

    group = mkOption {
      type = types.str;
      default = "second-brain";
      description = "System group used by the service.";
    };

    openFirewall = mkOption {
      type = types.bool;
      default = false;
      description = "Open the configured TCP port in the NixOS firewall.";
    };

    port = mkOption {
      type = types.port;
      default = 8000;
      description = "Port to open when openFirewall is enabled; keep it in sync with app.port in configFile.";
    };

    extraEnvironment = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Additional environment variables for the server process.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.configFile != "";
        message = "services.second-brain.configFile must name a runtime config.yml file.";
      }
      {
        assertion = cfg.vaultPath != "";
        message = "services.second-brain.vaultPath must name an existing writable vault.";
      }
    ];

    users.groups.${cfg.group} = { };
    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.dataDir;
      createHome = true;
    };

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0750 ${cfg.user} ${cfg.group} -"
    ];

    systemd.services.second-brain = {
      description = "Second Brain server";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = cfg.extraEnvironment // {
        CONFIG_PATH = cfg.configFile;
      };
      path = [ pkgs.git pkgs.openssh pkgs.ripgrep ];
      serviceConfig = {
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.dataDir;
        Restart = "on-failure";
        RestartSec = "5s";
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ cfg.dataDir cfg.vaultPath ];
      };
      script = ''
        exec ${cfg.package}/bin/second-brain serve
      '';
    };

    networking.firewall.allowedTCPPorts = mkIf cfg.openFirewall [ cfg.port ];
  };
}
