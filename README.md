# Klipper auto image
This repository implements a camera client that regularly captures images for a Klipper+Moonraker based 3d printer.
The client subscribes to updates to the `print_stats` *Printer Object* using the Moonraker websocket at `ws://host:port/websocket`.
By monitoring the `state` of the `print_stats` object, the client will start capturing images when `state == "printing"`.
Images are stored at the client machine (the machine running this software).

To install, clone repo and run `make install`. This will configure the client to run as a daemon using systemd.

To configure, edit `~/printer_data/config/klipper-auto-image.conf`.

**Note**: the client will not work if the camera is used by another process such as (spyglass)[https://github.com/mainsail-crew/spyglass].
