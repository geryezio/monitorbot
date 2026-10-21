"""
Server data collector — CPU, RAM, disk, Docker, network info.
"""

import os
import re
import shutil
from datetime import datetime, timedelta

import psutil

# ── CPU ──────────────────────────────────────────

def cpu_percent() -> float:
    return psutil.cpu_percent(interval=1)


def cpu_count() -> dict:
    return {"physical": psutil.cpu_count(logical=False), "logical": psutil.cpu_count(logical=True)}


def cpu_temp() -> str:
    """CPU temperature (Linux only, via thermal zone)."""
    try:
        paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        ]
        for p in paths:
            if os.path.exists(p):
                with open(p) as f:
                    return f"{int(f.read().strip()) / 1000:.1f}°C"
    except Exception:
        pass
    return "N/A"


# ── RAM ──────────────────────────────────────────

def ram_info() -> dict:
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 1),
        "used_gb": round(mem.used / (1024**3), 1),
        "available_gb": round(mem.available / (1024**3), 1),
        "percent": mem.percent,
    }


def swap_info() -> dict:
    sw = psutil.swap_memory()
    return {
        "total_gb": round(sw.total / (1024**3), 1),
        "used_gb": round(sw.used / (1024**3), 1),
        "percent": sw.percent,
    }


# ── DISK ─────────────────────────────────────────

HOST_ROOT = "/host_root"


def disk_info() -> list[dict]:
    """Read disk info. When inside a container, read from HOST_ROOT."""
    result = []

    use_host = os.path.isdir(HOST_ROOT)
    mounts_file = f"{HOST_ROOT}/proc/mounts" if use_host else "/proc/mounts"

    REAL_FS = {"ext4", "ext3", "ext2", "xfs", "btrfs", "zfs", "ntfs", "vfat", "fuseblk"}
    FUSE_PREFIX = "fuse."  # fuse.rclone, fuse.sshfs, etc.

    seen = set()
    with open(mounts_file) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mountpoint, fstype = parts[0], parts[1], parts[2]

            # Only real filesystems or FUSE
            if fstype not in REAL_FS and not fstype.startswith(FUSE_PREFIX):
                continue
            # Skip Docker overlay lower dirs
            if "docker/rootfs" in mountpoint:
                continue
            # Skip /boot
            if mountpoint.endswith("/boot") or mountpoint.endswith("/boot/efi"):
                continue
            # Skip non-essential bind mounts (Docker utilities, etc.)
            SKIP_PREFIXES = (
                "/usr/local/libexec/docker",
                "/usr/local/bin/docker",
            )
            if mountpoint.startswith(SKIP_PREFIXES):
                continue
            # When reading from host proc, mountpoint is already prefixed with /host_root
            path = mountpoint if use_host else f"{HOST_ROOT}{mountpoint}"
            if not os.path.isdir(path):
                continue
            if mountpoint in seen:
                continue
            seen.add(mountpoint)

            try:
                usage = shutil.disk_usage(path)
                display_mount = mountpoint.replace(HOST_ROOT, "") if use_host else mountpoint
                display_mount = display_mount or "/"  # root
                result.append({
                    "mount": display_mount,
                    "device": device,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": round(usage.used / usage.total * 100, 1),
                })
            except (PermissionError, FileNotFoundError, ZeroDivisionError):
                continue

    return result


# ── NETWORK ──────────────────────────────────────

def net_info() -> dict:
    """IP addresses + Tailscale status."""
    info = {}
    addrs = psutil.net_if_addrs()
    for iface, addr_list in addrs.items():
        if iface == "lo":
            continue
        for addr in addr_list:
            if addr.family == 2:  # AF_INET
                info[iface] = addr.address

    info["tailscale"] = _tailscale_ip()
    return info


def _tailscale_ip() -> str:
    # Try via interface (network_mode: host makes tailscale0 visible)
    addrs = psutil.net_if_addrs()
    for name in ("tailscale0", "tailscale"):
        if name in addrs:
            for addr in addrs[name]:
                if addr.family == 2:  # AF_INET
                    return addr.address
    # Fallback: CLI
    try:
        out = os.popen("tailscale ip -4 2>/dev/null").read().strip()
        return out if out else "not running"
    except Exception:
        return "not installed"


# ── UPTIME ───────────────────────────────────────

def uptime_info() -> dict:
    boot = datetime.fromtimestamp(psutil.boot_time())
    now = datetime.now()
    delta = now - boot

    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60

    return {
        "boot_time": boot.strftime("%Y-%m-%d %H:%M"),
        "uptime": f"{days}d {hours}h {minutes}m",
    }


# ── DOCKER ───────────────────────────────────────

import docker as _docker_mod

_docker_client = None


