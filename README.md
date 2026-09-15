# Klipper auto image
This repository implements a camera client that regularly captures images for a Klipper+Moonraker based 3d printer. Images are captured by sending requests to a snapshot endpoint of [crowsnest](https://docs.mainsail.xyz/crowsnest/).
The client captures images when the printer is printing, monitored using the [Moonraker](https://moonraker.readthedocs.io/en/latest/) API.
Images are stored at the client machine (the machine running this software).

To install, clone repo and run `make install` (in the repo root dir). This will configure the client to run as a daemon using systemd.

To configure, edit `~/printer_data/config/klipper-auto-image.conf` and reboot the machine (or restart the service using systemctl).

**Example configuration with crowsnest running on the same machine as klipper-auto-image**
```bash
# ~/printer_data/config/crowsnest.conf
[cam somecamname]
mode: ustreamer
port: 8001
device: /path/to/device
resolution: 1920x1080
max_fps: 10
```

```bash
# ~/printer_data/config/crowsnest.conf
[[cam]]
name: somecoolname
uri = "http://127.0.0.1:8001/snapshot"
```

The `[cam mycam]` sections in the crowsnest configuration file located at `~/printer_data/config/crowsnest.conf` should contain a `port` field. If `port: 8001`, then
