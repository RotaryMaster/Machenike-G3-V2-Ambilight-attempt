#!/usr/bin/env python3
"""Offline-only APK inspection of KeyLinker BLE codec and GATT transport.

Searches the provided APK for DEX and decompiles using existing JADX CLI.
With --fetch-portable-jadx, downloads the official JADX release into user
cache ONLY IF no installed JADX CLI is available. Never modifies the OS base,
never connects to any Bluetooth device, never uploads the APK/decompiled code.
Writes derived decompiled sources into ~/.cache/machenike-keylinker-analysis,
not into this Git repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import sys
import urllib.request
import zipfile

CACHE = Path.home() / ".cache" / "machenike-keylinker-analysis"
APK_MAX_ENTRY = 80 * 1024 * 1024
ZIP_MAX_SIZE = 220 * 1024 * 1024

PATTERNS = (
    (re.compile(r"\bclass\s+CodeHelper\b", re.I), 70),
    (re.compile(r"\b(?:sendDataKey|serialKey|decode|encrypt|decrypt|scramble)\b", re.I), 12),
    (re.compile(r"d7f010e[012]", re.I), 100),
    (re.compile(r"\b(?:getHostLighting|HOST_LIGHTING|getSerial|writeCharacteristic|onCharacteristicChanged|setCharacteristicNotification|writeDescriptor)\b", re.I), 15),
    (re.compile(r"BluetoothGatt|BluetoothGattCharacteristic|0x2902", re.I), 3),
)



def java_on_host() -> Path | None:
    """Use existing Java from PATH or an already cached portable JRE."""
    which = shutil.which("java")
    if which:
        return Path(which)
    for candidate in sorted((CACHE / "jre").glob("*/bin/java")):
        if candidate.is_file():
            return candidate
    return None


def download_limited(url: str, dst: Path, limit: int) -> None:
    if not url.startswith(("https://github.com/", "https://api.adoptium.net/")):
        raise RuntimeError("URL não confiável para artefato")
    dst.parent.mkdir(parents=True, exist_ok=True)
    partial = dst.with_suffix(dst.suffix + ".partial")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "machenike-keylinker-offline-audit"})
        with urllib.request.urlopen(req, timeout=120) as stream, partial.open("wb") as out:
            n = 0
            while chunk := stream.read(1024 * 1024):
                n += len(chunk)
                if n > limit:
                    raise RuntimeError("Download excedeu tamanho máximo")
                out.write(chunk)
        partial.replace(dst)
    finally:
        partial.unlink(missing_ok=True)


def sha256_file(src: Path) -> str:
    digest = hashlib.sha256()
    with src.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_java(fetch: bool) -> Path | None:
    java = java_on_host()
    if java:
        return java
    if not fetch:
        return None
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("JRE portátil automático suporta apenas Linux x86_64")
    # Adoptium's official API supplies the release archive URL and exact SHA256.
    api = ("https://api.adoptium.net/v3/assets/latest/21/hotspot"
           "?architecture=x64&image_type=jre&os=linux&vendor=eclipse")
    request = urllib.request.Request(
        api, headers={"User-Agent": "machenike-keylinker-offline-audit", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=35) as resp:
        assets = json.load(resp)
    packages = [
        obj.get("binary", {}).get("package", {})
        for obj in assets if obj.get("binary", {}).get("image_type") == "jre"
    ]
    if not packages:
        raise RuntimeError("API oficial Adoptium não retornou pacote Linux JRE")
    pkg = packages[0]
    url = pkg.get("link", "")
    checksum = pkg.get("checksum", "").lower()
    name = pkg.get("name", "")
    if not (
        url.startswith("https://github.com/adoptium/")
        and name.endswith(".tar.gz")
        and re.fullmatch(r"[0-9a-f]{64}", checksum)
    ):
        raise RuntimeError("Metadados ou SHA256 do JRE inesperados")
    jre_dir = CACHE / "jre"
    archive = jre_dir / name
    print(f"Obtendo Temurin 21 JRE portátil ({name}) ...", flush=True)
    if not archive.is_file() or sha256_file(archive) != checksum:
        download_limited(url, archive, 125 * 1024 * 1024)
    if sha256_file(archive) != checksum:
        archive.unlink(missing_ok=True)
        raise RuntimeError("SHA256 do Temurin JRE difere do release oficial")
    jre_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if sum(m.size for m in members if m.isfile()) > 400 * 1024 * 1024:
            raise RuntimeError("Tamanho extraído do JRE excede limite")
        # Python 3.15: data filter prevents path traversal/unsafe symlinks.
        tar.extractall(path=jre_dir, filter="data")
    java = java_on_host()
    if java is None:
        raise RuntimeError("JRE extraído, mas bin/java não foi encontrado")
    print("JRE verificado e extraído em cache:", java, flush=True)
    return java


def enable_java(java: Path) -> None:
    java_home = java.parent.parent
    os.environ["JAVA_HOME"] = str(java_home)
    os.environ["PATH"] = str(java.parent) + os.pathsep + os.environ.get("PATH", "")


def get_version_jadx() -> Path | None:
    candidates: list[Path] = []
    for name in ("jadx", "jadx-cli"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    env = os.environ.get("JADX_CLI")
    if env:
        candidates.append(Path(env).expanduser())
    for loc in (
        CACHE / "jadx" / "bin" / "jadx",
        Path.home() / "tools" / "jadx" / "bin" / "jadx",
        Path.home() / "Downloads" / "jadx" / "bin" / "jadx",
        Path.home() / "Applications" / "jadx" / "bin" / "jadx",
        Path("/opt/jadx/bin/jadx"),
    ):
        candidates.append(loc)
    for name in candidates:
        if name.is_file():
            return name
    return None


def retrieve_jadx() -> Path:
    target = CACHE / "jadx"
    binary = target / "bin" / "jadx"
    if binary.is_file():
        return binary
    headers = {"User-Agent": "machenike-offline-apk-inspection", "Accept": "application/vnd.github+json"}
    endpoint = "https://api.github.com/repos/skylot/jadx/releases/latest"
    with urllib.request.urlopen(urllib.request.Request(endpoint, headers=headers), timeout=35) as resp:
        release = json.load(resp)
    assets = release.get("assets", [])
    bundles = [a for a in assets if re.fullmatch(r"jadx-[0-9][\w.+-]*\.zip", a.get("name", ""))]
    if len(bundles) != 1:
        raise RuntimeError("Não foi possível identificar um único ZIP oficial do JADX")
    asset = bundles[0]
    url = asset["browser_download_url"]
    if not url.startswith("https://github.com/skylot/jadx/releases/download/"):
        raise RuntimeError("URL inesperada para artefato do JADX")
    archive = CACHE / asset["name"]
    print(f"JADX ausente: baixando pacote oficial {asset['name']} em {CACHE} ...", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        req = urllib.request.Request(url, headers={"User-Agent": headers["User-Agent"]})
        with urllib.request.urlopen(req, timeout=75) as resp, archive.open("wb") as out:
            total = 0
            while chunk := resp.read(1024 * 1024):
                total += len(chunk)
                if total > ZIP_MAX_SIZE:
                    raise RuntimeError("ZIP excedeu o limite de tamanho")
                out.write(chunk)
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        h = hashlib.sha256()
        with archive.open("rb") as src:
            for piece in iter(lambda: src.read(1024 * 1024), b""):
                h.update(piece)
        if h.hexdigest().lower() != digest.split(":", 1)[1].lower():
            raise RuntimeError("SHA256 do arquivo oficial JADX não corresponde ao release")
    # Explicitly validate entries before extraction.
    with zipfile.ZipFile(archive) as zf:
        for z in zf.infolist():
            path = Path(z.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Entrada inválida no ZIP JADX: {z.filename}")
            if z.file_size > APK_MAX_ENTRY:
                raise RuntimeError("Entrada do ZIP JADX excedeu limite")
            if stat.S_IFMT(z.external_attr >> 16) == stat.S_IFLNK:
                raise RuntimeError("ZIP JADX contém link simbólico; abortando")
        zf.extractall(target)
    if not binary.is_file():
        raise RuntimeError("Distribuição baixada não inclui bin/jadx")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    print("JADX portátil disponível em cache. Nenhum pacote do sistema foi alterado.", flush=True)
    return binary


def apk_info(apk: Path) -> str:
    sha = sha256_file(apk)
    print("APK:", apk)
    print("SHA256:", sha)
    with zipfile.ZipFile(apk) as zf:
        dex = [x for x in zf.infolist() if re.fullmatch(r"classes\d*\.dex", Path(x.filename).name)]
        print("DEX contidos:", ", ".join(f"{x.filename} ({x.file_size}B)" for x in dex) or "nenhum")
        if not dex:
            raise RuntimeError("Nenhum classes*.dex no APK")
        for entry in dex:
            if entry.file_size > APK_MAX_ENTRY:
                raise RuntimeError("DEX maior que limite; não extrair")
            data = zf.read(entry)
            terms = [p for p in ("CodeHelper", "d7f010e0", "d7f010e1", "d7f010e2",
                                 "KeyLinker", "encrypt", "BluetoothGatt") if p.lower().encode() in data.lower()]
            print(f"  {entry.filename}: strings aparentes: {', '.join(terms) if terms else '(nenhuma)'}")
    return sha


def source_review(sources: Path) -> None:
    ranked = []
    files = 0
    for file in sources.rglob("*"):
        if not file.is_file() or file.suffix.lower() not in (".java", ".kt"):
            continue
        files += 1
        if file.stat().st_size > 3_000_000:
            continue
        try:
            data = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = 100 if file.name.lower() == "codehelper.java" else 0
        for pat, weight in PATTERNS:
            if pat.search(data):
                score += weight
        if score >= 20:
            ranked.append((score, file, data))
    ranked.sort(key=lambda item: (-item[0], str(item[1])))
    print(f"\nArquivos de código examinados: {files}; candidatos: {len(ranked)}")
    for score, file, _ in ranked[:30]:
        print(f"  score={score:3d} {file.relative_to(sources)}")
    print("\n=== TRECHOS FOCADOS (source code do APK) ===")
    selected = ranked[:8]
    if not selected:
        print("Nenhum arquivo focado. Confira o log de decompilação.")
    for score, file, source in selected:
        print(f"\n--- {file.relative_to(sources)} (score {score}) ---")
        lines = source.splitlines()
        hits = []
        target = re.compile(
            r"\b(?:encrypt|decode|decrypt|scramble|sendDataKey|serialKey|CodeHelper|getSerial|"
            r"writeCharacteristic|onCharacteristicChanged|setCharacteristicNotification|"
            r"writeDescriptor)\b|d7f010e[012]",
            re.I,
        )
        for i, line in enumerate(lines):
            if target.search(line):
                hits.append(i)
        intervals = []
        for i in hits[:22]:
            a, b = max(0, i-5), min(len(lines), i+18)
            if intervals and a <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(intervals[-1][1], b))
            else:
                intervals.append((a, b))
        emitted = 0
        for a, b in intervals:
            for i in range(a, b):
                if emitted >= 170:
                    print("  [limite de 170 linhas por arquivo; arquivo completo no cache]")
                    break
                print(f"{i+1:05d}: {lines[i]}")
                emitted += 1
            if emitted >= 170:
                break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apk", type=Path, required=True)
    ap.add_argument("--fetch-portable-jadx", action="store_true",
                    help="se JADX ausente, baixar release oficial só no cache do usuário")
    ap.add_argument("--fetch-portable-java", action="store_true",
                    help="se Java ausente, baixar Temurin JRE oficial só no cache do usuário")
    args = ap.parse_args()
    apk = args.apk.expanduser().resolve()
    if not apk.is_file():
        print("APK não encontrado:", apk)
        return 2
    sha = apk_info(apk)
    sources = CACHE / f"apk-{sha[:12]}" / "sources"
    if sources.is_dir() and any(sources.rglob("*.java")):
        print("\nUsando decompilação anterior no cache:", sources)
        source_review(sources)
        return 0

    try:
        java = prepare_java(args.fetch_portable_java)
    except Exception as exc:
        print("JRE portátil não pôde ser preparado:", exc)
        return 3
    if java is None:
        print("Java não encontrado. Use --fetch-portable-java para obter Temurin 21 em cache.")
        return 3
    enable_java(java)
    print("Java selecionado:", java, flush=True)
    jadx = get_version_jadx()
    if not jadx and args.fetch_portable_jadx:
        try:
            jadx = retrieve_jadx()
        except Exception as exc:
            print(f"JADX portátil não pôde ser preparado: {exc}")
            return 3
    if not jadx:
        print("\nJADX não localizado. Execute novamente com --fetch-portable-jadx.")
        print("Nenhum pacote do sistema será modificado, e o APK permanece local.")
        return 3

    out = sources.parent
    out.mkdir(parents=True, exist_ok=True)
    log_file = out / "jadx.log"
    cmd = [str(jadx), "--no-res", "--threads-count", "2", "--output-dir", str(out), str(apk)]
    print(f"\nDescompilando APK com {jadx} ...", flush=True)
    print("Saída do JADX:", out)
    try:
        with log_file.open("w") as log:
            completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=420)
    except subprocess.TimeoutExpired:
        print("Limite de 420s atingido; consulte:", log_file)
        return 4
    print("JADX código:", completed.returncode, "| log:", log_file)
    if not sources.is_dir():
        print("Não foi possível gerar fontes. Confira o log:")
        print(log_file.read_text(errors="replace")[-3000:])
        return 4
    source_review(sources)
    print("\nFontes mantidas APENAS no cache local, não adicionadas ao GitHub.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}")
        sys.exit(1)
