## Klipper auto image installer
##
## Selfdocumenting Makefile
## Based on https://github.com/mainsail-crew/klipper-auto-image/blob/main/Makefile


.PHONY: help install uninstall


#### Install Paths
USER = $(shell whoami)
SYSTEMD = /etc/systemd/system
BIN_PATH = /usr/local/bin
PRINTER_DATA_PATH = /home/$(USER)/printer_data
CONF_PATH = $(PRINTER_DATA_PATH)/config

all:
	$(MAKE) help

install: ## Install klipper auto image as service
	@if [ "$$(id -u)" -eq 0 ]; then \
		echo "Please run without sudo/not as root"; \
		exit 1; \
	fi
	@mkdir -p $(CONF_PATH)
	@printf "\nCopying systemd service file ...\n"
	@sudo cp -f "${PWD}/resources/klipper-auto-image.service" $(SYSTEMD)
	@sudo sed -i "s/__USER__/$(USER)/g" $(SYSTEMD)/klipper-auto-image.service
	@printf "\nCopying Klipper auto image launch script ...\n"
	@sudo ln -sf "${PWD}/scripts/klipper-auto-image" $(BIN_PATH)
	@printf "\nCopying configuration file ...\n"
	@cp -f "${PWD}/resources/klipper-auto-image.conf" $(CONF_PATH)
	@printf "\nPopulate new service file ... \n"
	@sudo systemctl daemon-reload
	@sudo echo "klipper-auto-image" >> $(PRINTER_DATA_PATH)/moonraker.asvc
	@printf "\nEnable Klipper auto image service ... \n"
	@sudo systemctl enable klipper-auto-image
	@printf "\nTo be sure, everything is setup please reboot ...\n"

uninstall: ## Uninstall Klipper auto image
	@printf "\nDisable Klipper auto image service ... \n"
	@sudo systemctl disable klipper-auto-image
	@printf "\nRemove systemd service file ...\n"
	@sudo rm -f $(SYSTEMD)/klipper-auto-image.service
	@printf "\nRemoving Klipper auto image launch script ...\n"
	@sudo rm -f $(BIN_PATH)/klipper-auto-image
	@sudo sed '/klipper-auto-image/d' $(PRINTER_DATA_PATH)/moonraker.asvc > $(PRINTER_DATA_PATH)/moonraker.asvc

update: ## Update Klipper auto image (via git Repository)
	@git fetch && git pull

help: ## Show this help
	@printf "\nKlipper auto image Install Helper:\n"
	@grep -E -h '\s##\s' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
