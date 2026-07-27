import argparse
import logging
import urllib.parse
from pathlib import Path

import tomllib

from klipper_auto_image import exceptions


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--resolution", default="1920x1080")
    p.add_argument("--ws_uri", type=str, default=None)
    # argparse.BooleanOptionalAction makes it possible to override an arg back to false, even though it is configured in a config file to true,
    # as opposed to store_true, which is one-directional
    # p.add_argument("--hflip", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument(
        "-c",
        "--controls",
        default={},
        type=str,
        action="extend",
        nargs="*",
        help="Camera controls to be used by picamera2"
        "Can be used multiple times.\n"
        "Format: <control>=<value>",
    )
    p.add_argument("--fps", type=int, default=1)
    p.add_argument("--output_dir", type=Path, default=Path("/tmp"))

    # Logging verbose/debug
    p.add_argument(
        "-d",
        "--debug",
        help="Print lots of debugging statements",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
        default=logging.WARNING,
    )
    p.add_argument(
        "-v",
        "--verbose",
        help="Be verbose",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
    )
    return p


def get_config(argv=None):
    """
    Bulds argument parser and parses args from config file and command line.
    Command line overrides config. argv= overrides command line and config for
    testing purposes.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config is not None:  # only in systemd / explicit case
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f)
        cfg = {
            k.replace("-", "_"): v for k, v in cfg.items()
        }  # convert "-" to "_" without erroring

        parser.set_defaults(**cfg)
        args = parser.parse_args(argv)  # re-parse: config now backs the defaults
    return args


def check_ws_uri(websocket_uri):
    """Raises error if the uri is missing any of scheme, network location, hostname and port"""
    result = urllib.parse.urlsplit(websocket_uri)
    if not all(
        [
            result.scheme,
            result.netloc,
            result.hostname,
            result.port,
        ]
    ):
        logging.debug("Urllib websocket uri parse result: %s", result)
        raise exceptions.InvalidConfigError(
            "Websocket URI has wrong format. Example: ws://0.0.0.0/80 or ws://localhost:8080/path."
        )
    if "ws" not in result.scheme:
        raise exceptions.InvalidConfigError("Websocket URI misses 'ws' in scheme")


def get_hostname_port(websocket_uri):
    """Parses hostname and port from uri"""
    check_ws_uri(websocket_uri)
    result = urllib.parse.urlsplit(websocket_uri)
    return result.hostname, result.port
