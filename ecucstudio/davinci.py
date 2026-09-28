"""DaVinci Configurator command line (DVCfgCmd.exe) integration.

Finds a DVCfgCmd matching the project's SIP, builds command lines for validation,
generation and project update, runs them with streamed console output and parses
the DaVinciExecutionReport XML.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field

from lxml import etree

from .validation import Result, Severity

# Extra DVCfgCmd.exe candidates from ECUCSTUDIO_DVCFGCMD (";"-separated). The path chosen in
# Project > Settings is kept in the user's settings file, never in the code.
KNOWN_LOCATIONS = [p for p in os.environ.get("ECUCSTUDIO_DVCFGCMD", "").split(";") if p.strip()]

EXIT_CODES = {
    0: "Success",
    1: "Finished with errors (validation or generation errors)",
    6: "Invalid command line argument (e.g. unknown module definition path)",
}

_BRANDING = re.compile(r"com\.vector\.cfg\.gui\.branding\.dvcfg_(\d+\.\d+\.\d+)\.(r?\w+)\.jar")
_SUMMARY = re.compile(r"(\d+) Fatal errors, (\d+) Errors, (\d+) Warnings, (\d+) Infos")
_GEN_LINE = re.compile(r"^\s*\d+\s+(INFO|WARN|ERROR)\s+-\s+(Calculation|Validation|Generation)\t(\w+)\t?(.*)$")


@dataclass
class DvInstall:
    exe: str
    version: str = ""
    build: str = ""

    @property
    def core_dir(self) -> str:
        return os.path.dirname(self.exe)

    @property
    def label(self) -> str:
        return f"{self.version} ({self.build})  {self.exe}" if self.version else self.exe


def installation_info(exe: str) -> DvInstall:
    core = os.path.dirname(exe)
    ver = build = ""
    for jar in glob.glob(os.path.join(core, "plugins", "com.vector.cfg.gui.branding.dvcfg_*.jar")):
        m = _BRANDING.search(os.path.basename(jar))
        if m:
            ver, build = m.group(1), m.group(2)
    return DvInstall(exe=exe, version=ver, build=build)


def sip_tool_version(sip_dir: str) -> tuple[str, str] | None:
    """Version of the DaVinci Configurator the SIP was delivered with (branding jar)."""
    if not sip_dir:
        return None
    for jar in glob.glob(os.path.join(sip_dir, "DaVinciConfigurator", "Core", "plugins",
                                      "com.vector.cfg.gui.branding.dvcfg_*.jar")):
        m = _BRANDING.search(os.path.basename(jar))
        if m:
            return m.group(1), m.group(2)
    return None


def find_installations(sip_dir: str | None = None, extra=()) -> list[DvInstall]:
    cands = []
    if sip_dir:
        cands.append(os.path.join(sip_dir, "DaVinciConfigurator", "Core", "DVCfgCmd.exe"))
    cands.extend(extra)
    cands.extend(KNOWN_LOCATIONS)
    seen, out = set(), []
    for c in cands:
        if not c:
            continue
        c = os.path.normpath(c)
        key = os.path.normcase(c)
        if key in seen or not os.path.isfile(c):
            continue
        seen.add(key)
        out.append(installation_info(c))
    # the one matching the SIP version first
    want = sip_tool_version(sip_dir) if sip_dir else None
    if want:
        out.sort(key=lambda i: (i.version, i.build) != want)
    return out


# ----------------------------------------------------------------------------
# Command lines
# ----------------------------------------------------------------------------

@dataclass
class GenerateOptions:
    modules: list[str] = field(default_factory=list)       # def paths or short names, [] = all
    exclude: list[str] = field(default_factory=list)
    ext_gen_steps: str | None = None                        # None = default behaviour
    gen_type: str | None = None                             # REAL | VTT
    save_project: bool = False
    swcs: str | None = None                                 # None, "default", "" or list
    sync_system_description: bool = False
    gen_args: list[str] = field(default_factory=list)
    keep_temp: bool = False


_MSYS = re.compile(r"^[A-Za-z]:[/\\]Program Files[/\\]Git(?P<p>[/\\].*)$", re.I)


def fix_msys_path(defref: str) -> str:
    """Undo Git-Bash (MSYS) path conversion: 'C:/Program Files/Git/MICROSAR/Det' -> '/MICROSAR/Det'."""
    m = _MSYS.match(defref.strip())
    return m.group("p").replace("\\", "/") if m else defref.strip()


def build_validate_cmd(exe, dpa, report, log=None) -> list[str]:
    cmd = [exe, "-p", os.path.abspath(dpa), "-v", "--reportFile", report, "--reportArgs", "CreateXmlFile"]
    if log:
        cmd += ["-l", log]
    return cmd


def build_generate_cmd(exe, dpa, report, opts: GenerateOptions, log=None) -> list[str]:
    cmd = [exe, "-p", os.path.abspath(dpa), "-g"]
    if opts.modules:
        cmd += ["-m", ",".join(fix_msys_path(m) for m in opts.modules)]
    if opts.exclude:
        cmd += ["-x", ",".join(fix_msys_path(m) for m in opts.exclude)]
    if opts.ext_gen_steps is not None:
        cmd += ["--extGenStepsToGenerate", opts.ext_gen_steps]
    if opts.gen_type:
        cmd += ["--genType", opts.gen_type]
    if opts.save_project:
        cmd.append("--saveProject")
    if opts.swcs is not None:
        cmd += ["--swcsToGenerate", opts.swcs]
    if opts.sync_system_description:
        cmd.append("--syncSystemDescription")
    for a in opts.gen_args:
        cmd += ["--genArg", a]
    if opts.keep_temp:
        cmd.append("--keepGenTempFiles")
    cmd += ["--reportFile", report, "--reportArgs", "CreateXmlFile"]
    if log:
        cmd += ["-l", log]
    return cmd


def build_update_cmd(exe, dpa, inputs=(), only_ecuc=False, only_legacy_diag=False) -> list[str]:
    cmd = [exe, "-u", os.path.abspath(dpa)]
    if inputs:
        cmd += ["--input", ",".join(inputs)]
    if only_ecuc:
        cmd.append("--onlyEcuc")
    if only_legacy_diag:
        cmd.append("--onlyLegacyDiag")
    return cmd


def format_cmd(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd)


# ----------------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------------

class DvRun:
    """Runs DVCfgCmd in a background thread, streaming output lines to *on_line*."""

    def __init__(self, cmd: list[str], on_line=None, on_done=None, cwd=None):
        self.cmd = cmd
        self.on_line = on_line or (lambda s: None)
        self.on_done = on_done or (lambda rc: None)
        self.cwd = cwd or os.path.dirname(cmd[0])
        self.proc = None
        self.returncode = None
        self.summary = None
        self.thread = None
        self._cancelled = False

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=flags)
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                m = _SUMMARY.search(line)
                if m:
                    self.summary = tuple(int(x) for x in m.groups())
                self.on_line(line)
            self.proc.wait()
            self.returncode = self.proc.returncode
        except OSError as e:
            self.on_line(f"ERROR - cannot start DVCfgCmd: {e}")
            self.returncode = -1
        if self._cancelled:
            self.returncode = -2
        self.on_done(self.returncode)

    def cancel(self):
        self._cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                # DVCfgCmd spawns a Java VM; kill the whole tree
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                self.proc.kill()

    def wait(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)
        return self.returncode


def parse_progress(line: str):
    """(phase, event, generator text) for generator progress lines, else None."""
    m = _GEN_LINE.match(line)
    if not m:
        return None
    return m.group(2), m.group(3), m.group(4).strip()


# ----------------------------------------------------------------------------
# Report parsing
# ----------------------------------------------------------------------------

@dataclass
class GeneratedFile:
    path: str
    info: str


@dataclass
class GenerationResult:
    name: str
    state: str
    gen_type: str
    phases: list[tuple[str, str]] = field(default_factory=list)
    files: list[GeneratedFile] = field(default_factory=list)
    error: str = ""
    console: list[str] = field(default_factory=list)
    generator: str = ""
    version: str = ""
    definition: str = ""


@dataclass
class ExecutionReport:
    validation: list[Result] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    process_result: str | None = None
    duration: float | None = None
    generation: list[GenerationResult] = field(default_factory=list)


def parse_report(path: str) -> ExecutionReport:
    rep = ExecutionReport()
    if not path or not os.path.exists(path):
        return rep
    root = etree.parse(path).getroot()
    vr = root.find("ValidationResults")
    if vr is not None:
        rep.counts = {k: int(v) for k, v in vr.attrib.items() if v.isdigit()}
        for g in vr.findall("ValidationResultId"):
            rid = (g.get("origin") or "") + (g.get("id") or "")
            for x in g.findall("ValidationResult"):
                ces = [(ce.findtext("Object"), ce.findtext("Definition")) for ce in x.iter("CE")]
                obj, dfn = ces[0] if ces else (None, None)
                rep.validation.append(Result(
                    rule_id=rid, severity=Severity.parse(x.get("severity")),
                    title=g.get("message") or rid, message=(x.findtext("Description") or "").strip(),
                    obj=obj, definition=dfn, source="DaVinci",
                    acknowledged=x.findtext("Acknowledgement"),
                    ondemand=x.get("ondemandresult") == "true"))
    gp = root.find("GenerationProcessResult")
    if gp is not None:
        rep.process_result = gp.get("result")
        try:
            rep.duration = float(gp.get("lastexecutionduration") or 0)
        except ValueError:
            rep.duration = None
        for gr in gp.findall("GenerationResult"):
            res = GenerationResult(name=gr.findtext("Name") or "", state=gr.findtext("State") or "",
                                   gen_type=gr.get("generationtype") or "",
                                   error=(gr.findtext("ErrorMessage") or "").strip())
            for ph in gr.iter("Phase"):
                res.phases.append((ph.get("type"), ph.get("state")))
                for f in ph.iter("File"):
                    res.files.append(GeneratedFile(f.text or "", f.get("generationinfo") or ""))
                res.console.extend((c.text or "") for c in ph.iter("ConsoleOutput"))
            g = gr.find("Generator")
            if g is not None:
                res.generator = g.findtext("Name") or ""
                res.version = g.findtext("Version") or ""
                res.definition = g.findtext("Definition") or ""
            rep.generation.append(res)
    return rep
