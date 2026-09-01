#!/usr/bin/env python3
"""Retry VM.Standard.A1.Flex launch until capacity is available (Windows-friendly)."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import oci
from oci.core.models import (
    CreateVnicDetails,
    InstanceSourceViaImageDetails,
    LaunchInstanceDetails,
    LaunchInstanceShapeConfigDetails,
)
from oci.exceptions import ServiceError


def load_env_file(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing {path} - copy retry.env.example to retry.env")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        if key:
            os.environ[key.strip()] = val.strip()


def log(msg: str, log_path: Path) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"retry.env missing {name}")
    return val


def main() -> int:
    root = Path(__file__).resolve().parent
    load_env_file(root / "retry.env")

    compartment = require("COMPARTMENT_OCID")
    if not compartment.startswith("ocid1."):
        raise SystemExit("COMPARTMENT_OCID must start with ocid1.")

    ad = require("AVAILABILITY_DOMAIN")
    subnet = require("SUBNET_OCID")
    image = require("IMAGE_OCID")
    ssh_pub = Path(require("SSH_PUBLIC_KEY_FILE"))
    if not ssh_pub.is_file():
        raise SystemExit(f"SSH public key not found: {ssh_pub}")

    name = os.environ.get("DISPLAY_NAME", "kiln").strip() or "kiln"
    ocpus = int(os.environ.get("OCPUS", "2"))
    memory = int(os.environ.get("MEMORY_GB", "12"))
    boot_gb = int(os.environ.get("BOOT_VOLUME_GB", "50"))
    interval = max(45, int(os.environ.get("RETRY_INTERVAL_SEC", "90")))

    log_path = root / f"retry-{datetime.now():%Y%m%d-%H%M%S}.log"
    config = oci.config.from_file()
    compute = oci.core.ComputeClient(config)

    launch = LaunchInstanceDetails(
        availability_domain=ad,
        compartment_id=compartment,
        display_name=name,
        shape="VM.Standard.A1.Flex",
        shape_config=LaunchInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory,
        ),
        source_details=InstanceSourceViaImageDetails(
            image_id=image,
            boot_volume_size_in_gbs=boot_gb,
        ),
        create_vnic_details=CreateVnicDetails(
            subnet_id=subnet,
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": ssh_pub.read_text(encoding="utf-8").strip()},
    )

    log(f"Starting A1 retry loop - log: {log_path}", log_path)
    log(f"AD={ad} shape=VM.Standard.A1.Flex {ocpus}OCPU/{memory}GB interval={interval}s", log_path)

    attempt = 0
    while True:
        attempt += 1
        log(f"Attempt #{attempt} ...", log_path)
        try:
            response = compute.launch_instance(launch_instance_details=launch)
            instance_id = response.data.id
            log(f"Launched instance {instance_id}, waiting for RUNNING ...", log_path)

            instance = oci.wait_until(
                compute,
                compute.get_instance(instance_id),
                "lifecycle_state",
                "RUNNING",
                max_interval_seconds=30,
                max_wait_seconds=1800,
            ).data
            log("SUCCESS - instance RUNNING.", log_path)
            log(f"Instance OCID: {instance_id}", log_path)
            log(f"Console: Compute > Instances > {name}", log_path)
            log("Next: SSH ubuntu@<public-ip>, then deploy per docs/deploy-oracle.md", log_path)
            return 0
        except ServiceError as err:
            blob = f"{err.status} {err.code} {err.message}"
            if err.status == 429 or "TooManyRequests" in blob:
                wait = min(interval * 2, 300)
                log(f"Rate limited - sleep {wait}s", log_path)
                time.sleep(wait)
                continue
            if err.status in (500, 503) or any(
                x in blob for x in ("Out of capacity", "Out of host capacity", "InternalError")
            ):
                log(f"No capacity yet - sleep {interval}s ({blob[:120]})", log_path)
                time.sleep(interval)
                continue
            log("FAILED (fix config before retrying):", log_path)
            log(blob, log_path)
            return 1
        except Exception as err:  # noqa: BLE001
            log(f"FAILED: {err}", log_path)
            return 1


if __name__ == "__main__":
    sys.exit(main())
