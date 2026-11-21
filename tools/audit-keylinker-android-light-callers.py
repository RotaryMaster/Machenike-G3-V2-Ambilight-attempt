#!/usr/bin/env python3
"""Read-only Android APK xref audit for G3V2 lighting command builders.

Consumes *previously* JADX-decompiled Java sources under the user's cache.
Prints call sites for Android KeyLinker e.l0 (0x37 lighting write), e.X / e.h0
(0x25, CODE_TOOBLE_MODE / Turbo in KeyLinker family), and e.q (B7 read)
without executing
any Android code or touching the BLE/USB controller.

Never modifies the APK, user sources or the immutable Bazzite base.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import sys

METHODS = {
    "l0": "0x37 LampLight write",
    "X": "0x25 CODE_TOOBLE_MODE (Turbo, protocol-family identification)",
    "h0": "0x25 CODE_TOOBLE_MODE (Turbo, protocol-family identification)",
    "q": "0xB7 LampLight read",
}
DIRECT = re.compile(
    r"\b(?:com\.pulsenet\.inputset\.util\.)?e\s*\.\s*A\s*\(\s*\)\s*\.\s*"
    r"(?P<method>l0|X|h0|q)\s*\(",
)
LOOSE = re.compile(r"\b(?P<receiver>[A-Za-z_$][\w$]*)\s*\.\s*"
                   r"(?P<method>l0|X|h0|q)\s*\(")
SEND = re.compile(r"\bBluetoothGatt\b|\bwriteCharacteristic\b|"
                  r"\bsendDeviceData\b|\bP\s*\(\s*\)\s*\.", re.I)

DEFAULT = (Path.home() / ".cache/machenike-keylinker-analysis" /
           "apk-09408b714d31" / "sources")
LIMIT_PER_CLASS = 8


def excerpt(lines: list[str], line_no: int, radius: int) -> None:
    start = max(0, line_no - radius)
    end = min(len(lines), line_no + radius + 1)
    for i in range(start, end):
        flag = ">" if i == line_no else " "
        print(f"  {flag} {i+1:04}: {lines[i].rstrip()[:240]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", type=Path, default=DEFAULT)
    ap.add_argument("--radius", type=int, default=5)
    ap.add_argument("--max-per-kind", type=int, default=24)
    args = ap.parse_args()
    if not 0 <= args.radius <= 12 or not 1 <= args.max_per_kind <= 100:
        ap.error("radius 0..12; max-per-kind 1..100")
    sources = args.sources.expanduser().resolve()
    if not sources.is_dir():
        ap.error(f"Fontes JADX não encontradas: {sources}")

    # Call sites are sought across the app's own package, avoiding library
    # collisions. Any unqualified .l0() match is tentative until inspected.
    package = sources / "com" / "pulsenet" / "inputset"
    if not package.is_dir():
        ap.error(f"Pacote esperado ausente: {package}")

    direct: dict[str, list[tuple[Path, int, list[str]]]] = defaultdict(list)
    tentative: dict[str, list[tuple[Path, int, list[str]]]] = defaultdict(list)
    filecount = 0
    for path in package.rglob("*.java"):
        if path.relative_to(sources).as_posix() == "com/pulsenet/inputset/util/e.java":
            continue
        filecount += 1
        if path.stat().st_size > 3_000_000:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            matched = DIRECT.search(line)
            if matched:
                direct[matched.group("method")].append((path, idx, lines))
                continue
            candidate = LOOSE.search(line)
            if candidate:
                method = candidate.group("method")
                # Obfuscated local variable types must be checked in context.
                # A .q() or .X() call may be entirely unrelated to KeyLinker.
                tentative[method].append((path, idx, lines))

    print("=== KEYLINKER ANDROID — REFERÊNCIAS À ILUMINAÇÃO (OFFLINE) ===")
    print("Fontes:", sources)
    print("Classes examinadas:", filecount)
    print("SEM conexões USB/BLE, SEM comandos ao controle e SEM alterações.")
    print("Construtores APK conhecidos:")
    for method, meaning in METHODS.items():
        print(f"  e.A().{method}(...) => {meaning}")
    for kind, group in (
        ("CHAMADAS DIRETAS ao singleton KeyLinker e.A()", direct),
        ("CANDIDATOS INDIRETOS (exigem checagem de tipo)", tentative),
    ):
        print(f"\n=== {kind} ===")
        for method in METHODS:
            hits = group[method]
            print(f"\n  {method}(): {len(hits)} ocorrência(s)")
            by_path = defaultdict(int)
            printed = 0
            for path, idx, lines in hits:
                if by_path[path] >= LIMIT_PER_CLASS or printed >= args.max_per_kind:
                    continue
                by_path[path] += 1
                printed += 1
                print(f"\n  {path.relative_to(sources)}:{idx+1} ({METHODS[method]})")
                excerpt(lines, idx, args.radius)
                if any(SEND.search(line) for line in lines[max(0, idx-9):idx+10]):
                    print("  [Pista: chamada BLE/transport próxima; conferir contexto]")
            if len(hits) > printed:
                print(f"  ... {len(hits)-printed} ocorrências omitidas pelo limite")
    print("\n=== INTERPRETAÇÃO ===")
    print("0x37 já é uma escrita de configuração comprovada; NÃO usar em loop Ambilight.")
    print("0x25 é CODE_TOOBLE_MODE (Turbo), conforme docs x20ctl da família KeyLinker;")
    print("a atribuição é coerente com o APK, sem validação física separada no G3V2.")
    print("0x37 é o único escritor RGB confirmado neste APK; caminho RAM-only não demonstrado.")
    print("Não enviar 0x25 nem 0x37 em loop ao controle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
