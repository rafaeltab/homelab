if ! command -v nix >/dev/null 2>&1; then
    sh <(curl -L https://nixos.org/nix/install)
    . $HOME/.nix-profile/etc/profile.d/nix.sh

    mkdir -p ~/.config/nix/
    touch ~/.config/nix/nix.conf
    echo "experimental-features = nix-command flakes" >> ~/.config/nix/nix.conf
fi


