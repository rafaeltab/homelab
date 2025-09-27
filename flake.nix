{
  description = "Project dev shell with CLIs";

  outputs = { self, nixpkgs }: let
    systems = [ "x86_64-linux" "aarch64-linux" ];
    forAllSystems = f:
      nixpkgs.lib.genAttrs systems (system:
        f (import nixpkgs { inherit system; }));
  in {
    devShells = forAllSystems (pkgs:
      {
        default = pkgs.mkShell {
          packages = with pkgs; [
            git
            jq
            curl
            wget
            ripgrep
            fd
            just
            zsh
            gum
            flux
          ];
          env = {
              SHELL="${pkgs.zsh}/bin/zsh";
          };
          shellHook = ''
            if [ -z "$IN_ZSH_DEV_SHELL" ] && [ -t 1 ]; then
              export IN_ZSH_DEV_SHELL=1
              exec ${pkgs.zsh}/bin/zsh
            fi
          '';
        };
      });
  };
}
