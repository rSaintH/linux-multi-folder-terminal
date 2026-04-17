#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import getpass
import psutil


@dataclass(frozen=True)
class PortInfo:
    host: str
    port: int
    pid: int
    process_name: str
    command: str

    @property
    def search_text(self) -> str:
        return f"{self.host} {self.port} {self.pid} {self.process_name} {self.command}".lower()


@dataclass(frozen=True)
class ProcessDetails:
    pid: int
    name: str
    command: str
    cwd: str
    username: str
    is_root: bool


def get_listening_ports() -> list[PortInfo]:
    ports: list[PortInfo] = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != "LISTEN" or not conn.laddr:
                continue

            if hasattr(conn.laddr, "ip"):
                host = conn.laddr.ip
                port = conn.laddr.port
            else:
                host, port = conn.laddr[0], conn.laddr[1]

            pid = conn.pid or 0
            process_name = "—"
            command = "—"

            if pid:
                try:
                    proc = psutil.Process(pid)
                    process_name = proc.name()
                    command = " ".join(proc.cmdline()[:8]) or process_name
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            ports.append(PortInfo(host, port, pid, process_name, command))
    except (psutil.AccessDenied, AttributeError):
        pass

    return sorted(ports, key=lambda item: (item.port, item.host))


def get_process_details(pid: int) -> ProcessDetails | None:
    try:
        proc = psutil.Process(pid)
        command = " ".join(proc.cmdline()) or proc.name()
        try:
            cwd = proc.cwd()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cwd = "indisponivel"
        try:
            username = proc.username()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            username = getpass.getuser()
        return ProcessDetails(
            pid=pid,
            name=proc.name(),
            command=command,
            cwd=cwd,
            username=username,
            is_root=(username == "root"),
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def terminate_process_tree(pid: int) -> tuple[bool, str]:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False, "O processo ja nao existe."
    except psutil.AccessDenied:
        return False, "Sem permissao para encerrar esse processo."

    targets = proc.children(recursive=True)
    targets.append(proc)

    for target in targets:
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _, alive = psutil.wait_procs(targets, timeout=1.2)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    _, still_alive = psutil.wait_procs(alive, timeout=0.6)
    if still_alive:
        return False, "Alguns processos nao puderam ser encerrados."
    return True, "Processo encerrado com sucesso."