def _get_docker():
    global _docker_client
    if _docker_client is None:
        try:
            _docker_client = _docker_mod.from_env()
        except Exception:
            return None
    return _docker_client


def docker_containers() -> list[dict]:
    """List all containers + status via Docker SDK."""
    client = _get_docker()
    if client is None:
        return []

    try:
        all_containers = client.containers.list(all=True)
    except Exception:
        return []

    containers = []
    for c in all_containers:
        status = c.status  # 'running', 'exited', 'paused', etc.
        if status == "running":
            short = "🟢 Up"
        elif status == "exited":
            short = "🔴 Stopped"
        else:
            short = f"🟡 {status}"

        # The image may have been removed (prune/rmi) while the container is still
        # listed; c.image.tags raises ImageNotFound in that case and would break the
        # whole container list. Fall back to the image name from container metadata.
        try:
            image_tags = c.image.tags
            image = image_tags[0] if image_tags else c.image.short_id
        except Exception:
            image = c.attrs.get("Config", {}).get("Image") or c.attrs.get("Image", "")[:19]

        # Detect compose project from label
        project = c.labels.get("com.docker.compose.project", "")
        if not project:
            # Fallback: group by name prefix (npm-app → npm)
            project = _guess_project(c.name)

        containers.append({
            "name": c.name,
            "status": short,
            "image": image,
            "project": project,
        })

    return containers


def _guess_project(name: str) -> str:
    """Guess project name from container name."""
    # Pattern: project-suffix → e.g. npm-app → npm, myporto-db → myporto
    parts = name.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in ("app", "db", "webserver", "phpmyadmin", "backend", "frontend"):
        return parts[0]
    return name


def docker_groups() -> dict[str, list[dict]]:
    """Group containers by project."""
    containers = docker_containers()
    groups: dict[str, list[dict]] = {}
    for c in containers:
        p = c.get("project", "other")
        groups.setdefault(p, []).append(c)
    return groups


# ── DOCKER CONTROL ────────────────────────────────

def docker_restart(container: str) -> tuple[bool, str]:
    client = _get_docker()
    if client is None:
        return (False, "Docker not accessible")
    try:
        c = client.containers.get(container)
        c.restart()
        return (True, "OK")
    except Exception as e:
        return (False, str(e))


def docker_stop(container: str) -> tuple[bool, str]:
    client = _get_docker()
    if client is None:
        return (False, "Docker not accessible")
    try:
        c = client.containers.get(container)
        c.stop()
        return (True, "OK")
    except Exception as e:
        return (False, str(e))


def docker_start(container: str) -> tuple[bool, str]:
    client = _get_docker()
    if client is None:
        return (False, "Docker not accessible")
    try:
        c = client.containers.get(container)
        c.start()
        return (True, "OK")
    except Exception as e:
        return (False, str(e))


def docker_group_action(project: str, action: str) -> tuple[bool, str]:
    groups = docker_groups()
    if project not in groups:
        return (False, f"Project '{project}' not found")
        
    client = _get_docker()
    if not client:
        return (False, "Docker not accessible")
        
    success_count = 0
    errors = []
    
    for c in groups[project]:
        try:
            cont = client.containers.get(c["name"])
            if action == "restart":
                cont.restart()
            elif action == "stop":
                cont.stop()
            elif action == "start":
                cont.start()
            success_count += 1
        except Exception as e:
            errors.append(f"{c['name']}: {str(e)}")
            
    if errors:
        return (False, "\n".join(errors))
    return (True, f"{action.capitalize()}ed {success_count} containers")


def docker_group_restart(project: str) -> tuple[bool, str]:
    return docker_group_action(project, "restart")


def docker_group_stop(project: str) -> tuple[bool, str]:
    return docker_group_action(project, "stop")


def docker_group_start(project: str) -> tuple[bool, str]:
    return docker_group_action(project, "start")


def docker_group_down(project: str) -> tuple[bool, str]:
    # Placeholder: Removing compose dependency means this may no longer be functional
    # without implementing full compose-file logic.
    return (False, "Not implemented")


def docker_group_workdir(group: str) -> str | None:
    """Get compose working directory from container labels."""
    groups = docker_groups()
    if group not in groups:
        return None
    client = _get_docker()
    if client is None:
        return None
    for c in groups[group]:
        try:
            cont = client.containers.get(c["name"])
            wd = cont.labels.get("com.docker.compose.project.working_dir")
            if wd:
                return wd
        except Exception:
            continue
    return None


def docker_group_rebuild(group: str) -> tuple[bool, str]:
    """Not implemented without subprocess."""
    return (False, "Not implemented")


def docker_group_recreate(group: str) -> tuple[bool, str]:
    """Not implemented without subprocess."""
    return (False, "Not implemented")


