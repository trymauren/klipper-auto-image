# Klipper auto image
This repository implements a camera client that regularly captures images for a Klipper+Moonraker based 3d printer.
The client subscribes to a Moonraker websocket to obtain Klipper/Klippy states. The client captures images when the printer is printing.
Images are stored at the client machine (the machine running this software).

To install, clone repo and run `make install` (in the repo root dir). This will configure the client to run as a daemon using systemd.

To configure, edit `~/printer_data/config/klipper-auto-image.conf` and reboot the machine (or restart the service using systemctl).

**Note**: the client will not work if the camera is used by another process such as [spyglass](https://github.com/mainsail-crew/spyglass).
**Note**: the software requires picamera2, which cannot be installed using pip. Therefore, apt is used to install python3-picamera2. See [github issue](https://github.com/raspberrypi/picamera2/issues/294).
