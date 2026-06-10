#!/bin/bash

# Stackoverflow answer for getting absolute path of script
INSTALL_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Substitutes __INSTALL_DIR__ in the template service file and writes to a service file
sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    "$INSTALL_DIR/klipper-auto-image.service.template" \
    | sudo tee /etc/systemd/system/klipper-auto-image.service > /dev/null

sudo systemctl daemon-reload
# Enabling and starting the service
sudo systemctl enable --now klipper-auto-image.service
