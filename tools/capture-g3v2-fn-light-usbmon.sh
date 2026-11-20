#!/usr/bin/env bash
set -euo pipefail

DURATION="${DURATION:-14}"
TMP="$(mktemp /tmp/g3v2-fn-light-usbmon.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

if [[ ${EUID} -ne 0 ]]; then
  echo "Execute como root: sudo bash $0"
  exit 1
fi

usbdev=""
for h in /sys/class/hidraw/hidraw*; do
  [[ -e "$h/device" ]] || continue
  p="$(readlink -f "$h/device")"
  while [[ "$p" != "/" ]]; do
    if [[ -r "$p/idVendor" && -r "$p/idProduct" ]]; then
      vid="$(tr '[:lower:]' '[:upper:]' < "$p/idVendor")"
      pid="$(tr '[:lower:]' '[:upper:]' < "$p/idProduct")"
      if [[ "$vid" == "413D" && "$pid" == "2131" ]]; then
        usbdev="$p"
        break 2
      fi
    fi
    p="$(dirname "$p")"
  done
done

if [[ -z "$usbdev" ]]; then
  echo "G3V2 USB 413D:2131 não encontrado."
  exit 2
fi

bus="$(cat "$usbdev/busnum")"
dev="$(cat "$usbdev/devnum")"
dev3="$(printf '%03d' "$dev")"

modprobe usbmon 2>/dev/null || true
mon="/sys/kernel/debug/usb/usbmon/${bus}u"

if [[ ! -r "$mon" ]]; then
  echo "usbmon não acessível em $mon"
  echo "Verifique se debugfs está montado e usbmon disponível."
  exit 3
fi

echo "============================================================"
echo " G3V2 FN+→ — CAPTURA USBMON SOMENTE LEITURA"
echo "============================================================"
echo "USB sysfs : $usbdev"
echo "Bus/Dev   : $bus/$dev3"
echo "Duração   : ${DURATION}s"
echo
echo "Nenhum comando será enviado ao controle."
echo "A captura apenas observa o barramento USB."
echo

timeout "${DURATION}s" cat "$mon" >"$TMP" &
cap_pid=$!

sleep 2
echo ">>> AGORA pressione FN + → UMA VEZ para trocar o modo de luz <<<"
echo ">>> Depois não pressione mais nada até a captura terminar.       <<<"
echo

wait "$cap_pid" 2>/dev/null || true

echo
echo "=== TODO O TRÁFEGO DO G3V2 NO INTERVALO ==="
grep -E ":[[:space:]]*|$" "$TMP" | grep -E ":[0-9]+:${dev3}:" || true

echo
echo "=== HOST -> DEVICE COM PAYLOAD (OUTPUT URBs) ==="
awk -v bus="$bus" -v dev="$dev3" '
  $4 ~ ("^[CBIZ]o:" bus ":" dev ":") { print }
' "$TMP" || true

echo
echo "=== RESUMO ==="
total="$(grep -E -c ":[0-9]+:${dev3}:" "$TMP" || true)"
out="$(awk -v bus="$bus" -v dev="$dev3" '
  $4 ~ ("^[CBIZ]o:" bus ":" dev ":") { n++ }
  END { print n+0 }
' "$TMP")"
echo "URBs do G3V2 observadas : $total"
echo "URBs host->device       : $out"
echo
if [[ "$out" == "0" ]]; then
  echo "Nenhum payload host->device apareceu durante FN+→."
  echo "Isso favorece fortemente troca de efeito inteiramente local ao firmware."
else
  echo "Houve tráfego host->device; analisar as linhas acima antes de concluir."
fi