def docker_group_has_dockerfile(group: str) -> bool:
    """Check if project has a Dockerfile (build context)."""
    workdir = docker_group_workdir(group)
    if not workdir:
        return False
    # Container needs /host_root prefix to access host filesystem
    path = f"{HOST_ROOT}{workdir}/Dockerfile" if os.path.isdir(HOST_ROOT) else f"{workdir}/Dockerfile"
    return os.path.isfile(path)


# ── IMAGE UPDATES ────────────────────────────────

def _registry_parts(image_name: str) -> tuple[str, str, str]:
    """Split an image name into (registry, repo, tag)."""
    registry = "registry-1.docker.io"
    repo = image_name
    tag = "latest"

    if ":" in image_name:
        repo, tag = image_name.rsplit(":", 1)

    if "/" not in repo:
        repo = f"library/{repo}"

    parts = repo.split("/", 1)
    if "." in parts[0] and parts[0] != "docker.io":
        registry = parts[0]
        repo = parts[1]
    elif parts[0] == "docker.io":
        registry = "registry-1.docker.io"
        repo = parts[1]

    return registry, repo, tag


def _registry_token(registry: str, repo: str) -> str:
    """Bearer token for pulling public images (Docker Hub / GHCR)."""
    import json
    import urllib.request

    token = ""
    try:
        if registry == "registry-1.docker.io":
            auth_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
            with urllib.request.urlopen(urllib.request.Request(auth_url), timeout=10) as response:
                token = json.loads(response.read().decode()).get("token", "")
        elif registry == "ghcr.io":
            auth_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull"
            with urllib.request.urlopen(urllib.request.Request(auth_url), timeout=10) as response:
                token = json.loads(response.read().decode()).get("token", "")
    except Exception:
        pass

    return token


def _get_remote_digest(image_name: str) -> str:
    """Manifest digest of an image in the registry (Docker Hub / GHCR); "" if unavailable."""
    import urllib.request

    registry, repo, tag = _registry_parts(image_name)
    token = _registry_token(registry, repo)

    manifest_url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(manifest_url)
    req.add_header("Accept", "application/vnd.docker.distribution.manifest.v2+json")
    req.add_header("Accept", "application/vnd.docker.distribution.manifest.list.v2+json")
    req.add_header("Accept", "application/vnd.oci.image.index.v1+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.headers.get("Docker-Content-Digest", "")
    except Exception:
        return ""


def _get_remote_created(image_name: str, arch: str = "amd64", os_name: str = "linux") -> str:
    """Build time of the remote image config for a given platform ("" on failure).

    Used for base images: index digests can change because of re-attestation
    even when the content is identical, so we compare the config timestamps.
    """
    import json
    import urllib.request

    registry, repo, tag = _registry_parts(image_name)
    token = _registry_token(registry, repo)
    base = f"https://{registry}/v2/{repo}"

    def fetch(url: str, accept: str) -> dict:
        req = urllib.request.Request(url)
        req.add_header("Accept", accept)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())

    try:
        index = fetch(
            f"{base}/manifests/{tag}",
            "application/vnd.oci.image.index.v1+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.docker.distribution.manifest.v2+json",
        )

        manifest = index
        for entry in index.get("manifests", []):
            platform = entry.get("platform", {})
            if platform.get("architecture") == arch and platform.get("os") == os_name:
                manifest = fetch(
                    f"{base}/manifests/{entry['digest']}",
                    "application/vnd.docker.distribution.manifest.v2+json,"
                    "application/vnd.oci.image.manifest.v1+json",
                )
                break

        config_digest = manifest.get("config", {}).get("digest", "")
        if not config_digest:
            return ""

        req = urllib.request.Request(f"{base}/blobs/{config_digest}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode()).get("created", "")
    except Exception:
        return ""


def check_image_updates() -> tuple[list[str], list[str], list[str]]:

    client = _get_docker()
    if client is None:
        return ([], [], ["Docker not accessible"])

    try:
        containers = client.containers.list(all=True)
    except Exception as e:
        return ([], [], [str(e)])
        
    image_tags = set()
    for c in containers:
        try:
            tags = c.image.tags
        except Exception:
            # The image may have been removed (prune/rmi) — its original name is still
            # stored in the container metadata; use it so one container can't break all.
            fallback = c.attrs.get("Config", {}).get("Image")
            tags = [fallback] if fallback else []
        if tags:
            image_tags.add(tags[0])
            
    updated = []
    up_to_date = []
    errors = []
    
    for tag in image_tags:
        try:
            try:
                img = client.images.get(tag)
                local_repo_digests = img.attrs.get("RepoDigests", [])
            except Exception:
                continue
                
            remote_digest = _get_remote_digest(tag)
            
            if not remote_digest:
                # Skip local images or failed registry checks so they don't clutter the UI
                continue
                
            is_updated = True
            for ld in local_repo_digests:
                if remote_digest in ld:
                    is_updated = False
                    break
                    
            if is_updated:
                updated.append(tag)
            else:
                up_to_date.append(tag)
                
        except Exception:
            pass
            
    return (updated, up_to_date, errors)


# ── BASE IMAGE UPDATES (FROM Dockerfile) ─────────

FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+[Aa][Ss]\s+(\S+))?\s*$")
ARG_RE = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(\S+))?\s*$")


