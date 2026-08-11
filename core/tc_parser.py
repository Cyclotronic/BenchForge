"""
TestController File Parser & Config Generator (`tc_parser.py`)

Handles parsing of TestController device definition files (`Devices/*.txt`) and
generation/parsing of TestController configuration files (`settingsGPIB.txt` and `settingsLoad.txt`).
"""

import os
from typing import Dict, List, Any, Tuple


class TCDeviceDefinition:
    """Represents a parsed TestController device definition file."""

    def __init__(self, file_path: str = ""):
        self.file_path = file_path
        self.name: str = ""
        self.type: str = ""
        self.driver: str = ""
        self.port_type: str = ""
        self.cmd_mode: str = "SCPI"
        self.id_query: str = "*IDN?"
        self.id_pattern: str = ""
        self.commands: Dict[str, str] = {}  # query -> expected response pattern or description
        self.non_query_commands: set = set()  # set of non-query command names
        self.readings: List[Dict[str, Any]] = []
        self.raw_lines: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "driver": self.driver,
            "port_type": self.port_type,
            "cmd_mode": self.cmd_mode,
            "id_query": self.id_query,
            "id_pattern": self.id_pattern,
            "file_path": self.file_path,
            "command_count": len(self.commands),
        }


def parse_tc_device_file(file_path: str) -> TCDeviceDefinition:
    """Parses a TestController device definition file (.txt)."""
    device = TCDeviceDefinition(file_path=file_path)

    if not os.path.isfile(file_path):
        return device

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            device.raw_lines = lines

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith(";"):
                continue

            # Check for header tags
            if line_str.lower().startswith("#name"):
                device.name = line_str[5:].strip()
            elif line_str.lower().startswith("#type"):
                device.type = line_str[5:].strip()
            elif line_str.lower().startswith("#driver"):
                device.driver = line_str[7:].strip()
            elif line_str.lower().startswith("#porttype"):
                device.port_type = line_str[9:].strip()
            elif line_str.lower().startswith("#cmdmode"):
                device.cmd_mode = line_str[8:].strip()
            elif line_str.lower().startswith("#id?"):
                device.id_query = line_str[4:].strip() or "*IDN?"
            elif line_str.lower().startswith("#id"):
                device.id_pattern = line_str[3:].strip()
            elif line_str.lower().startswith("#cmd?"):
                parts = line_str[5:].strip().split(maxsplit=1)
                if parts:
                    cmd = parts[0].strip().lstrip(":").upper()
                    resp = parts[1] if len(parts) > 1 else ""
                    device.commands[cmd] = resp
            elif line_str.lower().startswith("#scpicmd") or line_str.lower().startswith("#cmd"):
                parts = line_str.split(maxsplit=2)
                if len(parts) >= 2:
                    cmd_name = parts[1].strip().lstrip(":").upper()
                    device.non_query_commands.add(cmd_name)

        # Default ID pattern if not explicitly provided
        if not device.id_pattern and device.name:
            device.id_pattern = f"SIMULATED,{device.name},SN12345,v1.0"

    except Exception as e:
        print(f"Error parsing TC device file {file_path}: {e}")

    return device


def parse_tc_devices_dir(dir_path: str) -> Dict[str, TCDeviceDefinition]:
    """Scans a directory for TestController device files and returns a map of name -> TCDeviceDefinition."""
    devices: Dict[str, TCDeviceDefinition] = {}

    if not os.path.isdir(dir_path):
        return devices

    for root, _, files in os.walk(dir_path):
        for file in files:
            if file.endswith(".txt") and not file.startswith("."):
                full_path = os.path.join(root, file)
                dev = parse_tc_device_file(full_path)
                if dev.name:
                    devices[dev.name] = dev

    return devices


def looks_like_path(value: str) -> bool:
    """
    Is this argument a filename, or the file's contents?

    These parsers accept either. os.path.isfile() is not a safe way to ask on
    its own: on macOS a long multi-line string raises OSError [Errno 63] "File
    name too long", while Windows quietly returns False. The project is worked
    from both, so the check has to be explicit rather than rely on the platform
    being forgiving.
    """
    if not value or "\n" in value or "\r" in value:
        return False
    # Comfortably under the shortest common limit (255 on most filesystems).
    if len(value) > 240:
        return False
    try:
        return os.path.isfile(value)
    except (OSError, ValueError):
        return False


