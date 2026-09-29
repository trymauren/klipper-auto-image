# Klipper auto image
This repository implements a camera client that regularly captures images for a Klipper+Moonraker based 3d printer. Images are captured by sending requests to a snapshot endpoint of [crowsnest](https://docs.mainsail.xyz/crowsnest/).
The client captures images when the printer is printing, monitored using the [Moonraker](https://moonraker.readthedocs.io/en/latest/) API.
Images are stored at the client machine (the machine running this software).

To install, clone repo and run `make install` (in the repo root dir). This will configure the client to run as a daemon using systemd.

To configure, edit `~/printer_data/config/klipper-auto-image.conf`.

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
# ~/printer_data/config/klipper-auto-image.conf
[[cam]]
name: somecoolname
uri = "http://127.0.0.1:8001/snapshot"
```

Note how the `port` field (8001) in the crowsnest configuration corresponds to the port in the configured klipper-auto-image URI.

After changing configuration, the machine must be rebooted or the service must be restarted:

```bash
# to reboot
sudo reboot

# to restart service
sudo systemctl restart klipper-auto-image
```

To check whether the service is running and grab the latest logged messages:

```bash
sudo systemctl status klipper-auto-image

# or

sudo journalctl --since "10 minutes ago"
```

While developing and making changes to code, the following commands can be used to reinstall the software to use the updated code:
```bash
sudo systemctl disable --now klipper-auto-image
sudo systemctl enable --now klipper-auto-image
```