def _parse_timestamp(value: str):
    """Parse ISO8601 (with 'Z') into an aware datetime; None on failure."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _dockerfile_path(workdir: str) -> str:
    """Host path of a compose project's Dockerfile (read via /host_root)."""
    base = f"{HOST_ROOT}{workdir}" if os.path.isdir(HOST_ROOT) else workdir
    path = os.path.join(base, "Dockerfile")
    return path if os.path.isfile(path) else ""


def _parse_base_images(path: str) -> list[str]:
    """Extract base images from FROM lines, resolving simple ARGs and skipping stage aliases."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except Exception:
        return []

    args = {}
    for line in lines:
        m = ARG_RE.match(line)
        if m and m.group(2):
            args[m.group(1)] = m.group(2)

    images = []
    stages = set()

    for line in lines:
        m = FROM_RE.match(line)
        if not m:
            continue

        image, alias = m.group(1), m.group(2)

        def _resolve(mm):
            name = mm.group(1) or mm.group(2)
            return args.get(name, mm.group(0))

        image = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", _resolve, image)

        if alias:
            stages.add(alias.lower())
        if not image or image.lower() == "scratch" or "$" in image or "@sha256:" in image:
            continue
        if image.lower() in stages:
            continue
        if image not in images:
            images.append(image)

    return images


def _base_image_projects() -> dict[str, list[str]]:
    """Map base image -> list of compose projects that use it."""
    client = _get_docker()
    if client is None:
        return {}

    projects = {}
    try:
        for c in client.containers.list(all=True):
            project = c.labels.get("com.docker.compose.project", "")
            workdir = c.labels.get("com.docker.compose.project.working_dir", "")
            if project and workdir and project not in projects:
                projects[project] = workdir
    except Exception:
        return {}

    uses = {}
    for project, workdir in projects.items():
        path = _dockerfile_path(workdir)
        if not path:
            continue
        for image in _parse_base_images(path):
            uses.setdefault(image, [])
            if project not in uses[image]:
                uses[image].append(project)

    return uses


def check_base_image_updates() -> tuple[list[dict], list[str], list[str], list[str]]:
    """Check the base image (FROM) of every Dockerfile: local vs registry.

    Returns (updated, up_to_date, not_local, errors). Items in `updated` are
    {"image": str, "projects": list[str]} so the bot can show which projects
    need a rebuild. `not_local` = base images missing locally (cannot be
    compared; they get pulled on the next build).
    """
    client = _get_docker()
    if client is None:
        return ([], [], [], ["Docker not accessible"])

    updated, up_to_date, not_local, errors = [], [], [], []

    for image, projects in sorted(_base_image_projects().items()):
        tag = image if ":" in image else f"{image}:latest"

        try:
            local = client.images.get(tag)
        except Exception:
            # Not available locally — the next build will pull the latest version.
            not_local.append(tag)
            continue

        remote_created = _get_remote_created(tag)
        if not remote_created:
            # Unknown registry / failed check — skip silently to keep the UI clean.
            continue

        local_dt = _parse_timestamp(local.attrs.get("Created", ""))
        remote_dt = _parse_timestamp(remote_created)
        if not local_dt or not remote_dt:
            continue

        if remote_dt > local_dt:
            updated.append({"image": tag, "projects": projects})
        else:
            up_to_date.append(tag)

    return (updated, up_to_date, not_local, errors)


# ── TOP PROCESSES ────────────────────────────────

def top_processes(count: int = 5) -> list[dict]:
    """Top N processes by CPU usage."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            info["cpu_percent"] = info["cpu_percent"] or 0
            info["memory_percent"] = round(info["memory_percent"] or 0, 1)
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return procs[:count]


# ── SUMMARY ──────────────────────────────────────

def full_status() -> dict:
    return {
        "cpu": {
            "percent": cpu_percent(),
            "count": cpu_count(),
            "temp": cpu_temp(),
        },
        "ram": ram_info(),
        "swap": swap_info(),
        "disk": disk_info(),
        "network": net_info(),
        "uptime": uptime_info(),
        "docker": {
            "total": len(docker_containers()),
            "running": len([c for c in docker_containers() if c["status"].startswith("🟢")]),
            "stopped": len([c for c in docker_containers() if c["status"].startswith("🔴")]),
        },
    }