def parse_settings_gpib(content_or_path: str) -> List[Dict[str, Any]]:
    """
    Parses TestController settingsGPIB.txt file or content string.
    Line format: <Type>|id:<ID>|address:<HOST or PORT>|baudrate:<SETTINGS>|settings:<OPTIONS>|
    """
    lines = []
    if looks_like_path(content_or_path):
        with open(content_or_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    else:
        lines = content_or_path.splitlines()

    controllers: List[Dict[str, Any]] = []

    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith(";") or "|" not in line_str:
            continue

        parts = line_str.split("|")
        controller_type = parts[0].strip()
        ctrl_data = {
            "type": controller_type,
            "id": "",
            "address": "127.0.0.1",
            "baudrate": "",
            "settings": "",
            "raw": line_str,
        }

        for part in parts[1:]:
            part_str = part.strip()
            if not part_str:
                continue
            if ":" in part_str:
                key, value = part_str.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "id":
                    ctrl_data["id"] = value
                elif key == "address":
                    ctrl_data["address"] = value
                elif key == "baudrate":
                    ctrl_data["baudrate"] = value
                elif key == "settings":
                    ctrl_data["settings"] = value

        controllers.append(ctrl_data)

    return controllers


def generate_settings_gpib(controllers: List[Dict[str, Any]]) -> str:
    """Generates TestController settingsGPIB.txt file content from a list of controller dicts."""
    lines = [
        "; settingsGPIB.txt generated by TestController Developer Tool",
        "; Interface definitions for Prologix Ethernet and shared interfaces",
    ]

    for ctrl in controllers:
        ctype = ctrl.get("type", "PrologixEthernet")
        cid = ctrl.get("id", "A")
        addr = ctrl.get("address", "127.0.0.1")
        baud = ctrl.get("baudrate", "")
        sett = ctrl.get("settings", "")

        line = f"{ctype}|id:{cid}|address:{addr}|baudrate:{baud}|settings:{sett}|"
        lines.append(line)

    return "\n".join(lines) + "\n"


def parse_settings_load(content_or_path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Parses TestController settingsLoad.txt file or content string.
    Returns (headers_dict, devices_list)
    Format: Device:<DriverName>|PortType:<Type>|Address:<ID>:<GPIB>|Baudrate:<rate>|Enabled:<0|1>
    """
    lines = []
    if looks_like_path(content_or_path):
        with open(content_or_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    else:
        lines = content_or_path.splitlines()

    headers = {
        "ScanSerialPorts": "0",
        "ExcludedSerialPorts": "",
    }
    devices: List[Dict[str, Any]] = []

    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith(";"):
            continue

        if line_str.startswith("ScanSerialPorts:"):
            headers["ScanSerialPorts"] = line_str.split(":", 1)[1].strip()
            continue
        elif line_str.startswith("ExcludedSerialPorts:"):
            headers["ExcludedSerialPorts"] = line_str.split(":", 1)[1].strip()
            continue

        if "|" not in line_str:
            continue

        parts = line_str.split("|")
        dev_data = {
            "device": "",
            "port_type": "GPIB",
            "address": "A:1",
            "controller_id": "A",
            "gpib_address": 1,
            "baudrate": "9600",
            "enabled": 1,
            "raw": line_str,
        }

        for part in parts:
            part_str = part.strip()
            if not part_str or ":" not in part_str:
                continue
            key, value = part_str.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "device":
                dev_data["device"] = value
            elif key == "porttype":
                dev_data["port_type"] = value
            elif key == "address":
                dev_data["address"] = value
                if ":" in value:
                    cid, gpib_str = value.split(":", 1)
                    dev_data["controller_id"] = cid.strip()
                    try:
                        dev_data["gpib_address"] = int(gpib_str.strip())
                    except ValueError:
                        dev_data["gpib_address"] = 1
                else:
                    dev_data["controller_id"] = "A"
                    try:
                        dev_data["gpib_address"] = int(value)
                    except ValueError:
                        dev_data["gpib_address"] = 1
            elif key == "baudrate":
                dev_data["baudrate"] = value
            elif key == "enabled":
                try:
                    dev_data["enabled"] = int(value)
                except ValueError:
                    dev_data["enabled"] = 1

        if dev_data["device"]:
            devices.append(dev_data)

    return headers, devices


def generate_settings_load(
    devices: List[Dict[str, Any]],
    scan_serial: int = 0,
    excluded_serial: str = "",
) -> str:
    """Generates TestController settingsLoad.txt file content."""
    lines = [
        f"ScanSerialPorts:{scan_serial}",
        f"ExcludedSerialPorts:{excluded_serial}",
    ]

    for dev in devices:
        name = dev.get("device", "")
        ptype = dev.get("port_type", "GPIB")
        cid = dev.get("controller_id", "A")
        gpib = dev.get("gpib_address", 1)
        addr = dev.get("address", f"{cid}:{gpib}")
        baud = dev.get("baudrate", "9600")
        enabled = dev.get("enabled", 1)

        line = f"Device:{name}|PortType:{ptype}|Address:{addr}|Baudrate:{baud}|Enabled:{enabled}"
        lines.append(line)

    return "\n".join(lines) + "\n"


def generate_recommended_configs(
    device_mappings: List[Dict[str, Any]],
    host: str = "127.0.0.1",
    port: int = 1234,
    mode: str = "single_gateway",
) -> Tuple[str, str]:
    """
    Generates a pair of (settingsGPIB.txt, settingsLoad.txt) consistent with
    BenchForge's emulator engine.

    A single controller definition ('id:A') is defined in settingsGPIB.txt pointing
    to the emulator's host and port. All virtual instruments map to 'A:<slot>' in
    settingsLoad.txt.
    """
    port_opt = f"port:{port}" if port != 1234 else ""

    controllers = [{
        "type": "PrologixEthernet",
        "id": "A",
        "address": host,
        "baudrate": "",
        "settings": port_opt,
    }]

    load_devices: List[Dict[str, Any]] = []
    for idx, dev in enumerate(device_mappings):
        gpib_addr = dev.get("gpib_address", idx + 1)
        dev_name = dev.get("name", f"Instrument {idx+1}")
        enabled = dev.get("enabled", 1)

        load_devices.append({
            "device": dev_name,
            "port_type": "GPIB",
            "address": f"A:{gpib_addr}",
            "controller_id": "A",
            "gpib_address": gpib_addr,
            "baudrate": "9600",
            "enabled": enabled,
        })

    gpib_txt = generate_settings_gpib(controllers)
    load_txt = generate_settings_load(load_devices)

    return gpib_txt, load_txt
